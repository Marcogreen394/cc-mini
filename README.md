# Mini Claude Code

A minimal Python replica of [Claude Code](https://claude.ai/code) — a terminal-based AI coding assistant powered by the Anthropic API.

## Features

- **Interactive REPL** with command history
- **Streaming responses** — text appears as it's generated
- **Tool use loop** — Claude can call tools multiple times per turn
- **5 built-in tools**: file read, file edit, glob, grep, bash
- **Permission system** — reads auto-approved, writes/bash ask for confirmation

## Requirements

- Python 3.11+
- [Anthropic API key](https://console.anthropic.com/)

## Installation

```bash
cd /path/to/cc-mini
pip install -e ".[dev]"
```

## Usage

### Set API key

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

You can also set a custom API base URL, which is useful when targeting a proxy or an Anthropic-compatible endpoint:

```bash
export ANTHROPIC_BASE_URL=https://your-gateway.example.com
```

Optional environment variables for runtime defaults:

```bash
export MINI_CLAUDE_MODEL=claude-sonnet-4-5
export MINI_CLAUDE_MAX_TOKENS=64000
```

### Interactive REPL

```bash
python3 -m mini_claude.main
```

```
Mini Claude Code  type 'exit' or Ctrl+C to quit

> list all python files in this project
↳ Glob(**/*.py) ✓
Here are all the .py files...

> read engine.py and explain how the tool loop works
↳ Read(mini_claude/engine.py) ✓
The submit() method implements an agentic loop...
```

Type `exit` or press `Ctrl+C` to quit.

### One-shot prompt

Pass a prompt directly as an argument:

```bash
python3 -m mini_claude.main "what tests exist in this project?"
```

### Non-interactive / scripted mode

Use `-p` to print the response and exit (no REPL):

```bash
python3 -m mini_claude.main -p "summarize this codebase in 3 bullets"
```

Pipe input:

```bash
echo "what does engine.py do?" | python3 -m mini_claude.main -p
```

### Auto-approve permissions

Skip permission prompts for all tools (use with care):

```bash
python3 -m mini_claude.main --auto-approve
```

### Configure API endpoint and model from CLI

```bash
python3 -m mini_claude.main \
  --base-url https://your-gateway.example.com \
  --api-key sk-ant-... \
  --model claude-sonnet-4
```

`max_tokens` now follows the selected model by default. Override it only when you need a tighter cap:

```bash
python3 -m mini_claude.main --model claude-3-5-haiku --max-tokens 2048
```

### Configure with a TOML file

Mini Claude looks for config files in these locations, in order:

1. `~/.config/mini-claude/config.toml`
2. `.mini-claude.toml` in the current working directory

The project-local file overrides the home config. You can also point to a specific file with `--config`.

Example:

```toml
[anthropic]
api_key = "sk-ant-..."
base_url = "https://your-gateway.example.com"
model = "claude-sonnet-4"
```

Top-level keys are also supported:

```toml
api_key = "sk-ant-..."
base_url = "https://your-gateway.example.com"
model = "claude-3-7-sonnet"
max_tokens = 64000
```

## Tools

| Tool | Name | Permission |
|------|------|------------|
| Read file | `Read` | auto-approved |
| Find files | `Glob` | auto-approved |
| Search content | `Grep` | auto-approved |
| Edit file | `Edit` | requires confirmation |
| Run command | `Bash` | requires confirmation |

### Permission prompt

When Claude wants to run a write or bash tool, you'll see:

```
Permission required: Bash
  command: pytest tests/ -v

  Allow? [y]es / [n]o / [a]lways:
```

- `y` — allow once
- `n` — deny (Claude sees "Permission denied")
- `a` — always allow this tool for the rest of the session

## Project structure

```
mini_claude/
├── main.py         # CLI entry point + REPL
├── engine.py       # Streaming API loop + tool execution
├── context.py      # System prompt (git status, date, CLAUDE.md)
├── permissions.py  # Permission checker
└── tools/
    ├── base.py     # Tool ABC + ToolResult
    ├── file_read.py
    ├── file_edit.py
    ├── glob_tool.py
    ├── grep_tool.py
    └── bash.py
```

## Running tests

```bash
pytest tests/ -v
```

## Tips

- Place a `CLAUDE.md` file in your project root — it will be included in the system prompt automatically
- Use `--auto-approve` when running non-interactively or for trusted tasks
- The REPL keeps conversation history within a session; each new run starts fresh
