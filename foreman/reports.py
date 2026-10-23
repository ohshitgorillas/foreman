"""The one reader of agent reports, gate output and upheld findings."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from foreman import steps
from foreman.errors import ConfigError
from foreman.run import Result
from foreman.steps import Group

_REJECTED = "REJECTED: STEERING"
_OVERLOADED = "OVERLOADED:"
_PATH_LINE = re.compile(r"(?<![\w./-])((?:/|\./)?[\w.-]+(?:/[\w.-]+)*):\d+")


@dataclass(frozen=True)
class Report:
    kind: str  # "done" | "rejected" | "overloaded"
    # lines under "FINDINGS <n>" or "FINDINGS <n>:", verbatim without indent
    findings: list[str]
    # lines under "TEST WRONG <n>" or "TEST WRONG <n>:", verbatim without indent
    test_wrong: list[str] = field(default_factory=list)


def _block(lines: list[str], header: str) -> list[str]:
    head = re.compile(rf"^{re.escape(header)} \d+(:.*)?\s*$")
    for i, ln in enumerate(lines):
        if head.match(ln):
            out: list[str] = []
            for body in lines[i + 1 :]:
                if not body.strip() or not body[0].isspace():
                    break
                out.append(body.lstrip())
            return out
    return []


def parse(text: str) -> Report:
    lines = text.splitlines()
    if any(ln.startswith(_REJECTED) for ln in lines):
        kind = "rejected"
    elif any(ln.startswith(_OVERLOADED) for ln in lines):
        kind = "overloaded"
    else:
        kind = "done"
    return Report(
        kind=kind,
        findings=_block(lines, "FINDINGS"),
        test_wrong=_block(lines, "TEST WRONG"),
    )


def read(result: Result) -> Report:
    return parse(result.out)


def lines(result: Result) -> list[str]:
    return result.out.splitlines()


def _relative(path: str, wt: Path) -> str | None:
    if not path.startswith("/"):
        return path.removeprefix("./")
    for base in (wt, wt.resolve()):
        try:
            return str(Path(path).relative_to(base))
        except ValueError:
            continue
    return None


def route(
    lines: list[str], wt: Path, group: Group
) -> tuple[dict[str, list[str]], list[str]]:
    owned: dict[str, list[str]] = {}
    unowned: list[str] = []
    for line in lines:
        found = [_relative(m.group(1), wt) for m in _PATH_LINE.finditer(line)]
        if not found:
            continue
        step = next(
            (
                s
                for rel in found
                if rel is not None and (s := steps.owner(group, rel)) is not None
            ),
            None,
        )
        if step is None:
            unowned.append(line)
        else:
            owned.setdefault(step.id, []).append(line)
    return owned, unowned


def upheld(text: str, kept: dict[str, Report]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        roles = [role for role, rep in kept.items() if line in rep.findings]
        if not roles:
            raise ConfigError(f"upheld line is in no kept review: {line}")
        for role in roles:
            out.setdefault(role, []).append(line)
    return out


def route_tests(
    lines: list[str], group: Group
) -> tuple[dict[str, list[str]], list[str]]:
    owned: dict[str, list[str]] = {}
    unowned: list[str] = []
    for line in lines:
        path, sep, _ = line.partition("::")
        step = steps.owner(group, path.strip()) if sep else None
        if step is None:
            unowned.append(line)
        else:
            owned.setdefault(step.id, []).append(line)
    return owned, unowned
