import json
from dataclasses import replace
from pathlib import Path

import pytest

from foreman import backends, forms, git, preflight
from foreman.config import Config, Reviewer
from foreman.errors import ConfigError
from foreman.preflight import (
    Finding,
    check_examples,
    check_installed,
    check_scope,
    check_verbatim,
    dispatched_roles,
    report_example,
    write_scope,
)
from foreman.run import run_ok
from foreman.steps import Group, Step

DESIGN = "docs/superpowers/specs/design.md"


@pytest.fixture(autouse=True)
def no_host_settings(monkeypatch):
    """Keep the hooks check off this host's real ~/.claude settings."""
    monkeypatch.setattr(preflight, "USER_SETTINGS", ())


@pytest.fixture(autouse=True)
def user_agents(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    """Point each backend's home agents folder at an empty temporary one."""
    homes = {
        "claude-local": tmp_path / "home" / ".claude" / "agents",
        "codex-local": tmp_path / "home" / ".codex" / "agents",
    }
    for home in homes.values():
        home.mkdir(parents=True)
    monkeypatch.setattr(preflight, "USER_AGENTS", homes)
    return homes


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


def commit(root: Path) -> None:
    run_ok(["git", "add", "--all"], root)
    run_ok(["git", "commit", "-q", "-m", "roles"], root)


VERDICT = """VERDICT build wrong
ACTION retry
STEP 5
REASON The build skips the second reply.
EVIDENCE gauntlet/a.py:3 | returns the first reply twice
END JUDGMENT
"""

RED = """SHAPE red
BRIEF Write the red tests.
VIEWS none
BEHAVIOR 1 | tests/test_a.py::test_x | docs: design.md 1 | breaks when: x changes
RUN red | tests/test_a.py::test_x | assertion | "FAILED tests/test_a.py::test_x - AssertionError"
RUN rerun | tests/test_a.py::test_x | assertion | "FAILED tests/test_a.py::test_x - AssertionError"
BLOCKED none
GATE make check | "1 failed" | not mine: none
CHANGED 4
STATUS ?? tests/test_a.py
DIFF none
COMMANDS Bash make check
END TESTS
"""

# The report each role's output is read as; any other role's report is
# read by reports.parse alone.
EXAMPLES = {"juror": VERDICT, "test-writer": RED}


def documented(example: str | None) -> str:
    """A role file body whose Report section documents `example`, if given."""
    report = f"## Report\n\n```text\n{example}```\n" if example is not None else ""
    return f"---\n---\n\n## Steps\n\n```\nnot the example\n```\n\n{report}"


def home_roles(user_agents: dict[str, Path], *names: str) -> None:
    """Install `names` as claude-local roles, each with a report example that parses."""
    for name in names:
        (user_agents["claude-local"] / f"{name}.md").write_text(
            documented(EXAMPLES.get(name, "DONE\n"))
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


CITED = "docs/superpowers/specs/cited.md"


@pytest.fixture
def citing(cfg: Config, repo: Path) -> Path:
    """A group design that cites `cited.md` by bare name, which quotes one behavior."""
    write(repo, DESIGN, "Why and Modules: `cited.md`.\n\n- Own line.\n")
    write(repo, CITED, "# Cited\n\n- A behavior only the cited\n  design quotes.\n")
    return repo


def test_behavior_only_in_a_cited_design_passes(cfg: Config, citing: Path):
    step = red("5", "A behavior only the cited design quotes.", "Own line.")
    assert check_verbatim(step, group(step), cfg) == []


def test_behavior_in_neither_design_is_one_finding_naming_both(
    cfg: Config, citing: Path
):
    step = red("5", "Gone.")
    found = check_verbatim(step, group(step), cfg)
    assert [(f.check, f.fixer) for f in found] == [("verbatim", "architect")]
    assert DESIGN in found[0].problem and CITED in found[0].problem


def test_citation_resolving_to_no_file_is_ignored(cfg: Config, repo: Path):
    write(repo, DESIGN, "See `absent.md` and `docs/none.md`.\n\n- Own line.\n")
    step = red("5", "Own line.", "Gone.")
    found = check_verbatim(step, group(step), cfg)
    assert [(f.check, f.fixer) for f in found] == [("verbatim", "architect")]
    assert "absent.md" not in found[0].problem
    assert "none.md" not in found[0].problem


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


# dispatched roles


def test_dispatched_roles_lists_each_dispatching_source_once(cfg: Config):
    cfg = replace(
        cfg,
        reviewers=[
            Reviewer("diff-reviewer", ["gauntlet/"]),
            Reviewer("test-reviewer", ["tests/"]),
        ],
    )
    seam = stub("4", "seam-builder", ["gauntlet/seams/a.py"])
    tests = red("5", "A behavior.")
    build = stub("6", "builder", ["gauntlet/b.py"])
    assert dispatched_roles(seam, group(seam, tests, build), cfg) == [
        "seam-builder",
        "diff-reviewer",
        "test-reviewer",
        "juror",
        "test-writer",
        "builder",
    ]


def test_no_red_producer_means_no_repair_test_writer(cfg: Config):
    step = stub("4", "seam-builder", ["gauntlet/a.py"])
    assert dispatched_roles(step, group(step), cfg) == ["seam-builder", "juror"]


def test_step_whose_paths_later_steps_all_hold_gets_no_fix(cfg: Config):
    early = stub("4", "seam-builder", ["gauntlet/a.py", "gauntlet/b.py"])
    late = stub("6", "builder", ["gauntlet/"])
    assert dispatched_roles(late, group(early, late), cfg) == ["builder", "juror"]


def test_step_keeping_one_unheld_path_gets_fixes(cfg: Config):
    early = stub("4", "seam-builder", ["gauntlet/a.py", "docs/x.md"])
    late = stub("6", "builder", ["gauntlet/"])
    assert dispatched_roles(late, group(early, late), cfg) == [
        "builder",
        "juror",
        "seam-builder",
    ]


# check 5: installed


def test_roles_committed_at_head_are_installed(cfg: Config, repo: Path):
    role(repo, "juror", "Judges. Writes nothing.")
    commit(repo)
    step = red("5", GOOD)
    assert check_installed(step, group(step), cfg) == []


def test_role_on_disk_but_not_committed_is_missing(cfg: Config, repo: Path):
    role(repo, "juror", "Judges. Writes nothing.")
    commit(repo)
    role(repo, "seam-builder", "Builds, writing only under `gauntlet/seams/`.")
    step = stub("4", "seam-builder", ["gauntlet/seams/a.py"])
    found = check_installed(step, group(step), cfg)
    assert [(f.step, f.check, f.fixer) for f in found] == [
        ("4", "installed", "role author")
    ]
    assert "seam-builder" in found[0].problem


def test_role_in_the_user_agents_folder_is_installed(
    cfg: Config, repo: Path, user_agents: dict[str, Path]
):
    home_roles(user_agents, "seam-builder", "juror")
    step = stub("4", "seam-builder", ["gauntlet/seams/a.py"])
    assert check_installed(step, group(step), cfg) == []


def test_each_missing_dispatched_role_is_one_finding_in_dispatch_order(
    cfg: Config, repo: Path
):
    cfg = replace(cfg, reviewers=[Reviewer("diff-reviewer", ["gauntlet/"])])
    step = stub("4", "seam-builder", ["gauntlet/seams/a.py"])
    found = check_installed(step, group(step), cfg)
    assert [f.problem.split()[1] for f in found] == [
        "seam-builder",
        "diff-reviewer",
        "juror",
    ]


def test_a_kept_worktree_is_checked_instead_of_head(cfg: Config, repo: Path):
    role(repo, "juror", "Judges. Writes nothing.")
    commit(repo)
    wt = git.make_worktree(cfg, "g1")
    step = stub("4", "seam-builder", ["gauntlet/seams/a.py"])
    assert len(check_installed(step, group(step), cfg)) == 1
    role(wt, "seam-builder", "Builds, writing only under `gauntlet/seams/`.")
    (wt / ".claude/agents/juror.md").unlink()
    found = check_installed(step, group(step), cfg)
    assert len(found) == 1 and "juror" in found[0].problem


def test_codex_reads_only_its_agents_folder(
    cfg: Config, repo: Path, user_agents: dict[str, Path]
):
    cfg = replace(cfg, backend="codex-local")
    role(repo, "juror", "Judges. Writes nothing.")
    commit(repo)
    home_roles(user_agents, "seam-builder", "juror")
    (user_agents["codex-local"] / "seam-builder.toml").write_text("")
    step = stub("4", "seam-builder", ["gauntlet/seams/a.py"])
    found = check_installed(step, group(step), cfg)
    assert [(f.check, f.fixer) for f in found] == [("installed", "role author")]
    assert "juror" in found[0].problem


def test_unknown_backend_is_a_config_error(cfg: Config, repo: Path):
    step = red("5", GOOD)
    with pytest.raises(ConfigError, match="unknown backend 'other'"):
        check_installed(step, group(step), replace(cfg, backend="other"))


def test_user_agents_default_to_each_backends_home_folder(monkeypatch):
    monkeypatch.undo()
    assert preflight.USER_AGENTS == {
        "claude-local": Path.home() / ".claude" / "agents",
        "codex-local": backends.CODEX_AGENTS,
    }
    assert backends.CODEX_AGENTS == Path.home() / ".codex" / "agents"


def test_installed_is_a_default_check():
    assert check_installed in preflight.CHECKS


# check 6: examples


def test_example_is_the_first_fence_in_the_report_section():
    text = (
        "# Role\n\n```\nbefore\n```\n\n## Report\n\nProse.\n\n"
        "````text\nA\n```\nB\n````\n\n```\nsecond\n```\n"
    )
    assert report_example(text) == "A\n```\nB\n"


@pytest.mark.parametrize(
    "text",
    [
        "## Steps\n\n```\nnot a report\n```\n",
        "## Report\n\nProse only.\n\n## Rails\n\n```\nlater section\n```\n",
        "## Report\n\n```\nnever closed\n",
    ],
)
def test_no_fenced_block_in_a_report_section_is_no_example(text: str):
    assert report_example(text) is None


def test_a_subheading_stays_in_the_report_section():
    assert report_example("## Report\n\n### Shape\n\n~~~\nX\n~~~\n") == "X\n"


def test_examples_that_parse_pass(
    cfg: Config, repo: Path, user_agents: dict[str, Path]
):
    home_roles(user_agents, "test-writer", "juror", "seam-builder")
    early = red("5", GOOD)
    late = stub("6", "seam-builder", ["gauntlet/seams/a.py"])
    assert check_examples(early, group(early, late), cfg) == []


def test_a_juror_example_that_is_no_verdict_quotes_the_verdict_error(
    cfg: Config, repo: Path, user_agents: dict[str, Path]
):
    home_roles(user_agents, "seam-builder")
    (user_agents["claude-local"] / "juror.md").write_text(
        documented("VERDICT <kind>\nACTION <action>\nEND JUDGMENT\n")
    )
    step = stub("4", "seam-builder", ["gauntlet/seams/a.py"])
    found = check_examples(step, group(step), cfg)
    assert [(f.step, f.check, f.fixer) for f in found] == [
        ("4", "examples", "role author")
    ]
    assert "juror.md" in found[0].problem
    assert "reports.verdict" in found[0].problem
    assert "verdict: invalid kind/action pair '<kind>'/'<action>'" in found[0].problem


def test_a_red_producers_example_must_parse_as_red_evidence(
    cfg: Config, repo: Path, user_agents: dict[str, Path]
):
    home_roles(user_agents, "juror")
    (user_agents["claude-local"] / "test-writer.md").write_text(documented("DONE\n"))
    step = red("5", GOOD)
    found = check_examples(step, group(step), cfg)
    assert len(found) == 1 and "test-writer.md" in found[0].problem
    assert "reports.red" in found[0].problem
    assert "red report: requires exactly one SHAPE red .. END TESTS block" in (
        found[0].problem
    )


def test_red_is_not_required_of_a_role_with_no_red_step(
    cfg: Config, repo: Path, user_agents: dict[str, Path]
):
    home_roles(user_agents, "juror")
    (user_agents["claude-local"] / "seam-builder.md").write_text(documented("DONE\n"))
    (user_agents["claude-local"] / "builder.md").write_text(documented("DONE\n"))
    step = stub("4", "seam-builder", ["gauntlet/seams/a.py"])
    builder = stub("6", "builder", ["gauntlet/b.py"])
    assert check_examples(step, group(step, builder), cfg) == []


def test_a_role_file_with_no_example_is_one_finding(
    cfg: Config, repo: Path, user_agents: dict[str, Path]
):
    home_roles(user_agents, "juror")
    (user_agents["claude-local"] / "seam-builder.md").write_text(documented(None))
    step = stub("4", "seam-builder", ["gauntlet/seams/a.py"])
    found = check_examples(step, group(step), cfg)
    assert [(f.check, f.fixer) for f in found] == [("examples", "role author")]
    assert "seam-builder.md documents no report example" in found[0].problem


def test_a_missing_role_is_left_to_the_installed_check(cfg: Config, repo: Path):
    step = stub("4", "seam-builder", ["gauntlet/seams/a.py"])
    assert check_examples(step, group(step), cfg) == []


def test_examples_are_read_at_head_not_from_the_working_tree(
    cfg: Config, repo: Path, user_agents: dict[str, Path]
):
    home_roles(user_agents, "seam-builder")
    write(repo, ".claude/agents/juror.md", documented(VERDICT))
    commit(repo)
    write(repo, ".claude/agents/juror.md", documented(None))
    step = stub("4", "seam-builder", ["gauntlet/seams/a.py"])
    assert check_examples(step, group(step), cfg) == []
    write(repo, ".claude/agents/juror.md", documented("VERDICT <kind>\n"))
    commit(repo)
    found = check_examples(step, group(step), cfg)
    assert len(found) == 1
    assert "role file .claude/agents/juror.md at HEAD" in found[0].problem


def test_examples_are_read_from_a_kept_worktree(
    cfg: Config, repo: Path, user_agents: dict[str, Path]
):
    home_roles(user_agents, "seam-builder")
    write(repo, ".claude/agents/juror.md", documented(VERDICT))
    commit(repo)
    wt = git.make_worktree(cfg, "g1")
    write(wt, ".claude/agents/juror.md", documented(None))
    step = stub("4", "seam-builder", ["gauntlet/seams/a.py"])
    found = check_examples(step, group(step), cfg)
    assert len(found) == 1
    assert f"{wt}/.claude/agents/juror.md documents no" in found[0].problem


def test_codex_examples_are_read_from_developer_instructions(
    cfg: Config, repo: Path, user_agents: dict[str, Path]
):
    cfg = replace(cfg, backend="codex-local")
    home = user_agents["codex-local"]
    instructions = json.dumps(documented("VERDICT <kind>\n"))
    (home / "juror.toml").write_text(f"developer_instructions = {instructions}\n")
    (home / "seam-builder.toml").write_text("developer_instructions = 3\n")
    step = stub("4", "seam-builder", ["gauntlet/seams/a.py"])
    found = check_examples(step, group(step), cfg)
    assert [f.problem.split(": ")[0] for f in found] == [
        f"cannot read role file {home / 'seam-builder.toml'}",
        f"role file {home / 'juror.toml'}",
    ]
    assert "reports.verdict" in found[1].problem


def test_examples_is_a_default_check():
    assert check_examples in preflight.CHECKS


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


@pytest.fixture
def installed(user_agents: dict[str, Path]) -> None:
    """Every role the QUEUE dispatches installed in the temporary home folder."""
    home_roles(user_agents, "seam-builder", "test-writer", "juror")


def test_clean_queue_prints_nothing_and_exits_0(
    repo: Path, conf_file: Path, tmp_path: Path, capsys, installed
):
    role(repo, "seam-builder", "Builds, writing only under `gauntlet/seams/`.")
    q = queue(tmp_path, GOOD, GOOD)
    assert preflight.main([str(q), "--config", str(conf_file)]) == 0
    assert capsys.readouterr().out == ""


def test_findings_print_one_per_line_in_queue_order_and_exit_1(
    repo: Path, conf_file: Path, tmp_path: Path, capsys, installed
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


def test_from_skips_earlier_steps(
    repo: Path, conf_file: Path, tmp_path: Path, capsys, installed
):
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
    repo: Path, conf_file: Path, tmp_path: Path, capsys, monkeypatch, installed
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
