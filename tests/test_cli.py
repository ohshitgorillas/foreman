import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from foreman import backends, cli, config, errors, events, loop, preflight
from foreman.cli import main
from foreman.config import Config
from foreman.errors import ConfigError, WrongTest
from foreman.run import run_ok

GROUP_FILE = (
    '[steps.s1]\nrole = "core-builder"\nshape = "stub"\nform = {step = "Approved step"}\npaths = ["gauntlet/core/a.py"]\n'
    '[groups.g1]\nsteps = ["s1"]\n'
)


ROLE = ".claude/agents/core-builder.md"
JUROR = ".claude/agents/juror.md"


# Report examples pre-flight's examples check parses: any for core-builder,
# a verdict for the juror.
REPORT = "\n## Report\n\n```\nDONE\n```\n"
VERDICT = (
    "\n## Report\n\n```\nVERDICT undetermined\nACTION owner\nREASON r\n"
    "EVIDENCE gauntlet/core/a.py:1 | c\nEND JUDGMENT\n```\n"
)


def role_file(cfg: Config, scope: str) -> None:
    """Commit the core-builder role at HEAD, where pre-flight finds it."""
    path = cfg.target_repo / ROLE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: core-builder\ndescription: Writes under `{scope}` only.\n---\n"
        + REPORT
    )
    run_ok(["git", "add", ROLE], cfg.target_repo)
    run_ok(["git", "commit", "-q", "-m", f"role scope {scope}"], cfg.target_repo)


@pytest.fixture(autouse=True)
def no_host_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep pre-flight's hooks check off this host's real ~/.claude settings."""
    monkeypatch.setattr(preflight, "USER_SETTINGS", ())


@pytest.fixture(autouse=True)
def no_host_agents(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep pre-flight's installed check off this host's real agent folders."""
    home = tmp_path / "agents-home"
    monkeypatch.setattr(
        preflight,
        "USER_AGENTS",
        {"claude-local": home / "claude", "codex-local": home / "codex"},
    )


@pytest.fixture
def conf_file(conf_file: Path, green_cfg: Config) -> Path:
    """The shared conf_file, its target repo carrying role files pre-flight passes."""
    role_file(green_cfg, "gauntlet/")
    (green_cfg.target_repo / JUROR).write_text("---\nname: juror\n---\n" + VERDICT)
    run_ok(["git", "add", ROLE, JUROR], green_cfg.target_repo)
    run_ok(["git", "commit", "-q", "-m", "role"], green_cfg.target_repo)
    return conf_file


@pytest.fixture
def steps_file(tmp_path: Path) -> Path:
    path = tmp_path / "m.toml"
    path.write_text(GROUP_FILE)
    return path


def test_missing_config_exits_2(tmp_path: Path) -> None:
    assert main(["--config", str(tmp_path / "none.toml"), "status"]) == 2


def test_status_ok(conf_file: Path, capsys) -> None:
    assert main(["--config", str(conf_file), "status"]) == 0
    assert capsys.readouterr().out.strip() == "no events"


def test_overloaded_step_file_exits_2(conf_file: Path, tmp_path: Path) -> None:
    sf = tmp_path / "big.toml"
    sf.write_text(
        '[steps.a]\nrole = "core-builder"\nshape = "stub"\nform = {step = "Approved step"}\n'
        'paths = ["gauntlet/core/a.py", "gauntlet/core/b.py", "gauntlet/core/c.py", '
        '"gauntlet/core/d.py", "gauntlet/core/e.py", "gauntlet/core/f.py"]\n'
        '[groups.g]\nsteps = ["a"]\n'
    )
    assert main(["--config", str(conf_file), "run", str(sf)]) == 2


def test_unknown_backend_exits_2(
    conf_file: Path, steps_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backends.ClaudeLocal, "name", "other")
    assert main(["--config", str(conf_file), "run", str(steps_file)]) == 2


def test_unknown_group_exits_2(conf_file: Path, steps_file: Path) -> None:
    assert (
        main(["--config", str(conf_file), "run", str(steps_file), "--group", "zz"]) == 2
    )


def test_run_group_lands_and_prints_commit(
    conf_file, steps_file, origin, fake_backend, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        backends, "get", lambda cfg: fake_backend([{"gauntlet/core/a.py": "1"}])
    )
    assert (
        main(["--config", str(conf_file), "run", str(steps_file), "--group", "g1"]) == 0
    )
    commit = run_ok(["git", "rev-parse", "foreman/g1"], origin).out.strip()
    assert capsys.readouterr().out.strip() == commit


def test_run_section_lands_each_group(
    conf_file, steps_file, origin, fake_backend, monkeypatch
) -> None:
    monkeypatch.setattr(
        backends, "get", lambda cfg: fake_backend([{"gauntlet/core/a.py": "1"}])
    )
    assert main(["--config", str(conf_file), "run", str(steps_file)]) == 0
    assert run_ok(["git", "rev-parse", "foreman/g1"], origin).out.strip()


def test_stop_maps_to_its_exit_code(
    conf_file, steps_file, origin, fake_backend, monkeypatch
) -> None:
    monkeypatch.setattr(
        backends, "get", lambda cfg: fake_backend(outputs=["REJECTED: no"])
    )
    assert main(["--config", str(conf_file), "run", str(steps_file)]) == 6


@pytest.mark.parametrize("command", ["upheld", "retest"])
@pytest.mark.parametrize("problem", ["missing", "directory", "invalid_utf8"])
def test_continuation_unreadable_file_exits_2(
    conf_file, steps_file, tmp_path: Path, capsys, command, problem
) -> None:
    path = tmp_path / "brief.txt"
    if problem == "directory":
        path.mkdir()
    elif problem == "invalid_utf8":
        path.write_bytes(b"\xff")
    argv = ["--config", str(conf_file), command, str(steps_file), "g1", str(path)]
    assert main(argv) == 2
    assert f"cannot read {path}" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["upheld"])
@pytest.mark.parametrize("text", ["  Void's brief: café\n\nkeep this indent\n", ""])
def test_continuation_forwards_file_verbatim_and_prints_commit(
    conf_file, steps_file, tmp_path: Path, monkeypatch, capsys, command, text
) -> None:
    path = tmp_path / "brief.txt"
    path.write_text(text, encoding="utf-8")
    calls = []

    def continue_group(cfg, group, brief, log):
        calls.append((group.id, brief))
        return "landed-commit"

    monkeypatch.setattr(loop, f"run_{command}", continue_group)
    argv = ["--config", str(conf_file), command, str(steps_file), "g1", str(path)]
    assert main(argv) == 0
    assert calls == [("g1", text)]
    assert capsys.readouterr().out == "landed-commit\n"


def test_retest_unknown_group_exits_2(
    conf_file, steps_file, tmp_path: Path, monkeypatch, capsys
) -> None:
    path = tmp_path / "brief.txt"
    path.write_text("")

    def unexpected(*args):
        pytest.fail("unknown group must not reach run_retest")

    monkeypatch.setattr(loop, "run_retest", unexpected)
    argv = ["--config", str(conf_file), "retest", str(steps_file), "unknown", str(path)]
    assert main(argv) == 2
    assert "unknown group 'unknown'" in capsys.readouterr().err


def test_retest_wrong_test_exits_8(
    conf_file, steps_file, tmp_path: Path, monkeypatch, capsys
) -> None:
    path = tmp_path / "brief.txt"
    path.write_text(
        "VERDICT undetermined\nACTION owner\nREASON Needs owner\n"
        "EVIDENCE README.md:1 | Missing requirement\nEND JUDGMENT"
    )

    def stopped(*args):
        raise WrongTest("builder stopped again")

    monkeypatch.setattr(loop, "run_retest", stopped)
    argv = ["--config", str(conf_file), "retest", str(steps_file), "g1", str(path)]
    assert main(argv) == 8
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "foreman: builder stopped again" in captured.err


@pytest.mark.parametrize("command", ["run", "upheld", "retest", "resume"])
def test_queue_review_must_match_config_before_dispatch(
    conf_file, steps_file, tmp_path, monkeypatch, capsys, command
):
    with steps_file.open("a") as stream:
        stream.write('[groups.g1.reviews.diff-reviewer]\nscope = "working-tree"\n')
    path = tmp_path / "verdict.txt"
    path.write_text("")

    def unexpected(*args):
        pytest.fail("unconfigured reviewer reached dispatch")

    for name in ["run_section", "run_upheld", "run_retest", "run_resume"]:
        monkeypatch.setattr(loop, name, unexpected)
    argv = ["--config", str(conf_file), command, str(steps_file)]
    if command == "resume":
        argv += ["g1", "--from", "s1"]
    elif command != "run":
        argv += ["g1", str(path)]
    assert main(argv) == 2
    assert "diff-reviewer" in capsys.readouterr().err


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_retest_cli_preserves_verdict_bytes(
    conf_file, steps_file, tmp_path, monkeypatch, newline
):
    text = newline.join(
        [
            "VERDICT undetermined",
            "ACTION owner",
            "REASON Needs owner",
            "EVIDENCE README.md:1 | Missing requirement",
            "END JUDGMENT",
        ]
    )
    path = tmp_path / "verdict.txt"
    path.write_bytes(text.encode())

    def continued(cfg, group, supplied, log):
        assert supplied == text
        loop.reports.verdict(supplied)
        return "landed"

    monkeypatch.setattr(loop, "run_retest", continued)
    assert main(
        ["--config", str(conf_file), "retest", str(steps_file), "g1", str(path)]
    ) == (0 if newline == "\n" else 2)


def test_resume_forwards_step_and_prints_commit(
    conf_file, steps_file, monkeypatch, capsys
) -> None:
    calls = []

    def resumed(cfg, group, step_id, log):
        calls.append((group.id, step_id))
        return "landed-commit"

    monkeypatch.setattr(loop, "run_resume", resumed)
    argv = ["--config", str(conf_file), "resume", str(steps_file), "g1"]
    assert main([*argv, "--from", "s1"]) == 0
    assert calls == [("g1", "s1")]
    assert capsys.readouterr().out == "landed-commit\n"


def test_resume_requires_from(conf_file, steps_file, capsys) -> None:
    with pytest.raises(SystemExit) as e:
        main(["--config", str(conf_file), "resume", str(steps_file), "g1"])
    assert e.value.code == 2
    assert "--from" in capsys.readouterr().err


def test_resume_unknown_step_exits_2(conf_file, steps_file, green_cfg, capsys):
    argv = ["--config", str(conf_file), "resume", str(steps_file), "g1"]
    assert main([*argv, "--from", "s9"]) == 2
    assert "group g1 has no step 's9' (steps: s1)" in capsys.readouterr().err
    assert not green_cfg.worktree_root.exists()


def test_resume_without_kept_worktree_exits_2(
    conf_file, steps_file, green_cfg, capsys
) -> None:
    argv = ["--config", str(conf_file), "resume", str(steps_file), "g1"]
    assert main([*argv, "--from", "s1"]) == 2
    assert "group g1 has no kept worktree to resume" in capsys.readouterr().err
    assert not green_cfg.worktree_root.exists()


# Pre-flight: run, upheld, retest and resume print findings as warnings, log them
# as mismatches and dispatch anyway.


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace every loop entry point with a recorder, so nothing is dispatched."""
    calls: list[str] = []

    def recorder(name: str):
        def record(*args):
            calls.append(name)
            return [] if name == "run_section" else "landed"

        return record

    for name in ["run_section", "run_group", "run_upheld", "run_retest", "run_resume"]:
        monkeypatch.setattr(loop, name, recorder(name))
    return calls


def gate_argv(conf_file: Path, steps_file: Path, tmp_path: Path, command: str):
    path = tmp_path / "continuation.txt"
    path.write_text("")
    argv = ["--config", str(conf_file), command, str(steps_file)]
    if command == "resume":
        return argv + ["g1", "--from", "s1"]
    return argv + (["g1", str(path)] if command != "run" else [])


COMMANDS = ["run", "upheld", "retest", "resume"]


def _all_error_classes(cls: type) -> list[type]:
    found = [cls]
    for sub in cls.__subclasses__():
        found.extend(_all_error_classes(sub))
    return found


def test_preflight_failed_and_exit_code_9_are_removed() -> None:
    assert not hasattr(errors, "PreflightFailed")
    codes = {c.exit_code for c in _all_error_classes(errors.ForemanError)}
    assert 9 not in codes


SCOPE_FINDING = (
    "s1 | scope | gauntlet/core/a.py outside core-builder's write scope (docs/)"
)


@pytest.mark.parametrize("command", COMMANDS)
def test_preflight_finding_warns_and_dispatches(
    conf_file, steps_file, green_cfg, tmp_path, dispatched, capsys, command
) -> None:
    role_file(green_cfg, "docs/")
    assert main(gate_argv(conf_file, steps_file, tmp_path, command)) == 0
    assert len(dispatched) == 1
    err = capsys.readouterr().err
    warned = [x for x in err.splitlines() if SCOPE_FINDING in x]
    assert len(warned) == 1
    assert "warning" in warned[0].lower()
    assert "--override-preflight" not in err


def _mismatches(cfg: Config) -> list[dict]:
    path = cfg.state_dir / "events.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    return [r for r in rows if r["kind"] == "mismatch"]


@pytest.mark.parametrize("command", COMMANDS)
def test_preflight_findings_are_each_logged_once_as_mismatches(
    conf_file, steps_file, green_cfg, tmp_path, dispatched, command
) -> None:
    role_file(green_cfg, "docs/")
    cfg = config.load(conf_file)
    findings = preflight.preflight(
        cli._load(steps_file, cfg), cfg, None, preflight.CHECKS
    )
    assert findings
    assert main(gate_argv(conf_file, steps_file, tmp_path, command)) == 0
    rows = _mismatches(green_cfg)
    assert [r["mismatch_kind"] for r in rows] == ["preflight"] * len(findings)
    assert sum(SCOPE_FINDING in r["text"] for r in rows) == 1


@pytest.mark.parametrize("command", COMMANDS)
def test_clean_preflight_logs_no_mismatch(
    conf_file, steps_file, green_cfg, tmp_path, dispatched, command
) -> None:
    assert main(gate_argv(conf_file, steps_file, tmp_path, command)) == 0
    assert _mismatches(green_cfg) == []


@pytest.mark.parametrize("command", COMMANDS)
def test_override_preflight_flag_is_removed(
    conf_file, steps_file, tmp_path, dispatched, capsys, command
) -> None:
    argv = gate_argv(conf_file, steps_file, tmp_path, command)
    with pytest.raises(SystemExit) as exc:
        main([*argv, "--override-preflight"])
    assert exc.value.code == 2
    assert "--override-preflight" in capsys.readouterr().err
    assert dispatched == []


@pytest.mark.parametrize("command", COMMANDS)
def test_clean_preflight_launches(
    conf_file, steps_file, tmp_path, dispatched, capsys, command
) -> None:
    assert main(gate_argv(conf_file, steps_file, tmp_path, command)) == 0
    assert len(dispatched) == 1
    assert capsys.readouterr().err == ""


def test_section_preflight_skips_done_groups(
    conf_file, steps_file, green_cfg, tmp_path, dispatched
) -> None:
    role_file(green_cfg, "docs/")
    events.EventLog(green_cfg).emit("group_done", "g1", commit="abc")
    assert main(gate_argv(conf_file, steps_file, tmp_path, "run")) == 0
    assert dispatched == ["run_section"]


# Exit alert. The ping is a stub under a temporary HOME; alert also fakes Popen
# and signal.signal, so no watcher starts and pytest's handlers stay. The watch
# script itself runs in the foreground against an already-gone pid.


@pytest.fixture
def stub_ping(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    home = tmp_path / "home"
    ping = home / ".local" / "bin" / "claude-ping"
    ping.parent.mkdir(parents=True)
    pinged = tmp_path / "pinged"
    ping.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {pinged}\n')
    ping.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("FOREMAN_NO_ALERT", raising=False)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(events, "LAST", [""])
    return SimpleNamespace(ping=ping, pinged=pinged, tmp=tmp_path)


@pytest.fixture
def alert(monkeypatch: pytest.MonkeyPatch, stub_ping):
    popens: list[list[str]] = []
    handlers: dict[int, object] = {}
    monkeypatch.setattr(cli.run, "spawn_detached", lambda cmd: popens.append(cmd))
    monkeypatch.setattr(
        cli.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn)
    )
    stub_ping.popens, stub_ping.handlers = popens, handlers
    return stub_ping


def argv(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["foreman", *args])


def test_arm_alert_off_when_foreman_no_alert(alert, monkeypatch) -> None:
    monkeypatch.setenv("FOREMAN_NO_ALERT", "1")
    argv(monkeypatch, "run", "q/m2.toml")
    assert cli._arm_alert() is None
    assert alert.popens == [] and alert.handlers == {}


def test_arm_alert_off_without_executable_ping(alert, monkeypatch) -> None:
    alert.ping.chmod(0o644)
    argv(monkeypatch, "run", "q/m2.toml")
    assert cli._arm_alert() is None
    assert alert.popens == []


def test_arm_alert_off_for_status(alert, monkeypatch) -> None:
    argv(monkeypatch, "status")
    assert cli._arm_alert() is None
    assert alert.popens == []


def test_arm_alert_off_and_silent_on_bad_argv(alert, monkeypatch, capsys) -> None:
    argv(monkeypatch, "run")
    assert cli._arm_alert() is None
    assert alert.popens == []
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "args",
    [
        ("run", "q/m2.toml", "--group", "g1"),
        ("upheld", "q/m2.toml", "g1", "f.md"),
        ("retest", "q/m2.toml", "g1", "v.md"),
        ("resume", "q/m2.toml", "g1", "--from", "s1"),
    ],
)
def test_arm_alert_starts_detached_watcher(alert, monkeypatch, args) -> None:
    argv(monkeypatch, "--config", "x.toml", *args)
    label, status = cli._arm_alert()
    assert label == f"Foreman {args[0]} m2"
    assert Path(status).parent == alert.tmp and Path(status).read_text() == ""
    [cmd] = alert.popens
    assert cmd[:3] == ["/bin/sh", "-c", cli._WATCH]
    assert cmd[3:] == [
        "sh",
        str(os.getpid()),
        status,
        "foreman:m2",
        label,
        str(alert.ping),
    ]
    assert set(alert.handlers) == {signal.SIGTERM, signal.SIGHUP}
    with pytest.raises(SystemExit) as e:
        alert.handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert e.value.code == 128 + signal.SIGTERM


def armed_entry(alert, monkeypatch, fake_main) -> tuple[pytest.ExceptionInfo, str]:
    argv(monkeypatch, "run", "q/m2.toml")
    monkeypatch.setattr(cli, "main", fake_main)
    with pytest.raises(BaseException) as e:
        cli.entry()
    [cmd] = alert.popens
    return e, Path(cmd[5]).read_text(encoding="utf-8")


def test_entry_reports_finished_with_last_line(alert, monkeypatch, capsys) -> None:
    def ok() -> int:
        events.say("x" * 400 + "landed abc")
        return 0

    e, text = armed_entry(alert, monkeypatch, ok)
    assert e.type is SystemExit and e.value.code == 0
    assert text == "Foreman run m2 finished: " + ("x" * 400 + "landed abc")[-300:]
    assert not list(alert.tmp.glob("*.tmp"))


def test_entry_reports_stop_code_and_failure(alert, monkeypatch, capsys) -> None:
    def stop() -> int:
        events.fail("owner ruling needed")
        return 6

    e, text = armed_entry(alert, monkeypatch, stop)
    assert e.type is SystemExit and e.value.code == 6
    assert text == "Foreman run m2 STOPPED (exit 6): foreman: owner ruling needed"


def test_entry_reports_signal_exit(alert, monkeypatch) -> None:
    def killed() -> int:
        raise SystemExit(143)

    e, text = armed_entry(alert, monkeypatch, killed)
    assert e.type is SystemExit and e.value.code == 143
    assert text == "Foreman run m2 STOPPED (exit 143): "


def test_entry_treats_bare_exit_as_finished(alert, monkeypatch) -> None:
    def bare() -> int:
        raise SystemExit

    e, text = armed_entry(alert, monkeypatch, bare)
    assert e.type is SystemExit and e.value.code is None
    assert text == "Foreman run m2 finished: "


def test_entry_reports_crash_and_reraises(alert, monkeypatch) -> None:
    def crash() -> int:
        raise RuntimeError("boom")

    e, text = armed_entry(alert, monkeypatch, crash)
    assert e.type is RuntimeError
    assert text == "Foreman run m2 STOPPED (exit crashed: RuntimeError: boom): "


def test_entry_unarmed_passes_code_through(alert, monkeypatch) -> None:
    monkeypatch.setenv("FOREMAN_NO_ALERT", "1")
    argv(monkeypatch, "run", "q/m2.toml")
    monkeypatch.setattr(cli, "main", lambda: 4)
    with pytest.raises(SystemExit) as e:
        cli.entry()
    assert e.value.code == 4
    assert alert.popens == [] and not list(alert.tmp.glob("foreman-alert-*"))


def gone_pid() -> int:
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


@pytest.mark.parametrize(
    ("written", "message"),
    [
        ("Foreman run m2 finished: abc", "Foreman run m2 finished: abc"),
        ("", "Foreman run m2 died without reporting an exit (killed or crashed)"),
    ],
)
def test_watch_script_pings_once_pid_is_gone(stub_ping, written, message) -> None:
    status = stub_ping.tmp / "foreman-alert-x"
    status.write_text(written)
    subprocess.run(
        [
            "/bin/sh",
            "-c",
            cli._WATCH,
            "sh",
            str(gone_pid()),
            str(status),
            "foreman:m2",
            "Foreman run m2",
            str(stub_ping.ping),
        ],
        check=True,
        timeout=15,
    )
    assert stub_ping.pinged.read_text() == f"foreman:m2\n{message}\n"
    assert not status.exists()


# An HQPTuner foreman.toml: its own builder roles and spec folder.
HQPTUNER_CONFIG = """\
target_repo = "/work/hqptuner"
worktree_root = "/work/wt"
check_cmd = ["make", "check"]
format_cmd = ["ruff", "format"]
backend = "claude-local"
state_dir = "/work/state"
view_cmd = ["uv", "run", "stubgen"]
view_root = "/work/views"
batch_budget = 300
dispatch_seconds = 600
spec_dir = "docs/specs/"

[[reviewers]]
role = "test-reviewer"
paths = ["tests/"]

[builders]
backend = ["hqptuner/backend/", "hqptuner/__init__.py"]
frontend = ["hqptuner/frontend/"]
"""

HQPTUNER_QUEUE = """\
[groups.g1]
steps = ["api-stub", "red", "api-build", "ui-stub", "init-stub"]
design = "docs/specs/tuner.md"
[groups.g1.reviews.test-reviewer]
scope = "working-tree"
specs = ["docs/specs/tuner.md"]
[steps.api-stub]
role = "backend"
shape = "stub"
paths = ["hqptuner/backend/api.py"]
form = { step = "Approved api stub" }
[steps.red]
role = "test-writer"
shape = "red"
paths = ["tests/test_api.py"]
views = ["hqptuner/backend/api.py"]
expect_code = 1
form = { behaviors = [{text = "Reads the filter", source = "hqptuner/backend/api.py"}], origin = { design = "docs/specs/tuner.md", steps = ["1"] } }
[steps.api-build]
role = "backend"
shape = "build"
paths = ["hqptuner/backend/api.py"]
form = { step = "Approved api build", red = [{ step = "red" }] }
[steps.ui-stub]
role = "frontend"
shape = "stub"
paths = ["hqptuner/frontend/app.py"]
form = { step = "Approved ui stub" }
[steps.init-stub]
role = "backend"
shape = "stub"
paths = ["hqptuner/__init__.py"]
form = { step = "Approved package stub" }
"""


def _hqptuner(tmp_path: Path, queue: str) -> tuple[Config, Path]:
    conf = tmp_path / "foreman.toml"
    conf.write_text(HQPTUNER_CONFIG)
    queue_file = tmp_path / "hqptuner.toml"
    queue_file.write_text(queue)
    return config.load(conf), queue_file


def test_hqptuner_queue_loads_through_config(tmp_path: Path) -> None:
    cfg, queue_file = _hqptuner(tmp_path, HQPTUNER_QUEUE)
    (group,) = cli._load(queue_file, cfg)
    assert [(s.role, s.paths) for s in group.steps if s.role != "test-writer"] == [
        ("backend", ["hqptuner/backend/api.py"]),
        ("backend", ["hqptuner/backend/api.py"]),
        ("frontend", ["hqptuner/frontend/app.py"]),
        ("backend", ["hqptuner/__init__.py"]),
    ]
    assert group.reviews["test-reviewer"].specs == ("docs/specs/tuner.md",)


def test_hqptuner_step_outside_its_role_writes_is_refused(tmp_path: Path) -> None:
    queue = HQPTUNER_QUEUE.replace(
        'paths = ["hqptuner/frontend/app.py"]', 'paths = ["hqptuner/backend/app.py"]'
    )
    cfg, queue_file = _hqptuner(tmp_path, queue)
    with pytest.raises(ConfigError, match="ui-stub"):
        cli._load(queue_file, cfg)


def test_hqptuner_single_file_write_does_not_cover_siblings(tmp_path: Path) -> None:
    queue = HQPTUNER_QUEUE.replace(
        'paths = ["hqptuner/__init__.py"]', 'paths = ["hqptuner/other.py"]'
    )
    cfg, queue_file = _hqptuner(tmp_path, queue)
    with pytest.raises(ConfigError, match="init-stub"):
        cli._load(queue_file, cfg)


def test_hqptuner_spec_outside_spec_dir_is_refused(tmp_path: Path) -> None:
    queue = HQPTUNER_QUEUE.replace(
        'specs = ["docs/specs/tuner.md"]',
        'specs = ["docs/superpowers/specs/tuner.md"]',
    )
    cfg, queue_file = _hqptuner(tmp_path, queue)
    with pytest.raises(ConfigError, match="specs"):
        cli._load(queue_file, cfg)


def test_stats_without_foreman_toml_reads_the_default_history(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = tmp_path / "home"
    folder = home / ".claude" / "projects" / "-p" / "session-1" / "subagents"
    folder.mkdir(parents=True)
    (folder / "agent-a1.meta.json").write_text(
        json.dumps({"agentType": "spec-scout", "description": "x", "finished": True})
    )
    usage = {"input_tokens": 300, "output_tokens": 200}
    lines = [
        {"type": "user", "timestamp": "2026-10-18T23:00:00.000Z"},
        {
            "type": "assistant",
            "timestamp": "2026-10-18T23:00:00.000Z",
            "message": {"id": "m1", "role": "assistant", "usage": usage},
        },
        {"type": "user", "timestamp": "2026-10-18T23:01:40.000Z"},
    ]
    (folder / "agent-a1.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(bare)
    code = main(["stats"])
    out, err = capsys.readouterr()
    assert code == 0, err
    assert out.strip() == (
        "spec-scout: tokens: 500.0 seconds: 100.0 overruns: 0 refusals: 0"
    )


def _stops(cfg: Config) -> list[dict]:
    path = cfg.state_dir / "events.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines() if x]
    return [r for r in rows if r["kind"] == "stop"]


def test_stop_writes_one_stop_event_with_its_kind(
    conf_file, steps_file, origin, fake_backend, monkeypatch, green_cfg
) -> None:
    monkeypatch.setattr(
        backends, "get", lambda cfg: fake_backend(outputs=["REJECTED: no"])
    )
    assert main(["--config", str(conf_file), "run", str(steps_file)]) == 6
    [stop] = _stops(green_cfg)
    assert stop["stop_kind"] == "no change"
    assert stop["exit_code"] == 6


def test_success_writes_no_stop_event(
    conf_file, steps_file, origin, fake_backend, monkeypatch, green_cfg
) -> None:
    monkeypatch.setattr(
        backends, "get", lambda cfg: fake_backend([{"gauntlet/core/a.py": "1"}])
    )
    assert main(["--config", str(conf_file), "run", str(steps_file)]) == 0
    assert _stops(green_cfg) == []


def test_sigterm_exit_writes_killed_stop(
    conf_file, steps_file, monkeypatch, green_cfg
) -> None:
    def killed(*_a, **_k):
        raise SystemExit(128 + signal.SIGTERM)

    monkeypatch.setattr(loop, "run_section", killed)
    with pytest.raises(SystemExit):
        main(["--config", str(conf_file), "run", str(steps_file)])
    [stop] = _stops(green_cfg)
    assert stop["stop_kind"] == "killed" and stop["exit_code"] == 143


def test_other_exception_writes_crashed_stop(
    conf_file, steps_file, monkeypatch, green_cfg
) -> None:
    def crash(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(loop, "run_section", crash)
    with pytest.raises(RuntimeError):
        main(["--config", str(conf_file), "run", str(steps_file)])
    [stop] = _stops(green_cfg)
    assert stop["stop_kind"] == "crashed" and "boom" in stop["text"]


def test_keyboard_interrupt_writes_crashed_stop(
    conf_file, steps_file, monkeypatch, green_cfg
) -> None:
    def interrupted(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(loop, "run_section", interrupted)
    with pytest.raises(KeyboardInterrupt):
        main(["--config", str(conf_file), "run", str(steps_file)])
    [stop] = _stops(green_cfg)
    assert stop["stop_kind"] == "crashed" and "KeyboardInterrupt" in stop["text"]
