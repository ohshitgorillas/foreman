"""Agent launch: every dispatch is a fresh agent through a local CLI adapter."""

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


class ClaudeLocal:
    """`claude -p` in the worktree; the session id is the job handle."""

    name = "claude-local"

    def __init__(
        self,
        command: list[str] | None = None,
        args: list[str] | None = None,
    ) -> None:
        self.command = command if command is not None else ["claude"]
        self.args = (
            args if args is not None else ["--permission-mode", "bypassPermissions"]
        )
        self._results: dict[str, Result] = {}

    def _launch(
        self, flags: list[str], brief: str, worktree: Path, handle: str
    ) -> None:
        cmd = [*self.command, "-p", *self.args, *flags, brief]
        try:
            self._results[handle] = run.run(cmd, worktree)
        except BackendError:
            raise
        except Exception as e:
            raise BackendError(f"cannot start {self.command}: {e}") from e

    def start(self, brief: str, worktree: Path, role: str) -> Job:
        handle = str(uuid.uuid4())
        self._launch(["--agent", role, "--session-id", handle], brief, worktree, handle)
        return Job(self.name, worktree.name, worktree, handle)

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
    ) -> None:
        self.command = command if command is not None else ["codex"]
        self.agents_dir = (
            agents_dir if agents_dir is not None else Path.home() / ".codex" / "agents"
        )
        self._results: dict[str, Result] = {}

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
                result = run.run(cmd, worktree)
                self._results[handle] = replace(
                    result, out=output.read_text(encoding="utf-8")
                )
        except BackendError:
            raise
        except Exception as e:
            raise BackendError(f"cannot start Codex role {role!r}: {e}") from e
        return Job(self.name, worktree.name, worktree, handle)

    def wait(self, job: Job) -> Result:
        try:
            return self._results.pop(job.handle)
        except KeyError:
            raise BackendError(f"no running job {job.handle}") from None


def get(cfg: Config) -> Backend:
    if cfg.backend == ClaudeLocal.name:
        return ClaudeLocal()
    if cfg.backend == CodexLocal.name:
        return CodexLocal()
    raise ConfigError(f"unknown backend {cfg.backend!r}")
