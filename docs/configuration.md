# Configuration

## API Keys

### Anthropic (default)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export ANTHROPIC_BASE_URL=https://your-gateway.example.com  # optional
```

### OpenAI-compatible

```bash
export CC_MINI_PROVIDER=openai
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://your-openai-gateway.example.com
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `CC_MINI_MODEL` | Model name (e.g. `claude-sonnet-4-5`) |
| `CC_MINI_MAX_TOKENS` | Max output tokens |
| `CC_MINI_EFFORT` | Reasoning effort (`low`, `medium`, `high`) |
| `CC_MINI_PROVIDER` | `anthropic` or `openai` |
| `CC_MINI_BUDDY_MODEL` | Model for companion pet reactions |
| `CC_MINI_BUDDY_SEED` | Override buddy seed for specific companion |

## CLI Flags

```bash
cc-mini \
  --provider anthropic \
  --base-url https://your-gateway.example.com \
  --api-key sk-ant-... \
  --model claude-sonnet-4 \
  --max-tokens 64000 \
  --auto-approve \
  --coordinator \
  --resume 1
```

## TOML Config Files

Loaded in order (later overrides earlier):

1. `~/.config/cc-mini/config.toml`
2. `.cc-mini.toml` in the current working directory

Point to a specific file with `--config`.

### Anthropic example

```toml
provider = "anthropic"

[anthropic]
api_key = "sk-ant-..."
base_url = "https://your-gateway.example.com"
model = "claude-sonnet-4"
```

### OpenAI example

```toml
provider = "openai"

[openai]
api_key = "sk-..."
base_url = "https://your-openai-gateway.example.com/v1"
model = "gpt-4.1-mini"
max_tokens = 8192
effort = "medium"
buddy_model = "gpt-4.1-mini"
```

### OpenRouter (low-cost testing)

```toml
provider = "openai"

[openai]
api_key = "sk-or-..."
base_url = "https://openrouter.ai/api/v1"
model = "qwen/qwen3.6-plus-preview:free"
```

When `provider = "openai"`, `OPENAI_API_KEY` / `OPENAI_BASE_URL` are used. When `provider = "anthropic"`, `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` are used.
