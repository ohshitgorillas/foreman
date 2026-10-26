"""Measure enclosing source units and pack findings for each receiving role."""

import ast
from dataclasses import dataclass
from pathlib import Path

from foreman import forms, overload, reports, steps
from foreman.steps import Group


@dataclass(frozen=True)
class Unit:
    path: str
    start: int
    weight: int


@dataclass(frozen=True)
class Batch:
    role: str
    source: forms.FindingSource
    lines: tuple[str, ...]
    load: int


def unit(wt: Path, path: str, line: int) -> Unit:
    """Use the innermost def/class span, or the whole file when none applies."""
    data = (wt / path).read_bytes()
    whole = Unit(path, 1, len(data.splitlines()))
    if not path.endswith(".py"):
        return whole
    try:
        tree = ast.parse(data, filename=path)
    except (SyntaxError, UnicodeError, ValueError):
        return whole
    enclosing = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.lineno <= line <= node.end_lineno
    ]
    if not enclosing:
        return whole
    node = min(enclosing, key=lambda node: node.end_lineno - node.lineno)
    return Unit(path, node.lineno, node.end_lineno - node.lineno + 1)


def cut(
    lines: list[str],
    source: forms.FindingSource,
    group: Group,
    wt: Path,
    budget: int,
) -> tuple[list[Batch], list[str]]:
    """Pack indivisible units in first-finding order, separately for each role."""
    roles: dict[str, dict[Unit, list[tuple[int, str]]]] = {}
    unowned: list[str] = []
    for index, finding in enumerate(lines):
        location = reports.anchor(finding, wt)
        owner = steps.owner(group, location[0]) if location is not None else None
        if owner is None:
            unowned.append(finding)
            continue
        measured = unit(wt, *location)
        roles.setdefault(owner.role, {}).setdefault(measured, []).append(
            (index, finding)
        )

    batches: list[Batch] = []
    for role, units in roles.items():
        pending: list[tuple[int, str]] = []
        paths: set[str] = set()
        load = 0
        for measured, findings in units.items():
            if pending and (
                load + measured.weight > budget
                or len(paths | {measured.path}) > overload.MAX_FILES
            ):
                batches.append(
                    Batch(
                        role, source, tuple(line for _, line in sorted(pending)), load
                    )
                )
                pending = []
                paths = set()
                load = 0
            pending.extend(findings)
            paths.add(measured.path)
            load += measured.weight
        if pending:
            batches.append(
                Batch(role, source, tuple(line for _, line in sorted(pending)), load)
            )
    return batches, unowned
