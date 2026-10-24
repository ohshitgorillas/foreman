from dataclasses import replace
from pathlib import Path

from conftest import make_step

from foreman import forms, verify
from foreman.config import Config
from foreman.steps import Step


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


def _step(check: list[str], expect_code: int = 0) -> Step:
    return make_step(
        "s1",
        ["tests/test_x.py"],
        role="test-writer",
        form=forms.Repair((), ("Assertion failed.",)),
        check=check,
        expect_code=expect_code,
    )


def test_check_step_empty_check_is_none(tmp_path: Path, fake_run) -> None:
    calls, _ = fake_run
    assert verify.check_step(_step([]), tmp_path) is None
    assert calls == []


def test_check_step_green_on_expected_code(tmp_path: Path, fake_run) -> None:
    calls, _ = fake_run
    v = verify.check_step(_step(["true"]), tmp_path)
    assert v is not None and v.green
    assert calls == [(["true"], tmp_path)]


def test_check_step_red_on_other_code(tmp_path: Path, fake_run) -> None:
    _, codes = fake_run
    codes["pytest"] = 1
    v = verify.check_step(_step(["pytest"]), tmp_path)
    assert v is not None and not v.green
    assert v.result.code == 1


def test_check_step_expect_code_nonzero_is_green(tmp_path: Path, fake_run) -> None:
    _, codes = fake_run
    codes["pytest"] = 1
    v = verify.check_step(_step(["pytest"], expect_code=1), tmp_path)
    assert v is not None and v.green


def test_check_step_zero_is_red_when_nonzero_expected(tmp_path: Path, fake_run) -> None:
    v = verify.check_step(_step(["true"], expect_code=1), tmp_path)
    assert v is not None and not v.green
