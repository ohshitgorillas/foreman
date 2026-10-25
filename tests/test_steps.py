from dataclasses import replace
from pathlib import Path

import pytest

from foreman import forms
from foreman.errors import ConfigError
from foreman.steps import Group, Step, lint, load, next_ready, owner


def mk(id: str, **kw: object) -> Step:
    base = Step(
        id,
        "core-builder",
        forms.Stub("core-builder", "Approved step"),
        ["gauntlet/core/a.py"],
        ["make", "check"],
    )
    return replace(base, **kw)


def grp(id: str, steps: list[Step] | None = None, **kw: object) -> Group:
    return replace(
        Group(id, steps if steps is not None else [mk(id + "-s")], "", []), **kw
    )


GOOD = """
[groups.g1]
steps = ["stub", "red", "build"]
design = "docs/superpowers/specs/example.md"
[groups.g2]
steps = ["last"]
after = ["g1"]
[steps.stub]
role = "core-builder"
shape = "stub"
paths = ["gauntlet/core/a.py"]
check = ["make", "check"]
form = { step = "Approved stub" }
[steps.red]
role = "test-writer"
shape = "red"
paths = ["tests/test_a.py", "tests/test_b.py"]
views = ["gauntlet/core/a.py"]
expect_code = 1
form = { behaviors = [{text = "Preserves data", source = "gauntlet/core/a.py"}], origin = { design = "docs/superpowers/specs/example.md", steps = ["2"] } }
[steps.build]
role = "core-builder"
shape = "build"
paths = ["gauntlet/core/a.py"]
form = { step = "Approved build", red = [{ step = "red", files = ["tests/test_a.py"] }] }
[steps.last]
role = "seam-builder"
shape = "stub"
paths = ["gauntlet/seams/a.py"]
form = { step = "Approved seam stub" }
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "steps.toml"
    p.write_text(text)
    return p


def test_load_groups_and_steps(tmp_path: Path) -> None:
    groups = load(write(tmp_path, GOOD))
    assert [g.id for g in groups] == ["g1", "g2"]
    g1, g2 = groups
    assert [s.id for s in g1.steps] == ["stub", "red", "build"]
    assert g1.design == "docs/superpowers/specs/example.md" and g1.after == []
    assert g2.after == ["g1"] and g2.design == ""
    stub, red, build = g1.steps
    assert stub.check == ["make", "check"] and stub.expect_code == 0
    assert red.check == [] and red.expect_code == 1
    assert red.views == ["gauntlet/core/a.py"] and stub.views == []
    assert stub.form == forms.Stub("core-builder", "Approved stub")
    assert build.form.red == (forms.RedSelection("red", ("tests/test_a.py",)),)
    assert g1.reviews == {}  # no specs inherited from design metadata
    assert lint(groups) == []


@pytest.mark.parametrize(
    "old,new,msg",
    [
        ('shape = "stub"\n', "", "missing keys: shape"),
        ('form = { step = "Approved stub" }', "", "missing keys: form"),
        ('role = "seam-builder"', "role = 3", "role"),
        ("expect_code = 1", "expect_code = true", "expect_code"),
        ('steps = ["last"]', 'steps = ["last", "ghost"]', "unknown steps: ghost"),
        ('steps = ["last"]', "steps = []\nbogus = 1", "unknown keys: bogus"),
        ('steps = ["last"]', "steps = []", "steps: must be nonempty"),
        ('after = ["g1"]', 'after = ["ghost"]', "after unknown"),
        ('after = ["g1"]', 'after = ["g2"]', "after itself"),
        ('steps = ["last"]', 'steps = ["last", "last"]', "duplicate step"),
        ('steps = ["last"]', 'steps = ["last", "stub"]', "more than one group"),
        ('views = ["gauntlet/core/a.py"]', 'views = "a.py"', "views must be a list"),
        ('views = ["gauntlet/core/a.py"]', "views = []", "distinct view sources"),
        (
            'views = ["gauntlet/core/a.py"]',
            'views = ["gauntlet/core/b.py"]',
            "distinct view sources",
        ),
        ('paths = ["gauntlet/seams/a.py"]', "paths = []", "no paths"),
        (
            'paths = ["gauntlet/seams/a.py"]',
            'paths = ["gauntlet/core/a.py"]',
            "outside seam-builder",
        ),
        (
            'paths = ["gauntlet/seams/a.py"]',
            'paths = ["gauntlet/seams/../a.py"]',
            "without traversal",
        ),
        (
            'paths = ["gauntlet/seams/a.py"]',
            'paths = ["gauntlet/seams/a.py", "gauntlet/seams/a.py"]',
            "duplicate values",
        ),
        (
            'check = ["make", "check"]',
            'check = [["make"], ["check"]]',
            "check must be a list",
        ),
        ('check = ["make", "check"]', 'check = [""]', "expected one argv"),
        (
            'step = "Approved seam stub"',
            'step = "Approved seam stub", red = []',
            "unknown field",
        ),
        ('role = "seam-builder"', 'role = "gatesmith"', "unsupported role/shape"),
        ('steps = ["2"]', 'steps = ["2", "2"]', "duplicate values"),
        (
            ', origin = { design = "docs/superpowers/specs/example.md", steps = ["2"] }',
            "",
            "origin: required",
        ),
    ],
)
def test_load_rejects(tmp_path: Path, old: str, new: str, msg: str) -> None:
    path = write(tmp_path, GOOD.replace(old, new))
    with pytest.raises(ConfigError, match=msg) as caught:
        load(path)
    assert str(path) in str(caught.value)


@pytest.mark.parametrize("value", ['"old"', '""', "3"])
@pytest.mark.parametrize("typed", [True, False])
def test_legacy_brief_always_has_migration_error(
    tmp_path: Path, value: str, typed: bool
) -> None:
    text = GOOD.replace("[steps.last]", f"[steps.last]\nbrief = {value}")
    if not typed:
        text = text.replace(
            'shape = "stub"\npaths = ["gauntlet/seams/a.py"]\nform = { step = "Approved seam stub" }',
            'paths = ["gauntlet/seams/a.py"]',
        )
    with pytest.raises(
        ConfigError,
        match=r"steps.last.brief: free-text briefs removed; use role, shape and form",
    ):
        load(write(tmp_path, text))


@pytest.mark.parametrize(
    "selection,msg",
    [
        ('{ step = "build" }', "earlier test-writer red"),
        ('{ step = "last" }', "earlier test-writer red"),
        ('{ step = "stub" }', "earlier test-writer red"),
        ('{ step = "ghost" }', "earlier test-writer red"),
        ('{ step = "red", files = [] }', "nonempty"),
        ('{ step = "red", files = ["tests/other.py"] }', "subset of producer paths"),
        (
            '{ step = "red", files = ["tests/test_a.py", "tests/test_a.py"] }',
            "duplicate",
        ),
        ('{ step = "red" }, { step = "red" }', "duplicate"),
    ],
)
def test_red_selection_rejected(tmp_path: Path, selection: str, msg: str) -> None:
    text = GOOD.replace('{ step = "red", files = ["tests/test_a.py"] }', selection)
    with pytest.raises(ConfigError, match=msg):
        load(write(tmp_path, text))


def test_red_cannot_cross_groups(tmp_path: Path) -> None:
    text = GOOD.replace('"stub", "red", "build"', '"stub", "red"').replace(
        'steps = ["last"]', 'steps = ["build", "last"]'
    )
    with pytest.raises(ConfigError, match="earlier test-writer red"):
        load(write(tmp_path, text))


def test_unfiltered_and_multiple_red_producers(tmp_path: Path) -> None:
    text = GOOD.replace('"stub", "red", "build"', '"stub", "red", "red2", "build"')
    red = GOOD.split("[steps.red]")[1].split("[steps.build]")[0]
    text += "\n[steps.red2]" + red
    text = text.replace(
        '{ step = "red", files = ["tests/test_a.py"] }',
        '{ step = "red2" }, { step = "red" }',
    )
    build = load(write(tmp_path, text))[0].steps[-1]
    assert build.form.red == (forms.RedSelection("red2"), forms.RedSelection("red"))


@pytest.mark.parametrize(
    "review,msg",
    [
        (
            '[groups.g1.reviews.diff-reviewer]\nscope = "working-tree"\nspecs = []',
            "unknown field",
        ),
        (
            '[groups.g1.reviews.test-reviewer]\nscope = "range"\nrange = "a..b"',
            "require working-tree",
        ),
        (
            '[groups.g1.reviews.spec-reviewer]\nscope = "working-tree"',
            "unsupported role/shape",
        ),
        (
            '[groups.g1.reviews.test-reviewer]\nscope = "working-tree"\nspecs = ["docs/a.md"]',
            "repository-relative",
        ),
    ],
)
def test_bad_reviews(tmp_path: Path, review: str, msg: str) -> None:
    with pytest.raises(ConfigError, match=msg):
        load(write(tmp_path, GOOD + "\n" + review))


def test_explicit_reviews(tmp_path: Path) -> None:
    text = (
        GOOD
        + """
[groups.g1.reviews.diff-reviewer]
scope = "working-tree"
[groups.g1.reviews.test-reviewer]
scope = "working-tree"
specs = ["docs/superpowers/specs/review.md"]
"""
    )
    g = load(write(tmp_path, text))[0]
    assert g.reviews == {
        "diff-reviewer": forms.DiffReview("working-tree"),
        "test-reviewer": forms.TestReview(
            "working-tree", specs=("docs/superpowers/specs/review.md",)
        ),
    }


def test_replacement_scope_and_run_uniqueness(tmp_path: Path) -> None:
    text = GOOD.replace(
        'text = "Preserves data"',
        'text = "Preserves data", replaces = "tests/other.py::test_old"',
    )
    with pytest.raises(ConfigError, match="file must be in paths"):
        load(write(tmp_path, text))
    text = text.replace("tests/other.py", "tests/test_a.py")
    groups = load(write(tmp_path, text))
    red = groups[0].steps[1]
    assert any(
        "duplicate replacement" in p
        for p in lint([groups[0], grp("extra", [replace(red, id="r2")])])
    )


def test_load_orphan_file_and_structure_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="steps in no group: lost"):
        load(
            write(
                tmp_path,
                GOOD
                + '\n[steps.lost]\nrole = "core-builder"\nshape = "stub"\npaths = ["gauntlet/core/lost.py"]\nform = {step = "Lost"}',
            )
        )
    with pytest.raises(ConfigError, match="cannot read"):
        load(tmp_path / "none.toml")
    for text, msg in [
        ("[groups", "invalid TOML"),
        ("", "no \\[groups"),
        ("x = 1\n" + GOOD, "unknown keys: x"),
        ("[groups.g]\nsteps = []", "no \\[steps"),
    ]:
        with pytest.raises(ConfigError, match=msg):
            load(write(tmp_path, text))


def test_lint_programmatic_groups() -> None:
    assert lint([grp("a"), grp("b", after=["a"])]) == []
    out = lint(
        [
            grp(
                "g",
                [
                    mk("none", paths=[]),
                    mk("many", paths=[f"gauntlet/core/{i}.py" for i in range(6)]),
                ],
            )
        ]
    )
    assert "none: no paths" in out and "many: 6 paths, more than 5" in out
    assert (
        lint([grp("g", [mk("s", paths=[f"gauntlet/core/{i}.py" for i in range(5)])])])
        == []
    )
    assert lint([grp("g", [mk("s", form=forms.Stub("core-builder", ""))])])
    assert lint([grp("g", [mk("s", role="test-writer")])])
    assert lint([grp("g", [mk("s", expect_code=True)])])


def test_lint_graph_and_duplicates() -> None:
    s = mk("s")
    out = lint([grp("g", [s]), grp("g", [mk("t")]), grp("h", [s])])
    assert "g: duplicate group id" in out
    assert "s: step in more than one group: g, h" in out
    assert any("duplicate step id" in p for p in lint([grp("g", [s, s])]))
    out = lint([grp("a", after=["ghost", "a"])])
    assert "a: after unknown group ghost" in out and "a: after itself" in out
    assert any(
        "after cycle" in p for p in lint([grp("a", after=["b"]), grp("b", after=["a"])])
    )


def test_next_ready_and_owner() -> None:
    a, b = grp("a"), grp("b", after=["a"])
    assert next_ready([b, a], set()) is a
    assert next_ready([a, b], {"a"}) is b
    assert next_ready([a, b], {"a", "b"}) is None
    assert next_ready([b], set()) is None
    first = mk("first", paths=["foreman/", "x.py"])
    second = mk("second", paths=["foreman/steps.py"])
    g = grp("g", [first, second])
    assert owner(g, "foreman/steps.py") is second
    assert owner(g, "foreman/other.py") is first
    assert owner(g, "x.py") is first
    for path in ("x.pyc", "foremanx/a.py", "nowhere.py"):
        assert owner(g, path) is None


@pytest.mark.parametrize("source", ["gauntlet/core/a.py", "gauntlet/core/existing.py"])
def test_load_views_need_no_earlier_producing_step(tmp_path: Path, source: str) -> None:
    text = GOOD.replace('"stub", "red", "build"', '"red", "stub", "build"')
    text = text.replace('views = ["gauntlet/core/a.py"]', f'views = ["{source}"]')
    text = text.replace('source = "gauntlet/core/a.py"', f'source = "{source}"')
    groups = load(write(tmp_path, text))
    assert groups[0].steps[0].views == [source]
    assert lint(groups) == []


def test_lint_views_accepts_worktree_sources_and_rejects_non_python() -> None:
    first = mk("a", paths=["gauntlet/core/a.py"])
    viewer = mk("b", views=["gauntlet/core/a.py", "gauntlet/core/existing.py"])
    assert lint([grp("g", [viewer, first])]) == []
    assert lint([grp("g", [viewer])]) == []
    bad = replace(viewer, views=["gauntlet/core/README.md"])
    assert any("not a .py" in problem for problem in lint([grp("g", [bad])]))


def test_legacy_top_level_red_cannot_bypass_typed_selection(tmp_path: Path) -> None:
    text = GOOD.replace("[steps.build]", '[steps.build]\nred = "red"')
    with pytest.raises(ConfigError, match="unknown keys: red"):
        load(write(tmp_path, text))


PORT_CHECK = """
[groups.g1]
steps = ["port", "stub"]
[steps.port]
role = "test-writer"
shape = "port"
paths = ["tests/support/process.py"]
views = ["gauntlet/seams/process.py"]
check = ["uv", "run", "pytest", "-q", "tests/test_support_process.py::test_run"]
form = { origin = { design = "docs/superpowers/specs/x.md", steps = ["7"] }, sources = ["gauntlet/seams/process.py"] }
[steps.stub]
role = "core-builder"
shape = "stub"
paths = ["gauntlet/core/a.py"]
check = ["uv", "run", "pytest", "-q", "tests/test_a.py"]
form = { step = "Approved stub" }
"""


def test_test_step_holds_the_test_files_its_check_runs(tmp_path: Path) -> None:
    from foreman import brief

    port, stub = load(write(tmp_path, PORT_CHECK))[0].steps
    assert port.paths == ["tests/support/process.py", "tests/test_support_process.py"]
    text = brief.render(
        port.form,
        paths=port.paths,
        view_paths={"gauntlet/seams/process.py": "views/process.md"},
        red_ids=[],
    )
    assert "files: tests/test_support_process.py" in text.splitlines()
    assert stub.paths == ["gauntlet/core/a.py"]
