"""Agent launch: the Backend interface and the local Claude Code adapter."""

import uuid
from dataclasses import dataclass
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

    def start(self, brief: str, worktree: Path, agent: str) -> Job: ...
    def wait(self, job: Job) -> Result: ...
    def resume(self, job: Job, brief: str) -> Job: ...


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
        self._agents: dict[str, str] = {}

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

    def start(self, brief: str, worktree: Path, agent: str) -> Job:
        handle = str(uuid.uuid4())
        self._agents[handle] = agent
        self._launch(
            ["--agent", agent, "--session-id", handle], brief, worktree, handle
        )
        return Job(self.name, worktree.name, worktree, handle)

    def wait(self, job: Job) -> Result:
        try:
            return self._results.pop(job.handle)
        except KeyError:
            raise BackendError(f"no running job {job.handle}") from None

    def resume(self, job: Job, brief: str) -> Job:
        agent = self._agents[job.handle]
        self._launch(
            ["--agent", agent, "--resume", job.handle], brief, job.worktree, job.handle
        )
        return job


def get(cfg: Config) -> Backend:
    if cfg.backend == ClaudeLocal.name:
        return ClaudeLocal()
    raise ConfigError(f"unknown backend {cfg.backend!r}")
