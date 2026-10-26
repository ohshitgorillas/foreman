import sys
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import make_step

from foreman import forms, run, verify
from foreman.config import Config
from foreman.errors import RulingNeeded
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


def test_check_step_empty_check_is_none(
    green_cfg: Config, tmp_path: Path, fake_run
) -> None:
    calls, _ = fake_run
    assert verify.check_step(green_cfg, _step([]), tmp_path) is None
    assert calls == []


def test_check_step_green_on_expected_code(
    green_cfg: Config, tmp_path: Path, fake_run
) -> None:
    calls, _ = fake_run
    v = verify.check_step(green_cfg, _step(["true"]), tmp_path)
    assert v is not None and v.green
    assert calls == [(["true"], tmp_path)]


def test_check_step_red_on_other_code(
    green_cfg: Config, tmp_path: Path, fake_run
) -> None:
    _, codes = fake_run
    codes["pytest"] = 1
    v = verify.check_step(green_cfg, _step(["pytest"]), tmp_path)
    assert v is not None and not v.green
    assert v.result.code == 1


def test_check_step_expect_code_nonzero_is_green(
    green_cfg: Config, tmp_path: Path, fake_run
) -> None:
    _, codes = fake_run
    codes["pytest"] = 1
    v = verify.check_step(green_cfg, _step(["pytest"], expect_code=1), tmp_path)
    assert v is not None and v.green


def test_check_step_zero_is_red_when_nonzero_expected(
    green_cfg: Config, tmp_path: Path, fake_run
) -> None:
    v = verify.check_step(green_cfg, _step(["true"], expect_code=1), tmp_path)
    assert v is not None and not v.green


def _repair_source(tmp_path: Path) -> tuple[Path, tuple[str, ...]]:
    path = tmp_path / "tests/test_repair.py"
    path.parent.mkdir()
    path.write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('value', [1, 2], ids=['one space', 'two'])\n"
        "class TestValue:\n"
        "    def test_value(self, value):\n"
        "        actual = value\n"
        "        assert actual == value, 'same'\n"
    )
    return path, ("tests/test_repair.py::TestValue::test_value[one space]",)


def test_repair_keeps_exact_parameter_selector_and_allows_machinery_changes(
    green_cfg: Config,
    tmp_path: Path,
) -> None:
    path, ids = _repair_source(tmp_path)
    original = verify.capture_repair(green_cfg, tmp_path, ids)
    path.write_text(
        path.read_text()
        .replace("actual = value", "actual = int(value)")
        .replace(
            "assert actual == value, 'same'",
            "assert actual == value, 'same' # explanation",
        )
    )
    assert original.red_ids == ids
    assert verify.validate_repair(green_cfg, original, tmp_path) is None


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("def test_value", "def test_renamed"),
        ("['one space', 'two']", "['one space', 'different']"),
        ("[1, 2]", "[1, 2, 3]"),
    ],
)
def test_repair_rejects_changed_collected_membership(
    green_cfg: Config, tmp_path: Path, before: str, after: str
) -> None:
    path, ids = _repair_source(tmp_path)
    original = verify.capture_repair(green_cfg, tmp_path, ids)
    path.write_text(path.read_text().replace(before, after))
    with pytest.raises(RulingNeeded):
        verify.validate_repair(green_cfg, original, tmp_path)


@pytest.mark.parametrize(
    "replacement", ["assert actual != value, 'same'", "pass", "assert True"]
)
def test_repair_rejects_changed_or_removed_assertions(
    green_cfg: Config, tmp_path: Path, replacement: str
) -> None:
    path, ids = _repair_source(tmp_path)
    original = verify.capture_repair(green_cfg, tmp_path, ids)
    path.write_text(
        path.read_text().replace("assert actual == value, 'same'", replacement)
    )
    with pytest.raises(RulingNeeded, match="assertion"):
        verify.validate_repair(green_cfg, original, tmp_path)


def test_repair_capture_requires_every_original_selector(
    green_cfg: Config, tmp_path: Path
) -> None:
    _repair_source(tmp_path)
    with pytest.raises(RulingNeeded, match="selector"):
        verify.capture_repair(green_cfg, tmp_path, ("tests/test_repair.py::missing",))


def test_repair_fails_closed_when_source_disappears(
    green_cfg: Config, tmp_path: Path
) -> None:
    path, ids = _repair_source(tmp_path)
    original = verify.capture_repair(green_cfg, tmp_path, ids)
    path.unlink()
    with pytest.raises(RulingNeeded):
        verify.validate_repair(green_cfg, original, tmp_path)


def test_repair_capture_rejects_empty_or_duplicate_evidence(
    green_cfg: Config, tmp_path: Path
) -> None:
    _, ids = _repair_source(tmp_path)
    for invalid in ((), ids + ids, ("../test_escape.py::test_escape",)):
        with pytest.raises(RulingNeeded):
            verify.capture_repair(green_cfg, tmp_path, invalid)


@pytest.mark.parametrize(
    ("assertion", "changed"),
    [
        ("pytest.raises(ValueError)", "pytest.raises(TypeError)"),
        ("pytest.warns(UserWarning)", "pytest.warns(RuntimeWarning)"),
        ("case.assertEqual(1, 1)", "case.assertEqual(1, 2)"),
    ],
)
def test_repair_preserves_assertion_api_calls(
    green_cfg: Config, tmp_path: Path, assertion: str, changed: str
) -> None:
    path, ids = _repair_source(tmp_path)
    path.write_text(path.read_text() + f"\ndef helper(case):\n    {assertion}\n")
    original = verify.capture_repair(green_cfg, tmp_path, ids)
    path.write_text(path.read_text().replace(assertion, changed))
    with pytest.raises(RulingNeeded, match="assertion"):
        verify.validate_repair(green_cfg, original, tmp_path)


_RED_SOURCE = """
import pytest


def test_assertion():
    assert 1 == 2


def test_stub():
    raise NotImplementedError


def test_not_raised():
    with pytest.raises(ValueError):
        pass


def test_passes():
    assert True


def test_error():
    raise KeyError("boom")


@pytest.mark.parametrize("n", [1])
def test_param(n):
    assert n == 2
"""


def _red_cfg(green_cfg: Config) -> Config:
    return replace(green_cfg, python_cmd=[sys.executable])


def test_red_reports_each_outcome_from_pytest(
    green_cfg: Config, tmp_path: Path
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(_RED_SOURCE)
    names = (
        "test_assertion",
        "test_stub",
        "test_not_raised",
        "test_passes",
        "test_error",
        "test_param[1]",
        "test_gone",
    )
    ids = tuple(f"tests/test_x.py::{n}" for n in names) + ("tests/nofile.py::test_a",)
    runs = verify.red(_red_cfg(green_cfg), tmp_path, ids)
    assert [r.test_id for r in runs] == list(ids)
    assert [r.outcome for r in runs] == [
        "assertion",
        "not-implemented",
        "not-raised",
        "passed",
        "error",
        "assertion",
        "missing",
        "missing",
    ]
    assert "1 == 2" in runs[0].summary
    assert "DID NOT RAISE" in runs[2].summary
    assert "KeyError" in runs[4].summary


def test_red_collection_error_is_error(green_cfg: Config, tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_bad.py").write_text("def broken(:\n")
    ids = ("tests/test_bad.py::test_a",)
    runs = verify.red(_red_cfg(green_cfg), tmp_path, ids)
    assert [r.outcome for r in runs] == ["error"]


def test_red_timeout_is_error(green_cfg: Config, tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_slow.py").write_text(
        "import time\n\n\ndef test_a():\n    time.sleep(30)\n"
    )
    cfg = replace(_red_cfg(green_cfg), check_seconds=2)
    runs = verify.red(cfg, tmp_path, ("tests/test_slow.py::test_a",))
    assert [r.outcome for r in runs] == ["error"]


def _timeouts(monkeypatch: pytest.MonkeyPatch) -> list[int | None]:
    seen: list[int | None] = []

    def fake(cmd, cwd, timeout=None, env=None):
        seen.append(timeout)
        return run.Result(cmd, 0, "", "", 0.0)

    monkeypatch.setattr(run, "run", fake)
    return seen


def test_verify_runs_both_commands_under_check_seconds(
    green_cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _timeouts(monkeypatch)
    verify.verify(replace(green_cfg, check_seconds=7), tmp_path)
    assert seen == [7, 7]


def test_check_step_runs_under_check_seconds(
    green_cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _timeouts(monkeypatch)
    verify.check_step(replace(green_cfg, check_seconds=7), _step(["true"]), tmp_path)
    assert seen == [7]


def test_repair_collection_runs_under_check_seconds(
    green_cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, ids = _repair_source(tmp_path)
    cfg = replace(green_cfg, check_seconds=7)
    original = verify.capture_repair(cfg, tmp_path, ids)
    seen = _timeouts(monkeypatch)
    with pytest.raises(RulingNeeded):
        verify.capture_repair(cfg, tmp_path, ids)
    with pytest.raises(RulingNeeded):
        verify.validate_repair(cfg, original, tmp_path)
    assert seen == [7, 7]
