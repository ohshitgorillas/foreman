from dataclasses import replace
from pathlib import Path

from foreman import verify
from foreman.config import Config


def test_green_runs_format_then_check_in_worktree(
    green_cfg: Config, tmp_path: Path, fake_run
) -> None:
    calls, _ = fake_run
    v = verify.verify(green_cfg, tmp_path)
    assert v.green
    assert calls == [(green_cfg.format_cmd, tmp_path), (green_cfg.check_cmd, tmp_path)]
    assert v.result.cmd == green_cfg.check_cmd


def test_red_check_returns_check_result(
    green_cfg: Config, tmp_path: Path, fake_run
) -> None:
    _, codes = fake_run
    codes["make"] = 2
    cfg = replace(green_cfg, check_cmd=["make", "check"])
    v = verify.verify(cfg, tmp_path)
    assert not v.green
    assert v.result.code == 2
    assert v.result.out == "out make"


def test_failed_format_is_red_and_skips_check(
    green_cfg: Config, tmp_path: Path, fake_run
) -> None:
    calls, codes = fake_run
    codes["true"] = 1
    v = verify.verify(green_cfg, tmp_path)
    assert not v.green
    assert v.result.cmd == green_cfg.format_cmd
    assert [c for c, _ in calls] == [green_cfg.format_cmd]


def test_uses_configured_commands(green_cfg: Config, tmp_path: Path, fake_run) -> None:
    calls, _ = fake_run
    custom = replace(green_cfg, format_cmd=["fmt", "-x"], check_cmd=["chk"])
    verify.verify(custom, tmp_path)
    assert [c for c, _ in calls] == [["fmt", "-x"], ["chk"]]
