import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from foreman import backends, cli, events, loop
from foreman.cli import main
from foreman.errors import WrongTest
from foreman.run import run_ok

GROUP_FILE = (
    '[steps.s1]\nrole = "core-builder"\nshape = "stub"\nform = {step = "Approved step"}\npaths = ["gauntlet/core/a.py"]\n'
    '[groups.g1]\nsteps = ["s1"]\n'
)


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


@pytest.mark.parametrize("command", ["run", "upheld", "retest"])
def test_queue_review_must_match_config_before_dispatch(
    conf_file, steps_file, tmp_path, monkeypatch, capsys, command
):
    with steps_file.open("a") as stream:
        stream.write('[groups.g1.reviews.diff-reviewer]\nscope = "working-tree"\n')
    path = tmp_path / "verdict.txt"
    path.write_text("")

    def unexpected(*args):
        pytest.fail("unconfigured reviewer reached dispatch")

    for name in ["run_section", "run_upheld", "run_retest"]:
        monkeypatch.setattr(loop, name, unexpected)
    argv = ["--config", str(conf_file), command, str(steps_file)]
    if command != "run":
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
    popens: list[tuple[list[str], dict]] = []
    handlers: dict[int, object] = {}
    monkeypatch.setattr(
        cli.subprocess, "Popen", lambda cmd, **kw: popens.append((cmd, kw))
    )
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
    ],
)
def test_arm_alert_starts_detached_watcher(alert, monkeypatch, args) -> None:
    argv(monkeypatch, "--config", "x.toml", *args)
    label, status = cli._arm_alert()
    assert label == f"Foreman {args[0]} m2"
    assert Path(status).parent == alert.tmp and Path(status).read_text() == ""
    [(cmd, kw)] = alert.popens
    assert cmd[:3] == ["/bin/sh", "-c", cli._WATCH]
    assert cmd[3:] == [
        "sh",
        str(os.getpid()),
        status,
        "foreman:m2",
        label,
        str(alert.ping),
    ]
    assert kw["start_new_session"] is True
    assert {kw[k] for k in ("stdin", "stdout", "stderr")} == {subprocess.DEVNULL}
    assert set(alert.handlers) == {signal.SIGTERM, signal.SIGHUP}
    with pytest.raises(SystemExit) as e:
        alert.handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert e.value.code == 128 + signal.SIGTERM


def armed_entry(alert, monkeypatch, fake_main) -> tuple[pytest.ExceptionInfo, str]:
    argv(monkeypatch, "run", "q/m2.toml")
    monkeypatch.setattr(cli, "main", fake_main)
    with pytest.raises(BaseException) as e:
        cli.entry()
    [(cmd, _)] = alert.popens
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
