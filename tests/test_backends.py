import json
from dataclasses import replace
from pathlib import Path

import pytest

from foreman import backends, run
from foreman.config import Config
from foreman.errors import BackendError, ConfigError


def test_start_runs_claude_in_worktree(fake_run, tmp_path: Path) -> None:
    b = backends.ClaudeLocal(dispatch_seconds=37)
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
    b = backends.ClaudeLocal(dispatch_seconds=37)
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
        backends.ClaudeLocal(dispatch_seconds=37).wait(job)


def test_start_failure_is_backend_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(cmd, cwd, timeout=None, env=None):
        raise OSError("no claude")

    monkeypatch.setattr(run, "run", boom)
    with pytest.raises(BackendError):
        backends.ClaudeLocal(dispatch_seconds=37).start("x", tmp_path, "builder")


def test_get_resolves_backend(cfg: Config) -> None:
    backend = backends.get(cfg)
    assert isinstance(backend, backends.ClaudeLocal)
    assert backend.dispatch_seconds == cfg.dispatch_seconds


def test_get_unknown_backend(cfg: Config) -> None:
    with pytest.raises(ConfigError):
        backends.get(replace(cfg, backend="codex"))


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
        command=["test-codex", "--custom"], agents_dir=codex_agents, dispatch_seconds=37
    )
    brief = "Do this.\n\n  Keep whitespace.\n"
    first = backend.start(brief, worktree, "builder")
    second = backend.start(brief, worktree, "builder")
    cmd, cwd = calls[0]
    assert cmd == [
        "test-codex",
        "--custom",
        "exec",
        "--json",
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
        backends.CodexLocal(agents_dir=tmp_path, dispatch_seconds=37).start(
            "brief", tmp_path, "builder"
        )
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
        backends.CodexLocal(agents_dir=codex_agents, dispatch_seconds=37).start(
            "brief", tmp_path, "builder"
        )
    assert not outputs[0].parent.exists()


def test_codex_failed_command_without_message(
    fake_run, tmp_path: Path, codex_agents: Path
) -> None:
    fake_run[1]["codex"] = 1
    backend = backends.CodexLocal(agents_dir=codex_agents, dispatch_seconds=37)
    result = backend.wait(backend.start("brief", tmp_path, "builder"))
    assert result.code == 1
    assert result.out == ""


def test_codex_defaults_and_factory(cfg: Config) -> None:
    backend = backends.get(replace(cfg, backend="codex-local"))
    assert isinstance(backend, backends.CodexLocal)
    assert backend.dispatch_seconds == cfg.dispatch_seconds
    assert backend.command == ["codex"]
    assert backend.agents_dir == Path.home() / ".codex" / "agents"


@pytest.mark.parametrize("kind", ["claude", "codex"])
@pytest.mark.parametrize("code", [0, 7, run.TIMEOUT_CODE])
def test_structured_report_usage_and_timeout(
    adapter_run, tmp_path: Path, codex_agents: Path, kind: str, code: int
) -> None:
    replies, calls = adapter_run
    report = "REJECTED: STEERING\n  verbatim é\n"
    if kind == "claude":
        stdout = json.dumps(
            {
                "result": report,
                "usage": {
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "cache_creation_input_tokens": 5,
                    "cache_read_input_tokens": 3,
                },
                "modelUsage": {"model": {"inputTokens": 999}},
            }
        )
        expected = 26
        backend = backends.ClaudeLocal(dispatch_seconds=19)
    else:
        stdout = "\n".join(
            json.dumps(event)
            for event in [
                {"type": "item.completed", "item": {"text": "transport prose"}},
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 11,
                        "cached_input_tokens": 5,
                        "output_tokens": 7,
                    },
                },
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                },
            ]
        )
        expected = 23
        backend = backends.CodexLocal(agents_dir=codex_agents, dispatch_seconds=19)
    replies.extend(
        [
            (stdout, report, code),
            (json.dumps({"result": "second"}) if kind == "claude" else "", "second", 0),
        ]
    )
    first = backend.start("brief", tmp_path, "builder")
    second = backend.start("second brief", tmp_path, "builder")
    result = backend.wait(first)
    assert (result.code, result.out, result.err, result.seconds) == (
        code,
        report,
        "diagnostic",
        1.25,
    )
    assert backend.tokens(first) == expected
    assert backend.wait(second).out == "second"
    assert backend.tokens(second) is None
    assert backend.tokens(first) == expected
    assert all(call[2] == 19 for call in calls)


@pytest.mark.parametrize("kind", ["claude", "codex"])
@pytest.mark.parametrize(
    "usage, expected",
    [
        (None, None),
        ({}, None),
        ({"input_tokens": 0, "output_tokens": 0}, 0),
        ({"input_tokens": 2}, None),
        ({"input_tokens": True, "output_tokens": 1}, None),
        ({"input_tokens": -1, "output_tokens": 1}, None),
        ({"input_tokens": 1.5, "output_tokens": 1}, None),
        ({"input_tokens": "2", "output_tokens": 1}, None),
    ],
)
def test_usage_never_guesses(
    adapter_run, tmp_path: Path, codex_agents: Path, kind: str, usage, expected
) -> None:
    replies, _ = adapter_run
    if kind == "claude":
        record = {"result": "report", "usage": usage}
        backend = backends.ClaudeLocal(dispatch_seconds=9)
    else:
        record = {"type": "turn.completed", "usage": usage}
        backend = backends.CodexLocal(agents_dir=codex_agents, dispatch_seconds=9)
    replies.append((json.dumps(record), "report", 0))
    job = backend.start("brief", tmp_path, "builder")
    assert backend.wait(job).out == "report"
    assert backend.tokens(job) == expected


@pytest.mark.parametrize("kind", ["claude", "codex"])
@pytest.mark.parametrize("with_fields", [False, True])
def test_truncated_timeout_keeps_available_report_and_usage(
    adapter_run, tmp_path: Path, codex_agents: Path, kind: str, with_fields: bool
) -> None:
    replies, _ = adapter_run
    if kind == "claude":
        stdout = (
            '{"result":"partial report","usage":'
            '{"input_tokens":4,"output_tokens":2},"unfinished":'
            if with_fields
            else '{"res'
        )
        backend = backends.ClaudeLocal(dispatch_seconds=5)
    else:
        stdout = (
            '{"type":"turn.completed","usage":'
            '{"input_tokens":4,"output_tokens":2}}\n{"type":'
            if with_fields
            else '{"ty'
        )
        backend = backends.CodexLocal(agents_dir=codex_agents, dispatch_seconds=5)
    report = "partial report" if with_fields else ""
    replies.append((stdout, report, run.TIMEOUT_CODE))
    job = backend.start("brief", tmp_path, "builder")
    result = backend.wait(job)
    assert result.code == run.TIMEOUT_CODE
    assert result.out == report
    assert result.err == "diagnostic"
    assert backend.tokens(job) == (6 if with_fields else None)


@pytest.mark.parametrize("kind", ["claude", "codex"])
@pytest.mark.parametrize("stdout", ["broken", "[]", '{"result":'])
def test_malformed_transport_stops(
    adapter_run, tmp_path: Path, codex_agents: Path, kind: str, stdout: str
) -> None:
    adapter_run[0].append((stdout, "report", 0))
    backend = (
        backends.ClaudeLocal(dispatch_seconds=5)
        if kind == "claude"
        else backends.CodexLocal(agents_dir=codex_agents, dispatch_seconds=5)
    )
    with pytest.raises(BackendError, match="JSON"):
        backend.start("brief", tmp_path, "builder")


@pytest.mark.parametrize(
    ("stdout", "stderr"),
    [
        ("", "Error: credit balance is too low"),
        ("Error: Invalid API key", "warning: retrying"),
    ],
)
def test_claude_stop_carries_claude_error_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stdout: str, stderr: str
) -> None:
    def failed(cmd, cwd, timeout=None, env=None):
        return run.Result(cmd, 1, stdout, stderr, 0.5)

    monkeypatch.setattr(run, "run", failed)
    with pytest.raises(BackendError) as caught:
        backends.ClaudeLocal(dispatch_seconds=5).start("brief", tmp_path, "builder")
    message = str(caught.value)
    assert stderr in message
    assert stdout in message


@pytest.mark.parametrize(
    ("stdout", "stderr"),
    [
        ('{"type":', "Error: stream disconnected before completion"),
        ("Error: unexpected status 401 Unauthorized", "warning: retrying"),
    ],
)
def test_codex_stop_carries_codex_error_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    codex_agents: Path,
    stdout: str,
    stderr: str,
) -> None:
    def failed(cmd, cwd, timeout=None, env=None):
        events = [json.dumps({"type": "thread.started"}), stdout]
        return run.Result(cmd, 1, "\n".join(events), stderr, 0.5)

    monkeypatch.setattr(run, "run", failed)
    with pytest.raises(BackendError) as caught:
        backends.CodexLocal(agents_dir=codex_agents, dispatch_seconds=5).start(
            "brief", tmp_path, "builder"
        )
    message = str(caught.value)
    assert "invalid Codex JSON event" in message
    assert stderr in message
    assert stdout in message


def test_fake_backend_post_wait_usage_and_timeout(fake_backend, tmp_path: Path) -> None:
    backend = fake_backend(
        outputs=["partial", "done"],
        token_counts=[42],
        codes=[run.TIMEOUT_CODE],
        seconds=[5.0],
    )
    first = backend.start("one", tmp_path, "builder")
    second = backend.start("two", tmp_path, "builder")
    assert backend.wait(first).code == run.TIMEOUT_CODE
    assert backend.wait(first).out == "partial"
    assert backend.wait(first).seconds == 5.0
    assert backend.tokens(first) == 42
    assert backend.wait(second).code == 0
    assert backend.tokens(second) is None
    assert backend.tokens(first) == 42
