"""The one output channel: events.jsonl, kept text, signals, stream writes."""

import json
import sys
import time
from collections.abc import Iterable
from pathlib import Path

from foreman.config import Config
from foreman.errors import ConfigError

OWNER_SIGNALS = ("milestone_done", "test_wrong")
DISPATCHER_SIGNALS = ("findings", "ruling_needed")
MISMATCH_KINDS = (
    "refused",
    "red report",
    "evidence",
    "outside paths",
    "overload",
    "verdict",
    "repair evidence",
    "preflight",
)
STOP_KINDS = (
    "timeout",
    "infrastructure",
    "steering",
    "overload",
    "reviewer refused",
    "findings",
    "owner",
    "evidence",
    "no change",
    "killed",
    "crashed",
)


def _mean(xs: list[float]) -> str:
    return f"{sum(xs) / len(xs):.1f}" if xs else "-"


class EventLog:
    def __init__(self, where: Config | Path) -> None:
        """Log under a `Config`'s state_dir, or under a bare state directory,
        so history and stats can be read with no foreman.toml."""
        self._dir = where if isinstance(where, Path) else where.state_dir
        self._path = self._dir / "events.jsonl"

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

    def dispatched(
        self,
        group_id: str,
        name: str,
        role: str,
        *,
        load: int | None,
        seconds: float,
        tokens: int | None,
        outcome: str,
    ) -> None:
        self.emit(
            "dispatch",
            group_id,
            name=name,
            role=role,
            load=load,
            seconds=seconds,
            tokens=tokens,
            outcome=outcome,
        )

    def mismatched(self, group_id: str | None, name: str, kind: str, text: str) -> None:
        if kind not in MISMATCH_KINDS:
            raise ValueError(f"unknown mismatch kind: {kind}")
        self.emit("mismatch", group_id, name=name, mismatch_kind=kind, text=text)

    def stopped(
        self, group_id: str | None, kind: str, exit_code: int, text: str
    ) -> None:
        if kind not in STOP_KINDS:
            raise ValueError(f"unknown stop kind: {kind}")
        self.emit("stop", group_id, stop_kind=kind, exit_code=exit_code, text=text)

    def keep_report(self, name: str, text: str) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        n = len(list(self._dir.glob(f"{name}-attempt-*.txt"))) + 1
        path = self._dir / f"{name}-attempt-{n}.txt"
        path.write_text(text, encoding="utf-8")
        (self._dir / f"{name}.txt").write_text(text, encoding="utf-8")
        self.emit("kept", name=f"{name}-attempt-{n}", path=str(path))
        return path

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

    def stats(self, extra: Iterable[dict] = ()) -> str:
        """Per role: mean tokens and seconds per finished dispatch, with counts.

        `extra` holds dispatch rows from elsewhere (role, tokens, seconds,
        outcome), counted beside the ones in events.jsonl."""
        rows = (
            [
                json.loads(x)
                for x in self._path.read_text(encoding="utf-8").splitlines()
                if x
            ]
            if self._path.exists()
            else []
        )
        roles: dict[str, list[dict]] = {}
        for r in [r for r in rows if r["kind"] == "dispatch"] + list(extra):
            roles.setdefault(r["role"], []).append(r)
        if not roles:
            return "no dispatches"
        out = []
        for role, rs in sorted(roles.items()):
            fin = [r for r in rs if r["outcome"] in ("done", "refused")]
            toks = [r["tokens"] for r in fin if r["tokens"] is not None]
            secs = [r["seconds"] for r in fin]
            over = sum(r["outcome"] == "overran" for r in rs)
            refused = sum(r["outcome"] == "refused" for r in rs)
            out.append(
                f"{role}: tokens: {_mean(toks)} seconds: {_mean(secs)} "
                f"overruns: {over} refusals: {refused}"
            )
        return "\n".join(out)


# The last line said or failed, for the exit alert.
LAST: list[str] = [""]


def say(text: str) -> None:
    LAST[0] = text
    sys.stdout.write(text + "\n")


def fail(text: str) -> None:
    LAST[0] = f"foreman: {text}"
    sys.stderr.write(f"foreman: {text}\n")
