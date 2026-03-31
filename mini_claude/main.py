from __future__ import annotations

import argparse
import base64
import mimetypes
import re
import sys
import time
import threading
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from .config import load_app_config
from .context import build_system_prompt
from .engine import AbortedError, Engine
from ._keylistener import EscListener
from .permissions import PermissionChecker
from .tools.bash import BashTool
from .tools.file_edit import FileEditTool
from .tools.file_read import FileReadTool
from .tools.file_write import FileWriteTool
from .tools.glob_tool import GlobTool
from .tools.grep_tool import GrepTool

console = Console()
_HISTORY_FILE = Path.home() / ".mini_claude_history"

# Match claude-code-main: useDoublePress DOUBLE_PRESS_TIMEOUT_MS = 800
_DOUBLE_PRESS_TIMEOUT_MS = 0.8


def _tool_preview(tool_name: str, tool_input: dict) -> str:
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return cmd[:80] + ("…" if len(cmd) > 80 else "")
    if tool_name in ("Read", "Edit", "Write"):
        fp = tool_input.get("file_path", "")
        return fp[-60:] if len(fp) > 60 else fp
    if tool_name in ("Glob", "Grep"):
        return tool_input.get("pattern", "")
    return ""


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_IMG_PATH_RE = re.compile(r"@(\S+)")


def _parse_input(text: str) -> str | list:
    """Parse user input, extracting @path image references into content blocks.

    Returns plain string if no images, or a list of content blocks if images found.
    """
    matches = list(_IMG_PATH_RE.finditer(text))
    if not matches:
        return text

    image_blocks = []
    for m in matches:
        fpath = Path(m.group(1))
        if not fpath.suffix.lower() in _IMAGE_EXTS:
            continue
        if not fpath.exists():
            continue
        media_type = mimetypes.guess_type(str(fpath))[0] or "image/png"
        data = base64.standard_b64encode(fpath.read_bytes()).decode("ascii")
        image_blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        })

    if not image_blocks:
        return text

    # Remove @path tokens from text
    cleaned = _IMG_PATH_RE.sub("", text).strip()
    content: list[dict] = list(image_blocks)
    if cleaned:
        content.append({"type": "text", "text": cleaned})
    return content


class _SpinnerManager:
    """Manages a Rich Live spinner that shows while waiting for API/tool responses.

    Matches claude-code-main's spinner behavior: show a spinning indicator
    with contextual text while the model is thinking or tools are executing.
    """

    def __init__(self, console: Console):
        self._console = console
        self._live: Live | None = None
        self._spinner_text = "Thinking…"

    def start(self, text: str = "Thinking…"):
        self._spinner_text = text
        self._live = Live(
            Spinner("dots", text=Text(self._spinner_text, style="dim")),
            console=self._console,
            refresh_per_second=12,
        )
        self._live.start()

    def update(self, text: str):
        self._spinner_text = text
        if self._live is not None:
            self._live.update(
                Spinner("dots", text=Text(self._spinner_text, style="dim"))
            )

    def stop(self):
        if self._live is not None:
            # Clear spinner line: update to empty then stop
            self._live.update("")
            self._live.stop()
            self._live = None


def run_query(engine: Engine, user_input: str | list, print_mode: bool,
              permissions: PermissionChecker | None = None) -> None:
    """Run a single turn. Ctrl+C or Esc cancels the active turn."""
    listener = EscListener(on_cancel=engine.abort)
    if permissions:
        permissions.set_esc_listener(listener)

    spinner = _SpinnerManager(console)
    first_text = True
    streaming = False

    try:
        with listener:
            spinner.start("Thinking…")

            for event in engine.submit(user_input):
                if streaming and listener.check_esc_nonblocking():
                    spinner.stop()
                    engine.cancel_turn()
                    console.print("\n[dim yellow]⏹ Turn cancelled (Esc)[/dim yellow]")
                    return

                if event[0] == "text":
                    if first_text:
                        spinner.stop()
                        listener.pause()
                        streaming = True
                        first_text = False
                    if print_mode:
                        print(event[1], end="", flush=True)
                    else:
                        console.print(event[1], end="", markup=False)

                elif event[0] == "waiting":
                    streaming = False
                    listener.resume()
                    spinner.start("Preparing tool call…")

                elif event[0] == "tool_call":
                    spinner.stop()
                    streaming = False
                    listener.pause()
                    _, tool_name, tool_input = event
                    preview = _tool_preview(tool_name, tool_input)
                    console.print(f"\n[dim]↳ {tool_name}({preview}) …[/dim]")

                elif event[0] == "tool_result":
                    _, tool_name, tool_input, result = event
                    status = "[red]✗[/red]" if result.is_error else "[green]✓[/green]"
                    console.print(f"[dim]  {status} done[/dim]")
                    if result.is_error:
                        console.print(f"  [red]{result.content[:300]}[/red]")
                    streaming = False
                    listener.resume()
                    spinner.start("Thinking…")
                    first_text = True

                elif event[0] == "error":
                    spinner.stop()
                    console.print(f"\n[bold red]{event[1]}[/bold red]")

            spinner.stop()
    except (AbortedError, KeyboardInterrupt):
        spinner.stop()
        if not isinstance(sys.exc_info()[1], AbortedError):
            engine.cancel_turn()
        console.print("\n[dim yellow]⏹ Turn cancelled[/dim yellow]")
        return
    finally:
        spinner.stop()
        if permissions:
            permissions.set_esc_listener(None)

    if not print_mode:
        console.print()


def main() -> None:
    parser = argparse.ArgumentParser(prog="mini-claude",
                                     description="Minimal Python Claude Code")
    parser.add_argument("prompt", nargs="?", help="Prompt to send (optional)")
    parser.add_argument("-p", "--print", action="store_true",
                        help="Non-interactive: print response and exit")
    parser.add_argument("--auto-approve", action="store_true",
                        help="Auto-approve all tool permissions (dangerous)")
    parser.add_argument("--config", help="Path to a TOML config file")
    parser.add_argument("--api-key", help="Anthropic API key")
    parser.add_argument("--base-url", help="Anthropic-compatible API base URL")
    parser.add_argument("--model", help="Model name, e.g. claude-sonnet-4")
    parser.add_argument("--max-tokens", type=int,
                        help="Maximum output tokens for each model response")
    args = parser.parse_args()

    try:
        app_config = load_app_config(args)
    except ValueError as exc:
        parser.error(str(exc))

    tools = [FileReadTool(), GlobTool(), GrepTool(), FileEditTool(), FileWriteTool(), BashTool()]
    system_prompt = build_system_prompt()
    permissions = PermissionChecker(auto_approve=args.auto_approve)
    engine = Engine(
        tools=tools,
        system_prompt=system_prompt,
        permission_checker=permissions,
        api_key=app_config.api_key,
        base_url=app_config.base_url,
        model=app_config.model,
        max_tokens=app_config.max_tokens,
    )

    # Non-interactive / piped
    if args.print or args.prompt:
        prompt_text = args.prompt or sys.stdin.read()
        run_query(engine, _parse_input(prompt_text), print_mode=args.print, permissions=permissions)
        return

    # Interactive REPL
    config_note = f"[dim]{app_config.model} · max_tokens={app_config.max_tokens}[/dim]"
    console.print("[bold cyan]Mini Claude Code[/bold cyan]  "
                  f"{config_note}  "
                  "[dim]Esc or Ctrl+C to cancel, Ctrl+C twice to exit[/dim]")
    console.print('[dim]Enter to send, Alt+Enter for newline[/dim]\n')

    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _(event):
        event.current_buffer.insert_text("\n")

    session: PromptSession = PromptSession(
        history=FileHistory(str(_HISTORY_FILE)),
        key_bindings=kb,
    )

    # Track last Ctrl+C time for double-press exit (matches useDoublePress)
    last_ctrlc_time = 0.0

    while True:
        try:
            user_input = session.prompt("\n> ").strip()
        except KeyboardInterrupt:
            now = time.monotonic()
            if now - last_ctrlc_time <= _DOUBLE_PRESS_TIMEOUT_MS:
                console.print("\n[dim]Goodbye.[/dim]")
                break
            last_ctrlc_time = now
            console.print("\n[dim yellow]Press Ctrl+C again to exit[/dim yellow]")
            continue
        except EOFError:
            console.print("\n[dim]Goodbye.[/dim]")
            break

        # Reset double-press timer on any normal input
        last_ctrlc_time = 0.0

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            console.print("[dim]Goodbye.[/dim]")
            break

        run_query(engine, _parse_input(user_input), print_mode=False, permissions=permissions)


if __name__ == "__main__":
    main()
