"""The one reader of agent reports, gate output and upheld findings."""

import re
from dataclasses import dataclass
from pathlib import Path

from foreman import steps
from foreman.errors import ConfigError
from foreman.run import Result
from foreman.steps import Group

_REJECTED = "REJECTED: STEERING"
_OVERLOADED = "OVERLOADED:"
_FINDINGS = re.compile(r"^FINDINGS \d+\s*$")
_PATH_LINE = re.compile(r"(?<![\w./-])((?:/|\./)?[\w.-]+(?:/[\w.-]+)*):\d+")


@dataclass(frozen=True)
class Report:
    kind: str  # "done" | "rejected" | "overloaded"
    findings: list[
        str
    ]  # the lines under a "FINDINGS <n>" header, verbatim without indent


def parse(text: str) -> Report:
    lines = text.splitlines()
    if any(ln.startswith(_REJECTED) for ln in lines):
        kind = "rejected"
    elif any(ln.startswith(_OVERLOADED) for ln in lines):
        kind = "overloaded"
    else:
        kind = "done"
    findings: list[str] = []
    for i, ln in enumerate(lines):
        if _FINDINGS.match(ln):
            for body in lines[i + 1 :]:
                if not body.strip() or not body[0].isspace():
                    break
                findings.append(body.lstrip())
            break
    return Report(kind=kind, findings=findings)


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
