from pathlib import Path

import pytest

from foreman import backends, loop
from foreman.cli import main
from foreman.errors import WrongTest
from foreman.run import run_ok

GROUP_FILE = (
    '[steps.s1]\nrole = "builder"\nbrief = "do it"\npaths = ["a.py"]\n'
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


def test_overloaded_step_file_exits_3(conf_file: Path, tmp_path: Path) -> None:
    sf = tmp_path / "big.toml"
    sf.write_text(
        '[steps.a]\nrole = "r"\nbrief = "b"\npaths = ["1","2","3","4","5","6"]\n'
        '[groups.g]\nsteps = ["a"]\n'
    )
    assert main(["--config", str(conf_file), "run", str(sf)]) == 3


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
    monkeypatch.setattr(backends, "get", lambda cfg: fake_backend([{"a.py": "1"}]))
    assert (
        main(["--config", str(conf_file), "run", str(steps_file), "--group", "g1"]) == 0
    )
    commit = run_ok(["git", "rev-parse", "foreman/g1"], origin).out.strip()
    assert capsys.readouterr().out.strip() == commit


def test_run_section_lands_each_group(
    conf_file, steps_file, origin, fake_backend, monkeypatch
) -> None:
    monkeypatch.setattr(backends, "get", lambda cfg: fake_backend([{"a.py": "1"}]))
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


@pytest.mark.parametrize("command", ["upheld", "retest"])
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
    path.write_text("fix the tests")

    def stopped(*args):
        raise WrongTest("builder stopped again")

    monkeypatch.setattr(loop, "run_retest", stopped)
    argv = ["--config", str(conf_file), "retest", str(steps_file), "g1", str(path)]
    assert main(argv) == 8
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "foreman: builder stopped again" in captured.err
