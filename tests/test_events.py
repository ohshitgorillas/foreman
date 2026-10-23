import json

import pytest

from foreman.config import Config
from foreman.events import OWNER_SIGNALS, EventLog


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
    log.notify_owner("red_twice", "s2", "stuck")
    assert "stuck" in capsys.readouterr().err
    row = json.loads((cfg.state_dir / "events.jsonl").read_text())
    assert (row["kind"], row["step_id"], row["text"]) == ("red_twice", "s2", "stuck")


def test_notify_owner_rejects_unknown_signal(cfg: Config) -> None:
    assert "red_twice" in OWNER_SIGNALS
    with pytest.raises(ValueError):
        EventLog(cfg).notify_owner("bogus", None, "x")


def test_status_one_line_from_log(cfg: Config) -> None:
    log = EventLog(cfg)
    assert log.status() == "no events"
    log.emit("start", "s1")
    log.emit("verify", "s1")
    s = log.status()
    assert "\n" not in s and "2 events" in s and "verify" in s and "s1" in s
