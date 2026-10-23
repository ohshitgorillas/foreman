from pathlib import Path

from foreman import views
from foreman.config import Config
from foreman.steps import Step


def _step(*sources: str) -> Step:
    return Step("s", "builder", "b", ["x.py"], [], 0, list(sources))


def test_path_swaps_suffix_under_group_dir(cfg: Config) -> None:
    assert views.path(cfg, "g1", "foreman/loop.py") == (
        cfg.view_root / "g1" / "foreman" / "loop.pyi"
    )


def test_make_runs_view_cmd_with_output_dir_and_sources(
    cfg: Config, tmp_path: Path, fake_run
) -> None:
    calls, _ = fake_run
    views.make(cfg, "g1", _step("a/b.py", "a/c.py"), tmp_path)
    assert calls == [
        (["stubgen", "-o", str(cfg.view_root / "g1"), "a/b.py", "a/c.py"], tmp_path)
    ]


def test_make_green_when_code_zero_and_views_exist(
    cfg: Config, tmp_path: Path, fake_run
) -> None:
    view = views.path(cfg, "g1", "a/b.py")
    view.parent.mkdir(parents=True)
    view.write_text("")
    assert views.make(cfg, "g1", _step("a/b.py"), tmp_path).green


def test_make_red_when_a_view_is_missing(cfg: Config, tmp_path: Path, fake_run) -> None:
    v = views.make(cfg, "g1", _step("a/b.py"), tmp_path)
    assert not v.green
    assert v.result.code == 0


def test_make_red_when_command_fails(cfg: Config, tmp_path: Path, fake_run) -> None:
    _, codes = fake_run
    codes["stubgen"] = 1
    view = views.path(cfg, "g1", "a/b.py")
    view.parent.mkdir(parents=True)
    view.write_text("")
    v = views.make(cfg, "g1", _step("a/b.py"), tmp_path)
    assert not v.green
    assert v.result.code == 1


def test_clear_removes_only_the_group_dir(cfg: Config) -> None:
    for g in ("g1", "g2"):
        p = views.path(cfg, g, "a.py")
        p.parent.mkdir(parents=True)
        p.write_text("")
    views.clear(cfg, "g1")
    assert not (cfg.view_root / "g1").exists()
    assert (cfg.view_root / "g2").exists()


def test_clear_absent_group_is_quiet(cfg: Config) -> None:
    views.clear(cfg, "nope")
