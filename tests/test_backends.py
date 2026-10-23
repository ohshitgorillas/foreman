from pathlib import Path

import pytest

from foreman import backends, run
from foreman.config import Config
from foreman.errors import BackendError, ConfigError
from foreman.run import Result


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], Path]]:
    seen: list[tuple[list[str], Path]] = []

    def fake(cmd, cwd, timeout=None, env=None):
        seen.append((cmd, cwd))
        return Result(cmd, 0, "done", "", 0.0)

    monkeypatch.setattr(run, "run", fake)
    return seen


def test_start_runs_claude_in_worktree(calls, tmp_path: Path) -> None:
    b = backends.ClaudeLocal()
    job = b.start("do it", tmp_path, "builder")
    cmd, cwd = calls[0]
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[-1] == "do it"
    assert cmd[cmd.index("--session-id") + 1] == job.handle
    assert cmd[cmd.index("--agent") + 1] == "builder"
    assert cwd == tmp_path
    assert job.backend == "claude-local"
    assert b.wait(job).out == "done"


def test_resume_reuses_session(calls, tmp_path: Path) -> None:
    b = backends.ClaudeLocal()
    job = b.start("first", tmp_path, "builder")
    b.wait(job)
    again = b.resume(job, "fix it")
    cmd, _ = calls[1]
    assert cmd[cmd.index("--resume") + 1] == job.handle
    assert cmd[cmd.index("--agent") + 1] == "builder"
    assert cmd[-1] == "fix it"
    assert again.handle == job.handle
    assert b.wait(again).code == 0


def test_wait_without_start_raises(tmp_path: Path) -> None:
    job = backends.Job("claude-local", "s", tmp_path, "nope")
    with pytest.raises(BackendError):
        backends.ClaudeLocal().wait(job)


def test_start_failure_is_backend_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(cmd, cwd, timeout=None, env=None):
        raise OSError("no claude")

    monkeypatch.setattr(run, "run", boom)
    with pytest.raises(BackendError):
        backends.ClaudeLocal().start("x", tmp_path, "builder")


def test_get_resolves_backend(cfg: Config) -> None:
    assert isinstance(backends.get(cfg), backends.ClaudeLocal)


def test_get_unknown_backend(cfg: Config) -> None:
    from dataclasses import replace

    with pytest.raises(ConfigError):
        backends.get(replace(cfg, backend="codex"))
