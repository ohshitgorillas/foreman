"""Immutable brief forms and their closed structural grammar."""

import re
import unicodedata
from dataclasses import dataclass
from typing import ClassVar

from foreman.errors import ConfigError


@dataclass(frozen=True)
class Origin:
    design: str
    steps: tuple[str, ...]


@dataclass(frozen=True)
class Behavior:
    text: str
    source: str
    replaces: str | None = None


@dataclass(frozen=True)
class DocumentChange:
    anchor: str
    before: str
    after: str


@dataclass(frozen=True)
class ReviewerSource:
    role: str
    round: str
    kind: ClassVar[str] = "reviewer"


@dataclass(frozen=True)
class GateSource:
    run: str
    kind: ClassVar[str] = "gate"


FindingSource = ReviewerSource | GateSource


@dataclass(frozen=True)
class Red:
    behaviors: tuple[Behavior, ...]
    origin: Origin | None = None
    role: ClassVar[str] = "test-writer"
    shape: ClassVar[str] = "red"


@dataclass(frozen=True)
class Port:
    origin: Origin
    sources: tuple[str, ...]
    role: ClassVar[str] = "test-writer"
    shape: ClassVar[str] = "port"


@dataclass(frozen=True)
class Repair:
    sources: tuple[str, ...]
    failures: tuple[str, ...]
    role: ClassVar[str] = "test-writer"
    shape: ClassVar[str] = "repair"


@dataclass(frozen=True)
class Moved:
    changes: tuple[DocumentChange, ...]
    sources: tuple[str, ...]
    role: ClassVar[str] = "test-writer"
    shape: ClassVar[str] = "moved"


@dataclass(frozen=True)
class Finding:
    source: FindingSource
    lines: tuple[str, ...]
    role: ClassVar[str] = "test-writer"
    shape: ClassVar[str] = "finding"


@dataclass(frozen=True)
class RedSelection:
    step: str
    files: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Stub:
    role: str
    step: str
    exclude: tuple[str, ...] = ()
    shape: ClassVar[str] = "stub"


@dataclass(frozen=True)
class Build:
    role: str
    step: str
    red: tuple[RedSelection, ...]
    exclude: tuple[str, ...] = ()
    shape: ClassVar[str] = "build"


@dataclass(frozen=True)
class Rewrite:
    role: str
    line: str
    exclude: tuple[str, ...] = ()
    shape: ClassVar[str] = "rewrite"


@dataclass(frozen=True)
class BuilderFinding:
    role: str
    source: FindingSource
    lines: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    shape: ClassVar[str] = "finding"


@dataclass(frozen=True)
class DiffReview:
    scope: str
    range: str | None = None
    role: ClassVar[str] = "diff-reviewer"
    shape: ClassVar[str] = "review"


@dataclass(frozen=True)
class TestReview:
    scope: str
    range: str | None = None
    specs: tuple[str, ...] = ()
    role: ClassVar[str] = "test-reviewer"
    shape: ClassVar[str] = "review"


Form = (
    Red
    | Port
    | Repair
    | Moved
    | Finding
    | Stub
    | Build
    | Rewrite
    | BuilderFinding
    | DiffReview
    | TestReview
)


def _table(
    value: object, where: str, required: set[str], optional: set[str] = frozenset()
) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: must be a table")
    for key in sorted(required - value.keys()):
        raise ConfigError(f"{where}.{key}: required field")
    for key in value.keys() - required - optional:
        raise ConfigError(f"{where}.{key}: unknown field")
    return value


def _line(value: object, where: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(c in value for c in "\r\n\0")
    ):
        raise ConfigError(
            f"{where}: must be a nonempty line without surrounding whitespace, CR, LF or NUL"
        )
    return value


def _path(value: object, where: str, *, suffix: str = "", prefix: str = "") -> str:
    value = _line(value, where)
    if (
        any(c.isspace() or unicodedata.category(c) == "Cc" for c in value)
        or any(c in value for c in "\\:")
        or any(part in ("", ".", "..") for part in value.split("/"))
        or not value.startswith(prefix)
        or not value.endswith(suffix)
        or (prefix and value == prefix.rstrip("/"))
        or not value.rsplit("/", 1)[-1].removesuffix(suffix)
    ):
        raise ConfigError(
            f"{where}: expected repository-relative POSIX file path {prefix}*{suffix} without traversal, whitespace or controls"
        )
    return value


def _array(value: object, where: str, *, empty: bool = False) -> list:
    if not isinstance(value, list) or (not value and not empty):
        raise ConfigError(f"{where}: must be {'an' if empty else 'a nonempty'} array")
    return value


def _distinct(values: tuple, where: str) -> tuple:
    if len(set(values)) != len(values):
        raise ConfigError(f"{where}: duplicate values")
    return values


def _sources(value: object, where: str) -> tuple[str, ...]:
    return _distinct(
        tuple(
            _path(v, f"{where}[{i}]", suffix=".py")
            for i, v in enumerate(_array(value, where, empty=True))
        ),
        where,
    )


def _origin(value: object, where: str) -> Origin:
    table = _table(value, where, {"design", "steps"})
    design = _path(
        table["design"],
        f"{where}.design",
        prefix="docs/superpowers/specs/",
        suffix=".md",
    )
    steps = _distinct(
        tuple(
            _line(v, f"{where}.steps[{i}]")
            for i, v in enumerate(_array(table["steps"], f"{where}.steps"))
        ),
        f"{where}.steps",
    )
    return Origin(design, steps)


def _test_id(value: object, where: str) -> str:
    value = _line(value, where)
    file, sep, selector = value.partition("::")
    _path(file, where, prefix="tests/", suffix=".py")
    # Parameter spelling is opaque, including spaces and :: inside brackets.
    head, bracket, rest = selector.partition("[")
    if (
        not sep
        or not head
        or any(not part or any(c.isspace() for c in part) for part in head.split("::"))
        or (bracket and (not rest.endswith("]") or head.endswith("::")))
        or any(unicodedata.category(c) == "Cc" for c in selector)
    ):
        raise ConfigError(
            f"{where}: expected exact pytest test id with a nonempty selector"
        )
    return value


def _red(value: object, where: str) -> Red:
    table = _table(value, where, {"behaviors"}, {"origin"})
    behaviors = []
    for i, raw in enumerate(_array(table["behaviors"], f"{where}.behaviors")):
        loc = f"{where}.behaviors[{i}]"
        b = _table(raw, loc, {"text", "source"}, {"replaces"})
        behaviors.append(
            Behavior(
                _line(b["text"], f"{loc}.text"),
                _path(b["source"], f"{loc}.source", suffix=".py"),
                _test_id(b["replaces"], f"{loc}.replaces") if "replaces" in b else None,
            )
        )
    _distinct(
        tuple(b.replaces for b in behaviors if b.replaces is not None),
        f"{where}.behaviors.replaces",
    )
    return Red(
        tuple(behaviors),
        _origin(table["origin"], f"{where}.origin") if "origin" in table else None,
    )


def _port(value: object, where: str) -> Port:
    table = _table(value, where, {"origin", "sources"})
    origin = _origin(table["origin"], f"{where}.origin")
    if len(origin.steps) != 1:
        raise ConfigError(f"{where}.origin.steps: port requires exactly one Order id")
    return Port(origin, _sources(table["sources"], f"{where}.sources"))


def _repair(value: object, where: str) -> Repair:
    table = _table(value, where, {"sources", "failures"})
    failures = tuple(
        _line(v, f"{where}.failures[{i}]")
        for i, v in enumerate(_array(table["failures"], f"{where}.failures"))
    )
    return Repair(_sources(table["sources"], f"{where}.sources"), failures)


def _moved(value: object, where: str) -> Moved:
    table = _table(value, where, {"changes", "sources"})
    changes = []
    for i, raw in enumerate(_array(table["changes"], f"{where}.changes")):
        loc = f"{where}.changes[{i}]"
        c = _table(raw, loc, {"anchor", "before", "after"})
        anchor = _line(c["anchor"], f"{loc}.anchor")
        path, sep, line = anchor.rpartition(":")
        _path(path, f"{loc}.anchor")
        if not sep or not re.fullmatch(r"[1-9][0-9]*", line):
            raise ConfigError(f"{loc}.anchor: expected document path:positive-line")
        before, after = (_line(c[k], f"{loc}.{k}") for k in ("before", "after"))
        for key, text in (("before", before), ("after", after)):
            if '"' in text or " | " in text:
                raise ConfigError(
                    f"{loc}.{key}: embedded quotes and ' | ' are unsupported"
                )
        changes.append(DocumentChange(anchor, before, after))
    return Moved(tuple(changes), _sources(table["sources"], f"{where}.sources"))


def _finding_source(value: object, loc: str, reviewer: str) -> FindingSource:
    source = _table(value, loc, {"kind"}, {"role", "round", "run"})
    if source["kind"] == "reviewer":
        source = _table(source, loc, {"kind", "role", "round"})
        if source["role"] != reviewer:
            raise ConfigError(f"{loc}.role: requires {reviewer} findings")
        parsed = ReviewerSource(source["role"], _line(source["round"], f"{loc}.round"))
    elif source["kind"] == "gate":
        source = _table(source, loc, {"kind", "run"})
        parsed = GateSource(_line(source["run"], f"{loc}.run"))
    else:
        raise ConfigError(f"{loc}.kind: expected reviewer or gate")
    return parsed


def _finding_lines(value: object, where: str) -> tuple[str, ...]:
    return tuple(_line(v, f"{where}[{i}]") for i, v in enumerate(_array(value, where)))


def _finding(value: object, where: str) -> Finding:
    table = _table(value, where, {"source", "lines"})
    return Finding(
        _finding_source(table["source"], f"{where}.source", "test-reviewer"),
        _finding_lines(table["lines"], f"{where}.lines"),
    )


def _block(value: object, where: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("\n")
        or value.endswith("\n")
        or any(c in value for c in "\r\0")
    ):
        raise ConfigError(
            f"{where}: expected nonempty approved-step block without CR, NUL or leading/trailing LF"
        )
    return value


def _builder_path(value: object, where: str, role: str) -> str:
    path = _path(value, where)
    prefix = "gauntlet/core/" if role == "core-builder" else "gauntlet/seams/"
    if not path.startswith(prefix) and not (
        role == "core-builder" and path == "gauntlet/__init__.py"
    ):
        raise ConfigError(f"{where}: path outside {role} writes")
    return path


def _exclusions(table: dict, where: str, role: str) -> tuple[str, ...]:
    if "exclude" not in table:
        return ()
    loc = f"{where}.exclude"
    return _distinct(
        tuple(
            _builder_path(v, f"{loc}[{i}]", role)
            for i, v in enumerate(_array(table["exclude"], loc))
        ),
        loc,
    )


def _selections(value: object, where: str) -> tuple[RedSelection, ...]:
    selections = []
    for i, raw in enumerate(_array(value, where)):
        loc = f"{where}[{i}]"
        table = _table(raw, loc, {"step"}, {"files"})
        files = None
        if "files" in table:
            files = _distinct(
                tuple(
                    _path(v, f"{loc}.files[{j}]", prefix="tests/", suffix=".py")
                    for j, v in enumerate(_array(table["files"], f"{loc}.files"))
                ),
                f"{loc}.files",
            )
        selections.append(RedSelection(_line(table["step"], f"{loc}.step"), files))
    _distinct(tuple(s.step for s in selections), where)
    return tuple(selections)


_BUILDER_CATEGORIES = frozenset(
    (
        "shortcut",
        "gate-gaming",
        "misplaced",
        "hand-rolled",
        "drift",
        "shell-text parsing",
        "leftover",
    )
)


def _builder_finding_line(line: str, where: str, role: str) -> None:
    # Validate the declared form fields, not a report or its incidental citations.
    fields = line.split(" | ")
    if len(fields) != 4:
        raise ConfigError(f"{where}: reviewer finding requires exactly four fields")
    category, anchor, defect, failure = fields
    if category not in _BUILDER_CATEGORIES:
        raise ConfigError(f"{where}: unsupported builder finding category")
    path, sep, number = anchor.rpartition(":")
    _builder_path(path, where, role)
    if not sep or not re.fullmatch(r"[1-9][0-9]*", number):
        raise ConfigError(f"{where}: expected writable path:positive-line")
    _line(defect, where)
    if not failure.startswith("fails when: "):
        raise ConfigError(f"{where}: expected fails when: scenario")
    _line(failure.removeprefix("fails when: "), where)


def _builder(role: str, shape: str, value: object, where: str) -> Form:
    required = {
        "stub": {"step"},
        "build": {"step", "red"},
        "rewrite": {"line"},
        "finding": {"source", "lines"},
    }[shape]
    table = _table(value, where, required, {"exclude"})
    exclude = _exclusions(table, where, role)
    if shape == "stub":
        return Stub(role, _block(table["step"], f"{where}.step"), exclude)
    if shape == "build":
        return Build(
            role,
            _block(table["step"], f"{where}.step"),
            _selections(table["red"], f"{where}.red"),
            exclude,
        )
    if shape == "rewrite":
        line = _line(table["line"], f"{where}.line")
        if not line.startswith("REWRITE ") or not line.removeprefix("REWRITE ").strip():
            raise ConfigError(f"{where}.line: expected one REWRITE line")
        return Rewrite(role, line, exclude)
    source = _finding_source(table["source"], f"{where}.source", "diff-reviewer")
    lines = _finding_lines(table["lines"], f"{where}.lines")
    if isinstance(source, ReviewerSource):
        for i, line in enumerate(lines):
            _builder_finding_line(line, f"{where}.lines[{i}]", role)
    return BuilderFinding(role, source, lines, exclude)


def _review(role: str, value: object, where: str) -> DiffReview | TestReview:
    optional = {"range", "specs"} if role == "test-reviewer" else {"range"}
    table = _table(value, where, {"scope"}, optional)
    scope = table["scope"]
    if scope not in ("working-tree", "range"):
        raise ConfigError(f"{where}.scope: expected working-tree or range")
    revision_range = None
    if scope == "range":
        if "range" not in table:
            raise ConfigError(f"{where}.range: required for range scope")
        revision_range = _line(table["range"], f"{where}.range")
        operands = re.fullmatch(
            r"([^.]+(?:\.[^.]+)*)\.{2,3}([^.]+(?:\.[^.]+)*)", revision_range
        )
        if (
            not operands
            or any(
                c.isspace() or unicodedata.category(c) == "Cc" for c in revision_range
            )
            or any(part.startswith("-") for part in operands.groups())
        ):
            raise ConfigError(
                f"{where}.range: expected single A..B or A...B expression without whitespace, controls or leading '-'"
            )
    elif "range" in table:
        raise ConfigError(f"{where}.range: allowed only for range scope")
    if role == "diff-reviewer":
        return DiffReview(scope, revision_range)
    specs = _distinct(
        tuple(
            _path(
                v, f"{where}.specs[{i}]", prefix="docs/superpowers/specs/", suffix=".md"
            )
            for i, v in enumerate(
                _array(table.get("specs", []), f"{where}.specs", empty=True)
            )
        ),
        f"{where}.specs",
    )
    return TestReview(scope, revision_range, specs)


_PARSERS = {
    ("test-writer", shape): parser
    for shape, parser in (
        ("red", _red),
        ("port", _port),
        ("repair", _repair),
        ("moved", _moved),
        ("finding", _finding),
    )
}


def _builder_parser(role: str, shape: str):
    return lambda value, where: _builder(role, shape, value, where)


for _role in ("core-builder", "seam-builder"):
    for _shape in ("stub", "build", "rewrite", "finding"):
        _PARSERS[_role, _shape] = _builder_parser(_role, _shape)
for _role in ("diff-reviewer", "test-reviewer"):
    _PARSERS[_role, "review"] = lambda value, where, role=_role: _review(
        role, value, where
    )


def parse(role: str, shape: str, fields: object, *, where: str) -> Form:
    """Parse one closed role/shape pair; errors retain the caller's location."""
    if (
        not isinstance(role, str)
        or not isinstance(shape, str)
        or (role, shape) not in _PARSERS
    ):
        raise ConfigError(f"{where}: unsupported role/shape pair {role!r}/{shape!r}")
    return _PARSERS[role, shape](fields, where)
