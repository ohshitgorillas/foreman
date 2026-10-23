from pathlib import Path

import pytest

from foreman.cli import main, run_step
from foreman.config import Config
from foreman.errors import Escalated
from foreman.events import EventLog
from foreman.git import head
from foreman.run import run_ok
from foreman.steps import Step


def _step(done: str = "true") -> Step:
    return Step("s1", ["out.txt"], [done], "ref", [], "small")


@pytest.fixture
def remote(cfg: Config, tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    bare.mkdir()
    run_ok(["git", "init", "-q", "--bare"], bare)
    run_ok(["git", "remote", "add", "origin", str(bare)], cfg.target_repo)
    for k, v in (
        ("user.name", "T"),
        ("user.email", "t@example.com"),
        ("commit.gpgsign", "false"),
    ):
        run_ok(["git", "config", k, v], cfg.target_repo)
    return bare


def _green(cfg: Config) -> Config:
    from dataclasses import replace

    return replace(cfg, check_cmd=["true"], format_cmd=["true"])


def test_green_step_commits_only_its_paths(
    cfg: Config, remote: Path, fake_backend: type
) -> None:
    c = _green(cfg)
    backend = fake_backend({"out.txt": "x\n", "stray.txt": "y\n"})
    commit = run_step(c, _step(), EventLog(c), backend)
    shown = run_ok(["git", "show", "--name-only", "--format=", commit], remote).out
    assert shown.split() == ["out.txt"]
    assert not (c.worktree_root / "s1").exists()
    assert "step_done" in (c.state_dir / "events.jsonl").read_text()


def test_brief_carries_head(cfg: Config, remote: Path, fake_backend: type) -> None:
    c = _green(cfg)
    backend = fake_backend({"out.txt": "x\n"})
    run_step(c, _step(), EventLog(c), backend)
    assert head(c.target_repo)[:7] in backend.briefs[0]


def test_red_then_green_resumes_once(
    cfg: Config, remote: Path, fake_backend: type
) -> None:
    from dataclasses import replace

    c = replace(
        _green(cfg),
        check_cmd=["test", "-f", "fixed.txt"],
    )
    backend = fake_backend({"out.txt": "x\n"}, {"fixed.txt": "ok\n"})
    run_step(
        c,
        Step("s1", ["out.txt", "fixed.txt"], ["t"], "r", [], "s"),
        EventLog(c),
        backend,
    )
    assert len(backend.briefs) == 2


def test_red_twice_escalates(cfg: Config, remote: Path, fake_backend: type) -> None:
    from dataclasses import replace

    c = replace(_green(cfg), check_cmd=["false"])
    log = EventLog(c)
    with pytest.raises(Escalated):
        run_step(c, _step(), log, fake_backend({"out.txt": "x\n"}))
    assert "red_twice" in (c.state_dir / "events.jsonl").read_text()
    assert not (c.worktree_root / "s1").exists()


def _write_config(cfg: Config, path: Path) -> Path:
    path.write_text(
        f'target_repo = "{cfg.target_repo}"\n'
        f'worktree_root = "{cfg.worktree_root}"\n'
        f'state_dir = "{cfg.state_dir}"\n'
        'check_cmd = ["true"]\nformat_cmd = ["true"]\n'
        'standing_lines = []\nbackend = "nope"\n'
    )
    return path


def test_main_missing_config_exits_2(tmp_path: Path) -> None:
    assert main(["--config", str(tmp_path / "none.toml"), "status"]) == 2


def test_main_status_ok(cfg: Config, tmp_path: Path, capsys) -> None:
    conf = _write_config(cfg, tmp_path / "foreman.toml")
    assert main(["--config", str(conf), "status"]) == 0
    assert capsys.readouterr().out.strip() == "no events"


def test_main_overloaded_step_file_exits_3(cfg: Config, tmp_path: Path) -> None:
    conf = _write_config(cfg, tmp_path / "foreman.toml")
    sf = tmp_path / "m.toml"
    sf.write_text(
        '[steps.a]\npaths = ["1","2","3","4","5","6"]\ndone_check = ["x"]\n'
        'design_ref = "r"\ndepends_on = []\nsize = "s"\nagent = "a"\n'
    )
    assert main(["--config", str(conf), "run", str(sf)]) == 3


def test_main_unknown_backend_exits_2(cfg: Config, tmp_path: Path) -> None:
    conf = _write_config(cfg, tmp_path / "foreman.toml")
    sf = tmp_path / "m.toml"
    sf.write_text(
        '[steps.a]\npaths = ["1"]\ndone_check = ["x"]\n'
        'design_ref = "r"\ndepends_on = []\nsize = "s"\nagent = "a"\n'
    )
    assert main(["--config", str(conf), "run", str(sf)]) == 2
