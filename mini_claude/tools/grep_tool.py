import re
import subprocess
from pathlib import Path
import glob as glob_module
from .base import Tool, ToolResult


class GrepTool(Tool):
    name = "Grep"
    description = (
        "Search for a regex pattern in files. "
        "Uses ripgrep if available, falls back to Python re. "
        "output_mode='files_with_matches' returns paths; 'content' returns matching lines."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern"},
            "path": {"type": "string", "description": "Directory or file to search"},
            "glob": {"type": "string", "description": "File glob filter e.g. '*.py'"},
            "output_mode": {
                "type": "string",
                "enum": ["files_with_matches", "content"],
                "default": "files_with_matches",
            },
            "-i": {"type": "boolean", "description": "Case insensitive", "default": False},
            "-C": {"type": "integer", "description": "Context lines around each match", "default": 0},
        },
        "required": ["pattern"],
    }

    def is_read_only(self) -> bool:
        return True

    def execute(self, pattern: str, path: str = ".", glob: str | None = None,
                output_mode: str = "files_with_matches", **kwargs) -> ToolResult:
        cmd = ["rg", "--no-heading"]
        if kwargs.get("-i"):
            cmd.append("-i")
        context = kwargs.get("-C", 0)
        if context:
            cmd.extend(["-C", str(context)])
        cmd.append("-l" if output_mode == "files_with_matches" else "-n")
        if glob:
            cmd.extend(["-g", glob])
        cmd.extend([pattern, path])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            output = result.stdout.strip()
            return ToolResult(content=output if output else "No matches found.")
        except FileNotFoundError:
            return self._python_grep(pattern, path, glob, kwargs.get("-i", False))
        except subprocess.TimeoutExpired:
            return ToolResult(content="Error: Search timed out.", is_error=True)

    def _python_grep(self, pattern: str, path: str, glob_filter: str | None,
                     case_insensitive: bool) -> ToolResult:
        base = Path(path)
        flags = re.IGNORECASE if case_insensitive else 0

        if base.is_file():
            files = [base]
        else:
            pat = glob_filter or "**/*"
            files = [base / p for p in glob_module.glob(pat, root_dir=str(base), recursive=True)]

        matched = []
        for f in files:
            if not f.is_file():
                continue
            try:
                if re.search(pattern, f.read_text(encoding="utf-8", errors="replace"), flags):
                    matched.append(str(f))
            except OSError:
                pass

        return ToolResult(content="\n".join(matched) if matched else "No matches found.")
