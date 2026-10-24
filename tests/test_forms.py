"""Structural form validation before a queue can dispatch any text."""

from dataclasses import FrozenInstanceError

import pytest

from foreman.errors import ConfigError
from foreman.forms import (
    Behavior,
    Build,
    BuilderFinding,
    DiffReview,
    DocumentChange,
    Finding,
    GateSource,
    Moved,
    Origin,
    Port,
    Red,
    RedSelection,
    Repair,
    ReviewerSource,
    Rewrite,
    Stub,
    parse,
)
from foreman.forms import (
    TestReview as ReviewTests,
)

DESIGN = "docs/superpowers/specs/feature.md"
ORIGIN = {"design": DESIGN, "steps": ["2"]}
BEHAVIOR = {"text": "Errors preserve safe data.", "source": "gauntlet/core/errors.py"}


@pytest.mark.parametrize(
    "shape,fields,expected",
    [
        ("red", {"behaviors": [BEHAVIOR]}, Red((Behavior(**BEHAVIOR),))),
        ("port", {"origin": ORIGIN, "sources": []}, Port(Origin(DESIGN, ("2",)), ())),
        (
            "repair",
            {
                "sources": [],
                "failures": ["FAILED tests/test_x.py::test_x - AssertionError"],
            },
            Repair((), ("FAILED tests/test_x.py::test_x - AssertionError",)),
        ),
        (
            "moved",
            {
                "sources": [],
                "changes": [
                    {
                        "anchor": "docs/contract.md:12",
                        "before": "Old rule.",
                        "after": "New rule.",
                    }
                ],
            },
            Moved(
                (DocumentChange("docs/contract.md:12", "Old rule.", "New rule."),), ()
            ),
        ),
        (
            "finding",
            {
                "source": {"kind": "gate", "run": "g1-check"},
                "lines": ["tests/test_x.py:2: F821 Undefined name"],
            },
            Finding(
                GateSource("g1-check"), ("tests/test_x.py:2: F821 Undefined name",)
            ),
        ),
    ],
)
def test_shapes_parse_to_distinct_frozen_values(shape, fields, expected):
    form = parse("test-writer", shape, fields, where="queue.toml: steps.2.form")
    assert type(form) is type(expected)
    assert form == expected
    assert (form.role, form.shape) == ("test-writer", shape)
    with pytest.raises(FrozenInstanceError):
        form.shape = "other"


def test_red_preserves_order_parameterized_ids_and_origin_without_aliasing():
    fields = {
        "origin": {"design": DESIGN, "steps": ["2", "3a"]},
        "behaviors": [
            {
                **BEHAVIOR,
                "replaces": "tests/test_x.py::TestErrors::test_x[value with spaces::x]",
            },
            {"text": "Second behavior.", "source": "gauntlet/core/errors.py"},
        ],
    }
    form = parse("test-writer", "red", fields, where="q: steps.2.form")
    fields["origin"]["steps"].append("4")
    fields["behaviors"][0]["text"] = "Changed"
    assert form.origin.steps == ("2", "3a")
    assert [b.text for b in form.behaviors] == [BEHAVIOR["text"], "Second behavior."]
    assert form.behaviors[0].replaces.endswith("[value with spaces::x]")
    with pytest.raises(FrozenInstanceError):
        form.behaviors[0].text = "Changed"


def test_reviewer_source_keeps_provenance_separate_from_lines():
    line = "weak tests/test_x.py:3 | accepts wrong data | passes on wrong code: unsafe | docs: none | fix: assert safety"
    form = parse(
        "test-writer",
        "finding",
        {
            "source": {
                "kind": "reviewer",
                "role": "test-reviewer",
                "round": "g1-review-test-reviewer",
            },
            "lines": [line],
        },
        where="q.form",
    )
    assert form.source == ReviewerSource("test-reviewer", "g1-review-test-reviewer")
    assert form.lines == (line,)


@pytest.mark.parametrize(
    "role,shape",
    [
        ("unknown", "red"),
        ("test-writer", "build"),
        ("diff-reviewer", "red"),
        ([], "red"),
        ("test-writer", []),
    ],
)
def test_registry_rejects_unsupported_pairs(role, shape):
    with pytest.raises(ConfigError, match="q.form: unsupported role/shape"):
        parse(role, shape, {}, where="q.form")


@pytest.mark.parametrize(
    "shape,fields,field",
    [
        ("red", None, "form"),
        ("red", {}, "behaviors"),
        ("red", {"behaviors": []}, "behaviors"),
        ("red", {"behaviors": "text"}, "behaviors"),
        ("red", {"behaviors": [BEHAVIOR], "sources": []}, "sources"),
        ("red", {"behaviors": [{**BEHAVIOR, "case": "invented"}]}, "case"),
        ("red", {"behaviors": [{"text": "Behavior."}]}, "source"),
        ("port", {"origin": {**ORIGIN, "steps": ["2", "3"]}, "sources": []}, "steps"),
        ("port", {"origin": {**ORIGIN, "steps": ["2", "2"]}, "sources": []}, "steps"),
        ("port", {"origin": {**ORIGIN, "steps": []}, "sources": []}, "steps"),
        ("port", {"origin": {**ORIGIN, "extra": "x"}, "sources": []}, "extra"),
        ("port", {"origin": ORIGIN, "sources": ["pkg/a.py", "pkg/a.py"]}, "sources"),
        ("repair", {"sources": [], "failures": []}, "failures"),
        ("repair", {"sources": [], "failures": [True]}, "failures"),
        ("moved", {"sources": [], "changes": []}, "changes"),
        ("finding", {"source": {"kind": "other"}, "lines": ["x"]}, "kind"),
        (
            "finding",
            {"source": {"kind": "gate", "run": "r", "round": "s"}, "lines": ["x"]},
            "round",
        ),
        (
            "finding",
            {
                "source": {"kind": "reviewer", "role": "diff-reviewer", "round": "r"},
                "lines": ["x"],
            },
            "role",
        ),
        (
            "finding",
            {"source": [{"kind": "gate", "run": "r"}], "lines": ["x"]},
            "source",
        ),
        ("finding", {"source": {"kind": "gate", "run": "r"}, "lines": []}, "lines"),
    ],
)
def test_bad_fields_report_exact_location(shape, fields, field):
    with pytest.raises(ConfigError) as exc:
        parse("test-writer", shape, fields, where="queue.toml: steps.2.form")
    assert "queue.toml: steps.2.form" in str(exc.value)
    assert field in str(exc.value)


@pytest.mark.parametrize(
    "text", ["", " leading", "trailing ", "a\nb", "a\rb", "a\0b", False, 3]
)
def test_line_values_rejected_without_cleanup(text):
    with pytest.raises(ConfigError, match=r"behaviors\[0\].text"):
        parse(
            "test-writer",
            "red",
            {"behaviors": [{**BEHAVIOR, "text": text}]},
            where="q.form",
        )


@pytest.mark.parametrize(
    "source",
    [
        "/pkg/a.py",
        "../a.py",
        "pkg/../a.py",
        "./a.py",
        "pkg//a.py",
        "pkg/a.py/",
        "pkg/a.pyi",
        "pkg/a b.py",
        "pkg/a\tb.py",
        "pkg/a\x1bb.py",
        "C:/a.py",
        "pkg\\a.py",
        ".py",
    ],
)
def test_source_paths_are_validated_without_normalization(source):
    with pytest.raises(ConfigError, match=r"behaviors\[0\].source"):
        parse(
            "test-writer",
            "red",
            {"behaviors": [{**BEHAVIOR, "source": source}]},
            where="q.form",
        )


@pytest.mark.parametrize(
    "design",
    [
        "docs/feature.md",
        "docs/superpowers/specs/../feature.md",
        "docs/superpowers/specs/x.txt",
        "/docs/superpowers/specs/x.md",
    ],
)
def test_origin_requires_spec_path(design):
    with pytest.raises(ConfigError, match="origin.design"):
        parse(
            "test-writer",
            "port",
            {"origin": {**ORIGIN, "design": design}, "sources": []},
            where="q.form",
        )


@pytest.mark.parametrize(
    "selector",
    [
        "tests/test_x.py",
        "tests/test_x.py::",
        "tests/test_x.py::A::",
        "tests/test_x.py::test x",
        "other/test_x.py::test_x",
        "tests/../test_x.py::test_x",
        "tests/test_x.pyi::test_x",
        "tests/test_x.py::test_x[broken",
    ],
)
def test_replacement_requires_exact_test_selector(selector):
    with pytest.raises(ConfigError, match="replaces"):
        parse(
            "test-writer",
            "red",
            {"behaviors": [{**BEHAVIOR, "replaces": selector}]},
            where="q.form",
        )


def test_replacement_cannot_be_repeated_in_run():
    behavior = {**BEHAVIOR, "replaces": "tests/test_x.py::test_x"}
    with pytest.raises(ConfigError, match="duplicate"):
        parse("test-writer", "red", {"behaviors": [behavior, behavior]}, where="q.form")


@pytest.mark.parametrize(
    "key,value",
    [
        ("anchor", "docs/x.md:0"),
        ("anchor", "docs/x.md:True"),
        ("anchor", "../x.md:2"),
        ("before", 'embedded "quote"'),
        ("after", "embedded | separator"),
    ],
)
def test_moved_rejects_ambiguous_quoting_and_bad_anchors(key, value):
    change = {"anchor": "docs/x.md:2", "before": "Old.", "after": "New.", key: value}
    with pytest.raises(ConfigError) as exc:
        parse(
            "test-writer", "moved", {"changes": [change], "sources": []}, where="q.form"
        )
    assert f"changes[0].{key}" in str(exc.value)


@pytest.mark.parametrize("role", ["core-builder", "seam-builder"])
def test_builder_shapes_preserve_blocks_selections_and_exclusions(role):
    layer = "core" if role == "core-builder" else "seams"
    excluded = f"gauntlet/{layer}/other.py"
    step = "Approved Order row.\n\n    def interface(): ..."
    selections = [
        {"step": "red-2", "files": ["tests/test_b.py", "tests/test_a.py"]},
        {"step": "red-1"},
    ]
    fields = {"step": step, "red": selections, "exclude": [excluded]}
    form = parse(role, "build", fields, where="q.form")
    selections[0]["files"].append("tests/test_c.py")
    assert form == Build(
        role,
        step,
        (
            RedSelection("red-2", ("tests/test_b.py", "tests/test_a.py")),
            RedSelection("red-1"),
        ),
        (excluded,),
    )
    assert parse(role, "stub", {"step": step}, where="q.form") == Stub(role, step)
    line = f"REWRITE gauntlet/{layer}/x.py:3 | delete equivalent branch"
    assert parse(role, "rewrite", {"line": line}, where="q.form") == Rewrite(role, line)
    with pytest.raises(FrozenInstanceError):
        form.step = "changed"
    with pytest.raises(FrozenInstanceError):
        form.red[0].step = "changed"


@pytest.mark.parametrize(
    "category",
    [
        "shortcut",
        "gate-gaming",
        "misplaced",
        "hand-rolled",
        "drift",
        "shell-text parsing",
        "leftover",
    ],
)
@pytest.mark.parametrize(
    "role,anchor",
    [
        ("core-builder", "gauntlet/core/x.py:12"),
        ("core-builder", "gauntlet/__init__.py:1"),
        ("seam-builder", "gauntlet/seams/x.py:12"),
    ],
)
def test_builder_reviewer_findings_keep_primary_anchor_and_verbatim_evidence(
    category, role, anchor
):
    line = f'{category} | {anchor} | duplicates "gauntlet/core/shared.py:2" | fails when: data arrives'
    source = {"kind": "reviewer", "role": "diff-reviewer", "round": "future-round"}
    form = parse(role, "finding", {"source": source, "lines": [line]}, where="q.form")
    assert form == BuilderFinding(
        role, ReviewerSource("diff-reviewer", "future-round"), (line,)
    )


def test_gate_batch_keeps_order_and_single_run_provenance():
    lines = [
        "gauntlet/core/b.py:4: F821 undefined name",
        "gauntlet/core/a.py:8: F401 unused import",
    ]
    form = parse(
        "core-builder",
        "finding",
        {
            "source": {"kind": "gate", "run": "future-check"},
            "lines": lines,
        },
        where="q.form",
    )
    lines.reverse()
    assert form.source == GateSource("future-check")
    assert form.lines[0].startswith("gauntlet/core/b.py:4")


@pytest.mark.parametrize(
    "shape,fields,field",
    [
        ("build", {"step": "row", "red": []}, "red"),
        ("build", {"step": "row", "red": ["2"]}, "red[0]"),
        ("build", {"step": "row", "red": [{"step": True}]}, "step"),
        ("build", {"step": "row", "red": [{"step": "2", "files": []}]}, "files"),
        (
            "build",
            {"step": "row", "red": [{"step": "2", "files": ["tests/x.py::test_x"]}]},
            "files",
        ),
        (
            "build",
            {
                "step": "row",
                "red": [{"step": "2", "files": ["tests/x.py", "tests/x.py"]}],
            },
            "files",
        ),
        ("build", {"step": "row", "red": [{"step": "2"}, {"step": "2"}]}, "red"),
        ("build", {"step": "row", "red": [{"step": "2", "report": "x"}]}, "report"),
        ("stub", {"step": "row", "red": []}, "red"),
        ("rewrite", {"line": "REWRITE x", "red": []}, "red"),
        ("rewrite", {"line": "rewrite x"}, "line"),
        ("rewrite", {"line": "REWRITE"}, "line"),
        ("rewrite", {"line": "REWRITE x\nextra"}, "line"),
        ("stub", {"step": "row", "exclude": []}, "exclude"),
        ("stub", {"step": "row", "exclude": ["tests/x.py"]}, "exclude"),
        ("stub", {"step": "row", "exclude": ["gauntlet/seams/x.py"]}, "exclude"),
        ("stub", {"step": "row", "exclude": ["gauntlet/core/../x.py"]}, "exclude"),
        (
            "stub",
            {"step": "row", "exclude": ["gauntlet/core/x.py", "gauntlet/core/x.py"]},
            "exclude",
        ),
        ("stub", {"step": "row", "suffix": "extra"}, "suffix"),
    ],
)
def test_builder_rejects_wrong_fields_and_selections(shape, fields, field):
    with pytest.raises(ConfigError) as exc:
        parse("core-builder", shape, fields, where="queue.toml: steps.3.form")
    assert "queue.toml: steps.3.form" in str(exc.value)
    assert field in str(exc.value)


@pytest.mark.parametrize(
    "block", ["", "\nrow", "row\n", "row\rtext", "row\0text", False]
)
def test_approved_blocks_reject_invalid_bytes_without_cleanup(block):
    with pytest.raises(ConfigError, match=r"q.form.step"):
        parse("core-builder", "stub", {"step": block}, where="q.form")


@pytest.mark.parametrize(
    "line",
    [
        "shortcut | gauntlet/core/x.py:1 | defect | fails when: scenario | fix: edit",
        "unknown | gauntlet/core/x.py:1 | defect | fails when: scenario",
        "shortcut | gauntlet/seams/x.py:1 | defect | fails when: scenario",
        "shortcut | tests/test_x.py:1 | defect | fails when: scenario",
        "shortcut | gauntlet/core/x.py:0 | defect | fails when: scenario",
        "shortcut | gauntlet/core/x.py:1 |  | fails when: scenario",
        "shortcut | gauntlet/core/x.py:1 | defect | fails when:",
        "shortcut | gauntlet/core/x.py:1 | defect | scenario",
    ],
)
def test_builder_reviewer_findings_fail_closed(line):
    with pytest.raises(ConfigError, match=r"q.form.lines\[0\]"):
        parse(
            "core-builder",
            "finding",
            {
                "source": {"kind": "reviewer", "role": "diff-reviewer", "round": "r"},
                "lines": [line],
            },
            where="q.form",
        )


@pytest.mark.parametrize(
    "source",
    [
        [{"kind": "gate", "run": "a"}, {"kind": "gate", "run": "b"}],
        {"kind": "gate", "run": ["a", "b"]},
        {"kind": "gate", "run": "a", "round": "b"},
        {"kind": "reviewer", "role": "diff-reviewer", "round": ["a", "b"]},
        {"kind": "reviewer", "role": "diff-reviewer", "round": "a", "run": "b"},
        {"kind": "reviewer", "role": "test-reviewer", "round": "a"},
    ],
)
def test_builder_batch_cannot_mix_sources_or_review_rounds(source):
    with pytest.raises(ConfigError, match="q.form.source"):
        parse(
            "core-builder",
            "finding",
            {"source": source, "lines": ["x"]},
            where="q.form",
        )


@pytest.mark.parametrize(
    "role,expected",
    [
        ("diff-reviewer", DiffReview("working-tree")),
        ("test-reviewer", ReviewTests("working-tree")),
    ],
)
def test_review_contract_has_explicit_scope(role, expected):
    form = parse(role, "review", {"scope": "working-tree"}, where="q.review")
    assert form == expected
    assert form.role == role
    with pytest.raises(FrozenInstanceError):
        form.scope = "range"


@pytest.mark.parametrize(
    "revision_range", ["HEAD~2..HEAD", "v1.2...topic.v3", "refs/heads/main..abc123"]
)
def test_review_ranges_and_ordered_specs_are_preserved(revision_range):
    specs = [DESIGN, "docs/superpowers/specs/earlier.md"]
    form = parse(
        "test-reviewer",
        "review",
        {
            "scope": "range",
            "range": revision_range,
            "specs": specs,
        },
        where="q.review",
    )
    specs.reverse()
    assert form == ReviewTests(
        "range", revision_range, (DESIGN, "docs/superpowers/specs/earlier.md")
    )
    assert parse(
        "diff-reviewer",
        "review",
        {"scope": "range", "range": revision_range},
        where="q.review",
    ) == DiffReview("range", revision_range)


@pytest.mark.parametrize(
    "revision_range",
    [
        "",
        "A",
        "A.B",
        "A....B",
        "A..B..C",
        "..B",
        "A...",
        "-A..B",
        "A..-B",
        "A ..B",
        "A..B\nextra",
        "A..B\x1b",
        True,
    ],
)
def test_invalid_review_ranges_are_rejected(revision_range):
    with pytest.raises(ConfigError, match="q.review.range"):
        parse(
            "diff-reviewer",
            "review",
            {"scope": "range", "range": revision_range},
            where="q.review",
        )


@pytest.mark.parametrize(
    "role,fields,field",
    [
        ("diff-reviewer", {}, "scope"),
        ("diff-reviewer", {"scope": "other"}, "scope"),
        ("diff-reviewer", {"scope": "range"}, "range"),
        ("diff-reviewer", {"scope": "working-tree", "range": "A..B"}, "range"),
        ("diff-reviewer", {"scope": "working-tree", "specs": []}, "specs"),
        ("diff-reviewer", {"scope": "working-tree", "spec": False}, "spec"),
        (
            "test-reviewer",
            {"scope": "working-tree", "specs": [DESIGN, DESIGN]},
            "specs",
        ),
        (
            "test-reviewer",
            {"scope": "working-tree", "specs": ["docs/other.md"]},
            "specs",
        ),
        ("test-reviewer", {"scope": "working-tree", "specs": False}, "specs"),
    ],
)
def test_review_contract_rejects_extra_or_incompatible_fields(role, fields, field):
    with pytest.raises(ConfigError) as exc:
        parse(role, "review", fields, where="q.review")
    assert f"q.review.{field}" in str(exc.value)
