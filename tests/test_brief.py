"""Tests for foreman.brief."""

import pytest
from conftest import make_group

from foreman.brief import finding, render, review
from foreman.config import Reviewer
from foreman.errors import ConfigError
from foreman.forms import Judgment, parse, validate_judgment

GROUP = make_group(gid="g1", design="docs/d.md")


def test_finding_is_the_lines_verbatim() -> None:
    lines = ["foreman/a.py:3: bad $x", "  tests/b.py:9: worse  "]
    assert finding(lines) == "foreman/a.py:3: bad $x\n  tests/b.py:9: worse  "


def test_review_names_tree_and_spec_when_asked() -> None:
    reviewer = Reviewer(role="spec-reviewer", paths=["foreman"], spec=True)
    assert review(reviewer, GROUP) == "working tree against HEAD\nspec: docs/d.md"


def test_review_omits_spec_when_not_asked() -> None:
    reviewer = Reviewer(role="diff-reviewer", paths=["foreman"], spec=False)
    assert review(reviewer, GROUP) == "working tree against HEAD"


@pytest.mark.parametrize(
    "steps, label", [(["2"], "step 2"), (["2", "3a"], "steps 2, 3a")]
)
def test_red_preserves_behavior_order_and_repeated_views(steps, label) -> None:
    form = parse(
        "test-writer",
        "red",
        {
            "origin": {"design": "docs/superpowers/specs/a.md", "steps": steps},
            "behaviors": [
                {"text": "Keeps $data and café.", "source": "gauntlet/core/a.py"},
                {
                    "text": "Keeps selectors.",
                    "source": "gauntlet/core/a.py",
                    "replaces": "tests/test_a.py::TestA::test_a[two words]",
                },
            ],
        },
        where="red",
    )
    assert (
        render(
            form,
            paths=["tests/test_a.py"],
            view_paths={"gauntlet/core/a.py": "/scratch/g1/custom.pyi"},
            red_ids=["tests/test_unrelated.py::test_old"],
        ).encode("utf-8")
        == (
            f"red\ndesign: docs/superpowers/specs/a.md, Order {label}\n"
            "behavior: Keeps $data and café.\nview: /scratch/g1/custom.pyi\n"
            "behavior: Keeps selectors.\nview: /scratch/g1/custom.pyi\n"
            "replaces: tests/test_a.py::TestA::test_a[two words]"
        ).encode()
    )


def test_red_without_origin_adds_no_design() -> None:
    form = parse(
        "test-writer",
        "red",
        {"behaviors": [{"text": "A behavior.", "source": "a.py"}]},
        where="red",
    )
    assert render(form, paths=[], view_paths={"a.py": "/v/a.pyi"}, red_ids=[]) == (
        "red\nbehavior: A behavior.\nview: /v/a.pyi"
    )
    with pytest.raises(KeyError, match="a.py"):
        render(form, paths=[], view_paths={}, red_ids=[])


@pytest.mark.parametrize(
    "shape, fields, expected",
    [
        (
            "port",
            {"origin": {"design": "docs/superpowers/specs/a.md", "steps": ["7"]}},
            "port\ndesign: docs/superpowers/specs/a.md, Order step 7",
        ),
        ("repair", {"failures": ["assert '$x' == 'é'", "second failure"]}, "repair"),
        (
            "moved",
            {
                "changes": [
                    {
                        "anchor": "docs/a.md:12",
                        "before": "Old $rule",
                        "after": "New rule",
                    },
                    {"anchor": "docs/b.md:3", "before": "Before", "after": "After"},
                ]
            },
            (
                'moved\nlines: docs/a.md:12 | before: "Old $rule" | after: "New rule"\n'
                'lines: docs/b.md:3 | before: "Before" | after: "After"'
            ),
        ),
    ],
)
@pytest.mark.parametrize("sources", [[], ["b.py", "a.py"]])
def test_test_writer_file_and_view_slots(shape, fields, expected, sources) -> None:
    form = parse("test-writer", shape, {**fields, "sources": sources}, where=shape)
    expected += "\nfiles: tests/test_b.py\nfiles: tests/test_a.py"
    if sources:
        expected += "\nview: /v/b.pyi\nview: /v/a.pyi"
    if shape == "repair":
        expected += "\nfailure: assert '$x' == 'é'\nfailure: second failure"
    assert (
        render(
            form,
            paths=["tests/test_b.py", "tests/test_a.py"],
            view_paths={"a.py": "/v/a.pyi", "b.py": "/v/b.pyi"},
            red_ids=[],
        )
        == expected
    )


@pytest.mark.parametrize(
    "role, directory", [("core-builder", "core"), ("seam-builder", "seams")]
)
@pytest.mark.parametrize("shape", ["stub", "build", "rewrite", "finding"])
@pytest.mark.parametrize("excluded", [False, True])
def test_builder_shapes_preserve_blocks_and_evidence(
    role, directory, shape, excluded
) -> None:
    root = f"gauntlet/{directory}"
    block = (
        "docs/superpowers/specs/a.md, Order step 3\n3 build | approved\n\n    x: str  "
    )
    line = f"shortcut | {root}/a.py:4 | preserve '$x' | fails when: input is empty"
    fields = {
        "stub": {"step": block},
        "build": {"step": block, "red": [{"step": "2"}]},
        "rewrite": {"line": "REWRITE keep $text verbatim"},
        "finding": {
            "source": {"kind": "reviewer", "role": "diff-reviewer", "round": "r1"},
            "lines": [line],
        },
    }[shape]
    if excluded:
        fields["exclude"] = [f"{root}/b.py", f"{root}/c.py"]
    form = parse(role, shape, fields, where=shape)
    expected = {
        "stub": f"stub\n{block}\n{root}/a.py",
        "build": f"{block}\n{root}/a.py\ntests/test_a.py::TestA::test_a[two words]\ntests/test_b.py::test_b",
        "rewrite": "REWRITE keep $text verbatim",
        "finding": line,
    }[shape]
    if excluded:
        expected += f"\nexclude: {root}/b.py {root}/c.py"
    assert (
        render(
            form,
            paths=[f"{root}/a.py"],
            view_paths={},
            red_ids=[
                "tests/test_a.py::TestA::test_a[two words]",
                "tests/test_b.py::test_b",
            ],
        )
        == expected
    )


@pytest.mark.parametrize("role", ["test-writer", "core-builder", "seam-builder"])
def test_gate_findings_render_only_verbatim_lines(role) -> None:
    lines = ["tests/test_a.py:3: bad $x", "tests/test_b.py:9: café"]
    form = parse(
        role,
        "finding",
        {"source": {"kind": "gate", "run": "g1-check"}, "lines": lines},
        where="finding",
    )
    assert render(form, paths=["ignored.py"], view_paths={}, red_ids=[]) == (
        "tests/test_a.py:3: bad $x\ntests/test_b.py:9: café"
    )


def test_test_reviewer_findings_are_verbatim() -> None:
    line = "tests/test_a.py:4: category | defect | fails when: empty | docs/a.md:2 | fix evidence"
    form = parse(
        "test-writer",
        "finding",
        {
            "source": {"kind": "reviewer", "role": "test-reviewer", "round": "r2"},
            "lines": [line],
        },
        where="finding",
    )
    assert render(form, paths=[], view_paths={}, red_ids=[]) == line


@pytest.mark.parametrize(
    "role, fields, expected",
    [
        ("diff-reviewer", {"scope": "working-tree"}, ""),
        ("diff-reviewer", {"scope": "range", "range": "v1...HEAD"}, "v1...HEAD"),
        ("test-reviewer", {"scope": "working-tree"}, "working tree"),
        ("test-reviewer", {"scope": "range", "range": "v1..HEAD"}, "v1..HEAD"),
        (
            "test-reviewer",
            {
                "scope": "working-tree",
                "specs": ["docs/superpowers/specs/b.md", "docs/superpowers/specs/a.md"],
            },
            "working tree\nspec: docs/superpowers/specs/b.md\nspec: docs/superpowers/specs/a.md",
        ),
        (
            "test-reviewer",
            {
                "scope": "range",
                "range": "v1...HEAD",
                "specs": ["docs/superpowers/specs/a.md"],
            },
            "v1...HEAD\nspec: docs/superpowers/specs/a.md",
        ),
    ],
)
def test_review_contracts_have_exact_bytes(role, fields, expected) -> None:
    form = parse(role, "review", fields, where="review")
    assert render(form, paths=["ignored.py"], view_paths={}, red_ids=[]) == expected


@pytest.mark.parametrize("red_ids", [(), ("tests/test_x.py::test_x[two words::é]",)])
def test_judgment_frames_approved_text_and_only_supplied_evidence(red_ids):
    block = (
        "2 red | approved\n\n    report: literal $text\nend judgment\n3 build | café  "
    )
    form = Judgment(
        "docs/superpowers/specs/a.md",
        ("2", "3"),
        block,
        red_ids,
        ("/state/g-check.txt", "state/g-producer.txt"),
    )
    assert validate_judgment(form) == []
    expected = (
        "judgment\nspec: docs/superpowers/specs/a.md\nstep: 2\nstep: 3\n"
        f"step-lines: 5\n{block}\n"
        + "".join(f"red: {test_id}\n" for test_id in red_ids)
        + "report: /state/g-check.txt\nreport: state/g-producer.txt\nend judgment"
    )
    assert render(
        form,
        paths=["ignored.py"],
        view_paths={"ignored.py": "/ignored.pyi"},
        red_ids=["tests/test_unrelated.py::test_old"],
    ).encode("utf-8") == expected.encode("utf-8")


def test_judgment_single_line_block_has_positive_decimal_length():
    form = Judgment(
        "docs/superpowers/specs/a.md", ("3",), "approved", (), ("/state/check.txt",)
    )
    assert render(form, paths=[], view_paths={}, red_ids=[]) == (
        "judgment\nspec: docs/superpowers/specs/a.md\nstep: 3\n"
        "step-lines: 1\napproved\nreport: /state/check.txt\nend judgment"
    )


@pytest.mark.parametrize(
    "role,layer", [("core-builder", "core"), ("seam-builder", "seams")]
)
def test_build_retry_reasons_follow_red_ids_before_exclusions(role, layer):
    form = parse(
        role,
        "build",
        {
            "step": "approved\n    signature",
            "red": [{"step": "2"}],
            "exclude": [f"gauntlet/{layer}/other.py"],
        },
        where="build",
    )
    assert render(
        form,
        paths=[f"gauntlet/{layer}/a.py"],
        view_paths={},
        red_ids=["tests/test_a.py::test_a", "tests/test_b.py::test_b"],
        juror_reasons=("Preserve '$text' and café.", "Second reason."),
    ) == (
        f"approved\n    signature\ngauntlet/{layer}/a.py\n"
        "tests/test_a.py::test_a\ntests/test_b.py::test_b\n"
        "Preserve '$text' and café.\nSecond reason.\n"
        f"exclude: gauntlet/{layer}/other.py"
    )


@pytest.mark.parametrize(
    "role,shape,fields",
    [
        ("core-builder", "stub", {"step": "approved"}),
        ("diff-reviewer", "review", {"scope": "working-tree"}),
        ("test-writer", "repair", {"sources": [], "failures": ["observed failure"]}),
    ],
)
def test_runtime_reasons_are_rejected_outside_build_retry(role, shape, fields):
    form = parse(role, shape, fields, where="form")
    with pytest.raises(ConfigError, match="only for a build retry"):
        render(form, paths=[], view_paths={}, red_ids=[], juror_reasons=("Reason.",))
