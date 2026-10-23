"""Tests for foreman.brief."""

from dataclasses import replace

from foreman.brief import render, render_resume
from foreman.config import Config
from foreman.run import Result
from foreman.steps import Step

STEP = Step(
    id="m1-3",
    paths=["foreman/a.py", "tests/test_a.py"],
    done_check=["make check"],
    design_ref="design.md line 10",
    depends_on=[],
    size="small",
)


def test_render_carries_step_head_and_standing_lines(cfg: Config) -> None:
    cfg = replace(cfg, standing_lines=["Run ruff format.", "Add the copyright line."])
    text = render(cfg, STEP, "abc1234", {})
    for expected in (
        "m1-3",
        "abc1234",
        "- foreman/a.py",
        "- tests/test_a.py",
        "make check",
        "design.md line 10",
        "small",
        "- Run ruff format.",
        "- Add the copyright line.",
    ):
        assert expected in text
    assert "Earlier outputs" not in text
    assert "$" not in text


def test_render_includes_earlier_outputs(cfg: Config) -> None:
    text = render(cfg, STEP, "abc", {"m1-1": "wrote foreman/x.py"})
    assert "From step m1-1:\nwrote foreman/x.py" in text


def test_render_keeps_dollar_signs_in_values(cfg: Config) -> None:
    cfg = replace(cfg, standing_lines=["cost is $5 and ${x}"])
    assert "cost is $5 and ${x}" in render(cfg, STEP, "abc", {})


def test_render_resume_quotes_exact_failure() -> None:
    failure = Result(
        cmd=["make", "check"],
        code=2,
        out="FAILED tests/test_a.py::test_x - assert 1 == 2\n",
        err="boom $HOME\n",
        seconds=1.0,
    )
    text = render_resume(STEP, failure)
    assert "m1-3" in text
    assert "make check" in text
    assert "Exit code: 2" in text
    assert failure.out in text
    assert failure.err in text
    assert "- foreman/a.py" in text


def test_render_sends_a_steps_own_brief_verbatim(cfg: Config) -> None:
    step = replace(STEP, brief="stub\nexact text\ngates/x.py")
    assert render(cfg, step, "abc", {}) == "stub\nexact text\ngates/x.py"
