"""The one reader of agent reports, gate output and upheld findings."""

import re
from dataclasses import dataclass, field
from os.path import normpath
from pathlib import Path

from foreman import forms, steps
from foreman.errors import ConfigError
from foreman.run import Result
from foreman.steps import Group

_REJECTED = "REJECTED: STEERING"
_OVERLOADED = "OVERLOADED:"
_PATH_LINE = re.compile(r"((?:/|\./)?[\w.-]+(?:/[\w.-]+)*):([1-9][0-9]*)(?=[:\s]|$)")


@dataclass(frozen=True)
class Verdict:
    kind: str
    action: str
    step: str | None
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]
    failures: tuple[str, ...]


def _read(lines: list[str]) -> list[str]:
    """Drop the IGNORED and CHOSEN lines, which no parser reads."""
    return [ln for ln in lines if ln.partition(" ")[0] not in {"IGNORED", "CHOSEN"}]


def verdict(text: str) -> Verdict:
    """Decode the shared juror/owner envelope; context checks belong to loop."""
    where = "verdict"
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ConfigError(f"{where}: requires UTF-8 text") from exc
    records = _read(text.removesuffix("\n").split("\n"))
    for record in records:
        forms._line(record, where)
    index = 0

    def take(key: str) -> str:
        nonlocal index
        if index >= len(records) or not records[index].startswith(key + " "):
            raise ConfigError(f"{where}: expected {key} record")
        value = forms._line(records[index][len(key) + 1 :], f"{where} {key}")
        index += 1
        return value

    def many(key: str) -> tuple[str, ...]:
        values = [take(key)]
        while index < len(records) and records[index].startswith(key + " "):
            values.append(take(key))
        return tuple(values)

    kind, action = take("VERDICT"), take("ACTION")
    allowed = {
        ("spec wrong", "owner"),
        ("tests wrong", "repair"),
        ("tests wrong", "redo red"),
        ("build wrong", "retry"),
        ("undetermined", "owner"),
    }
    if (kind, action) not in allowed:
        raise ConfigError(f"{where}: invalid kind/action pair {kind!r}/{action!r}")
    step = None if action == "owner" else take("STEP")
    reasons = many("REASON")
    evidence = many("EVIDENCE")
    for record in evidence:
        location, delimiter, claim = record.partition(" | ")
        if not delimiter:
            raise ConfigError(f"{where}: EVIDENCE requires path:line | claim")
        forms._line(claim, f"{where} EVIDENCE claim")
        path, colon, number = location.rpartition(":")
        if not colon or re.fullmatch(r"[1-9][0-9]*", number) is None:
            raise ConfigError(f"{where}: EVIDENCE requires a positive line number")
        # Absolute kept-report references are checked against the judgment later.
        forms._path(path.removeprefix("/"), f"{where} EVIDENCE path")
    failures = many("FAILURE") if action == "repair" else ()
    if records[index:] != ["END JUDGMENT"]:
        raise ConfigError(f"{where}: expected only terminal END JUDGMENT")
    return Verdict(kind, action, step, reasons, evidence, failures)


def _envelope(lines: list[str]) -> list[str]:
    """Exclude quoted brief text and the diff/command payloads."""
    out = []
    in_brief = False
    for line in lines:
        key = line.partition(" ")[0]
        if key in {"DIFF", "COMMANDS"}:
            break
        if key == "BRIEF":
            in_brief = True
            continue
        if in_brief:
            if key != "VIEWS":
                continue
            in_brief = False
        out.append(line)
    return out


@dataclass(frozen=True)
class RedClaims:
    ids: tuple[tuple[int, str], ...]  # (behavior number, test id), report order
    notes: tuple[str, ...]  # LEFT, UNTESTABLE and CONTRADICTS lines, verbatim


_BEHAVIOR = re.compile(
    r"BEHAVIOR ([1-9][0-9]*) \| (.+?) \| docs: (.+?) \| breaks when: (.+)"
)
_NOTES = {"LEFT", "UNTESTABLE", "CONTRADICTS"}


def red(text: str) -> RedClaims:
    """Read a red report's claims: each BEHAVIOR id and number, and each note.

    ConfigError only for no SHAPE red .. END TESTS block, a malformed BEHAVIOR
    line, an invalid or duplicate test id, or a refusal marker in the block.
    """
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line == "SHAPE red"]
    ends = [i for i, line in enumerate(lines) if line == "END TESTS"]
    if len(starts) != 1 or len(ends) != 1 or starts[0] > ends[0]:
        raise ConfigError(
            "red report: requires exactly one SHAPE red .. END TESTS block"
        )
    envelope = _read(_envelope(lines[starts[0] : ends[0] + 1]))
    for line in envelope:
        if line.startswith((_REJECTED, _OVERLOADED)):
            raise ConfigError(f"red report: refusal marker in the block: {line}")
    ids: dict[str, int] = {}
    notes = []
    for line in envelope:
        key = line.partition(" ")[0]
        if key == "BEHAVIOR":
            match = _BEHAVIOR.fullmatch(line)
            if not match:
                raise ConfigError(
                    "red report: malformed BEHAVIOR; needs BEHAVIOR <n> | <test id>"
                    f" | docs: <docs> | breaks when: <why>; record: {line}"
                )
            number, test_id = match.group(1), match.group(2)
            try:
                forms._test_id(test_id, "red report")
            except ConfigError as exc:
                raise ConfigError(
                    f"red report: invalid test id ({exc}); record: {line}"
                ) from exc
            if test_id in ids:
                raise ConfigError(
                    f"red report: duplicate BEHAVIOR {test_id}; record: {line}"
                )
            ids[test_id] = int(number)
        elif key in _NOTES:
            notes.append(line)
    return RedClaims(tuple((n, i) for i, n in ids.items()), tuple(notes))


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
    lines = _read(_envelope(text.splitlines()))
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
    target = Path(normpath(path))
    if not target.is_absolute():
        return str(target) if target.parts and target.parts[0] != ".." else None
    for base in (wt.absolute(), wt.resolve()):
        try:
            return str(target.relative_to(base))
        except ValueError:
            continue
    return None


def _primary_anchor(line: str) -> re.Match[str] | None:
    fields = line.split(" | ")
    match = _PATH_LINE.match(line)
    if match is None and len(fields) > 1:
        match = _PATH_LINE.fullmatch(fields[1])
    if match is None and len(fields) > 1:
        # The test-reviewer's shape: "<category> <path>:<line> | ...".
        _, _, rest = fields[0].partition(" ")
        match = _PATH_LINE.fullmatch(rest)
    return match


def anchor(line: str, wt: Path) -> tuple[str, int] | None:
    """Read the primary anchor relative to wt, never an incidental citation."""
    match = _primary_anchor(line)
    if match is None:
        return None
    path = _relative(match.group(1), wt)
    return (path, int(match.group(2))) if path is not None else None


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
