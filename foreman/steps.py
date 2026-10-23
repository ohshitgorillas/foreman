"""Reads a milestone's step file, lints steps, and picks the next ready step."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from foreman.errors import ConfigError

MAX_PATHS = 5
DONE_CHECKS = 1


@dataclass(frozen=True)
class Step:
    id: str
    paths: list[str]  # 5 or fewer
    done_check: list[str]  # exactly one command
    design_ref: str
    depends_on: list[str]
    size: str


_LISTS = ("paths", "done_check", "depends_on")
_STRS = ("design_ref", "size")


def _str_list(where: str, key: str, value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where}: {key} must be a list of strings")
    return list(value)


def load(path: Path) -> list[Step]:
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from e

    tables = data.get("steps")
    if not isinstance(tables, dict) or not tables:
        raise ConfigError(f"{path}: no [steps.<id>] tables")
    unknown_top = sorted(set(data) - {"steps"})
    if unknown_top:
        raise ConfigError(f"{path}: unknown keys: {', '.join(unknown_top)}")

    required = (*_LISTS, *_STRS)
    steps: list[Step] = []
    for step_id, table in tables.items():
        where = f"{path}: steps.{step_id}"
        if not isinstance(table, dict):
            raise ConfigError(f"{where} must be a table")
        missing = [k for k in required if k not in table]
        if missing:
            raise ConfigError(f"{where}: missing keys: {', '.join(missing)}")
        unknown = sorted(set(table) - set(required))
        if unknown:
            raise ConfigError(f"{where}: unknown keys: {', '.join(unknown)}")
        for key in _STRS:
            if not isinstance(table[key], str):
                raise ConfigError(f"{where}: {key} must be a string")
        steps.append(
            Step(
                id=step_id,
                paths=_str_list(where, "paths", table["paths"]),
                done_check=_str_list(where, "done_check", table["done_check"]),
                design_ref=table["design_ref"],
                depends_on=_str_list(where, "depends_on", table["depends_on"]),
                size=table["size"],
            )
        )
    return steps


def lint(steps: list[Step]) -> list[str]:
    """Return the problems found; an empty list means clean."""
    problems: list[str] = []
    ids = [s.id for s in steps]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"{dup}: duplicate step id")
    known = set(ids)
    for s in steps:
        if not s.paths:
            problems.append(f"{s.id}: no paths")
        elif len(s.paths) > MAX_PATHS:
            problems.append(f"{s.id}: {len(s.paths)} paths, more than {MAX_PATHS}")
        if len(s.done_check) != DONE_CHECKS:
            problems.append(
                f"{s.id}: {len(s.done_check)} done-checks, must be {DONE_CHECKS}"
            )
        for dep in s.depends_on:
            if dep == s.id:
                problems.append(f"{s.id}: depends on itself")
            elif dep not in known:
                problems.append(f"{s.id}: depends on unknown step {dep}")
    problems.extend(_cycles(steps))
    return problems


def _cycles(steps: list[Step]) -> list[str]:
    deps = {s.id: [d for d in s.depends_on if d != s.id] for s in steps}
    state: dict[str, int] = {}  # 1 visiting, 2 done
    found: list[str] = []

    def visit(node: str) -> None:
        state[node] = 1
        for dep in deps.get(node, []):
            if dep not in deps:
                continue
            if state.get(dep) == 1:
                found.append(f"{node}: dependency cycle through {dep}")
            elif dep not in state:
                visit(dep)
        state[node] = 2

    for node in deps:
        if node not in state:
            visit(node)
    return found


def next_ready(steps: list[Step], done: set[str]) -> Step | None:
    """First step in file order that is not done and whose dependencies are."""
    for s in steps:
        if s.id not in done and all(d in done for d in s.depends_on):
            return s
    return None
