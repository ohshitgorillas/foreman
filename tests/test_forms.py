"""Structural form validation before a queue can dispatch any text."""

from dataclasses import FrozenInstanceError

import pytest

from foreman.errors import ConfigError
from foreman.forms import (
    Behavior,
    DocumentChange,
    Finding,
    GateSource,
    Moved,
    Origin,
    Port,
    Red,
    Repair,
    ReviewerSource,
    parse,
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
