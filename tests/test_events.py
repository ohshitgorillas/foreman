import json

import pytest

from foreman.config import Config
from foreman.errors import (
    ConfigError,
    FindingsToRule,
    ForemanError,
    RulingNeeded,
    WrongTest,
)
from foreman.events import DISPATCHER_SIGNALS, OWNER_SIGNALS, EventLog, fail, say


def test_emit_appends_json_lines(cfg: Config) -> None:
    log = EventLog(cfg)
    log.emit("start", "s1", n=1)
    log.emit("done")
    rows = [
        json.loads(x) for x in (cfg.state_dir / "events.jsonl").read_text().splitlines()
    ]
    assert [(r["kind"], r["step_id"]) for r in rows] == [
        ("start", "s1"),
        ("done", None),
    ]
    assert rows[0]["n"] == 1


def test_notify_owner_logs_and_writes_stderr(
    cfg: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    log = EventLog(cfg)
    log.notify_owner("milestone_done", "s2", "yay")
    assert "yay" in capsys.readouterr().err
    row = json.loads((cfg.state_dir / "events.jsonl").read_text())
    assert (row["kind"], row["step_id"], row["text"]) == ("milestone_done", "s2", "yay")


@pytest.mark.parametrize("signal", ["bogus", "findings", "ruling_needed", "red_twice"])
def test_notify_owner_rejects_unknown_signal(cfg: Config, signal: str) -> None:
    assert OWNER_SIGNALS == ("milestone_done", "test_wrong")
    with pytest.raises(ValueError):
        EventLog(cfg).notify_owner(signal, None, "x")
    assert not (cfg.state_dir / "events.jsonl").exists()


def test_status_one_line_from_log(cfg: Config) -> None:
    log = EventLog(cfg)
    assert log.status() == "no events"
    log.emit("start", "s1")
    log.emit("verify", "s1")
    s = log.status()
    assert "\n" not in s and "2 events" in s and "verify" in s and "s1" in s


@pytest.mark.parametrize("signal", ["findings", "ruling_needed"])
def test_notify_dispatcher_logs_and_writes_stderr(
    cfg: Config, capsys: pytest.CaptureFixture[str], signal: str
) -> None:
    log = EventLog(cfg)
    assert DISPATCHER_SIGNALS == (
        "findings",
        "ruling_needed",
    )
    log.notify_dispatcher(signal, "g1", "stuck")
    assert capsys.readouterr().err == f"[{signal}] g1: stuck\n"
    row = json.loads((cfg.state_dir / "events.jsonl").read_text())
    assert (row["kind"], row["step_id"], row["text"]) == (signal, "g1", "stuck")


@pytest.mark.parametrize(
    "signal", ["bogus", "milestone_done", "test_wrong", "red_twice"]
)
def test_notify_dispatcher_rejects_removed_and_unknown_signals(
    cfg: Config, signal: str
) -> None:
    with pytest.raises(ValueError):
        EventLog(cfg).notify_dispatcher(signal, "g1", "x")
    assert not (cfg.state_dir / "events.jsonl").exists()


def test_keep_and_kept_round_trip(cfg: Config) -> None:
    log = EventLog(cfg)
    path = log.keep("g1-reply", "hello")
    assert path == cfg.state_dir / "g1-reply.txt"
    assert log.kept("g1-reply") == "hello"
    row = json.loads((cfg.state_dir / "events.jsonl").read_text().splitlines()[-1])
    assert row["kind"] == "kept"


def test_kept_absent_raises_config_error(cfg: Config) -> None:
    with pytest.raises(ConfigError):
        EventLog(cfg).kept("nope")


def test_done_lists_group_done_ids(cfg: Config) -> None:
    log = EventLog(cfg)
    assert log.done() == set()
    log.emit("group_done", "g1")
    log.emit("start", "g2")
    log.emit("group_done", "g3")
    assert log.done() == {"g1", "g3"}


def test_say_and_fail(capsys: pytest.CaptureFixture[str]) -> None:
    say("hi")
    fail("bad")
    out = capsys.readouterr()
    assert (out.out, out.err) == ("hi\n", "foreman: bad\n")


def test_new_error_exit_codes() -> None:
    assert (RulingNeeded.exit_code, FindingsToRule.exit_code) == (6, 7)


def test_wrong_test_exit_code_and_signal(
    cfg: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    assert WrongTest.exit_code == 8
    assert isinstance(WrongTest("owner must act"), ForemanError)
    EventLog(cfg).notify_owner("test_wrong", "g1", "bad test")
    assert capsys.readouterr().err == "[test_wrong] g1: bad test\n"
    row = json.loads((cfg.state_dir / "events.jsonl").read_text())
    assert (row["kind"], row["step_id"]) == ("test_wrong", "g1")


@pytest.mark.parametrize("outcome", ["done", "overran", "refused"])
@pytest.mark.parametrize(("load", "tokens"), [(237, 1234), (None, None), (0, 0)])
def test_dispatched_records_one_event_with_measurements(
    cfg: Config,
    capsys: pytest.CaptureFixture[str],
    outcome: str,
    load: int | None,
    tokens: int | None,
) -> None:
    log = EventLog(cfg)
    log.dispatched(
        "g1",
        "g1-review-diff-reviewer-batch-2",
        "core-builder",
        load=load,
        seconds=1.25,
        tokens=tokens,
        outcome=outcome,
    )
    lines = (cfg.state_dir / "events.jsonl").read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert isinstance(row.pop("ts"), float)
    assert row == {
        "kind": "dispatch",
        "step_id": "g1",
        "name": "g1-review-diff-reviewer-batch-2",
        "role": "core-builder",
        "load": load,
        "seconds": 1.25,
        "tokens": tokens,
        "outcome": outcome,
    }
    assert capsys.readouterr() == ("", "")
    assert log.done() == set()
