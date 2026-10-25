from dataclasses import replace
from pathlib import Path

import pytest

from foreman import forms, preflight
from foreman.config import Config
from foreman.preflight import Finding, check_scope, check_verbatim, write_scope
from foreman.steps import Group, Step

DESIGN = "docs/superpowers/specs/design.md"


@pytest.fixture(autouse=True)
def no_host_settings(monkeypatch):
    """Keep the hooks check off this host's real ~/.claude settings."""
    monkeypatch.setattr(preflight, "USER_SETTINGS", ())


def red(id: str, *texts: str, paths: list[str] | None = None) -> Step:
    form = forms.Red(tuple(forms.Behavior(t, "gauntlet/a.py") for t in texts))
    return Step(id, "test-writer", form, paths or ["tests/test_a.py"], [])


def stub(id: str, role: str, paths: list[str]) -> Step:
    return Step(id, role, forms.Stub(role, "Approved stub"), paths, [])


def group(*members: Step, design: str = DESIGN) -> Group:
    return Group("g1", list(members), design, [])


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def role(root: Path, name: str, description: str) -> None:
    write(
        root,
        f".claude/agents/{name}.md",
        f"---\nname: {name}\ndescription: {description}\n---\n\nBody.\n",
    )


@pytest.fixture
def repo(cfg: Config) -> Path:
    write(
        cfg.target_repo,
        DESIGN,
        "# Design\n\n- A runner returns its seeded\n  replies   in order.\n",
    )
    role(cfg.target_repo, "test-writer", "Writes under `tests/` only, one step.")
    return cfg.target_repo


# check 1: verbatim


def test_behavior_in_design_across_a_line_break_passes(cfg: Config, repo: Path):
    step = red("5", "A runner returns its seeded replies in order.")
    assert check_verbatim(step, group(step), cfg) == []


def test_each_missing_behavior_is_one_finding_for_the_architect(
    cfg: Config, repo: Path
):
    step = red("5", "A runner returns its seeded replies in order.", "Gone.", "Lost.")
    found = check_verbatim(step, group(step), cfg)
    assert [(f.step, f.check, f.fixer) for f in found] == [
        ("5", "verbatim", "architect")
    ] * 2
    assert "Gone." in found[0].problem and "Lost." in found[1].problem


@pytest.mark.parametrize(
    "text",
    [
        "a runner returns its seeded replies in order.",  # case
        "A runner returns its seeded replies, in order.",  # punctuation
        "A runner returns its seed",  # a word cut short
        "runner returns its seeded replies in orde",  # a word cut short
        "A runner returns seeded replies in order.",  # a dropped word
    ],
)
def test_only_whitespace_is_forgiven(cfg: Config, repo: Path, text: str):
    step = red("5", text)
    assert len(check_verbatim(step, group(step), cfg)) == 1


def test_non_red_step_is_not_checked_for_verbatim(cfg: Config, repo: Path):
    step = stub("4", "seam-builder", ["gauntlet/seams/a.py"])
    assert check_verbatim(step, group(step, design="docs/none.md"), cfg) == []


@pytest.mark.parametrize("design", ["", "docs/none.md"])
def test_missing_design_is_one_finding_for_foreman(
    cfg: Config, repo: Path, design: str
):
    step = red("5", "Anything.", "Else.")
    found = check_verbatim(step, group(step, design=design), cfg)
    assert [(f.check, f.fixer) for f in found] == [("verbatim", "Foreman")]


# check 3: scope


@pytest.mark.parametrize(
    ("description", "scope"),
    [
        ("Writes under `tests/` only, one step per dispatch: `red`.", ["tests/"]),
        (
            "Builds, writing only under `gauntlet/seams/`. Not `x/`.",
            ["gauntlet/seams/"],
        ),
        (
            (
                "Builds, writing only under `gauntlet/core/` plus `gauntlet/__init__.py`"
                " and `gauntlet/core/__init__.py`. Not for `tests/`."
            ),
            ["gauntlet/core/", "gauntlet/__init__.py", "gauntlet/core/__init__.py"],
        ),
        (
            (
                "Builds, writing only `gates/`, `Makefile` and the shared"
                " `hooks/pre-commit`. Never opens `gauntlet/`."
            ),
            ["gates/", "Makefile", "hooks/pre-commit"],
        ),
        ("Writes only under `tests/` and its scratch copy.", ["tests/"]),
        ("Blind reviewer of `gauntlet/`. Writes nothing, never commits.", []),
        ("Designs one file under `docs/`. Never commits.", None),
        ("Writes under `a/` only; writing only under `b/`.", None),
    ],
)
def test_write_scope(description: str, scope: list[str] | None):
    assert write_scope(description) == scope


def test_paths_inside_scope_pass(cfg: Config, repo: Path):
    step = red("5", "x", paths=["tests/test_a.py", "tests/support/b.py"])
    assert check_scope(step, group(step), cfg) == []


def test_each_path_outside_scope_is_a_finding_for_foreman(cfg: Config, repo: Path):
    step = red("5", "x", paths=["tests/test_a.py", "gauntlet/a.py", "testsx/b.py"])
    found = check_scope(step, group(step), cfg)
    assert [(f.check, f.fixer) for f in found] == [("scope", "Foreman")] * 2
    assert "gauntlet/a.py" in found[0].problem and "testsx/b.py" in found[1].problem


def test_role_that_writes_nothing_flags_every_path(cfg: Config, repo: Path):
    role(repo, "diff-reviewer", "Blind reviewer. Writes nothing, never commits.")
    step = stub("9", "diff-reviewer", ["gauntlet/a.py"])
    assert len(check_scope(step, group(step), cfg)) == 1


@pytest.mark.parametrize(
    "contents",
    [
        None,  # no role file
        "No frontmatter here.\n",
        "---\nname: spiker\n---\n",  # no description
        "---\nname: spiker\ndescription: Answers one question. Never commits.\n---\n",
    ],
)
def test_unreadable_role_scope_is_one_finding_for_the_role_author(
    cfg: Config, repo: Path, contents: str | None
):
    if contents is not None:
        write(repo, ".claude/agents/spiker.md", contents)
    step = stub("6", "spiker", ["gauntlet/a.py", "tests/b.py"])
    found = check_scope(step, group(step), cfg)
    assert [(f.check, f.fixer) for f in found] == [("scope", "role author")]


# the command


QUEUE = """
[groups.g1]
steps = ["4", "5"]
design = "docs/superpowers/specs/design.md"
[groups.g2]
steps = ["8"]
design = "docs/superpowers/specs/design.md"
[steps.4]
role = "seam-builder"
shape = "stub"
paths = ["gauntlet/seams/a.py"]
form = {{ step = "Approved stub" }}
[steps.5]
role = "test-writer"
shape = "red"
paths = ["tests/test_a.py"]
views = ["gauntlet/seams/a.py"]
[steps.5.form]
origin = {{ design = "docs/superpowers/specs/design.md", steps = ["5"] }}
[[steps.5.form.behaviors]]
text = "{five}"
source = "gauntlet/seams/a.py"
[steps.8]
role = "test-writer"
shape = "red"
paths = ["tests/test_b.py"]
views = ["gauntlet/seams/a.py"]
[steps.8.form]
origin = {{ design = "docs/superpowers/specs/design.md", steps = ["8"] }}
[[steps.8.form.behaviors]]
text = "{eight}"
source = "gauntlet/seams/a.py"
"""


def queue(tmp_path: Path, five: str, eight: str) -> Path:
    path = tmp_path / "queue.toml"
    path.write_text(QUEUE.format(five=five, eight=eight))
    return path


GOOD = "A runner returns its seeded replies in order."


def test_clean_queue_prints_nothing_and_exits_0(
    repo: Path, conf_file: Path, tmp_path: Path, capsys
):
    role(repo, "seam-builder", "Builds, writing only under `gauntlet/seams/`.")
    q = queue(tmp_path, GOOD, GOOD)
    assert preflight.main([str(q), "--config", str(conf_file)]) == 0
    assert capsys.readouterr().out == ""


def test_findings_print_one_per_line_in_queue_order_and_exit_1(
    repo: Path, conf_file: Path, tmp_path: Path, capsys
):
    q = queue(tmp_path, "Five is gone.", "Eight is gone.")
    assert preflight.main([str(q), "--config", str(conf_file)]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert [line.split(" | ")[:2] for line in lines] == [
        ["4", "scope"],
        ["5", "verbatim"],
        ["8", "verbatim"],
    ]
    assert lines[0].endswith(" | role author")
    assert (
        lines[1]
        == Finding("5", "verbatim", lines[1].split(" | ")[2], "architect").line()
    )


def test_from_skips_earlier_steps(repo: Path, conf_file: Path, tmp_path: Path, capsys):
    q = queue(tmp_path, "Five is gone.", "Eight is gone.")
    assert preflight.main([str(q), "--config", str(conf_file), "--from", "5"]) == 1
    steps = [line.split(" | ")[0] for line in capsys.readouterr().out.splitlines()]
    assert steps == ["5", "8"]


def test_from_unknown_step_is_a_config_error(
    repo: Path, conf_file: Path, tmp_path: Path, capsys
):
    q = queue(tmp_path, GOOD, GOOD)
    assert preflight.main([str(q), "--config", str(conf_file), "--from", "7"]) == 2
    assert "unknown step '7'" in capsys.readouterr().err


def test_unloadable_queue_is_a_config_error(conf_file: Path, tmp_path: Path, capsys):
    assert (
        preflight.main([str(tmp_path / "none.toml"), "--config", str(conf_file)]) == 2
    )
    assert "cannot read" in capsys.readouterr().err


def test_design_resolves_under_target_repo(
    cfg: Config, repo: Path, tmp_path: Path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    step = red("5", GOOD)
    elsewhere = replace(cfg, target_repo=tmp_path / "other")
    assert check_verbatim(step, group(step), cfg) == []
    assert [f.fixer for f in check_verbatim(step, group(step), elsewhere)] == [
        "Foreman"
    ]


def test_judge_flag_adds_the_agent_checks(
    repo: Path, conf_file: Path, tmp_path: Path, capsys, monkeypatch
):
    from foreman import judge

    asked = []
    monkeypatch.setattr(judge, "ask", lambda p: asked.append(p) or "NONE")
    role(repo, "seam-builder", "Builds, writing only under `gauntlet/seams/`.")
    q = queue(tmp_path, GOOD, GOOD)
    assert preflight.main([str(q), "--config", str(conf_file)]) == 0
    assert asked == []
    assert preflight.main([str(q), "--config", str(conf_file), "--judge"]) == 0
    assert asked
