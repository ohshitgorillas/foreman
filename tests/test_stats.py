"""`foreman stats`: per role, tokens and seconds per finished dispatch, with
overrun and refusal counts, read from Foreman's existing event log and from
Claude Code's subagent history and top-level agent sessions."""

import json
import re
from pathlib import Path

import pytest

from foreman.cli import main
from foreman.config import Config
from foreman.events import EventLog

NUMBER = r"\d+(?:\.\d+)?"


def history(conf_file: Path) -> Path:
    """The fixture history root beside conf_file, never the real home."""
    return conf_file.parent / "history"


def stats(conf_file: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """Run `foreman stats` against conf_file; return its exit code and stdout."""
    argv = ["--config", str(conf_file), "stats", "--history", str(history(conf_file))]
    try:
        code = main(argv)
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


def transcript(
    root: Path, name: str, role: str, usages: list[dict], start: str, end: str
) -> None:
    """One subagent transcript under root with its sibling meta file."""
    folder = root / "-some-project" / "session-1" / "subagents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"agent-{name}.meta.json").write_text(
        json.dumps({"agentType": role, "description": "x", "finished": True})
    )
    lines = [{"type": "user", "timestamp": start, "message": {"role": "user"}}]
    for i, usage in enumerate(usages):
        lines.append(
            {
                "type": "assistant",
                "timestamp": start,
                "message": {
                    "id": f"msg_{name}_{i}",
                    "role": "assistant",
                    "usage": usage,
                },
            }
        )
    lines.append({"type": "user", "timestamp": end, "message": {"role": "user"}})
    (folder / f"agent-{name}.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in lines)
    )


def usage(inp: int, out: int, created: int = 0, read: int = 0) -> dict:
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_creation_input_tokens": created,
        "cache_read_input_tokens": read,
    }


def test_stats_reads_subagent_history(conf_file: Path, capsys) -> None:
    root = history(conf_file)
    transcript(
        root,
        "a1",
        "spec-scout",
        [usage(100, 100, 200, 100), usage(200, 100, 0, 200)],
        "2026-10-18T23:13:08.000Z",
        "2026-10-18T23:14:48.000Z",
    )
    transcript(
        root,
        "a2",
        "spec-scout",
        [usage(1000, 2000)],
        "2026-10-18T23:00:00.000Z",
        "2026-10-18T23:05:00.000Z",
    )
    code, out = stats(conf_file, capsys)
    assert code == 0
    line = role_line(out, "spec-scout")
    assert figure(line, "token") == 2000
    assert figure(line, "sec") == 200
    assert figure(line, "overr") == 0
    assert figure(line, "refus") == 0


def test_stats_prints_history_roles_beside_event_roles(
    conf_file: Path, log: EventLog, capsys
) -> None:
    dispatch(log, "core-builder", 1000, 10.0, "done")
    transcript(
        history(conf_file),
        "b1",
        "general-purpose",
        [usage(4000, 1000)],
        "2026-10-18T10:00:00Z",
        "2026-10-18T10:00:30Z",
    )
    code, out = stats(conf_file, capsys)
    assert code == 0
    builder = role_line(out, "core-builder")
    assert figure(builder, "token") == 1000
    general = role_line(out, "general-purpose")
    assert figure(general, "token") == 5000
    assert figure(general, "sec") == 30


def test_stats_merges_a_role_found_in_both_sources(
    conf_file: Path, log: EventLog, capsys
) -> None:
    dispatch(log, "juror", 1000, 10.0, "done")
    dispatch(log, "juror", 1000, 600.0, "overran")
    transcript(
        history(conf_file),
        "c1",
        "juror",
        [usage(2000, 1000)],
        "2026-10-18T10:00:00Z",
        "2026-10-18T10:00:50Z",
    )
    code, out = stats(conf_file, capsys)
    assert code == 0
    line = role_line(out, "juror")
    assert figure(line, "token") == 2000
    assert figure(line, "sec") == 30
    assert figure(line, "overr") == 1


def session(
    root: Path,
    name: str,
    role: str,
    usages: list[dict],
    start: str,
    end: str,
    finished: bool = True,
) -> None:
    """One top-level `claude -p --agent <role>` session file under root."""
    folder = root / "-some-project"
    folder.mkdir(parents=True, exist_ok=True)
    lines: list[dict] = [
        {"type": "agent-setting", "agentSetting": role, "sessionId": name},
        {"type": "user", "timestamp": start, "message": {"role": "user"}},
    ]
    for i, use in enumerate(usages):
        message = {"id": f"msg_{name}_{i}", "role": "assistant", "usage": use}
        # A streamed message repeats its usage on each content line.
        lines += [{"type": "assistant", "timestamp": start, "message": message}] * 2
    lines.append({"type": "agent-setting", "agentSetting": role, "sessionId": name})
    lines.append({"type": "user", "timestamp": end, "message": {"role": "user"}})
    if finished:
        lines.append({"type": "cost-state", "sessionId": name, "totalCostUSD": 0.1})
    (folder / f"{name}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))


def test_stats_reads_top_level_agent_sessions(conf_file: Path, capsys) -> None:
    root = history(conf_file)
    session(
        root,
        "s-lead",
        "lead",
        [usage(10000, 2000, 3000, 5000)],
        "2026-10-26T10:00:00Z",
        "2026-10-26T10:20:00Z",
    )
    session(
        root,
        "s-divvy-1",
        "divvyer",
        [usage(100, 200), usage(300, 400)],
        "2026-10-26T11:00:00Z",
        "2026-10-26T11:01:00Z",
    )
    session(
        root,
        "s-divvy-2",
        "divvyer",
        [usage(1000, 1000)],
        "2026-10-26T12:00:00Z",
        "2026-10-26T12:03:00Z",
    )
    code, out = stats(conf_file, capsys)
    assert code == 0
    lead = role_line(out, "lead")
    assert figure(lead, "token") == 20000
    assert figure(lead, "sec") == 1200
    assert figure(lead, "overr") == 0
    assert figure(lead, "refus") == 0
    divvyer = role_line(out, "divvyer")
    assert figure(divvyer, "token") == 1500
    assert figure(divvyer, "sec") == 120


def test_stats_leaves_out_an_unfinished_session_and_one_with_no_agent(
    conf_file: Path, capsys
) -> None:
    root = history(conf_file)
    session(
        root,
        "s-done",
        "architect",
        [usage(500, 500)],
        "2026-10-26T10:00:00Z",
        "2026-10-26T10:00:40Z",
    )
    session(
        root,
        "s-open",
        "architect",
        [usage(90000, 9000)],
        "2026-10-26T11:00:00Z",
        "2026-10-26T11:09:00Z",
        finished=False,
    )
    plain = root / "-some-project" / "s-plain.jsonl"
    plain.write_text(
        json.dumps({"type": "user", "timestamp": "2026-10-26T09:00:00Z"})
        + "\n"
        + json.dumps({"type": "cost-state", "sessionId": "s-plain"})
        + "\n"
    )
    code, out = stats(conf_file, capsys)
    assert code == 0
    line = role_line(out, "architect")
    assert figure(line, "token") == 1000
    assert figure(line, "sec") == 40
