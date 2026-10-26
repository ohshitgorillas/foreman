"""Pre-flight: checks a queue's steps against the target repo before any dispatch.

Usage: python -m foreman.preflight QUEUE --config CFG [--from STEP] [--judge]

--judge adds the checks in foreman.judge, which ask an agent.

Prints one finding per line as `step | check | problem | fixer`; exits 0 with
no findings, 1 with any, and ConfigError's code when the queue or config
cannot be loaded or STEP is not in the queue.
"""

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from foreman import backends, config, forms, git, steps
from foreman.config import Config
from foreman.errors import ConfigError
from foreman.steps import Group, Step


@dataclass(frozen=True)
class Finding:
    step: str  # step id
    check: str  # the check's name, e.g. "verbatim"
    problem: str  # one line
    fixer: str  # who fixes it: "architect", "role author", "Foreman", "hook"

    def line(self) -> str:
        return f"{self.step} | {self.check} | {self.problem} | {self.fixer}"


type Check = Callable[[Step, Group, Config], list[Finding]]


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _quotes(design: str, text: str) -> bool:
    """Whether collapsed `design` holds collapsed `text` word for word, no word cut."""
    pattern = r"(?<!\w)" + re.escape(_collapse(text)) + r"(?!\w)"
    return re.search(pattern, design) is not None


def check_verbatim(step: Step, group: Group, cfg: Config) -> list[Finding]:
    """Check 1: each behavior of a red step appears word for word in the group's design."""
    if not isinstance(step.form, forms.Red):
        return []

    def found(problem: str, fixer: str) -> Finding:
        return Finding(step.id, "verbatim", problem, fixer)

    if not group.design:
        return [found(f"group {group.id} names no design file", "Foreman")]
    path = cfg.target_repo / group.design
    try:
        design = _collapse(path.read_text())
    except OSError as e:
        return [found(f"cannot read design {group.design}: {e.strerror}", "Foreman")]
    return [
        found(f"behavior not in {group.design}: {b.text}", "architect")
        for b in step.form.behaviors
        if not _quotes(design, b.text)
    ]


_DESCRIPTION = re.compile(r"^description:[ \t]*(.*)$", re.MULTILINE)
_ITEM = r"`[^`]+`"
# Backticked paths joined by commas, "and" or "plus", e.g.
# "`a/` plus `b.py` and `c.py`" or "`a`, `b` and the shared `c`".
_LIST = rf"{_ITEM}(?:(?:,\s*|\s+(?:and|plus)\s+)(?:the\s+\w+\s+)?{_ITEM})*"
_WRITE_CLAUSES = (
    re.compile(rf"\bWrites\s+under\s+({_LIST})\s+only\b"),
    re.compile(rf"\b(?:Writes|writing)\s+only\s+(?:under\s+)?({_LIST})"),
)
_WRITES_NOTHING = re.compile(r"\bWrites\s+nothing\b")


def write_scope(description: str) -> list[str] | None:
    """The paths a role description says the role may write, or None if unparseable.

    Only the shapes above are read; prose beside the backticked paths adds
    nothing to the scope. A description with no write clause, or with more
    than one, is unparseable.
    """
    scopes = [
        re.findall(r"`([^`]+)`", m.group(1))
        for pattern in _WRITE_CLAUSES
        for m in pattern.finditer(description)
    ]
    scopes += [[] for _ in _WRITES_NOTHING.finditer(description)]
    return scopes[0] if len(scopes) == 1 else None


def _description(text: str) -> str | None:
    """The frontmatter description of a role file, or None if it has none."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None
    m = _DESCRIPTION.search("\n".join(lines[1:end]))
    return m.group(1).strip() if m else None


def check_scope(step: Step, group: Group, cfg: Config) -> list[Finding]:
    """Check 3: each of a step's paths lies inside what the step's role may write."""

    def found(problem: str, fixer: str) -> Finding:
        return Finding(step.id, "scope", problem, fixer)

    rel = f".claude/agents/{step.role}.md"
    try:
        text = (cfg.target_repo / rel).read_text()
    except OSError as e:
        return [found(f"cannot read role file {rel}: {e.strerror}", "role author")]
    description = _description(text)
    if description is None:
        return [found(f"role file {rel} has no frontmatter description", "role author")]
    scope = write_scope(description)
    if scope is None:
        return [found(f"role file {rel}: write scope cannot be parsed", "role author")]
    shown = ", ".join(scope) or "nothing"
    return [
        found(f"{p} outside {step.role}'s write scope ({shown})", "Foreman")
        for p in step.paths
        if not steps.holds(scope, p)
    ]


# Check 4: hooks. Settings files read besides <target_repo>/.claude/settings.json;
# tests point this elsewhere so the real ones are never read.
USER_SETTINGS: tuple[Path, ...] = (
    Path("~/.claude/settings.json").expanduser(),
    Path("~/.claude/settings.local.json").expanduser(),
)
HOOK_SECONDS = 10
_COMMANDS = ("uv", "make", "pytest", "python", "git")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PLACEHOLDER = re.compile(r"<[^<>]+>")


def role_commands(text: str, paths: list[str]) -> list[str]:
    """Backticked commands in a role file, placeholders filled with `paths`.

    A span counts when its first word after any leading VAR=value
    assignments is one of _COMMANDS.
    """
    joined = " ".join(paths)
    found = []
    for span in re.findall(r"`([^`\n]+)`", text):
        words = span.split()
        while words and _ASSIGNMENT.match(words[0]):
            words.pop(0)
        if words and words[0] in _COMMANDS:
            found.append(_PLACEHOLDER.sub(lambda _: joined, span))
    return found


def _names_bash(matcher: str) -> bool:
    if matcher == "*":
        return True
    try:
        return re.fullmatch(matcher, "Bash") is not None
    except re.error:
        return "Bash" in matcher.split("|")


def bash_hooks(path: Path) -> list[str]:
    """Commands of the PreToolUse hooks in settings `path` whose matcher names Bash.

    An absent file holds no hooks. Raises OSError, ValueError or TypeError
    when the file cannot be read or is not the settings shape.
    """
    try:
        text = path.read_text()
    except FileNotFoundError:
        return []
    settings = json.loads(text)
    if not isinstance(settings, dict):
        raise TypeError("not a JSON object")
    entries = settings.get("hooks", {}).get("PreToolUse", [])
    commands = []
    for entry in entries:
        matcher = entry.get("matcher")
        if not matcher or not _names_bash(matcher):
            continue
        for hook in entry.get("hooks", []):
            if hook.get("type", "command") == "command" and hook.get("command"):
                commands.append(hook["command"])
    return commands


_INTERPRETER = re.compile(r"(python[\d.]*|sh|bash|zsh|env|node|perl|uv)")


def script_name(command: str) -> str:
    """The script a hook command runs: its first path not an interpreter, else word one."""
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    for w in words:
        name = Path(w).name
        if "/" in w and not w.startswith("-") and not _INTERPRETER.fullmatch(name):
            return name
    return Path(words[0]).name if words else command


@dataclass(frozen=True)
class HookResult:
    blocked: bool
    timed_out: bool
    stderr: str  # the first stderr line, or the deny reason when stderr is empty


def run_hook(hook: str, command: str, cwd: Path) -> HookResult:
    """Feed `command` to `hook` as Bash PreToolUse input, in its own process group."""
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command, "run_in_background": False},
    }
    proc = subprocess.Popen(
        hook,
        shell=True,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(json.dumps(payload), timeout=HOOK_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        return HookResult(False, True, "")
    reason = ""
    denied = False
    try:
        decision = json.loads(out)
    except ValueError:
        decision = None
    if isinstance(decision, dict):
        for d in (decision, decision.get("hookSpecificOutput")):
            if isinstance(d, dict) and d.get("permissionDecision") == "deny":
                denied = True
                reason = str(d.get("permissionDecisionReason", ""))
    lines = err.strip().splitlines()
    first = lines[0] if lines else reason.strip().split("\n")[0]
    return HookResult(proc.returncode == 2 or denied, False, first)


def check_hooks(step: Step, group: Group, cfg: Config) -> list[Finding]:
    """Check 4: no Bash hook blocks a command the step's role must run."""

    def found(problem: str) -> Finding:
        return Finding(step.id, "hooks", problem, "hook")

    findings = []
    commands = [shlex.join(step.check)] if step.check else []
    rel = f".claude/agents/{step.role}.md"
    try:
        commands += role_commands((cfg.target_repo / rel).read_text(), step.paths)
    except OSError:
        pass  # check 3 reports an unreadable role file
    hooks: list[str] = []
    for path in (*USER_SETTINGS, cfg.target_repo / ".claude/settings.json"):
        try:
            hooks += [h for h in bash_hooks(path) if h not in hooks]
        except (OSError, ValueError, AttributeError, TypeError) as e:
            findings.append(found(f"cannot read settings {path}: {e}"))
    with tempfile.TemporaryDirectory(prefix="preflight-hooks-") as cwd:
        for command in dict.fromkeys(commands):
            for hook in hooks:
                r = run_hook(hook, command, Path(cwd))
                if r.timed_out:
                    findings.append(
                        found(
                            f"{script_name(hook)} timed out after {HOOK_SECONDS}s"
                            f" on: {command}"
                        )
                    )
                elif r.blocked:
                    findings.append(
                        found(f"{command} blocked by {script_name(hook)}: {r.stderr}")
                    )
    return findings


def dispatched_roles(step: Step, group: Group, cfg: Config) -> list[str]:
    """Every role Foreman may dispatch for `step` and its group, first use first.

    The step's own role; each configured reviewer's; `juror`, which judges
    any failing step or group check; `test-writer` when the group holds a red
    producer it could repair; and the role of each group step that can own an
    upheld finding's path, which batches.cut hands the fix to.
    """
    roles = [step.role, *(r.role for r in cfg.reviewers), "juror"]
    if any(
        s.role == "test-writer" and isinstance(s.form, forms.Red) for s in group.steps
    ):
        roles.append("test-writer")
    roles += [
        s.role
        for i, s in enumerate(group.steps)
        # steps.owner picks the last step holding a path, so a step owns
        # nothing when later steps hold every one of its paths.
        if any(
            not any(steps.holds(later.paths, p) for later in group.steps[i + 1 :])
            for p in s.paths
        )
    ]
    return list(dict.fromkeys(roles))


# Check 5: installed. Each backend's home-directory agent folder; tests point
# this elsewhere so the real ~/.claude and ~/.codex are never read.
USER_AGENTS: dict[str, Path] = {
    backends.ClaudeLocal.name: Path("~/.claude/agents").expanduser(),
    backends.CodexLocal.name: backends.CODEX_AGENTS,
}


def check_installed(step: Step, group: Group, cfg: Config) -> list[Finding]:
    """Check 5: each dispatched role is where cfg.backend finds it in the group's worktree.

    claude-local runs `claude --agent <role>` in the worktree, which finds
    <worktree>/.claude/agents/<role>.md or <role>.md in the user agents
    folder. The worktree is the kept one if it exists, else the one
    git.make_worktree will add from HEAD, so a role file not committed at
    HEAD is missing. codex-local reads <role>.toml from its agents folder
    wherever it runs.
    """
    if cfg.backend not in USER_AGENTS:
        raise ConfigError(f"unknown backend {cfg.backend!r}")
    home = USER_AGENTS[cfg.backend]
    if cfg.backend == backends.CodexLocal.name:

        def installed(role: str) -> bool:
            return (home / f"{role}.toml").is_file()

        where = str(home)
    else:
        wt = git.worktree_path(cfg, group.id)

        def installed(role: str) -> bool:
            rel = f".claude/agents/{role}.md"
            if (home / f"{role}.md").is_file():
                return True
            if wt.is_dir():
                return (wt / rel).is_file()
            return git.in_head(cfg.target_repo, rel)

        tree = f"worktree {wt}" if wt.is_dir() else "HEAD of the target repo"
        where = f"{home} or .claude/agents/ in {tree}"
    return [
        Finding(
            step.id,
            "installed",
            f"role {role} not installed for {cfg.backend}: not in {where}",
            "role author",
        )
        for role in dispatched_roles(step, group, cfg)
        if not installed(role)
    ]


CHECKS: tuple[Check, ...] = (check_verbatim, check_scope, check_hooks, check_installed)


def queued(groups: list[Group], start: str | None) -> Iterator[tuple[Step, Group]]:
    """Each step with its group in queue order, from step `start` on if given."""
    pairs = [(s, g) for g in groups for s in g.steps]
    if start is not None:
        ids = [s.id for s, _ in pairs]
        if start not in ids:
            raise ConfigError(f"unknown step {start!r}")
        pairs = pairs[ids.index(start) :]
    return iter(pairs)


def preflight(
    groups: list[Group],
    cfg: Config,
    start: str | None,
    checks: tuple[Check, ...] = CHECKS,
) -> list[Finding]:
    return [
        f
        for s, g in queued(groups, start)
        for check in checks
        for f in check(s, g, cfg)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m foreman.preflight")
    parser.add_argument("queue", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--from", dest="start")
    parser.add_argument(
        "--judge", action="store_true", help="also run the agent-judged checks"
    )
    args = parser.parse_args(argv)
    checks = CHECKS
    if args.judge:
        from foreman import judge  # judge imports Finding from here

        checks += (judge.check_observable, judge.check_rules)
    try:
        groups = steps.load(args.queue)
        cfg = config.load(args.config)
        findings = preflight(groups, cfg, args.start, checks)
    except ConfigError as e:
        print(f"preflight: {e}", file=sys.stderr)
        return e.exit_code
    for f in findings:
        print(f.line())
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
