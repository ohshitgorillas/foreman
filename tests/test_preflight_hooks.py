"""Check 4: hooks."""

import json
import sys
from pathlib import Path

import pytest

from foreman import forms, preflight
from foreman.config import Config
from foreman.preflight import check_hooks, role_commands
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
    return Step(
        "7", role, forms.Stub(role, "Approved stub"), paths or ["a/b.py"], check
    )


def group(s: Step) -> Group:
    return Group("g1", [s], "", [])


def ran(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_check_hooks_is_a_preflight_check():
    assert check_hooks in preflight.CHECKS


def test_a_hook_exiting_2_on_the_steps_check_is_one_finding(
    cfg: Config, user, tmp_path: Path, log: Path
):
    settings(user[0], entry(hook(tmp_path, log, "guard.py")))
    s = step(["make", "EXIT2", "two words"])
    found = check_hooks(s, group(s), cfg)
    command = "make EXIT2 'two words'"
    assert [(f.step, f.check, f.fixer) for f in found] == [("7", "hooks", "hook")]
    assert command in found[0].problem
    assert "guard.py" in found[0].problem
    assert "BLOCKED: no foreground runs" in found[0].problem
    assert "second line" not in found[0].problem
    assert [r["input"] for r in ran(log)] == [
        {
            "tool_name": "Bash",
            "tool_input": {"command": command, "run_in_background": False},
        }
    ]


def test_a_json_deny_is_a_block(cfg: Config, user, tmp_path: Path, log: Path):
    settings(user[1], entry(hook(tmp_path, log, "policy.py")))
    s = step(["git", "DENY"])
    found = check_hooks(s, group(s), cfg)
    assert len(found) == 1
    assert "git DENY" in found[0].problem and "policy.py" in found[0].problem


def test_an_allowing_hook_gives_no_finding(
    cfg: Config, user, tmp_path: Path, log: Path
):
    settings(user[0], entry(hook(tmp_path, log, "guard.py")))
    s = step(["make", "check"])
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
    s = step(["make", "EXIT2"])
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
    s = step(["make", "EXIT2"])
    assert check_hooks(s, group(s), cfg) == []
    assert ran(log) == []


def test_role_file_commands_are_fed_with_placeholders_filled(
    cfg: Config, user, tmp_path: Path, log: Path
):
    role = cfg.target_repo / ".claude/agents/builder.md"
    role.parent.mkdir(parents=True)
    role.write_text(
        "---\nname: builder\n---\n\n"
        "Run `PYTHONDONTWRITEBYTECODE=1 uv run pytest <files>` and `git diff`.\n"
        "Then `make check`; never `ls -la` or `pythonic <x>`.\n"
    )
    settings(user[0], entry(hook(tmp_path, log, "guard.py")))
    s = step([], paths=["tests/a.py", "tests/b.py"])
    assert check_hooks(s, group(s), cfg) == []
    assert [r["input"]["tool_input"]["command"] for r in ran(log)] == [
        "PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/a.py tests/b.py",
        "git diff",
        "make check",
    ]


def test_role_commands_keep_only_the_named_tools():
    text = "`A=1 B=2 python -m x <p>` `uvx tool` `pytest` `echo git` `make`"
    assert role_commands(text, ["p1", "p2"]) == [
        "A=1 B=2 python -m x p1 p2",
        "pytest",
        "make",
    ]


def test_hooks_run_from_a_temporary_directory(
    cfg: Config, user, tmp_path: Path, log: Path, monkeypatch
):
    monkeypatch.chdir(cfg.target_repo)
    settings(user[0], entry(hook(tmp_path, log, "guard.py")))
    s = step(["make", "check"])
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
    s = step(["make", "SLEEP"])
    found = check_hooks(s, group(s), cfg)
    assert len(found) == 1
    assert "slow.py" in found[0].problem and "timed out" in found[0].problem
    assert "make SLEEP" in found[0].problem
