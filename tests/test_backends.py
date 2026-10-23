from dataclasses import replace
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
    with pytest.raises(ConfigError):
        backends.get(replace(cfg, backend="codex"))


@pytest.fixture
def codex_agents(tmp_path: Path) -> Path:
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "builder.toml").write_text(
        'model = "test-model"\n'
        'model_reasoning_effort = "high"\n'
        'developer_instructions = "Follow the role.\\nPreserve this line."\n'
    )
    return agents


@pytest.mark.parametrize(
    "message",
    ["REJECTED: STEERING", "OVERLOADED: too big", "FINDINGS: x", "TEST WRONG"],
)
@pytest.mark.parametrize("code", [0, 7])
def test_codex_launch_and_last_message(
    fake_run,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    codex_agents: Path,
    message: str,
    code: int,
) -> None:
    calls, codes = fake_run
    codes["test-codex"] = code
    recorded_run = run.run
    output_paths = []

    def write_message(cmd, cwd, timeout=None, env=None):
        output = Path(cmd[cmd.index("-o") + 1])
        assert not output.is_relative_to(worktree)
        output_paths.append(output)
        output.write_text(message, encoding="utf-8")
        return replace(
            recorded_run(cmd, cwd, timeout, env), err="diagnostic", seconds=1.5
        )

    monkeypatch.setattr(run, "run", write_message)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    backend = backends.CodexLocal(
        command=["test-codex", "--custom"], agents_dir=codex_agents
    )
    brief = "Do this.\n\n  Keep whitespace.\n"
    first = backend.start(brief, worktree, "builder")
    second = backend.start(brief, worktree, "builder")
    cmd, cwd = calls[0]
    assert cmd == [
        "test-codex",
        "--custom",
        "exec",
        "-C",
        str(worktree),
        "-m",
        "test-model",
        "-c",
        "model_reasoning_effort=high",
        "--dangerously-bypass-approvals-and-sandbox",
        "-o",
        str(output_paths[0]),
        "Follow the role.\nPreserve this line.\n\n" + brief,
    ]
    assert cwd == worktree
    assert first.backend == "codex-local"
    assert first.step_id == worktree.name
    assert first.handle != second.handle
    assert output_paths[0] != output_paths[1]
    assert all(not path.parent.exists() for path in output_paths)
    result = backend.wait(first)
    assert (result.out, result.code, result.err, result.seconds) == (
        message,
        code,
        "diagnostic",
        1.5,
    )
    assert result.cmd == cmd
    assert backend.wait(second).out == message
    with pytest.raises(BackendError, match="no running job"):
        backend.wait(first)


@pytest.mark.parametrize(
    "missing", ["file", "model", "model_reasoning_effort", "developer_instructions"]
)
def test_codex_missing_role_setting(fake_run, tmp_path: Path, missing: str) -> None:
    settings = {
        "model": "test-model",
        "model_reasoning_effort": "high",
        "developer_instructions": "Do it.",
    }
    if missing != "file":
        del settings[missing]
        (tmp_path / "builder.toml").write_text(
            "".join(f'{key} = "{value}"\n' for key, value in settings.items())
        )
    with pytest.raises(BackendError, match="builder"):
        backends.CodexLocal(agents_dir=tmp_path).start("brief", tmp_path, "builder")
    assert fake_run[0] == []


def test_codex_launch_failure_cleans_output(
    fake_run, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, codex_agents: Path
) -> None:
    recorded_run = run.run
    outputs = []

    def fail(cmd, cwd, timeout=None, env=None):
        recorded_run(cmd, cwd, timeout, env)
        outputs.append(Path(cmd[cmd.index("-o") + 1]))
        raise OSError("cannot launch")

    monkeypatch.setattr(run, "run", fail)
    with pytest.raises(BackendError, match="builder"):
        backends.CodexLocal(agents_dir=codex_agents).start("brief", tmp_path, "builder")
    assert not outputs[0].parent.exists()


def test_codex_failed_command_without_message(
    fake_run, tmp_path: Path, codex_agents: Path
) -> None:
    fake_run[1]["codex"] = 1
    backend = backends.CodexLocal(agents_dir=codex_agents)
    result = backend.wait(backend.start("brief", tmp_path, "builder"))
    assert result.code == 1
    assert result.out == ""


def test_codex_defaults_and_factory(cfg: Config) -> None:
    backend = backends.get(replace(cfg, backend="codex-local"))
    assert isinstance(backend, backends.CodexLocal)
    assert backend.command == ["codex"]
    assert backend.agents_dir == Path.home() / ".codex" / "agents"
