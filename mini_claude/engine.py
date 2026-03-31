from __future__ import annotations
import time
from typing import Iterator
import anthropic
from .config import DEFAULT_MODEL, default_max_tokens_for_model, resolve_model
from .tools.base import Tool, ToolResult
from .permissions import PermissionChecker

_MAX_RETRIES = 3
_RETRY_BACKOFF = (1, 3, 10)


class AbortedError(Exception):
    """Raised when the current turn is aborted by the user (Esc / Ctrl+C)."""


class Engine:
    def __init__(self, tools: list[Tool], system_prompt: str,
                 permission_checker: PermissionChecker,
                 model: str = DEFAULT_MODEL,
                 max_tokens: int | None = None,
                 api_key: str | None = None,
                 base_url: str | None = None):
        self._model = resolve_model(model)
        self._max_tokens = max_tokens or default_max_tokens_for_model(self._model)
        self._client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
        self._tools = {t.name: t for t in tools}
        self._system_prompt = system_prompt
        self._permissions = permission_checker
        self._messages: list[dict] = []
        self._aborted = False
        self._active_stream = None  # reference to current HTTP stream

    def abort(self):
        """Abort the current turn immediately.

        Matches claude-code-main's AbortController.abort(): sets flag and
        closes the active HTTP stream so the generator unblocks at once.
        """
        self._aborted = True
        if self._active_stream is not None:
            try:
                self._active_stream.close()
            except Exception:
                pass

    def cancel_turn(self):
        """Roll back messages to the state before the current turn started."""
        while self._messages:
            last = self._messages[-1]
            if last["role"] == "assistant":
                break
            self._messages.pop()
        if self._messages and self._messages[-1]["role"] == "assistant":
            content = self._messages[-1].get("content", [])
            has_tool_use = any(
                getattr(b, "type", None) == "tool_use"
                or (isinstance(b, dict) and b.get("type") == "tool_use")
                for b in (content if isinstance(content, list) else [])
            )
            if has_tool_use:
                self._messages.pop()
                if self._messages and self._messages[-1]["role"] == "user":
                    last_content = self._messages[-1].get("content", "")
                    if isinstance(last_content, list) and all(
                        isinstance(c, dict) and c.get("type") == "tool_result"
                        for c in last_content
                    ):
                        self._messages.pop()

    def submit(self, user_input: str | list) -> Iterator[tuple]:
        """Send user message; yield events until the conversation turn completes.

        Yields:
          ("text", str)                         — streamed text chunk
          ("tool_call", name, input)            — before each tool executes
          ("tool_result", name, input, result)  — after each tool executes
          ("waiting",)                          — text done, waiting for tool_use
          ("error", str)                        — non-fatal API error shown to user

        Raises:
          AbortedError — if abort() was called (by Esc listener or Ctrl+C)
        """
        self._aborted = False
        self._messages.append({"role": "user", "content": user_input})

        try:
            while True:
                if self._aborted:
                    raise AbortedError()

                tool_uses = []

                # API call with retry
                final = None
                for attempt in range(_MAX_RETRIES):
                    try:
                        with self._client.messages.stream(
                            model=self._model,
                            max_tokens=self._max_tokens,
                            system=self._system_prompt,
                            tools=[t.to_api_schema() for t in self._tools.values()],
                            messages=self._messages,
                        ) as stream:
                            self._active_stream = stream
                            got_text = False
                            for text in stream.text_stream:
                                if self._aborted:
                                    raise AbortedError()
                                got_text = True
                                yield ("text", text)

                            if self._aborted:
                                raise AbortedError()

                            if got_text:
                                yield ("waiting",)

                            final = stream.get_final_message()
                            for block in final.content:
                                if block.type == "tool_use":
                                    tool_uses.append(block)
                        break  # success, exit retry loop
                    except AbortedError:
                        raise
                    except anthropic.AuthenticationError as e:
                        self._messages.pop()
                        yield ("error", f"Authentication failed: {e.message}")
                        return
                    except (anthropic.RateLimitError, anthropic.APIConnectionError,
                            anthropic.InternalServerError) as e:
                        if attempt < _MAX_RETRIES - 1:
                            wait = _RETRY_BACKOFF[attempt]
                            yield ("error", f"API error, retrying in {wait}s... ({e})")
                            time.sleep(wait)
                        else:
                            self._messages.pop()
                            yield ("error", f"API error after {_MAX_RETRIES} retries: {e}")
                            return
                    except anthropic.APIError as e:
                        self._messages.pop()
                        yield ("error", f"API error: {e.message}")
                        return
                    except Exception:
                        if self._aborted:
                            raise AbortedError()
                        raise
                    finally:
                        self._active_stream = None

                if final is None:
                    self._messages.pop()
                    return

                self._messages.append({"role": "assistant", "content": final.content})

                if not tool_uses:
                    break

                tool_results = []
                for tool_use in tool_uses:
                    if self._aborted:
                        raise AbortedError()
                    yield ("tool_call", tool_use.name, tool_use.input)
                    result = self._execute_tool(tool_use)
                    yield ("tool_result", tool_use.name, tool_use.input, result)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": result.content,
                        "is_error": result.is_error,
                    })

                self._messages.append({"role": "user", "content": tool_results})
        except AbortedError:
            self.cancel_turn()
            raise

    def _execute_tool(self, tool_use) -> ToolResult:
        tool = self._tools.get(tool_use.name)
        if tool is None:
            return ToolResult(content=f"Unknown tool: {tool_use.name}", is_error=True)

        if self._permissions.check(tool, tool_use.input) == "deny":
            return ToolResult(content="Permission denied.", is_error=True)

        try:
            return tool.execute(**tool_use.input)
        except Exception as e:
            return ToolResult(content=f"Tool error: {e}", is_error=True)
