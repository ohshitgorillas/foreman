"""Tests for foreman/reports.py."""

from pathlib import Path

import pytest
from conftest import make_group, make_step, verdict_text

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


def _report(*behaviors: str, extra: str = "") -> str:
    return "\n".join(
        ["prose", "SHAPE red", "BRIEF red", "behavior: quoted", "VIEWS none"]
        + list(behaviors)
        + [extra] * bool(extra)
        + ["DIFF none", "COMMANDS none", "END TESTS"]
    )


B1 = "BEHAVIOR 2 | tests/test_a.py::test_one[x y] | docs: d §1 | breaks when: b"
B2 = "BEHAVIOR 1 | tests/test_a.py::TestA::test_two | docs: d §2 | breaks when: c"


def test_red_returns_claims_in_report_order() -> None:
    claims = reports.red(_report(B1, B2))
    assert claims == reports.RedClaims(
        (
            (2, "tests/test_a.py::test_one[x y]"),
            (1, "tests/test_a.py::TestA::test_two"),
        ),
        (),
    )


def test_red_notes_are_kept_verbatim_and_never_reject() -> None:
    notes = ["LEFT 1 | x", "UNTESTABLE 2 | y", "CONTRADICTS 3 | z"]
    claims = reports.red(_report(B1, extra="\n".join(notes)))
    assert claims.notes == tuple(notes)
    assert claims.ids == ((2, "tests/test_a.py::test_one[x y]"),)


def test_red_reads_no_run_or_status_records() -> None:
    extra = 'RUN red | tests/test_a.py::test_one[x y] | | "junk"\nBLOCKED later'
    assert reports.red(_report(B1, extra=extra)) == reports.red(_report(B1))


@pytest.mark.parametrize("marker", ["IGNORED", "CHOSEN"])
def test_red_skips_ignored_and_chosen_lines(marker: str) -> None:
    extra = f"{marker} REJECTED: STEERING\n{marker} BEHAVIOR bad\n{marker} LEFT 1"
    assert reports.red(_report(B1, extra=extra)) == reports.red(_report(B1))


def test_red_ignores_text_outside_the_block_and_quoted_brief() -> None:
    text = "REJECTED: STEERING\n" + _report(B1) + "\nOVERLOADED: x"
    assert reports.red(text) == reports.red(_report(B1))
    quoted = _report(B1).replace("behavior: quoted", "BEHAVIOR 9 | bad")
    assert reports.red(quoted) == reports.red(_report(B1))


@pytest.mark.parametrize("marker", ["REJECTED: STEERING", "OVERLOADED: 8 files"])
def test_red_refuses_a_refusal_marker_in_the_block(marker: str) -> None:
    with pytest.raises(ConfigError, match="red report"):
        reports.red(_report(B1, extra=marker))


@pytest.mark.parametrize(
    "text", ["", "prose only", "SHAPE red\nBRIEF red", "END TESTS\nSHAPE red"]
)
def test_red_refuses_no_block(text: str) -> None:
    with pytest.raises(ConfigError, match="red report"):
        reports.red(text)


def test_red_refuses_two_blocks() -> None:
    with pytest.raises(ConfigError, match="red report"):
        reports.red(_report(B1) + "\n" + _report(B2))


@pytest.mark.parametrize(
    "record",
    [
        "BEHAVIOR 3 | tests/test_x.py::test_x | docs: d",
        "BEHAVIOR 0 | tests/test_x.py::test_x | docs: d | breaks when: b",
        "BEHAVIOR 3 | not-a-test | docs: d | breaks when: b",
    ],
)
def test_red_refuses_malformed_behavior_naming_it(record: str) -> None:
    with pytest.raises(ConfigError, match="red report") as info:
        reports.red(_report(record))
    assert record in str(info.value)


def test_red_refuses_a_duplicate_test_id() -> None:
    again = B1.replace("BEHAVIOR 2", "BEHAVIOR 5")
    with pytest.raises(ConfigError, match="duplicate") as info:
        reports.red(_report(B1, again))
    assert again in str(info.value)


def test_parse_and_verdict_skip_ignored_and_chosen_lines() -> None:
    text = "IGNORED REJECTED: STEERING\nCHOSEN OVERLOADED: x\nFINDINGS 1\n  a | b\n"
    assert reports.parse(text) == Report("done", ["a | b"])
    plain = verdict_text()
    noisy = "IGNORED x\n" + plain.replace("ACTION", "CHOSEN y\nACTION")
    assert reports.verdict(noisy) == reports.verdict(plain)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("foreman/a.py:12:3: E1 error", ("foreman/a.py", 12)),
        ("./tests/test_a.py:4: failure", ("tests/test_a.py", 4)),
        ("foreman/../tests/test_a.py:8: failure", ("tests/test_a.py", 8)),
        ("docs/design.md:19", ("docs/design.md", 19)),
        (
            'drift | foreman/b.py:7 | reuse "foreman/a.py:2" | fails when: x',
            ("foreman/b.py", 7),
        ),
        ("note mentions foreman/a.py:2", None),
        ("note | no anchor | foreman/a.py:2", None),
        ("drift | foreman/a.py:0 | cites foreman/b.py:3", None),
        ("foreman/a.py:-1: error", None),
        ("foreman/a.py:01: error", None),
        ("foreman/a.py:2suffix: error", None),
        ("../elsewhere/a.py:2: error", None),
        ("foreman/../../a.py:2: error", None),
    ],
)
def test_anchor_reads_only_primary_path_and_positive_line(
    tmp_path: Path, line: str, expected: tuple[str, int] | None
) -> None:
    assert reports.anchor(line, tmp_path) == expected


def test_anchor_absolute_paths_must_be_within_worktree(tmp_path: Path) -> None:
    wt = tmp_path / "worktree"
    assert reports.anchor(f"{wt}/foreman/a.py:42: E1", wt) == (
        "foreman/a.py",
        42,
    )
    assert reports.anchor(
        f"drift | {wt}/tests/test_a.py:9 | defect | fails when: x", wt
    ) == ("tests/test_a.py", 9)
    assert reports.anchor(f"{tmp_path}/elsewhere/a.py:3: E1", wt) is None
    assert reports.anchor(f"{wt}-other/a.py:3: E1", wt) is None
    assert reports.anchor(f"{wt}/../a.py:3: E1", wt) is None


def test_anchor_accepts_resolved_worktree_path(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    wt = tmp_path / "worktree"
    wt.symlink_to(target, target_is_directory=True)
    for base in (wt, wt.resolve()):
        assert reports.anchor(f"{base}/foreman/a.py:5: E1", wt) == (
            "foreman/a.py",
            5,
        )


VERDICT = verdict_text(
    "tests wrong",
    "repair",
    "red-2",
    (
        "The fixture fails before the original assertion.",
        "Preserve the original test ids and assertions.",
    ),
    (
        "tests/test_errors.py:12 | fixture accesses an absent attribute",
        "docs/superpowers/specs/errors.md:8 | the approved claim is clear",
        "/state/g-build.txt:19 | observed failure | additional detail",
    ),
    (
        "FAILED tests/test_errors.py::test_safe[two words] - AttributeError: missing | value",
        "ERROR tests/test_errors.py::test_hierarchy - fixture setup failed",
    ),
)


@pytest.mark.parametrize("final_lf", ["", "\n"])
def test_verdict_preserves_order_and_verbatim_evidence(final_lf: str) -> None:
    assert reports.verdict(VERDICT + final_lf) == reports.Verdict(
        kind="tests wrong",
        action="repair",
        step="red-2",
        reasons=(
            "The fixture fails before the original assertion.",
            "Preserve the original test ids and assertions.",
        ),
        evidence=(
            "tests/test_errors.py:12 | fixture accesses an absent attribute",
            "docs/superpowers/specs/errors.md:8 | the approved claim is clear",
            "/state/g-build.txt:19 | observed failure | additional detail",
        ),
        failures=(
            "FAILED tests/test_errors.py::test_safe[two words] - AttributeError: missing | value",
            "ERROR tests/test_errors.py::test_hierarchy - fixture setup failed",
        ),
    )


@pytest.mark.parametrize(
    ("kind", "action", "step"),
    [
        ("spec wrong", "owner", None),
        ("undetermined", "owner", None),
        ("tests wrong", "redo red", "red-2"),
        ("build wrong", "retry", "build-3"),
    ],
)
def test_verdict_decodes_each_other_permitted_transition(
    kind: str, action: str, step: str | None
) -> None:
    text = verdict_text(
        kind, action, step, ("Missing evidence.",), ("report.txt:1 | observed",)
    )
    assert reports.verdict(text) == reports.Verdict(
        kind, action, step, ("Missing evidence.",), ("report.txt:1 | observed",), ()
    )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("VERDICT tests wrong", "VERDICT build wrong"),
        ("VERDICT tests wrong", "VERDICT Tests wrong"),
        ("VERDICT tests wrong", "VERDICT unknown"),
        ("ACTION repair", "ACTION owner"),
        ("ACTION repair", "ACTION retry"),
        ("ACTION repair", "ACTION redo red"),
        ("ACTION repair", "ACTION unknown"),
        ("ACTION repair", "ACTION  repair"),
        ("STEP red-2\n", ""),
        ("STEP red-2", "STEP "),
        ("STEP red-2", "STEP red-2\nSTEP red-2"),
        ("ACTION repair", "ACTION repair\nACTION repair"),
        ("REASON The fixture fails before the original assertion.", "REASON "),
        ("REASON Preserve the original test ids and assertions.", "UNKNOWN value"),
        ("EVIDENCE tests/test_errors.py:12", "EVIDENCE ../tests/test_errors.py:12"),
        ("EVIDENCE tests/test_errors.py:12", "EVIDENCE ./tests/test_errors.py:12"),
        ("EVIDENCE tests/test_errors.py:12", "EVIDENCE tests//test_errors.py:12"),
        ("EVIDENCE tests/test_errors.py:12", "EVIDENCE tests/test errors.py:12"),
        ("EVIDENCE tests/test_errors.py:12", "EVIDENCE tests/test_errors.py:0"),
        ("EVIDENCE tests/test_errors.py:12", "EVIDENCE tests/test_errors.py:01"),
        ("EVIDENCE tests/test_errors.py:12", "EVIDENCE tests/test_errors.py:-1"),
        ("EVIDENCE tests/test_errors.py:12", "EVIDENCE tests/test_errors.py:one"),
        ("EVIDENCE tests/test_errors.py:12", "EVIDENCE tests/test_errors.py"),
        ("EVIDENCE /state/g-build.txt:19", "EVIDENCE //state/g-build.txt:19"),
        ("EVIDENCE /state/g-build.txt:19", "EVIDENCE /state/../g-build.txt:19"),
        (" | fixture accesses an absent attribute", " | "),
        (" | fixture accesses an absent attribute", " |  extra space"),
        (
            " | fixture accesses an absent attribute",
            "|fixture accesses an absent attribute",
        ),
        (
            "FAILURE ERROR tests/test_errors.py::test_hierarchy - fixture setup failed",
            "FAILURE ",
        ),
        ("END JUDGMENT", ""),
        ("END JUDGMENT", "END JUDGMENT\ntrailing prose"),
        ("END JUDGMENT", "END JUDGMENT\nEND JUDGMENT"),
    ],
)
def test_verdict_refuses_invalid_fields_and_transitions(old: str, new: str) -> None:
    with pytest.raises(ConfigError, match="verdict"):
        reports.verdict(VERDICT.replace(old, new))


@pytest.mark.parametrize("key", ["REASON", "EVIDENCE", "FAILURE"])
def test_verdict_requires_nonempty_repeated_fields(key: str) -> None:
    text = "\n".join(
        line for line in VERDICT.split("\n") if not line.startswith(key + " ")
    )
    with pytest.raises(ConfigError, match="verdict"):
        reports.verdict(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "preamble\n" + VERDICT,
        "```text\n" + VERDICT + "\n```",
        VERDICT + "\n\n",
        VERDICT + "\n" + VERDICT,
        VERDICT.replace("\n", "\r\n"),
        VERDICT.replace("original assertion.", "original\0assertion."),
        VERDICT.replace("original assertion.", "original assertion. "),
        VERDICT.replace("original assertion.", "original \ud800 assertion."),
        VERDICT.replace("\nSTEP", "\n\nSTEP"),
        "\n".join(" " + line for line in VERDICT.split("\n")),
        VERDICT.replace("ACTION repair\nSTEP red-2", "STEP red-2\nACTION repair"),
        VERDICT.replace(
            "REASON The fixture fails before the original assertion.",
            "EVIDENCE tests/test_errors.py:12 | out of order",
        ),
    ],
)
def test_verdict_refuses_non_envelopes(text: str) -> None:
    with pytest.raises(ConfigError, match="verdict"):
        reports.verdict(text)


@pytest.mark.parametrize(
    ("kind", "action"),
    [
        ("spec wrong", "owner"),
        ("undetermined", "owner"),
        ("tests wrong", "redo red"),
        ("build wrong", "retry"),
    ],
)
def test_verdict_forbids_failures_outside_repair(kind: str, action: str) -> None:
    text = verdict_text(
        kind,
        action,
        None if action == "owner" else "candidate",
        ("why",),
        ("report.txt:1 | claim",),
        ("observed",),
    )
    with pytest.raises(ConfigError, match="verdict"):
        reports.verdict(text)


@pytest.mark.parametrize("kind", ["spec wrong", "undetermined"])
def test_verdict_owner_action_forbids_target(kind: str) -> None:
    text = verdict_text(kind, "owner", "candidate", ("why",), ("report.txt:1 | claim",))
    with pytest.raises(ConfigError, match="verdict"):
        reports.verdict(text)
