"""Loads foreman.toml once into a frozen value."""

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from foreman.errors import ConfigError


@dataclass(frozen=True)
class Reviewer:
    role: str  # e.g. "diff-reviewer"
    paths: list[str]  # runs when the group changed a path equal to or under one


@dataclass(frozen=True)
class Config:
    target_repo: Path  # the repository being built (Gauntlet)
    worktree_root: Path  # where step worktrees are made
    check_cmd: list[str]  # e.g. ["make", "check"]
    format_cmd: list[str]  # e.g. ["ruff", "format"]
    backend: str  # "claude-local" or "codex-local"
    state_dir: Path  # holds events.jsonl
    reviewers: list[Reviewer]  # [[reviewers]] tables, in run order
    view_cmd: list[str]  # the stubgen argv, e.g. ["uv", "run", "stubgen"]
    view_root: Path  # outside every worktree; views live under view_root/<group id>/
    batch_budget: int  # enclosing-unit lines per finding batch
    dispatch_seconds: int  # backend run timeout
    juror_rounds: int = 2  # attempts per failing step or group-check context
    page_chars: int = 3000  # 7.1f "about a page": characters of a rendered brief
    red_behaviors: int = 6  # 7.1f dispatch cap: behaviors per red brief


_PATHS = ("target_repo", "worktree_root", "state_dir", "view_root")
_LISTS = ("check_cmd", "format_cmd", "view_cmd")
_REQUIRED_INTS = ("batch_budget", "dispatch_seconds")
_OPTIONAL_INTS = ("juror_rounds", "page_chars", "red_behaviors")
_DEFAULTS = {f.name: f.default for f in fields(Config) if f.name in _OPTIONAL_INTS}
_REVIEWER_ROLES = ("diff-reviewer", "test-reviewer")


def _str_list(key: str, value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{key} must be a list of strings")
    return list(value)


def _reviewers(value: object, *, where: str) -> list[Reviewer]:
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise ConfigError(f"{where}: reviewers must be [[reviewers]] tables")
    out: list[Reviewer] = []
    for index, table in enumerate(value):
        entry = f"{where}.{index}"
        if "spec" in table:
            raise ConfigError(
                f"{entry}.spec: removed; put explicit specs in the group's "
                "test-reviewer review form"
            )
        missing = [k for k in ("role", "paths") if k not in table]
        if missing:
            raise ConfigError(f"{entry}: missing keys: {', '.join(missing)}")
        unknown = sorted(set(table) - {"role", "paths"})
        if unknown:
            raise ConfigError(f"{entry}: unknown keys: {', '.join(unknown)}")
        if not isinstance(table["role"], str) or not table["role"]:
            raise ConfigError(f"{entry}.role must be a non-empty string")
        if table["role"] not in _REVIEWER_ROLES:
            raise ConfigError(
                f"{entry}.role: unsupported reviewer role {table['role']!r}; "
                f"expected one of: {', '.join(_REVIEWER_ROLES)}"
            )
        out.append(
            Reviewer(
                table["role"],
                _str_list(f"{entry}.paths", table["paths"]),
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

    required = (*_PATHS, *_LISTS, *_REQUIRED_INTS, "backend")
    missing = [k for k in required if k not in data]
    if missing:
        raise ConfigError(f"{path}: missing keys: {', '.join(missing)}")
    unknown = sorted(set(data) - set(required) - {"reviewers", *_OPTIONAL_INTS})
    if unknown:
        raise ConfigError(f"{path}: unknown keys: {', '.join(unknown)}")

    values: dict[str, object] = {}
    for key in _PATHS:
        if not isinstance(data[key], str):
            raise ConfigError(f"{key} must be a string path")
        values[key] = Path(data[key]).expanduser()
    for key in _LISTS:
        values[key] = _str_list(key, data[key])
    for key in (*_REQUIRED_INTS, *_OPTIONAL_INTS):
        value = data[key] if key in data else _DEFAULTS[key]
        if type(value) is not int or value < 1:
            raise ConfigError(f"{path}: {key} must be an integer >= 1")
        values[key] = value
    if not isinstance(data["backend"], str):
        raise ConfigError("backend must be a string")
    values["backend"] = data["backend"]
    values["reviewers"] = _reviewers(
        data.get("reviewers", []), where=f"{path}: reviewers"
    )
    return Config(**values)  # type: ignore[arg-type]
