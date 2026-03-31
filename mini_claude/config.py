from __future__ import annotations

import os
import tomllib
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "claude-sonnet-4-20250514"
_FALLBACK_MAX_TOKENS = 8192
_MODEL_ALIASES = {
    "claude-opus-4.6": "claude-opus-4-6",
    "claude-opus-4.1": "claude-opus-4-1",
    "claude-opus-4": "claude-opus-4",
    "claude-sonnet-4.6": "claude-sonnet-4-6",
    "claude-sonnet-4": "claude-sonnet-4",
    "claude-3.7-sonnet": "claude-3-7-sonnet",
    "claude-3.5-sonnet": "claude-3-5-sonnet",
    "claude-3.5-haiku": "claude-3-5-haiku",
    "claude-3-haiku": "claude-3-haiku",
}
_MODEL_MAX_TOKENS = (
    ("claude-opus-4-6", 32000),
    ("claude-opus-4-1", 32000),
    ("claude-opus-4", 32000),
    ("claude-sonnet-4-6", 64000),
    ("claude-sonnet-4", 64000),
    ("claude-3-7-sonnet", 64000),
    ("claude-3-5-sonnet", 8192),
    ("claude-3-5-haiku", 8192),
    ("claude-3-haiku", 4096),
)
_ENV_MODEL = "MINI_CLAUDE_MODEL"
_ENV_MAX_TOKENS = "MINI_CLAUDE_MAX_TOKENS"
_DEFAULT_CONFIG_PATHS = (
    Path.home() / ".config" / "mini-claude" / "config.toml",
    Path.cwd() / ".mini-claude.toml",
)


@dataclass(frozen=True)
class AppConfig:
    api_key: str | None
    base_url: str | None
    model: str
    max_tokens: int
    config_paths: tuple[Path, ...] = ()


def resolve_model(model: str | None) -> str:
    if not model:
        return DEFAULT_MODEL
    normalized = model.strip()
    return _MODEL_ALIASES.get(normalized, normalized)


def default_max_tokens_for_model(model: str | None) -> int:
    resolved = resolve_model(model)
    for prefix, limit in _MODEL_MAX_TOKENS:
        if resolved.startswith(prefix):
            return limit
    return _FALLBACK_MAX_TOKENS


def load_app_config(args: Namespace) -> AppConfig:
    file_values, config_paths = _load_file_values(args.config)
    env_values = _load_env_values()

    raw_model = args.model or env_values.get("model") or file_values.get("model")
    model = resolve_model(raw_model)

    raw_max_tokens = (
        args.max_tokens
        if args.max_tokens is not None
        else env_values.get("max_tokens", file_values.get("max_tokens"))
    )
    max_tokens = _parse_max_tokens(raw_max_tokens, default=default_max_tokens_for_model(model))

    return AppConfig(
        api_key=args.api_key or env_values.get("api_key") or file_values.get("api_key"),
        base_url=args.base_url or env_values.get("base_url") or file_values.get("base_url"),
        model=model,
        max_tokens=max_tokens,
        config_paths=config_paths,
    )


def _load_file_values(explicit_path: str | None) -> tuple[dict[str, Any], tuple[Path, ...]]:
    values: dict[str, Any] = {}
    loaded_paths: list[Path] = []

    if explicit_path:
        path = Path(explicit_path).expanduser()
        if not path.exists():
            raise ValueError(f"Config file not found: {path}")
        values.update(_read_config_file(path))
        loaded_paths.append(path)
        return values, tuple(loaded_paths)

    for path in _DEFAULT_CONFIG_PATHS:
        if not path.exists():
            continue
        values.update(_read_config_file(path))
        loaded_paths.append(path)

    return values, tuple(loaded_paths)


def _read_config_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid TOML in config file {path}: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"Could not read config file {path}: {exc}") from exc

    values: dict[str, Any] = {}
    anthropic_section = data.get("anthropic", {})
    if isinstance(anthropic_section, dict):
        values.update(anthropic_section)

    for key in ("api_key", "base_url", "model", "max_tokens"):
        if key in data:
            values[key] = data[key]

    return values


def _load_env_values() -> dict[str, Any]:
    values: dict[str, Any] = {}
    if os.getenv("ANTHROPIC_API_KEY"):
        values["api_key"] = os.environ["ANTHROPIC_API_KEY"]
    if os.getenv("ANTHROPIC_BASE_URL"):
        values["base_url"] = os.environ["ANTHROPIC_BASE_URL"]
    if os.getenv(_ENV_MODEL):
        values["model"] = os.environ[_ENV_MODEL]
    if os.getenv(_ENV_MAX_TOKENS):
        values["max_tokens"] = os.environ[_ENV_MAX_TOKENS]
    return values


def _parse_max_tokens(raw_value: Any, default: int) -> int:
    if raw_value is None:
        return default

    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid max_tokens value: {raw_value!r}") from exc

    if value <= 0:
        raise ValueError("max_tokens must be a positive integer")
    return value
