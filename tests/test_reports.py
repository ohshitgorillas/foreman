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


RED_REPORT = """SHAPE red
BRIEF red
behavior: Preserves safe data.
view: /scratch/errors.pyi
VIEWS /scratch/errors.pyi
BEHAVIOR 2 | tests/test_errors.py::TestErrors::test_safe[two words] | docs: docs/design.md §1 | breaks when: data leaks
BEHAVIOR 1 | tests/test_errors.py::test_hierarchy | docs: docs/design.md §2 | breaks when: base changes
RUN red | tests/test_errors.py::test_old | passed | "PASSED tests/test_errors.py::test_old"
RUN red | tests/test_errors.py::test_hierarchy | not-implemented | "FAILED tests/test_errors.py::test_hierarchy - NotImplementedError"
RUN red | tests/test_errors.py::TestErrors::test_safe[two words] | assertion | "FAILED tests/test_errors.py::TestErrors::test_safe[two words] - AssertionError: expected safe | got unsafe"
RUN rerun | tests/test_errors.py::TestErrors::test_safe[two words] | assertion | "FAILED tests/test_errors.py::TestErrors::test_safe[two words] - AssertionError: expected safe | got unsafe"
RUN rerun | tests/test_errors.py::test_old | passed | "PASSED tests/test_errors.py::test_old"
RUN rerun | tests/test_errors.py::test_hierarchy | not-implemented | "FAILED tests/test_errors.py::test_hierarchy - NotImplementedError"
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
        "assertion",
        "FAILED tests/test_errors.py::TestErrors::test_safe[two words] - AssertionError: expected safe | got unsafe",
    )
    assert evidence[0].rerun == evidence[0].red
    assert evidence[1].red.outcome == "not-implemented"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("SHAPE red", "SHAPE repair"),
        ("END TESTS", ""),
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
            'RUN rerun | tests/test_errors.py::test_hierarchy | not-implemented | "FAILED tests/test_errors.py::test_hierarchy - NotImplementedError"',
            'RUN rerun | tests/test_errors.py::test_hierarchy | not-implemented | "different"',
        ),
        (
            'RUN rerun | tests/test_errors.py::test_hierarchy | not-implemented | "FAILED tests/test_errors.py::test_hierarchy - NotImplementedError"\n',
            "",
        ),
    ],
)
def test_red_refuses_partial_or_invalid_evidence(old: str, new: str) -> None:
    with pytest.raises(ConfigError, match="red report"):
        reports.red(RED_REPORT.replace(old, new))


# The STATUS shape of the g7-5 test-writer report: one record per changed path.
G7_5_STATUS = """STATUS ?? gauntlet/seams/process.py
STATUS ?? tests/support/process.py
STATUS ?? tests/test_support_process.py
"""


def test_red_accepts_one_status_record_per_changed_path() -> None:
    text = RED_REPORT.replace("STATUS M tests/test_errors.py\n", G7_5_STATUS)
    assert reports.red(text) == reports.red(RED_REPORT)


@pytest.mark.parametrize(
    "status",
    ["", "STATUS\n", "STATUS \n", "STATUS M tests/test_errors.py\nSTATUS\n"],
)
def test_red_refuses_missing_or_bare_status(status: str) -> None:
    text = RED_REPORT.replace("STATUS M tests/test_errors.py\n", status)
    with pytest.raises(ConfigError, match="STATUS"):
        reports.red(text)


def test_red_accepts_one_views_record_per_view_read() -> None:
    text = RED_REPORT.replace(
        "VIEWS /scratch/errors.pyi", "VIEWS /scratch/errors.pyi\nVIEWS /scratch/b.pyi"
    )
    assert reports.red(text) == reports.red(RED_REPORT)


@pytest.mark.parametrize(
    "views",
    ["VIEWS \n", "VIEWS\n", "VIEWS /scratch/errors.pyi\nVIEWS \n"],
)
def test_red_refuses_bare_views(views: str) -> None:
    text = RED_REPORT.replace("VIEWS /scratch/errors.pyi\n", views)
    with pytest.raises(ConfigError, match="VIEWS"):
        reports.red(text)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("SHAPE red", "SHAPE red\nSHAPE red"),
        ("BLOCKED none", "BLOCKED none\nBLOCKED none"),
        ('GATE uv run gate | "ok" | not mine: none', 'GATE a | "ok"\nGATE b | "ok"'),
        ("CHANGED 23", "CHANGED 23\nCHANGED 24"),
    ],
)
def test_red_keeps_other_envelope_records_exactly_one(old: str, new: str) -> None:
    text = RED_REPORT.replace("STATUS M tests/test_errors.py\n", G7_5_STATUS).replace(
        old, new
    )
    with pytest.raises(ConfigError, match="exactly one"):
        reports.red(text)


NOT_RAISED_ID = "tests/test_core_proxy.py::test_x[/v1/messages?beta=%]"
NOT_RAISED_LINE = f"FAILED {NOT_RAISED_ID} - Failed: DID NOT RAISE LaunchError"


def _not_raised_report(line: str) -> str:
    return RED_REPORT.replace(
        "BLOCKED none",
        f"BEHAVIOR 3 | {NOT_RAISED_ID} | docs: docs/design.md §3 | breaks when: x\n"
        f'RUN red | {NOT_RAISED_ID} | not-raised | "{line}"\n'
        f'RUN rerun | {NOT_RAISED_ID} | not-raised | "{line}"\n'
        "BLOCKED none",
    )


def test_red_accepts_not_raised_evidence() -> None:
    evidence = reports.red(_not_raised_report(NOT_RAISED_LINE))
    assert [(e.test_id, e.behavior) for e in evidence][-1] == (NOT_RAISED_ID, 3)
    assert evidence[-1].red == reports.RedRun("not-raised", NOT_RAISED_LINE)
    assert evidence[-1].rerun == evidence[-1].red


@pytest.mark.parametrize(
    "line",
    [
        f"FAILED {NOT_RAISED_ID} - AssertionError",
        f"FAILED {NOT_RAISED_ID} - Failed: Timeout",
        f"FAILED {NOT_RAISED_ID} - Failed: DID NOT RAISE",
        f"FAILED {NOT_RAISED_ID}",
        "FAILED tests/test_core_proxy.py::test_y - Failed: DID NOT RAISE LaunchError",
    ],
)
def test_red_refuses_not_raised_without_its_reason(line: str) -> None:
    with pytest.raises(ConfigError, match="does not print that reason"):
        reports.red(_not_raised_report(line))


@pytest.mark.parametrize(
    ("label", "line"),
    [
        ("not-implemented", "FAILED tests/test_errors.py::test_hierarchy - KeyError"),
        ("not-implemented", "FAILED tests/test_errors.py::test_hierarchy"),
        (
            "not-implemented",
            "FAILED tests/test_errors.py::test_hierarchy - NotImplementedErrorX",
        ),
        (
            "not-implemented",
            "FAILED tests/test_errors.py::test_other - NotImplementedError",
        ),
        ("assertion", "FAILED tests/test_errors.py::test_hierarchy - TypeError: x"),
        ("assertion", "FAILED tests/test_errors.py::test_hierarchy - Assertion"),
        (
            "assertion",
            "FAILED tests/test_errors.py::test_hierarchy - Failed: DID NOT RAISE E",
        ),
    ],
)
def test_red_refuses_label_without_its_reason(label: str, line: str) -> None:
    old = 'not-implemented | "FAILED tests/test_errors.py::test_hierarchy - NotImplementedError"'
    text = RED_REPORT.replace(old, f'{label} | "{line}"')
    with pytest.raises(ConfigError, match="does not print that reason"):
        reports.red(text)


def test_red_accepts_reasons_with_and_without_message() -> None:
    old = 'not-implemented | "FAILED tests/test_errors.py::test_hierarchy - NotImplementedError"'
    line = "FAILED tests/test_errors.py::test_hierarchy - NotImplementedError: later"
    evidence = reports.red(RED_REPORT.replace(old, f'not-implemented | "{line}"'))
    assert evidence[1].red == reports.RedRun("not-implemented", line)
    line = "FAILED tests/test_errors.py::test_hierarchy - AssertionError"
    evidence = reports.red(RED_REPORT.replace(old, f'assertion | "{line}"'))
    assert evidence[1].red == reports.RedRun("assertion", line)


def test_red_refuses_run_with_unread_reason_and_no_outcome() -> None:
    old = 'not-implemented | "FAILED tests/test_errors.py::test_hierarchy - NotImplementedError"'
    text = RED_REPORT.replace(old, '"FAILED tests/test_errors.py::test_hierarchy"')
    with pytest.raises(ConfigError, match="malformed RUN"):
        reports.red(text)


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


def test_red_accepts_block_surrounded_by_prose_and_fence() -> None:
    text = (
        "Done: I added 2 red tests. Nothing is committed.\n\n```\n"
        + RED_REPORT
        + "\n```\n\nThings the lead should check:\n- the second view path\n"
    )
    assert reports.red(text) == reports.red(RED_REPORT)


def test_red_refuses_two_blocks() -> None:
    with pytest.raises(ConfigError, match="red report"):
        reports.red(RED_REPORT + "\nnote\n" + RED_REPORT)


@pytest.mark.parametrize(
    "text", ["", "prose only", "SHAPE red\nBRIEF red", "END TESTS\nSHAPE red"]
)
def test_red_refuses_no_block(text: str) -> None:
    with pytest.raises(ConfigError, match="red report"):
        reports.red(text)


HIER = "tests/test_errors.py::test_hierarchy"
HIER_LINE = f"FAILED {HIER} - NotImplementedError"
HIER_RED = f'RUN red | {HIER} | not-implemented | "{HIER_LINE}"'


@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize("outcome", ["passed", "assertion", "collection", ""])
def test_red_accepts_before_and_after_records_without_counting_them(
    phase: str, outcome: str
) -> None:
    extra = f'RUN {phase} | {HIER} | {outcome} | "{HIER_LINE}"'
    text = RED_REPORT.replace("BLOCKED none", extra + "\nBLOCKED none")
    assert reports.red(text) == reports.red(RED_REPORT)


@pytest.mark.parametrize("phase", ["red", "rerun"])
@pytest.mark.parametrize("gap", [" ", "  "])
def test_red_accepts_left_out_outcome_but_never_as_evidence(
    phase: str, gap: str
) -> None:
    old = next(
        line
        for line in RED_REPORT.splitlines()
        if line.startswith(f"RUN {phase} | " + HIER)
    )
    left_out = f'RUN {phase} | {HIER} |{gap}| "{HIER_LINE}"'
    with pytest.raises(ConfigError, match="missing or unstable") as info:
        reports.red(RED_REPORT.replace(old, left_out))
    assert HIER in str(info.value)


def test_red_before_record_cannot_stand_in_for_red_evidence() -> None:
    text = RED_REPORT.replace(f"RUN red | {HIER}", f"RUN before | {HIER}")
    with pytest.raises(ConfigError, match="missing or unstable"):
        reports.red(text)


def test_red_refuses_other_phases() -> None:
    record = f'RUN during | {HIER} | not-implemented | "{HIER_LINE}"'
    text = RED_REPORT.replace("BLOCKED none", record + "\nBLOCKED none")
    with pytest.raises(ConfigError, match="malformed RUN") as info:
        reports.red(text)
    assert record in str(info.value)


@pytest.mark.parametrize(
    ("record", "rule"),
    [
        ("BEHAVIOR 3 | tests/test_x.py::test_x | docs: d", "malformed BEHAVIOR"),
        (
            "BEHAVIOR 0 | tests/test_x.py::test_x | docs: d | breaks when: b",
            "malformed BEHAVIOR",
        ),
        ("BEHAVIOR 3 | not-a-test | docs: d | breaks when: b", "test id"),
        (f'RUN red | {HIER} | "{HIER_LINE}"', "malformed RUN"),
        (f'RUN red | {HIER} | exploded | "{HIER_LINE}"', "unknown RUN outcome"),
        (f'RUN before | {HIER} | exploded | "{HIER_LINE}"', "unknown RUN outcome"),
        ('RUN red | not-a-test | assertion | "x"', "test id"),
        (f"RUN red | {HIER} | assertion | unquoted", "malformed RUN"),
    ],
)
def test_red_rejections_name_the_record_and_the_rule(record: str, rule: str) -> None:
    text = RED_REPORT.replace("BLOCKED none", record + "\nBLOCKED none")
    with pytest.raises(ConfigError, match=rule) as info:
        reports.red(text)
    assert record in str(info.value)


@pytest.mark.parametrize("prefix", ["BEHAVIOR 1", "RUN red", "RUN rerun", "RUN before"])
def test_red_duplicate_rejection_names_the_record(prefix: str) -> None:
    base = RED_REPORT.replace(
        "BLOCKED none", f'RUN before | {HIER} | passed | "PASSED {HIER}"\nBLOCKED none'
    )
    record = next(line for line in base.splitlines() if line.startswith(prefix))
    with pytest.raises(ConfigError, match="duplicate") as info:
        reports.red(base.replace(record, record + "\n" + record))
    assert record in str(info.value)
