from dataclasses import replace
from pathlib import Path

import pytest

from foreman import forms, judge, preflight
from foreman.config import Config
from foreman.errors import ForemanError
from foreman.judge import check_observable, check_rules
from foreman.preflight import Finding
from foreman.run import run_ok
from foreman.steps import Group, Step

DESIGN = "docs/superpowers/specs/design.md"
DESIGN_TEXT = "# Design\n\nCleanup removes the worktree.\n"
ROLE_TEXT = "---\nname: test-writer\n---\n\nA moved step edits tests only.\n"


def red(id: str, *texts: str) -> Step:
    form = forms.Red(tuple(forms.Behavior(t, "gauntlet/a.py") for t in texts))
    return Step(id, "test-writer", form, ["tests/test_a.py"], [])


def moved(id: str) -> Step:
    change = forms.DocumentChange("a.py:1", "old comment", "new comment")
    form = forms.Moved((change,), ("gauntlet/a.py",))
    return Step(id, "test-writer", form, ["tests/test_a.py", "gauntlet/a.py"], [])


def build(id: str, text: str) -> Step:
    form = forms.Build("builder", text, (forms.RedSelection("5"),))
    return Step(id, "builder", form, ["gauntlet/a.py"], [])


def group(*members: Step, design: str = DESIGN) -> Group:
    return Group("g1", list(members), design, [])


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class FakeAsk:
    def __init__(self, answer: str | Exception):
        self.answer = answer
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture(autouse=True)
def user_agents(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    """Point each backend's home agents folder at an empty temporary one."""
    homes = {
        "claude-local": tmp_path / "home" / ".claude" / "agents",
        "codex-local": tmp_path / "home" / ".codex" / "agents",
    }
    for home in homes.values():
        home.mkdir(parents=True)
    monkeypatch.setattr(preflight, "USER_AGENTS", homes)
    return homes


@pytest.fixture
def repo(cfg: Config) -> Path:
    write(cfg.target_repo, DESIGN, DESIGN_TEXT)
    write(cfg.target_repo, ".claude/agents/test-writer.md", ROLE_TEXT)
    write(cfg.target_repo, ".claude/agents/builder.md", "---\nname: builder\n---\n")
    run_ok(["git", "add", "--all"], cfg.target_repo)
    run_ok(["git", "commit", "-q", "-m", "roles"], cfg.target_repo)
    return cfg.target_repo


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch):
    def install(answer: str | Exception) -> FakeAsk:
        f = FakeAsk(answer)
        monkeypatch.setattr(judge, "ask", f)
        return f

    return install


# check 2: observable


def test_observable_prompt_carries_each_behavior_and_the_design(
    cfg: Config, repo: Path, fake
):
    ask = fake("NONE")
    step = red("5", "Failed cleanup raises ProcessError.", "Cleanup removes it.")
    assert check_observable(step, group(step), cfg) == []
    [prompt] = ask.prompts
    assert "Failed cleanup raises ProcessError." in prompt
    assert "Cleanup removes it." in prompt
    assert DESIGN_TEXT.strip() in prompt


def test_observable_problems_become_architect_findings(cfg: Config, repo: Path, fake):
    fake(
        "PROBLEM: cleanup failure is undefined | architect\n"
        "PROBLEM: second line vague | architect\n"
    )
    step = red("5", "Failed cleanup raises ProcessError.")
    assert check_observable(step, group(step), cfg) == [
        Finding("5", "observable", "cleanup failure is undefined", "architect"),
        Finding("5", "observable", "second line vague", "architect"),
    ]


def test_non_red_step_is_not_judged_observable(cfg: Config, repo: Path, fake):
    ask = fake("NONE")
    step = build("6", "Approved build")
    assert check_observable(step, group(step), cfg) == []
    assert ask.prompts == []


def test_unreadable_design_is_a_finding_without_asking(cfg: Config, repo: Path, fake):
    ask = fake("NONE")
    step = red("5", "A line.")
    [f] = check_observable(step, group(step, design="docs/none.md"), cfg)
    assert (f.check, f.fixer) == ("observable", "Foreman")
    assert "docs/none.md" in f.problem
    assert ask.prompts == []


@pytest.mark.parametrize(
    "answer",
    [
        "",
        "Looks fine to me.",
        "none",
        "NONE\nPROBLEM: also this | architect",
        "PROBLEM: wrong fixer | role author",
        "PROBLEM: no fixer",
        "PROBLEM:  | architect",
        "PROBLEM: a | b | architect",
        "PROBLEM: one | architect\n\nPROBLEM: two | architect",
        "Here you go:\nPROBLEM: x | architect",
    ],
)
def test_unparseable_answer_is_a_finding_not_a_pass(
    cfg: Config, repo: Path, fake, answer: str
):
    fake(answer)
    step = red("5", "A line.")
    [f] = check_observable(step, group(step), cfg)
    assert (f.step, f.check, f.fixer) == ("5", "observable", "Foreman")
    assert "unparseable" in f.problem
    assert "\n" not in f.problem


def test_failed_call_is_a_finding_not_a_pass(cfg: Config, repo: Path, fake):
    fake(judge.AskError("agent timed out after 300s"))
    step = red("5", "A line.")
    [f] = check_observable(step, group(step), cfg)
    assert (f.check, f.fixer) == ("observable", "Foreman")
    assert "timed out" in f.problem


def test_whitespace_around_the_answer_is_forgiven(cfg: Config, repo: Path, fake):
    fake("\n  PROBLEM: vague | architect  \n\n")
    step = red("5", "A line.")
    assert check_observable(step, group(step), cfg) == [
        Finding("5", "observable", "vague", "architect")
    ]


# check 3, judgment half: rules


def test_rules_prompt_carries_role_file_shape_paths_and_form(
    cfg: Config, repo: Path, fake
):
    ask = fake("NONE")
    step = moved("7")
    assert check_rules(step, group(step), cfg) == []
    [prompt] = ask.prompts
    assert "A moved step edits tests only." in prompt
    assert "moved" in prompt
    assert "tests/test_a.py" in prompt and "gauntlet/a.py" in prompt
    assert "old comment" in prompt and "new comment" in prompt


def test_rules_prompt_carries_a_build_steps_step_text(cfg: Config, repo: Path, fake):
    ask = fake("NONE")
    text = "Build the runner so that it returns seeded replies in order."
    step = build("8", text)
    check_rules(step, group(step), cfg)
    [prompt] = ask.prompts
    assert text in prompt and "build" in prompt


def test_rules_problems_keep_their_fixer(cfg: Config, repo: Path, fake):
    fake(
        "PROBLEM: must edit a comment in gauntlet/a.py | role author\n"
        "PROBLEM: rules silent on fixtures | owner ruling"
    )
    step = moved("7")
    assert check_rules(step, group(step), cfg) == [
        Finding("7", "scope", "must edit a comment in gauntlet/a.py", "role author"),
        Finding("7", "scope", "rules silent on fixtures", "owner ruling"),
    ]


def test_rules_reject_a_fixer_outside_their_set(cfg: Config, repo: Path, fake):
    fake("PROBLEM: vague | architect")
    step = moved("7")
    [f] = check_rules(step, group(step), cfg)
    assert (f.check, f.fixer) == ("scope", "Foreman")
    assert "unparseable" in f.problem


def test_rules_prompt_names_the_role_file_at_head(cfg: Config, repo: Path, fake):
    ask = fake("NONE")
    step = moved("7")
    check_rules(step, group(step), cfg)
    [prompt] = ask.prompts
    assert "Role file .claude/agents/test-writer.md at HEAD:" in prompt


def test_rules_read_a_role_in_the_user_agents_folder(
    cfg: Config, repo: Path, fake, user_agents: dict[str, Path]
):
    ask = fake("NONE")
    path = user_agents["claude-local"] / "test-writer.md"
    path.write_text("---\nname: test-writer\n---\n\nThe user's own rules.\n")
    step = moved("7")
    assert check_rules(step, group(step), cfg) == []
    [prompt] = ask.prompts
    assert "The user's own rules." in prompt
    assert "A moved step edits tests only." not in prompt
    assert f"Role file {path}:" in prompt


def test_rules_read_a_codex_roles_developer_instructions(
    cfg: Config, repo: Path, fake, user_agents: dict[str, Path]
):
    ask = fake("NONE")
    cfg = replace(cfg, backend="codex-local")
    path = user_agents["codex-local"] / "test-writer.toml"
    path.write_text('developer_instructions = "Codex rules: tests only."\n')
    step = moved("7")
    assert check_rules(step, group(step), cfg) == []
    [prompt] = ask.prompts
    assert "Codex rules: tests only." in prompt
    assert f"Role file {path}:" in prompt


def test_missing_role_is_no_finding_and_no_ask(cfg: Config, repo: Path, fake):
    ask = fake("NONE")
    step = Step("9", "ghost", forms.Stub("ghost", "Stub it"), ["gauntlet/a.py"], [])
    assert check_rules(step, group(step), cfg) == []
    assert ask.prompts == []


def test_unreadable_role_is_no_finding_and_no_ask(
    cfg: Config, repo: Path, fake, user_agents: dict[str, Path]
):
    ask = fake("NONE")
    cfg = replace(cfg, backend="codex-local")
    (user_agents["codex-local"] / "test-writer.toml").write_text("not toml [\n")
    step = moved("7")
    assert check_rules(step, group(step), cfg) == []
    assert ask.prompts == []


def test_failed_rules_call_is_a_finding(cfg: Config, repo: Path, fake):
    fake(ForemanError("cannot run ['claude']: not found"))
    step = moved("7")
    [f] = check_rules(step, group(step), cfg)
    assert (f.check, f.fixer) == ("scope", "Foreman")
    assert "agent call failed" in f.problem


# the default ask, with the claude command swapped for a shell stand-in


def test_default_ask_sends_stdin_and_runs_in_the_target_repo(
    cfg: Config, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(judge, "ASK_CMD", ["sh", "-c", 'pwd; cat; printf "\\nNONE"'])
    seen: list[str] = []

    def spy(prompt: str) -> str:
        out = judge.default_ask(prompt)
        seen.append(out)
        return out.splitlines()[-1]

    monkeypatch.setattr(judge, "ask", spy)
    step = red("5", "A line.")
    assert check_observable(step, group(step), cfg) == []
    [out] = seen
    assert out.splitlines()[0] == str(cfg.target_repo.resolve())
    assert "A line." in out


def test_default_ask_turns_a_failed_exit_into_a_finding(
    cfg: Config, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(judge, "ASK_CMD", ["sh", "-c", "echo broke >&2; exit 3"])
    step = red("5", "A line.")
    [f] = check_observable(step, group(step), cfg)
    assert f.fixer == "Foreman"
    assert "exited 3" in f.problem and "broke" in f.problem


def test_default_ask_turns_a_timeout_into_a_finding(
    cfg: Config, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(judge, "ASK_CMD", ["sh", "-c", "exec sleep 5"])
    monkeypatch.setattr(judge, "ASK_SECONDS", 0.2)
    step = red("5", "A line.")
    [f] = check_observable(step, group(step), cfg)
    assert f.fixer == "Foreman"
    assert "timed out" in f.problem


def test_default_ask_turns_a_missing_command_into_a_finding(
    cfg: Config, repo: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(judge, "ASK_CMD", ["/nonexistent/claude"])
    step = moved("7")
    [f] = check_rules(step, group(step), cfg)
    assert (f.check, f.fixer) == ("scope", "Foreman")
    assert "agent call failed" in f.problem
