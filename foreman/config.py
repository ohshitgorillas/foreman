"""Loads foreman.toml once into a frozen value."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from foreman.errors import ConfigError


@dataclass(frozen=True)
class Config:
    target_repo: Path  # the repository being built (Gauntlet)
    worktree_root: Path  # where step worktrees are made
    check_cmd: list[str]  # e.g. ["make", "check"]
    format_cmd: list[str]  # e.g. ["ruff", "format"]
    standing_lines: list[str]
    backend: str  # "claude-local" in the first version
    state_dir: Path  # holds events.jsonl


_PATHS = ("target_repo", "worktree_root", "state_dir")
_LISTS = ("check_cmd", "format_cmd", "standing_lines")


def _str_list(key: str, value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{key} must be a list of strings")
    return list(value)


def load(path: Path) -> Config:
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from e

    required = (*_PATHS, *_LISTS, "backend")
    missing = [k for k in required if k not in data]
    if missing:
        raise ConfigError(f"{path}: missing keys: {', '.join(missing)}")
    unknown = sorted(set(data) - set(required))
    if unknown:
        raise ConfigError(f"{path}: unknown keys: {', '.join(unknown)}")

    values: dict[str, object] = {}
    for key in _PATHS:
        if not isinstance(data[key], str):
            raise ConfigError(f"{key} must be a string path")
        values[key] = Path(data[key]).expanduser()
    for key in _LISTS:
        values[key] = _str_list(key, data[key])
    if not isinstance(data["backend"], str):
        raise ConfigError("backend must be a string")
    values["backend"] = data["backend"]
    return Config(**values)  # type: ignore[arg-type]
