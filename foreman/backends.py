"""Agent launch: every dispatch is a fresh agent through a local CLI adapter."""

import json
import tempfile
import tomllib
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from foreman import run
from foreman.config import Config
from foreman.errors import BackendError, ConfigError
from foreman.run import Result


@dataclass
class Job:
    backend: str
    step_id: str
    worktree: Path
    handle: str  # session id or pid


class Backend(Protocol):
    name: str

    def start(self, brief: str, worktree: Path, role: str) -> Job: ...
    def wait(self, job: Job) -> Result: ...
    def tokens(self, job: Job) -> int | None: ...


def _usage(value: object, *, cache_keys: tuple[str, ...] = ()) -> int | None:
    """Count reported usage only; Codex cached input is already in input_tokens."""
    if not isinstance(value, dict):
        return None
    counts = [value.get("input_tokens"), value.get("output_tokens")]
    counts.extend(value.get(key, 0) for key in cache_keys)
    if any(type(count) is not int or count < 0 for count in counts):
        return None
    return sum(counts)


def _claude_record(text: str, *, partial: bool) -> dict:
    try:
        record = json.loads(text)
    except ValueError as e:
        if not partial:
            raise BackendError("invalid Claude JSON output") from e
        # A timeout may cut the final object mid-field. Keep complete fields only.
        decoder = json.JSONDecoder()
        tail = text.lstrip()
        record = {}
        if not tail.startswith("{"):
            return record
        tail = tail[1:].lstrip()
        while tail:
            try:
                key, end = decoder.raw_decode(tail)
                tail = tail[end:].lstrip()
                if not isinstance(key, str) or not tail.startswith(":"):
                    break
                value, end = decoder.raw_decode(tail[1:].lstrip())
                record[key] = value
                tail = tail[1:].lstrip()[end:].lstrip()
                if not tail.startswith(","):
                    break
                tail = tail[1:].lstrip()
            except ValueError:
                break
    if not isinstance(record, dict):
        if partial:
            return {}
        raise BackendError("Claude JSON output must be an object")
    return record


def _codex_tokens(text: str, *, partial: bool) -> int | None:
    counts = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            event = None
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            if partial:
                continue
            raise BackendError("invalid Codex JSON event")
        if event["type"] == "turn.completed":
            count = _usage(event.get("usage"))
            if count is not None:
                counts.append(count)
    return sum(counts) if counts else None


class ClaudeLocal:
    """`claude -p` in the worktree; the session id is the job handle."""

    name = "claude-local"

    def __init__(
        self,
        command: list[str] | None = None,
        args: list[str] | None = None,
        *,
        dispatch_seconds: int,
    ) -> None:
        self.command = command if command is not None else ["claude"]
        self.args = (
            args if args is not None else ["--permission-mode", "bypassPermissions"]
        )
        self.dispatch_seconds = dispatch_seconds
        self._results: dict[str, Result] = {}
        self._tokens: dict[str, int | None] = {}

    def _launch(
        self, flags: list[str], brief: str, worktree: Path, handle: str
    ) -> None:
        cmd = [
            *self.command,
            "-p",
            *self.args,
            *flags,
            "--output-format",
            "json",
            brief,
        ]
        try:
            result = run.run(cmd, worktree, timeout=self.dispatch_seconds)
            record = _claude_record(result.out, partial=result.code == run.TIMEOUT_CODE)
            text = record.get("result", "")
            if not isinstance(text, str):
                if result.code != run.TIMEOUT_CODE:
                    raise BackendError("Claude result must be text")
                text = ""
            if "result" not in record and result.code == 0:
                raise BackendError("Claude JSON has no result text")
            self._tokens[handle] = _usage(
                record.get("usage"),
                cache_keys=("cache_creation_input_tokens", "cache_read_input_tokens"),
            )
            self._results[handle] = replace(result, out=text)
        except BackendError:
            raise
        except Exception as e:
            raise BackendError(f"cannot start {self.command}: {e}") from e

    def start(self, brief: str, worktree: Path, role: str) -> Job:
        handle = str(uuid.uuid4())
        self._launch(["--agent", role, "--session-id", handle], brief, worktree, handle)
        return Job(self.name, worktree.name, worktree, handle)

    def tokens(self, job: Job) -> int | None:
        return self._tokens.get(job.handle)

    def wait(self, job: Job) -> Result:
        try:
            return self._results.pop(job.handle)
        except KeyError:
            raise BackendError(f"no running job {job.handle}") from None


class CodexLocal:
    """`codex exec` with instructions and model settings from a role's TOML."""

    name = "codex-local"

    def __init__(
        self,
        command: list[str] | None = None,
        agents_dir: Path | None = None,
        *,
        dispatch_seconds: int,
    ) -> None:
        self.command = command if command is not None else ["codex"]
        self.agents_dir = (
            agents_dir if agents_dir is not None else Path.home() / ".codex" / "agents"
        )
        self.dispatch_seconds = dispatch_seconds
        self._results: dict[str, Result] = {}
        self._tokens: dict[str, int | None] = {}

    def start(self, brief: str, worktree: Path, role: str) -> Job:
        try:
            with (self.agents_dir / f"{role}.toml").open("rb") as f:
                settings = tomllib.load(f)
            model = settings["model"]
            effort = settings["model_reasoning_effort"]
            instructions = settings["developer_instructions"]
            for key in ("model", "model_reasoning_effort", "developer_instructions"):
                if not isinstance(settings[key], str):
                    raise TypeError(f"{key} must be a string")
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise BackendError(f"cannot load Codex role {role!r}: {e}") from e

        handle = str(uuid.uuid4())
        try:
            with tempfile.TemporaryDirectory(prefix="foreman-codex-") as temp:
                output = Path(temp) / "last-message.txt"
                output.write_text("", encoding="utf-8")
                cmd = [
                    *self.command,
                    "exec",
                    "--json",
                    "-C",
                    str(worktree),
                    "-m",
                    model,
                    "-c",
                    f"model_reasoning_effort={effort}",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "-o",
                    str(output),
                    instructions + "\n\n" + brief,
                ]
                result = run.run(cmd, worktree, timeout=self.dispatch_seconds)
                self._tokens[handle] = _codex_tokens(
                    result.out, partial=result.code == run.TIMEOUT_CODE
                )
                self._results[handle] = replace(
                    result, out=output.read_text(encoding="utf-8")
                )
        except BackendError:
            raise
        except Exception as e:
            raise BackendError(f"cannot start Codex role {role!r}: {e}") from e
        return Job(self.name, worktree.name, worktree, handle)

    def tokens(self, job: Job) -> int | None:
        return self._tokens.get(job.handle)

    def wait(self, job: Job) -> Result:
        try:
            return self._results.pop(job.handle)
        except KeyError:
            raise BackendError(f"no running job {job.handle}") from None


def get(cfg: Config) -> Backend:
    if cfg.backend == ClaudeLocal.name:
        return ClaudeLocal(dispatch_seconds=cfg.dispatch_seconds)
    if cfg.backend == CodexLocal.name:
        return CodexLocal(dispatch_seconds=cfg.dispatch_seconds)
    raise ConfigError(f"unknown backend {cfg.backend!r}")
