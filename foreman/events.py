"""The one output channel: events.jsonl, the status line, owner signals."""

import json
import sys
import time

from foreman.config import Config

OWNER_SIGNALS = ("ruling_needed", "red_twice", "milestone_done")


class EventLog:
    def __init__(self, cfg: Config) -> None:
        self._path = cfg.state_dir / "events.jsonl"

    def emit(self, kind: str, step_id: str | None = None, **fields: object) -> None:
        record = {"ts": time.time(), "kind": kind, "step_id": step_id, **fields}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def notify_owner(self, signal: str, step_id: str | None, text: str) -> None:
        if signal not in OWNER_SIGNALS:
            raise ValueError(f"unknown owner signal: {signal}")
        self.emit(signal, step_id, text=text)
        sys.stderr.write(f"[{signal}] {step_id or '-'}: {text}\n")

    def status(self) -> str:
        if not self._path.exists():
            return "no events"
        lines = self._path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return "no events"
        last = json.loads(lines[-1])
        return (
            f"{len(lines)} events; last: {last['kind']} step {last['step_id'] or '-'}"
        )
