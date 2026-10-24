"""Reads a step file's groups and steps, lints them, and picks the next ready group."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from foreman.errors import ConfigError

MAX_PATHS = 5


@dataclass(frozen=True)
class Step:
    id: str
    role: str  # the target repo's role
    brief: str  # sent verbatim; required
    paths: list[str]  # 1 to 5
    check: list[str]  # one argv; empty means the step runs no check
    expect_code: int = 0  # the check's passing exit code
    views: list[str] = field(default_factory=list)  # .py sources viewed before the step
    red: str = ""  # earlier step whose failing check ids end this step's brief


@dataclass(frozen=True)
class Group:
    id: str
    steps: list[Step]  # dispatched in this order
    design: str  # design file path for reviewer spec: lines
    after: list[str]  # group ids that land first


def _str_list(where: str, key: str, value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where}: {key} must be a list of strings")
    return list(value)


def _table(where: str, table: object, required: set[str], optional: set[str]) -> dict:
    if not isinstance(table, dict):
        raise ConfigError(f"{where} must be a table")
    missing = sorted(required - set(table))
    if missing:
        raise ConfigError(f"{where}: missing keys: {', '.join(missing)}")
    unknown = sorted(set(table) - required - optional)
    if unknown:
        raise ConfigError(f"{where}: unknown keys: {', '.join(unknown)}")
    return table


def _str(where: str, key: str, value: object) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{where}: {key} must be a string")
    return value


def _step(path: Path, step_id: str, raw: object) -> Step:
    where = f"{path}: steps.{step_id}"
    t = _table(
        where, raw, {"role", "brief", "paths"}, {"check", "expect_code", "views", "red"}
    )
    code = t.get("expect_code", 0)
    if isinstance(code, bool) or not isinstance(code, int):
        raise ConfigError(f"{where}: expect_code must be an integer")
    return Step(
        id=step_id,
        role=_str(where, "role", t["role"]),
        brief=_str(where, "brief", t["brief"]),
        paths=_str_list(where, "paths", t["paths"]),
        check=_str_list(where, "check", t.get("check", [])),
        expect_code=code,
        views=_str_list(where, "views", t.get("views", [])),
        red=_str(where, "red", t.get("red", "")),
    )


def load(path: Path) -> list[Group]:
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from e

    unknown_top = sorted(set(data) - {"groups", "steps"})
    if unknown_top:
        raise ConfigError(f"{path}: unknown keys: {', '.join(unknown_top)}")
    group_tables = data.get("groups")
    if not isinstance(group_tables, dict) or not group_tables:
        raise ConfigError(f"{path}: no [groups.<id>] tables")
    step_tables = data.get("steps")
    if not isinstance(step_tables, dict) or not step_tables:
        raise ConfigError(f"{path}: no [steps.<id>] tables")

    steps = {sid: _step(path, sid, raw) for sid, raw in step_tables.items()}
    groups: list[Group] = []
    for gid, raw in group_tables.items():
        where = f"{path}: groups.{gid}"
        t = _table(where, raw, {"steps"}, {"design", "after"})
        ids = _str_list(where, "steps", t["steps"])
        unknown = [i for i in ids if i not in steps]
        if unknown:
            raise ConfigError(f"{where}: unknown steps: {', '.join(unknown)}")
        design = _str(where, "design", t["design"]) if "design" in t else ""
        groups.append(
            Group(
                id=gid,
                steps=[steps[i] for i in ids],
                design=design,
                after=_str_list(where, "after", t.get("after", [])),
            )
        )
    grouped = {s.id for g in groups for s in g.steps}
    orphans = [sid for sid in steps if sid not in grouped]
    if orphans:
        raise ConfigError(f"{path}: steps in no group: {', '.join(orphans)}")
    return groups


def lint(groups: list[Group]) -> list[str]:
    """Return the problems found; an empty list means clean."""
    problems: list[str] = []
    gids = [g.id for g in groups]
    for dup in sorted({i for i in gids if gids.count(i) > 1}):
        problems.append(f"{dup}: duplicate group id")
    sids = [s.id for g in groups for s in g.steps]
    for dup in sorted({i for i in sids if sids.count(i) > 1}):
        owners = [g.id for g in groups if any(s.id == dup for s in g.steps)]
        if len(owners) > 1:
            problems.append(f"{dup}: step in more than one group: {', '.join(owners)}")
        else:
            problems.append(f"{dup}: duplicate step id")
    for g in groups:
        for s in g.steps:
            if not s.paths:
                problems.append(f"{s.id}: no paths")
            elif len(s.paths) > MAX_PATHS:
                problems.append(f"{s.id}: {len(s.paths)} paths, more than {MAX_PATHS}")
            if not s.brief.strip():
                problems.append(f"{s.id}: no brief")
    for g in groups:
        for s in g.steps:
            for v in s.views:
                if not v.endswith(".py"):
                    problems.append(f"{s.id}: view {v} is not a .py source")
    for g in groups:
        for i, s in enumerate(g.steps):
            checked = {e.id for e in g.steps[:i] if e.check}
            if s.red and s.red not in checked:
                problems.append(f"{s.id}: red {s.red} is not an earlier checked step")
    known = set(gids)
    for g in groups:
        for dep in g.after:
            if dep == g.id:
                problems.append(f"{g.id}: after itself")
            elif dep not in known:
                problems.append(f"{g.id}: after unknown group {dep}")
    problems.extend(_cycles(groups))
    return problems


def _cycles(groups: list[Group]) -> list[str]:
    deps = {g.id: [d for d in g.after if d != g.id] for g in groups}
    state: dict[str, int] = {}  # 1 visiting, 2 done
    found: list[str] = []

    def visit(node: str) -> None:
        state[node] = 1
        for dep in deps.get(node, []):
            if dep not in deps:
                continue
            if state.get(dep) == 1:
                found.append(f"{node}: after cycle through {dep}")
            elif dep not in state:
                visit(dep)
        state[node] = 2

    for node in deps:
        if node not in state:
            visit(node)
    return found


def next_ready(groups: list[Group], done: set[str]) -> Group | None:
    """First group in file order that is not done and whose `after` groups are."""
    for g in groups:
        if g.id not in done and all(d in done for d in g.after):
            return g
    return None


def holds(roots: list[str], path: str) -> bool:
    """Whether path equals one of roots or lies under one."""
    return any(
        path == r.rstrip("/") or path.startswith(r.rstrip("/") + "/") for r in roots
    )


def owner(group: Group, path: str) -> Step | None:
    """The last step in group order whose paths hold `path`."""
    for s in reversed(group.steps):
        if holds(s.paths, path):
            return s
    return None
