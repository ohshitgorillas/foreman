from pathlib import Path

import pytest

from foreman import backends
from foreman.cli import main
from foreman.config import Config
from foreman.run import run_ok

GROUP_FILE = (
    '[steps.s1]\nrole = "builder"\nbrief = "do it"\npaths = ["a.py"]\n'
    '[groups.g1]\nsteps = ["s1"]\n'
)


@pytest.fixture
def conf(green_cfg: Config, tmp_path: Path) -> Path:
    path = tmp_path / "foreman.toml"
    path.write_text(
        f'target_repo = "{green_cfg.target_repo}"\n'
        f'worktree_root = "{green_cfg.worktree_root}"\n'
        f'state_dir = "{green_cfg.state_dir}"\n'
        'check_cmd = ["true"]\nformat_cmd = ["true"]\nbackend = "nope"\n'
    )
    return path


@pytest.fixture
def steps_file(tmp_path: Path) -> Path:
    path = tmp_path / "m.toml"
    path.write_text(GROUP_FILE)
    return path


@pytest.fixture
def origin(green_cfg: Config, tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    run_ok(["git", "init", "-q", "--bare", str(bare)], tmp_path)
    run_ok(["git", "remote", "add", "origin", str(bare)], green_cfg.target_repo)
    return bare


def test_missing_config_exits_2(tmp_path: Path) -> None:
    assert main(["--config", str(tmp_path / "none.toml"), "status"]) == 2


def test_status_ok(conf: Path, capsys) -> None:
    assert main(["--config", str(conf), "status"]) == 0
    assert capsys.readouterr().out.strip() == "no events"


def test_overloaded_step_file_exits_3(conf: Path, tmp_path: Path) -> None:
    sf = tmp_path / "big.toml"
    sf.write_text(
        '[steps.a]\nrole = "r"\nbrief = "b"\npaths = ["1","2","3","4","5","6"]\n'
        '[groups.g]\nsteps = ["a"]\n'
    )
    assert main(["--config", str(conf), "run", str(sf)]) == 3


def test_unknown_backend_exits_2(conf: Path, steps_file: Path) -> None:
    assert main(["--config", str(conf), "run", str(steps_file)]) == 2


def test_unknown_group_exits_2(conf: Path, steps_file: Path) -> None:
    assert main(["--config", str(conf), "run", str(steps_file), "--group", "zz"]) == 2


def test_run_group_lands_and_prints_commit(
    conf, steps_file, origin, fake_backend, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(backends, "get", lambda cfg: fake_backend([{"a.py": "1"}]))
    assert main(["--config", str(conf), "run", str(steps_file), "--group", "g1"]) == 0
    commit = run_ok(["git", "rev-parse", "foreman/g1"], origin).out.strip()
    assert capsys.readouterr().out.strip() == commit


def test_run_section_lands_each_group(
    conf, steps_file, origin, fake_backend, monkeypatch
) -> None:
    monkeypatch.setattr(backends, "get", lambda cfg: fake_backend([{"a.py": "1"}]))
    assert main(["--config", str(conf), "run", str(steps_file)]) == 0
    assert run_ok(["git", "rev-parse", "foreman/g1"], origin).out.strip()


def test_stop_maps_to_its_exit_code(
    conf, steps_file, origin, fake_backend, monkeypatch
) -> None:
    monkeypatch.setattr(
        backends, "get", lambda cfg: fake_backend(outputs=["REJECTED: no"])
    )
    assert main(["--config", str(conf), "run", str(steps_file)]) == 6


def test_upheld_unreadable_file_exits_2(conf, steps_file, tmp_path: Path) -> None:
    argv = ["--config", str(conf), "upheld", str(steps_file), "g1"]
    assert main([*argv, str(tmp_path / "none.txt")]) == 2
