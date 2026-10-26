"""Reads a step file's groups and steps, lints them, and picks the next ready group."""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from foreman import forms, overload
from foreman.errors import ConfigError


@dataclass(frozen=True)
class Step:
    id: str
    role: str  # the target repo's role
    form: forms.Form  # validated role-specific fields
    paths: list[str]  # 1 to 5
    check: list[str]  # one argv; empty means the step runs no check
    expect_code: int = 0  # the check's passing exit code
    views: list[str] = field(default_factory=list)  # .py sources viewed before the step

    def __post_init__(self) -> None:
        # A test step writes the test files its check runs, so those are its paths
        # too; every consumer (brief, out-of-paths checks, restore, commit) reads
        # them here.
        derived = check_tests(self.form, self.paths, self.check)
        if derived:
            object.__setattr__(self, "paths", [*self.paths, *derived])


_TEST_FILE = re.compile(r"tests/(?:[^/]+/)*test_[^/]*\.py")


def check_tests(form: object, paths: object, check: object) -> list[str]:
    """Test files a test form's check names that its paths do not already hold."""
    if not isinstance(form, (forms.Port, forms.Red)):
        return []
    if not isinstance(paths, list) or not isinstance(check, list):
        return []
    held = [p for p in paths if isinstance(p, str)]
    found: list[str] = []
    for arg in check:
        if not isinstance(arg, str):
            continue
        file = arg.partition("::")[0]
        if _TEST_FILE.fullmatch(file) and not holds(held, file) and file not in found:
            found.append(file)
    return found


@dataclass(frozen=True)
class Group:
    id: str
    steps: list[Step]  # dispatched in this order
    design: str  # documentary metadata
    after: list[str]  # group ids that land first
    reviews: dict[str, forms.DiffReview | forms.TestReview] = field(
        default_factory=dict
    )


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
    if isinstance(raw, dict) and "brief" in raw:
        raise ConfigError(
            f"{where}.brief: free-text briefs removed; use role, shape and form"
        )
    t = _table(
        where,
        raw,
        {"role", "shape", "form", "paths"},
        {"check", "expect_code", "views"},
    )
    code = t.get("expect_code", 0)
    if isinstance(code, bool) or not isinstance(code, int):
        raise ConfigError(f"{where}: expect_code must be an integer")
    return Step(
        id=step_id,
        role=_str(where, "role", t["role"]),
        form=forms.parse(t["role"], t["shape"], t["form"], where=f"{where}.form"),
        paths=_str_list(where, "paths", t["paths"]),
        check=_str_list(where, "check", t.get("check", [])),
        expect_code=code,
        views=_str_list(where, "views", t.get("views", [])),
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
        t = _table(where, raw, {"steps"}, {"design", "after", "reviews"})
        ids = _str_list(where, "steps", t["steps"])
        if not ids:
            raise ConfigError(f"{where}.steps: must be nonempty")
        unknown = [i for i in ids if i not in steps]
        if unknown:
            raise ConfigError(f"{where}: unknown steps: {', '.join(unknown)}")
        design = _str(where, "design", t["design"]) if "design" in t else ""
        reviews = t.get("reviews", {})
        if not isinstance(reviews, dict):
            raise ConfigError(f"{where}.reviews: must be a table")
        parsed_reviews = {}
        for role, value in reviews.items():
            parsed_reviews[role] = forms.parse(
                role, "review", value, where=f"{where}.reviews.{role}"
            )
        groups.append(
            Group(
                id=gid,
                steps=[steps[i] for i in ids],
                design=design,
                after=_str_list(where, "after", t.get("after", [])),
                reviews=parsed_reviews,
            )
        )
    grouped = {s.id for g in groups for s in g.steps}
    orphans = [sid for sid in steps if sid not in grouped]
    if orphans:
        raise ConfigError(f"{path}: steps in no group: {', '.join(orphans)}")
    problems = lint(groups)
    if problems:
        raise ConfigError(f"{path}: " + "\n".join(problems))
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
            elif over := overload.files(s.paths):
                problems.append(f"{s.id}: {over.found} paths, more than {over.limit}")
            if s.role != getattr(s.form, "role", None):
                problems.append(f"steps.{s.id}.role: does not match form role")
            problems.extend(
                f"steps.{s.id}.{p}"
                for p in forms.validate_step(s.form, s.paths, s.views)
            )
            if not isinstance(s.check, list) or not all(
                isinstance(arg, str) and arg and "\0" not in arg for arg in s.check
            ):
                problems.append(
                    f"steps.{s.id}.check: expected one argv or an empty array"
                )
            if isinstance(s.expect_code, bool) or not isinstance(s.expect_code, int):
                problems.append(f"steps.{s.id}.expect_code: must be an integer")
        if not g.steps:
            problems.append(f"groups.{g.id}.steps: must be nonempty")
        problems.extend(forms.validate_group(g))
    for g in groups:
        for s in g.steps:
            for v in s.views:
                if not v.endswith(".py"):
                    problems.append(f"{s.id}: view {v} is not a .py source")
    replaced: set[str] = set()
    for g in groups:
        for s in g.steps:
            if isinstance(s.form, forms.Red):
                for b in s.form.behaviors:
                    if b.replaces is not None:
                        if b.replaces in replaced:
                            problems.append(
                                f"steps.{s.id}.form.behaviors.replaces: duplicate replacement {b.replaces}"
                            )
                        replaced.add(b.replaces)
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
