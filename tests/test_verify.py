from dataclasses import replace
from pathlib import Path

import pytest
from conftest import make_step

from foreman import forms, verify
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
    tmp_path: Path,
) -> None:
    path, ids = _repair_source(tmp_path)
    original = verify.capture_repair(tmp_path, ids)
    path.write_text(
        path.read_text()
        .replace("actual = value", "actual = int(value)")
        .replace(
            "assert actual == value, 'same'",
            "assert actual == value, 'same' # explanation",
        )
    )
    assert original.red_ids == ids
    assert verify.validate_repair(original, tmp_path) is None


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("def test_value", "def test_renamed"),
        ("['one space', 'two']", "['one space', 'different']"),
        ("[1, 2]", "[1, 2, 3]"),
    ],
)
def test_repair_rejects_changed_collected_membership(
    tmp_path: Path, before: str, after: str
) -> None:
    path, ids = _repair_source(tmp_path)
    original = verify.capture_repair(tmp_path, ids)
    path.write_text(path.read_text().replace(before, after))
    with pytest.raises(RulingNeeded):
        verify.validate_repair(original, tmp_path)


@pytest.mark.parametrize(
    "replacement", ["assert actual != value, 'same'", "pass", "assert True"]
)
def test_repair_rejects_changed_or_removed_assertions(
    tmp_path: Path, replacement: str
) -> None:
    path, ids = _repair_source(tmp_path)
    original = verify.capture_repair(tmp_path, ids)
    path.write_text(
        path.read_text().replace("assert actual == value, 'same'", replacement)
    )
    with pytest.raises(RulingNeeded, match="assertion"):
        verify.validate_repair(original, tmp_path)


def test_repair_capture_requires_every_original_selector(tmp_path: Path) -> None:
    _repair_source(tmp_path)
    with pytest.raises(RulingNeeded, match="selector"):
        verify.capture_repair(tmp_path, ("tests/test_repair.py::missing",))


def test_repair_fails_closed_when_source_disappears(tmp_path: Path) -> None:
    path, ids = _repair_source(tmp_path)
    original = verify.capture_repair(tmp_path, ids)
    path.unlink()
    with pytest.raises(RulingNeeded):
        verify.validate_repair(original, tmp_path)


def test_repair_capture_rejects_empty_or_duplicate_evidence(tmp_path: Path) -> None:
    _, ids = _repair_source(tmp_path)
    for invalid in ((), ids + ids, ("../test_escape.py::test_escape",)):
        with pytest.raises(RulingNeeded):
            verify.capture_repair(tmp_path, invalid)


@pytest.mark.parametrize(
    ("assertion", "changed"),
    [
        ("pytest.raises(ValueError)", "pytest.raises(TypeError)"),
        ("pytest.warns(UserWarning)", "pytest.warns(RuntimeWarning)"),
        ("case.assertEqual(1, 1)", "case.assertEqual(1, 2)"),
    ],
)
def test_repair_preserves_assertion_api_calls(
    tmp_path: Path, assertion: str, changed: str
) -> None:
    path, ids = _repair_source(tmp_path)
    path.write_text(path.read_text() + f"\ndef helper(case):\n    {assertion}\n")
    original = verify.capture_repair(tmp_path, ids)
    path.write_text(path.read_text().replace(assertion, changed))
    with pytest.raises(RulingNeeded, match="assertion"):
        verify.validate_repair(original, tmp_path)
