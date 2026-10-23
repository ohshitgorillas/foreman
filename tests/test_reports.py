"""Tests for foreman/reports.py."""

from pathlib import Path

import pytest
from conftest import make_group, make_step

from foreman import reports
from foreman.errors import ConfigError
from foreman.reports import Report
from foreman.run import Result


def result(out: str) -> Result:
    return Result(cmd=["x"], code=0, out=out, err="", seconds=0.0)


GROUP = make_group(
    make_step("s1", ["foreman/a.py", "tests/"]),
    make_step("s2", ["foreman/b.py"]),
    gid="g",
    design="d.md",
)


def test_parse_done_with_findings_unindented() -> None:
    text = "SCOPE x\nFINDINGS 2\n  cat | a.py:1 | bad | fails when: y\n  cat | b.py:2 | worse | fails when: z\nCOMMANDS\n  ls\n"
    rep = reports.parse(text)
    assert rep == Report(
        kind="done",
        findings=[
            "cat | a.py:1 | bad | fails when: y",
            "cat | b.py:2 | worse | fails when: z",
        ],
    )


def test_parse_zero_findings() -> None:
    assert reports.parse("FINDINGS 0\nEND REVIEW\n") == Report("done", [])


def test_parse_rejected_and_overloaded() -> None:
    assert reports.parse('REJECTED: STEERING\n"x"\n').kind == "rejected"
    assert reports.parse("OVERLOADED: 7 files\nsplit\n").kind == "overloaded"


def test_read_uses_out() -> None:
    assert reports.read(result("OVERLOADED: no")).kind == "overloaded"


def test_lines_verbatim() -> None:
    assert reports.lines(result("  a\nb\n")) == ["  a", "b"]


def test_route_relative_and_absolute(tmp_path: Path) -> None:
    out = [
        "foreman/a.py:3: error",
        f"{tmp_path}/foreman/b.py:9: E1",
        "tests/test_x.py:4: F",
        "plain line",
    ]
    owned, unowned = reports.route(out, tmp_path, GROUP)
    assert owned == {
        "s1": ["foreman/a.py:3: error", "tests/test_x.py:4: F"],
        "s2": [f"{tmp_path}/foreman/b.py:9: E1"],
    }
    assert unowned == []


def test_route_unowned(tmp_path: Path) -> None:
    out = ["other/z.py:1: bad", "/elsewhere/foreman/a.py:2: bad"]
    owned, unowned = reports.route(out, tmp_path, GROUP)
    assert owned == {}
    assert unowned == out


def test_upheld_maps_roles() -> None:
    kept = {
        "diff-reviewer": Report("done", ["c | a.py:1 | x", "c | b.py:2 | y"]),
        "test-reviewer": Report("done", ["c | t.py:3 | z"]),
    }
    got = reports.upheld("c | b.py:2 | y\n\nc | t.py:3 | z\n", kept)
    assert got == {
        "diff-reviewer": ["c | b.py:2 | y"],
        "test-reviewer": ["c | t.py:3 | z"],
    }


def test_upheld_unknown_line_raises() -> None:
    with pytest.raises(ConfigError):
        reports.upheld("nope", {"r": Report("done", ["yes"])})
