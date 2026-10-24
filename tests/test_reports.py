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


def test_parse_test_wrong_block() -> None:
    text = "TEST WRONG 2\n  tests/t.py::a | why | spec:1\n  tests/t.py::b | why | spec:2\nTESTS\n"
    rep = reports.parse(text)
    assert rep.test_wrong == [
        "tests/t.py::a | why | spec:1",
        "tests/t.py::b | why | spec:2",
    ]
    assert rep.findings == []


def test_parse_headers_with_colon_tail() -> None:
    text = "TEST WRONG 1: x\n  t.py::a | w | s\n\nFINDINGS 1: gate\n  c | a.py:1 | b\n"
    rep = reports.parse(text)
    assert rep.test_wrong == ["t.py::a | w | s"]
    assert rep.findings == ["c | a.py:1 | b"]


def test_parse_test_wrong_absent() -> None:
    assert reports.parse("TESTS\n  ok\n").test_wrong == []


def test_block_stops_at_unindented() -> None:
    assert reports._block(["TEST WRONG 1", "  a", "b", "  c"], "TEST WRONG") == ["a"]


def test_route_tests_owned_and_unowned() -> None:
    lines = [
        "tests/test_x.py::one | why | s:1",
        "foreman/b.py::two | why | s:2",
        "other/z.py::three | why | s:3",
        "no separator",
    ]
    owned, unowned = reports.route_tests(lines, GROUP)
    assert owned == {"s1": [lines[0]], "s2": [lines[1]]}
    assert unowned == [lines[2], lines[3]]


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


def test_failed_ids_reads_summary_once_in_order() -> None:
    text = (
        "..F\n"
        "FAILED tests/t.py::test_a - AssertionError: x\n"
        "ERROR tests/u.py\n"
        "FAILED tests/t.py::test_a - again\n"
        "FAILED tests/t.py::test_b[1-2]\n"
        "1 failed, 1 error in 0.1s\n"
    )
    assert reports.failed_ids(text) == [
        "tests/t.py::test_a",
        "tests/u.py",
        "tests/t.py::test_b[1-2]",
    ]
    assert reports.failed_ids("3 passed\nFAILED\n") == []
