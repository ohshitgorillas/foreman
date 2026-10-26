"""Check 4: hooks."""

import json
import sys
from pathlib import Path

import pytest
from conftest import make_group, make_red, make_step

from foreman import forms, git, preflight
from foreman.config import Config
from foreman.preflight import check_hooks, role_commands
from foreman.run import run_ok
from foreman.steps import Group, Step

# A fake hook: logs its stdin and cwd, then acts on the command it was fed.
HOOK = """\
import json, os, sys
data = json.load(sys.stdin)
with open({log!r}, "a") as f:
    f.write(json.dumps({{"name": {name!r}, "cwd": os.getcwd(), "input": data}}) + "\\n")
command = data["tool_input"]["command"]
if "EXIT2" in command:
    sys.stderr.write("BLOCKED: no foreground runs\\nsecond line\\n")
    sys.exit(2)
if "DENY" in command:
    print(json.dumps({{"hookSpecificOutput": {{
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "denied by policy",
    }}}}))
if "SLEEP" in command:
    import time
    time.sleep(5)
"""


@pytest.fixture(autouse=True)
def user_agents(monkeypatch, tmp_path: Path) -> None:
    """Point each backend's home agents folder at an empty temporary one."""
    homes = {
        "claude-local": tmp_path / "agents-home" / ".claude" / "agents",
        "codex-local": tmp_path / "agents-home" / ".codex" / "agents",
    }
    for home in homes.values():
        home.mkdir(parents=True)
    monkeypatch.setattr(preflight, "USER_AGENTS", homes)


@pytest.fixture
def log(tmp_path: Path) -> Path:
    return tmp_path / "hook-log.jsonl"


def hook(tmp_path: Path, log: Path, name: str) -> str:
    """Write fake hook script `name` and return its settings command line."""
    script = tmp_path / "hooks" / name
    script.parent.mkdir(exist_ok=True)
    script.write_text(HOOK.format(log=str(log), name=name))
    return f'"{sys.executable}" -I "{script}" --hook'


def settings(path: Path, *entries: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": {"PreToolUse": list(entries)}}))
    return path


def entry(command: str, matcher: str | None = "Bash") -> dict:
    e: dict = {"hooks": [{"type": "command", "command": command}]}
    if matcher is not None:
        e["matcher"] = matcher
    return e


@pytest.fixture
def user(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """Point the user settings at tmp_path; neither file exists yet."""
    paths = (tmp_path / "home" / "settings.json", tmp_path / "home" / "local.json")
    monkeypatch.setattr(preflight, "USER_SETTINGS", paths)
    return paths


def step(check: list[str], role: str = "builder", paths: list[str] | None = None):
    return make_step(
        "7",
        paths or ["a/b.py"],
        role=role,
        form=forms.Stub(role, "Approved stub"),
        check=check,
    )


def write_role(root: Path, text: str, role: str = "builder") -> None:
    """Write fixture role file `role` under `root`."""
    path = root / f".claude/agents/{role}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {role}\n---\n\n{text}\n")


def role_file(cfg: Config, text: str, role: str = "builder") -> None:
    """Commit fixture role file `role` at HEAD of the target repo."""
    write_role(cfg.target_repo, text, role)
    run_ok(["git", "add", "--all"], cfg.target_repo)
    run_ok(["git", "commit", "-q", "-m", f"role {role}"], cfg.target_repo)


def fed(log: Path) -> list[str]:
    return [r["input"]["tool_input"]["command"] for r in ran(log)]


def group(s: Step) -> Group:
    return make_group(s, design="")


def ran(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_check_hooks_is_a_preflight_check():
    assert check_hooks in preflight.CHECKS


def test_a_hook_exiting_2_on_a_role_command_is_one_finding(
    cfg: Config, user, tmp_path: Path, log: Path
):
    settings(user[0], entry(hook(tmp_path, log, "guard.py")))
    role_file(cfg, "Run `make EXIT2 'two words'`.")
    s = step([])
    found = check_hooks(s, group(s), cfg)
    command = "make EXIT2 'two words'"
    assert [(f.step, f.check, f.fixer) for f in found] == [("7", "hooks", "hook")]
    assert command in found[0].problem
    assert "guard.py" in found[0].problem
    assert "BLOCKED: no foreground runs" in found[0].problem
    assert "second line" not in found[0].problem
    assert [r["input"] for r in ran(log)] == [
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command, "run_in_background": False},
            "agent_type": "builder",
        }
    ]


def test_a_json_deny_is_a_block(cfg: Config, user, tmp_path: Path, log: Path):
    settings(user[1], entry(hook(tmp_path, log, "policy.py")))
    role_file(cfg, "Run `git DENY`.")
    s = step([])
    found = check_hooks(s, group(s), cfg)
    assert len(found) == 1
    assert "git DENY" in found[0].problem and "policy.py" in found[0].problem


def test_an_allowing_hook_gives_no_finding(
    cfg: Config, user, tmp_path: Path, log: Path
):
    settings(user[0], entry(hook(tmp_path, log, "guard.py")))
    role_file(cfg, "Run `make check`.")
    s = step([])
    assert check_hooks(s, group(s), cfg) == []
    assert len(ran(log)) == 1


def test_every_settings_file_contributes_its_bash_hooks(
    cfg: Config, user, tmp_path: Path, log: Path
):
    settings(user[0], entry(hook(tmp_path, log, "one.py")))
    settings(user[1], entry(hook(tmp_path, log, "two.py"), matcher="Write|Bash"))
    settings(
        cfg.target_repo / ".claude/settings.json",
        entry(hook(tmp_path, log, "three.py"), matcher="*"),
    )
    role_file(cfg, "Run `make EXIT2`.")
    s = step([])
    found = check_hooks(s, group(s), cfg)
    assert len(found) == 3
    assert sorted(r["name"] for r in ran(log)) == ["one.py", "three.py", "two.py"]


def test_hooks_without_a_bash_matcher_are_skipped(
    cfg: Config, user, tmp_path: Path, log: Path
):
    settings(
        user[0],
        entry(hook(tmp_path, log, "nomatcher.py"), matcher=None),
        entry(hook(tmp_path, log, "empty.py"), matcher=""),
        entry(hook(tmp_path, log, "edit.py"), matcher="Write|Edit"),
    )
    role_file(cfg, "Run `make EXIT2`.")
    s = step([])
    assert check_hooks(s, group(s), cfg) == []
    assert ran(log) == []


def test_role_file_commands_are_fed_with_placeholders_filled(
    cfg: Config, user, tmp_path: Path, log: Path
):
    role_file(
        cfg,
        "Run `PYTHONDONTWRITEBYTECODE=1 uv run pytest <files>` and `git diff`.\n"
        "Then `make check`; never `ls -la` or `pythonic <x>`.",
    )
    settings(user[0], entry(hook(tmp_path, log, "guard.py")))
    s = step([], paths=["tests/a.py", "tests/b.py"])
    assert check_hooks(s, group(s), cfg) == []
    assert fed(log) == [
        "PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/a.py tests/b.py",
        "git diff",
        "make check",
    ]


def test_the_role_is_read_where_the_backend_finds_it(
    cfg: Config, user, tmp_path: Path, log: Path
):
    settings(user[0], entry(hook(tmp_path, log, "guard.py")))
    role_file(cfg, "Run `git status`.")
    wt = git.make_worktree(cfg, "g1")
    write_role(wt, "Run `git diff`.")
    write_role(cfg.target_repo, "Run `make check`.")
    s = step([])
    assert check_hooks(s, group(s), cfg) == []
    assert fed(log) == ["git diff"]


def test_a_missing_role_feeds_no_command_and_is_no_finding(
    cfg: Config, user, tmp_path: Path, log: Path
):
    settings(user[0], entry(hook(tmp_path, log, "guard.py")))
    write_role(cfg.target_repo, "Run `make check`.")
    s = step([])
    assert check_hooks(s, group(s), cfg) == []
    assert ran(log) == []


def test_role_commands_keep_only_the_named_tools():
    text = "`A=1 B=2 python -m x <p>` `uvx tool` `pytest` `echo git` `make`"
    assert role_commands(text, ["p1", "p2"], []) == [
        "A=1 B=2 python -m x p1 p2",
        "pytest",
        "make",
    ]


def test_hooks_run_from_a_temporary_directory(
    cfg: Config, user, tmp_path: Path, log: Path, monkeypatch
):
    monkeypatch.chdir(cfg.target_repo)
    settings(user[0], entry(hook(tmp_path, log, "guard.py")))
    role_file(cfg, "Run `make check`.")
    s = step([])
    check_hooks(s, group(s), cfg)
    cwd = Path(ran(log)[0]["cwd"]).resolve()
    assert cwd != cfg.target_repo.resolve()
    assert cwd != Path.cwd().resolve()
    assert not cwd.exists()


@pytest.mark.parametrize("text", ["{not json", '["a list"]', '{"hooks": []}'])
def test_an_unreadable_settings_file_is_a_finding(
    cfg: Config, user, tmp_path: Path, text: str
):
    user[0].parent.mkdir(parents=True)
    user[0].write_text(text)
    s = step(["make", "check"])
    found = check_hooks(s, group(s), cfg)
    assert [(f.check, f.fixer) for f in found] == [("hooks", "hook")]
    assert str(user[0]) in found[0].problem


def test_a_hook_that_times_out_is_a_finding(
    cfg: Config, user, tmp_path: Path, log: Path, monkeypatch
):
    monkeypatch.setattr(preflight, "HOOK_SECONDS", 1)
    settings(user[0], entry(hook(tmp_path, log, "slow.py")))
    role_file(cfg, "Run `make SLEEP`.")
    s = step([])
    found = check_hooks(s, group(s), cfg)
    assert len(found) == 1
    assert "slow.py" in found[0].problem and "timed out" in found[0].problem
    assert "make SLEEP" in found[0].problem


def test_the_steps_own_check_is_not_fed(cfg: Config, user, tmp_path: Path, log: Path):
    settings(user[0], entry(hook(tmp_path, log, "guard.py")))
    role_file(cfg, "Run `git status`.")
    s = step(["make", "EXIT2"])
    assert check_hooks(s, group(s), cfg) == []
    assert fed(log) == ["git status"]


def test_commands_named_only_to_forbid_or_as_anothers_are_not_fed(
    cfg: Config, user, tmp_path: Path, log: Path
):
    settings(user[0], entry(hook(tmp_path, log, "guard.py")))
    role_file(
        cfg,
        "You fix the code, then `make EXIT2 all`. Run `git status`.\n"
        "- Never `ls`, no `git EXIT2 log` or `git EXIT2 show`.\n"
        "- Bash runs `uv run gate -- make all`; never `make EXIT2 tests`."
        " `make EXIT2 all` is written behind the gate, never bare.\n"
        "The lead reads the diff and runs `make EXIT2 lead` before committing.\n"
        "A finding brief carries every finding one `make EXIT2 gate` run printed.",
    )
    s = step([])
    assert check_hooks(s, group(s), cfg) == []
    assert fed(log) == ["git status", "uv run gate -- make all"]


def test_a_red_test_placeholder_takes_the_red_test_paths(
    cfg: Config, user, tmp_path: Path, log: Path
):
    settings(user[0], entry(hook(tmp_path, log, "guard.py")))
    role_file(
        cfg,
        "Run `pytest -rA <each red test path>`, then `git add <files>`.",
        role="seam-builder",
    )
    red = make_red(
        "8",
        ("It works.",),
        ["tests/test_x.py", "tests/test_y.py"],
        source="src/x.py",
        views=[],
    )
    build = make_step(
        "9",
        ["src/x.py"],
        role="seam-builder",
        form=forms.Build("seam-builder", "Build x", (forms.RedSelection("8"),)),
    )
    picked = make_step(
        "10",
        ["src/y.py"],
        role="seam-builder",
        form=forms.Build(
            "seam-builder", "Build y", (forms.RedSelection("8", ("tests/test_y.py",)),)
        ),
    )
    stub = make_step(
        "11",
        ["src/z.py"],
        role="seam-builder",
        form=forms.Stub("seam-builder", "Stub z"),
    )
    g = make_group(red, build, picked, stub, design="")
    for s in (build, picked, stub):
        assert check_hooks(s, g, cfg) == []
    assert fed(log) == [
        "pytest -rA tests/test_x.py tests/test_y.py",
        "git add src/x.py",
        "pytest -rA tests/test_y.py",
        "git add src/y.py",
        "git add src/z.py",
    ]


# A fake hook that blocks on the session's agent_type, as
# ~/.claude/hooks/no-foreground-tests.py lets agent sessions run tests.
AGENT_HOOK = """\
import json, sys
data = json.load(sys.stdin)
with open({log!r}, "a") as f:
    f.write(json.dumps({{"name": "agent.py", "cwd": "", "input": data}}) + "\\n")
agent = data.get("agent_type")
if agent == {block_on!r}:
    sys.stderr.write("BLOCKED: agent " + str(agent) + "\\n")
    sys.exit(2)
"""


def agent_hook(tmp_path: Path, log: Path, block_on: str | None) -> str:
    """A hook blocking when agent_type is `block_on`, or absent when None."""
    script = tmp_path / "hooks" / "agent.py"
    script.parent.mkdir(exist_ok=True)
    script.write_text(AGENT_HOOK.format(log=str(log), block_on=block_on))
    return f'"{sys.executable}" -I "{script}" --hook'


def test_a_hook_blocking_only_outside_an_agent_session_gives_no_finding(
    cfg: Config, user, tmp_path: Path, log: Path
):
    settings(user[0], entry(agent_hook(tmp_path, log, None)))
    role_file(cfg, "Run `uv run pytest`.")
    s = step([])
    assert check_hooks(s, group(s), cfg) == []
    assert [r["input"].get("agent_type") for r in ran(log)] == ["builder"]


def test_a_hook_blocking_the_steps_role_is_a_finding(
    cfg: Config, user, tmp_path: Path, log: Path
):
    settings(user[0], entry(agent_hook(tmp_path, log, "builder")))
    role_file(cfg, "Run `uv run pytest`.")
    s = step([])
    found = check_hooks(s, group(s), cfg)
    assert len(found) == 1
    assert "uv run pytest" in found[0].problem
    assert "BLOCKED: agent builder" in found[0].problem
