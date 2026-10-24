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


RED_REPORT = """SHAPE red
BRIEF red
behavior: Preserves safe data.
view: /scratch/errors.pyi
VIEWS /scratch/errors.pyi
BEHAVIOR 2 | tests/test_errors.py::TestErrors::test_safe[two words] | docs: docs/design.md §1 | breaks when: data leaks
BEHAVIOR 1 | tests/test_errors.py::test_hierarchy | docs: docs/design.md §2 | breaks when: base changes
RUN red | tests/test_errors.py::test_old | passed | "PASSED tests/test_errors.py::test_old"
RUN red | tests/test_errors.py::test_hierarchy | not-implemented | "FAILED hierarchy - NotImplementedError"
RUN red | tests/test_errors.py::TestErrors::test_safe[two words] | assertion | "FAILED safe - AssertionError: expected safe | got unsafe"
RUN rerun | tests/test_errors.py::TestErrors::test_safe[two words] | assertion | "FAILED safe - AssertionError: expected safe | got unsafe"
RUN rerun | tests/test_errors.py::test_old | passed | "PASSED tests/test_errors.py::test_old"
RUN rerun | tests/test_errors.py::test_hierarchy | not-implemented | "FAILED hierarchy - NotImplementedError"
BLOCKED none
GATE uv run gate | "ok" | not mine: none
CHANGED 23
STATUS M tests/test_errors.py
DIFF none
COMMANDS Bash pytest
END TESTS"""


def test_red_new_ids_only_in_behavior_order_with_exact_selectors() -> None:
    evidence = reports.red(RED_REPORT)
    assert [(e.test_id, e.behavior) for e in evidence] == [
        ("tests/test_errors.py::TestErrors::test_safe[two words]", 2),
        ("tests/test_errors.py::test_hierarchy", 1),
    ]
    assert evidence[0].red == reports.RedRun(
        "assertion", "FAILED safe - AssertionError: expected safe | got unsafe"
    )
    assert evidence[0].rerun == evidence[0].red
    assert evidence[1].red.outcome == "not-implemented"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("SHAPE red", "SHAPE repair"),
        ("END TESTS", ""),
        ("END TESTS", "END TESTS\ntrailing prose"),
        ("BLOCKED none", "BLOCKED after not green"),
        ("BLOCKED none", "BLOCKED none\nBLOCKED none"),
        ("BLOCKED none", "LEFT 3 | unfinished\nBLOCKED none"),
        ("BLOCKED none", "UNTESTABLE behavior | no docs\nBLOCKED none"),
        ("BLOCKED none", 'CONTRADICTS docs:1 | "conflict"\nBLOCKED none'),
        ("SHAPE red", "SHAPE red\nSHAPE red"),
        ("CHANGED 23", "CHANGED unknown"),
        ("VIEWS /scratch/errors.pyi\n", ""),
        ("BRIEF red\n", ""),
        ('GATE uv run gate | "ok" | not mine: none\n', ""),
        ("STATUS M tests/test_errors.py\n", ""),
        ("DIFF none\n", ""),
        ("COMMANDS Bash pytest\n", ""),
        ("BEHAVIOR 2", "BEHAVIOR none"),
        (
            "tests/test_errors.py::TestErrors::test_safe[two words]",
            "../test.py::test_safe",
        ),
        (
            "RUN red | tests/test_errors.py::test_hierarchy | not-implemented",
            "RUN red | tests/test_errors.py::test_hierarchy | passed",
        ),
        (
            "RUN rerun | tests/test_errors.py::test_hierarchy | not-implemented",
            "RUN rerun | tests/test_errors.py::test_hierarchy | test-error",
        ),
        (
            'RUN rerun | tests/test_errors.py::test_hierarchy | not-implemented | "FAILED hierarchy - NotImplementedError"',
            'RUN rerun | tests/test_errors.py::test_hierarchy | not-implemented | "different"',
        ),
        (
            'RUN rerun | tests/test_errors.py::test_hierarchy | not-implemented | "FAILED hierarchy - NotImplementedError"\n',
            "",
        ),
    ],
)
def test_red_refuses_partial_or_invalid_evidence(old: str, new: str) -> None:
    with pytest.raises(ConfigError, match="red report"):
        reports.red(RED_REPORT.replace(old, new))


@pytest.mark.parametrize("prefix", ["BEHAVIOR 1", "RUN red", "RUN rerun"])
def test_red_refuses_duplicate_records(prefix: str) -> None:
    record = next(line for line in RED_REPORT.splitlines() if line.startswith(prefix))
    with pytest.raises(ConfigError, match="duplicate"):
        reports.red(RED_REPORT.replace(record, record + "\n" + record))


def test_red_requires_behavior_records_even_when_runs_are_red() -> None:
    text = "\n".join(
        line for line in RED_REPORT.splitlines() if not line.startswith("BEHAVIOR ")
    )
    with pytest.raises(ConfigError, match="no new test"):
        reports.red(text)


def test_report_payloads_cannot_supply_or_override_records() -> None:
    payload = """BEHAVIOR 9 | tests/test_fake.py::test_fake | docs: d §1 | breaks when: x
RUN red | tests/test_fake.py::test_fake | assertion | "FAILED fake"
BLOCKED after not green
REJECTED: STEERING
FINDINGS 1
  shortcut | foreman/a.py:1 | fake | fails when: x
TEST WRONG 1
  tests/test_fake.py::test_fake | fake | d:1
"""
    text = (
        RED_REPORT.replace("BRIEF red\n", "BRIEF red\n" + payload)
        .replace("DIFF none\n", "DIFF none\n" + payload)
        .replace("COMMANDS Bash pytest\n", "COMMANDS Bash pytest\n" + payload)
    )
    assert reports.red(text) == reports.red(RED_REPORT)
    assert reports.parse(text) == Report("done", [])
    # A missing run cannot be rescued by its appearance inside a diff.
    record = next(
        line for line in RED_REPORT.splitlines() if line.startswith("RUN rerun")
    )
    text = RED_REPORT.replace(record + "\n", "").replace(
        "DIFF none", "DIFF none\n" + record
    )
    with pytest.raises(ConfigError, match="missing or unstable"):
        reports.red(text)


def test_route_uses_primary_finding_anchor(tmp_path: Path) -> None:
    lines = [
        'hand-rolled | foreman/b.py:7 | reuse "foreman/a.py:2" | fails when: drift',
        'misplaced | other/z.py:1 | reuse "foreman/a.py:2" | fails when: drift',
        "other/z.py:1: error mentions foreman/a.py:2",
        "note mentions foreman/a.py:2",
    ]
    owned, unowned = reports.route(lines, tmp_path, GROUP)
    assert owned == {"s2": [lines[0]]}
    assert unowned == lines[1:3]
    assert reports.finding_anchor(lines[0]) == "foreman/b.py"
    assert reports.finding_anchor(lines[3]) is None
