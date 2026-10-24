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


Form = Red | Port | Repair | Moved | Finding


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


def _finding(value: object, where: str) -> Finding:
    table = _table(value, where, {"source", "lines"})
    loc = f"{where}.source"
    source = _table(table["source"], loc, {"kind"}, {"role", "round", "run"})
    if source["kind"] == "reviewer":
        source = _table(source, loc, {"kind", "role", "round"})
        if source["role"] != "test-reviewer":
            raise ConfigError(
                f"{loc}.role: test-writer requires test-reviewer findings"
            )
        parsed = ReviewerSource(source["role"], _line(source["round"], f"{loc}.round"))
    elif source["kind"] == "gate":
        source = _table(source, loc, {"kind", "run"})
        parsed = GateSource(_line(source["run"], f"{loc}.run"))
    else:
        raise ConfigError(f"{loc}.kind: expected reviewer or gate")
    lines = tuple(
        _line(v, f"{where}.lines[{i}]")
        for i, v in enumerate(_array(table["lines"], f"{where}.lines"))
    )
    return Finding(parsed, lines)


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


def parse(role: str, shape: str, fields: object, *, where: str) -> Form:
    """Parse one closed role/shape pair; errors retain the caller's location."""
    if (
        not isinstance(role, str)
        or not isinstance(shape, str)
        or (role, shape) not in _PARSERS
    ):
        raise ConfigError(f"{where}: unsupported role/shape pair {role!r}/{shape!r}")
    return _PARSERS[role, shape](fields, where)
