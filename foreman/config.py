"""Loads foreman.toml once into a frozen value."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from foreman.errors import ConfigError


@dataclass(frozen=True)
class Reviewer:
    role: str  # e.g. "diff-reviewer"
    paths: list[str]  # runs when the group changed a path equal to or under one
    spec: bool  # its brief adds "spec: <Group.design>"


@dataclass(frozen=True)
class Config:
    target_repo: Path  # the repository being built (Gauntlet)
    worktree_root: Path  # where step worktrees are made
    check_cmd: list[str]  # e.g. ["make", "check"]
    format_cmd: list[str]  # e.g. ["ruff", "format"]
    backend: str  # "claude-local" in the first version
    state_dir: Path  # holds events.jsonl
    reviewers: list[Reviewer]  # [[reviewers]] tables, in run order


_PATHS = ("target_repo", "worktree_root", "state_dir")
_LISTS = ("check_cmd", "format_cmd")


def _str_list(key: str, value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{key} must be a list of strings")
    return list(value)


def _reviewers(value: object) -> list[Reviewer]:
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise ConfigError("reviewers must be [[reviewers]] tables")
    out: list[Reviewer] = []
    for table in value:
        missing = [k for k in ("role", "paths", "spec") if k not in table]
        if missing:
            raise ConfigError(f"reviewers: missing keys: {', '.join(missing)}")
        unknown = sorted(set(table) - {"role", "paths", "spec"})
        if unknown:
            raise ConfigError(f"reviewers: unknown keys: {', '.join(unknown)}")
        if not isinstance(table["role"], str) or not table["role"]:
            raise ConfigError("reviewers: role must be a non-empty string")
        if not isinstance(table["spec"], bool):
            raise ConfigError("reviewers: spec must be a boolean")
        out.append(
            Reviewer(
                table["role"],
                _str_list("reviewers.paths", table["paths"]),
                table["spec"],
            )
        )
    return out


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
    unknown = sorted(set(data) - set(required) - {"reviewers"})
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
    values["reviewers"] = _reviewers(data.get("reviewers", []))
    return Config(**values)  # type: ignore[arg-type]
