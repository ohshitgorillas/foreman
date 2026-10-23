"""The one output channel: events.jsonl, kept text, signals, stream writes."""

import json
import sys
import time
from pathlib import Path

from foreman.config import Config
from foreman.errors import ConfigError

OWNER_SIGNALS = ("milestone_done",)
DISPATCHER_SIGNALS = ("findings", "ruling_needed", "red_twice")


class EventLog:
    def __init__(self, cfg: Config) -> None:
        self._dir = cfg.state_dir
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

    def notify_dispatcher(self, signal: str, group_id: str, text: str) -> None:
        if signal not in DISPATCHER_SIGNALS:
            raise ValueError(f"unknown dispatcher signal: {signal}")
        self.emit(signal, group_id, text=text)
        sys.stderr.write(f"[{signal}] {group_id}: {text}\n")

    def keep(self, name: str, text: str) -> Path:
        path = self._dir / f"{name}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.emit("kept", name=name, path=str(path))
        return path

    def kept(self, name: str) -> str:
        path = self._dir / f"{name}.txt"
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            raise ConfigError(f"no kept text {name}: {e}") from e

    def done(self) -> set[str]:
        if not self._path.exists():
            return set()
        rows = (
            json.loads(x)
            for x in self._path.read_text(encoding="utf-8").splitlines()
            if x
        )
        return {r["step_id"] for r in rows if r["kind"] == "group_done"}

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


def say(text: str) -> None:
    sys.stdout.write(text + "\n")


def fail(text: str) -> None:
    sys.stderr.write(f"foreman: {text}\n")
