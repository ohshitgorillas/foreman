from dataclasses import replace
from pathlib import Path

import pytest

from foreman import run, verify
from foreman.config import Config
from foreman.run import Result


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch):
    """Fake run.run: records commands; codes are looked up by argv[0]."""
    calls: list[tuple[list[str], Path]] = []
    codes: dict[str, int] = {}

    def fake(cmd, cwd, timeout=None, env=None):
        calls.append((cmd, cwd))
        return Result(cmd, codes.get(cmd[0], 0), f"out {cmd[0]}", "", 0.0)

    monkeypatch.setattr(run, "run", fake)
    return calls, codes


def test_green_runs_format_then_check_in_worktree(
    cfg: Config, tmp_path: Path, seen
) -> None:
    calls, _ = seen
    v = verify.verify(cfg, tmp_path)
    assert v.green
    assert calls == [(cfg.format_cmd, tmp_path), (cfg.check_cmd, tmp_path)]
    assert v.result.cmd == cfg.check_cmd


def test_red_check_returns_check_result(cfg: Config, tmp_path: Path, seen) -> None:
    _, codes = seen
    codes["make"] = 2
    v = verify.verify(cfg, tmp_path)
    assert not v.green
    assert v.result.code == 2
    assert v.result.out == "out make"


def test_failed_format_is_red_and_skips_check(
    cfg: Config, tmp_path: Path, seen
) -> None:
    calls, codes = seen
    codes["ruff"] = 1
    v = verify.verify(cfg, tmp_path)
    assert not v.green
    assert v.result.cmd == cfg.format_cmd
    assert [c for c, _ in calls] == [cfg.format_cmd]


def test_uses_configured_commands(cfg: Config, tmp_path: Path, seen) -> None:
    calls, _ = seen
    custom = replace(cfg, format_cmd=["fmt", "-x"], check_cmd=["chk"])
    verify.verify(custom, tmp_path)
    assert [c for c, _ in calls] == [["fmt", "-x"], ["chk"]]
