from pathlib import Path

import pytest

from foreman import backends, run
from foreman.config import Config
from foreman.errors import BackendError, ConfigError


def test_start_runs_claude_in_worktree(fake_run, tmp_path: Path) -> None:
    b = backends.ClaudeLocal()
    job = b.start("do it", tmp_path, "builder")
    calls, _ = fake_run
    cmd, cwd = calls[0]
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[-1] == "do it"
    assert cmd[cmd.index("--session-id") + 1] == job.handle
    assert cmd[cmd.index("--agent") + 1] == "builder"
    assert cwd == tmp_path
    assert job.backend == "claude-local"
    assert b.wait(job).out == "out claude"


def test_each_start_is_a_fresh_session(fake_run, tmp_path: Path) -> None:
    b = backends.ClaudeLocal()
    first = b.start("one", tmp_path, "builder")
    second = b.start("two", tmp_path, "gauntlet")
    calls, _ = fake_run
    assert first.handle != second.handle
    assert "--resume" not in calls[1][0]
    assert calls[1][0][calls[1][0].index("--agent") + 1] == "gauntlet"
    assert not hasattr(b, "resume")


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
