"""`foreman stats`: per role, tokens and seconds per finished dispatch, with
overrun and refusal counts, read from Foreman's existing event log."""

import re
from pathlib import Path

import pytest

from foreman.cli import main
from foreman.config import Config
from foreman.events import EventLog

NUMBER = r"\d+(?:\.\d+)?"


def stats(conf_file: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """Run `foreman stats` against conf_file; return its exit code and stdout."""
    try:
        code = main(["--config", str(conf_file), "stats"])
    except SystemExit as e:
        pytest.fail(f"foreman stats exited {e.code}: {capsys.readouterr().err.strip()}")
    return code, capsys.readouterr().out


def role_line(out: str, role: str) -> str:
    """The one output line that names `role` as a word."""
    lines = [x for x in out.splitlines() if re.search(rf"(?<![\w-]){role}(?![\w-])", x)]
    assert len(lines) == 1, f"want one line for {role!r}, got {lines!r} in {out!r}"
    return lines[0]


def figure(line: str, label: str) -> float:
    """The number `label` names in `line`: `label: N`, `label=N` or `N label`."""
    after = re.search(rf"{label}\w*\s*[:=]\s*({NUMBER})", line, re.IGNORECASE)
    before = re.search(rf"({NUMBER})\s*{label}", line, re.IGNORECASE)
    found = after or before
    assert found, f"no figure labelled {label!r} in {line!r}"
    return float(found.group(1))


def dispatch(
    log: EventLog, role: str, tokens: int | None, seconds: float, outcome: str
) -> None:
    log.dispatched(
        "g1",
        f"{role}-{outcome}-{tokens}-{seconds}",
        role,
        load=None,
        seconds=seconds,
        tokens=tokens,
        outcome=outcome,
    )


@pytest.fixture
def log(green_cfg: Config, conf_file: Path) -> EventLog:
    """The event log the config at conf_file names."""
    return EventLog(green_cfg)


def test_stats_with_no_events_exits_ok(conf_file: Path, capsys) -> None:
    code, _ = stats(conf_file, capsys)
    assert code == 0


def test_stats_reports_tokens_and_seconds_per_finished_dispatch(
    conf_file: Path, log: EventLog, capsys
) -> None:
    dispatch(log, "core-builder", 1000, 100.0, "done")
    dispatch(log, "core-builder", 3000, 300.0, "done")
    log.keep("noise", "kept text is not a dispatch")
    code, out = stats(conf_file, capsys)
    assert code == 0
    line = role_line(out, "core-builder")
    assert figure(line, "token") == 2000
    assert figure(line, "sec") == 200


def test_stats_reports_the_lead_role(conf_file: Path, log: EventLog, capsys) -> None:
    dispatch(log, "lead", 50000, 1800.0, "done")
    dispatch(log, "test-writer", 7000, 70.0, "done")
    code, out = stats(conf_file, capsys)
    assert code == 0
    lead = role_line(out, "lead")
    assert figure(lead, "token") == 50000
    assert figure(lead, "sec") == 1800
    writer = role_line(out, "test-writer")
    assert figure(writer, "token") == 7000
    assert figure(writer, "sec") == 70


def test_stats_counts_overruns_and_keeps_them_out_of_the_per_dispatch_figures(
    conf_file: Path, log: EventLog, capsys
) -> None:
    dispatch(log, "seam-builder", 400, 40.0, "done")
    dispatch(log, "seam-builder", 99999, 600.0, "overran")
    dispatch(log, "seam-builder", 88888, 600.0, "overran")
    dispatch(log, "seam-builder", 77777, 600.0, "overran")
    code, out = stats(conf_file, capsys)
    assert code == 0
    line = role_line(out, "seam-builder")
    assert figure(line, "overr") == 3
    assert figure(line, "token") == 400
    assert figure(line, "sec") == 40


def test_stats_counts_refusals(conf_file: Path, log: EventLog, capsys) -> None:
    dispatch(log, "gatesmith", 500, 50.0, "done")
    dispatch(log, "gatesmith", 500, 50.0, "refused")
    dispatch(log, "gatesmith", 500, 50.0, "refused")
    log.mismatched("g1", "gatesmith-x", "refused", "a mismatch record, not a dispatch")
    code, out = stats(conf_file, capsys)
    assert code == 0
    line = role_line(out, "gatesmith")
    assert figure(line, "refus") == 2
    assert figure(line, "overr") == 0


def test_stats_keeps_roles_apart(conf_file: Path, log: EventLog, capsys) -> None:
    dispatch(log, "core-builder", 1000, 10.0, "done")
    dispatch(log, "core-builder", 1000, 10.0, "refused")
    dispatch(log, "juror", 9000, 90.0, "done")
    dispatch(log, "juror", 9000, 600.0, "overran")
    code, out = stats(conf_file, capsys)
    assert code == 0
    builder = role_line(out, "core-builder")
    juror = role_line(out, "juror")
    assert figure(builder, "refus") == 1
    assert figure(builder, "overr") == 0
    assert figure(builder, "token") == 1000
    assert figure(juror, "overr") == 1
    assert figure(juror, "refus") == 0
    assert figure(juror, "token") == 9000
    assert figure(juror, "sec") == 90
