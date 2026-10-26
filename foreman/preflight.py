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
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from foreman import backends, config, forms, git, reports, steps
from foreman.config import Config
from foreman.errors import ConfigError, ForemanError
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
        text = path.read_text()
    except OSError as e:
        return [found(f"cannot read design {group.design}: {e.strerror}", "Foreman")]
    designs = {group.design: _collapse(text)}
    for rel, cited in _cited(text, path, cfg.target_repo):
        designs.setdefault(rel, _collapse(cited))
    searched = ", ".join(designs)
    return [
        found(f"behavior not in {searched}: {b.text}", "architect")
        for b in step.form.behaviors
        if not any(_quotes(d, b.text) for d in designs.values())
    ]


_CITATION = re.compile(r"`([^`\s]+\.md)`")


def _cited(text: str, design: Path, repo: Path) -> Iterator[tuple[str, str]]:
    """Each design `text` cites by a backticked `.md` name, as (repo path, text).

    A name resolves first against `design`'s folder, then the repo root; one
    that resolves to no readable file inside `repo` is skipped. Citations in
    the cited designs are not followed.
    """
    root = repo.resolve()
    for name in dict.fromkeys(_CITATION.findall(text)):
        for base in (design.parent, repo):
            path = (base / name).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                continue
            try:
                cited = path.read_text()
            except OSError:
                continue
            yield str(path.relative_to(root)), cited
            break


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

    # codex-local sends only developer_instructions, which carry no
    # frontmatter description to read a write scope from, so the scope of a
    # codex role is not checked.
    if cfg.backend == backends.CodexLocal.name:
        return []
    # The role's text where cfg.backend finds it; checks 5 and 6 report a
    # missing or unreadable role file.
    role = role_lookup(group, cfg).find(step.role)
    if role is None:
        return []
    try:
        text = role.read()
    except _UNREADABLE:
        return []
    description = _description(text)
    if description is None:
        return [
            found(
                f"role file {role.name} has no frontmatter description", "role author"
            )
        ]
    scope = write_scope(description)
    if scope is None:
        return [
            found(f"role file {role.name}: write scope cannot be parsed", "role author")
        ]
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
_RED_PLACEHOLDER = re.compile(r"<[^<>]*\bred test", re.IGNORECASE)
_SPAN = re.compile(r"`([^`\n]+)`")
# Read over text with every backticked span masked, so a span's own
# punctuation never ends a sentence or clause.
_SENTENCE_END = re.compile(r"[.!?](?=\s)|\n")
_LIST_MARK = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
# A clause break; a comma before another span joins a list instead.
_CLAUSE_BREAK = re.compile(r"[;:()]|,(?!\s*`)")
_NEGATION = re.compile(r"\b(?:never|no|not|nor)\b", re.IGNORECASE)
_NEVER_BARE = re.compile(r"\b(?:never|not)\s+bare\b", re.IGNORECASE)
# A sentence about a third party, e.g. "The lead certifies ...".
_OTHER_ACTOR = re.compile(r"^The\s+[\w-]+\s+\w+s\b")
_RUN_NOUN = re.compile(r"^\s+runs?\b")


def red_paths(step: Step, group: Group) -> list[str]:
    """The red tests `step` works from: its own paths for a red step, the
    selected red producers' files for a build step, else none."""
    if isinstance(step.form, forms.Red):
        return list(step.paths)
    if not isinstance(step.form, forms.Build):
        return []
    by_id = {s.id: s for s in group.steps}
    found: list[str] = []
    for selection in step.form.red:
        producer = by_id.get(selection.step)
        if producer is not None:
            found += selection.files if selection.files is not None else producer.paths
    return list(dict.fromkeys(found))


def _mentions(text: str) -> Iterator[tuple[str, bool, bool]]:
    """Each backticked span with whether it forbids it and whether it orders it.

    A span is forbidden when a never, no, not or nor opens its clause (a list
    of spans shares one clause) or its sentence goes on to say "never bare".
    A span is not an order when its sentence is about a third party ("The
    lead runs ...") or it names a run ("one `make check` run printed").
    """
    masked = _SPAN.sub(lambda m: "`" + "x" * (len(m.group(0)) - 2) + "`", text)
    for m in _SPAN.finditer(text):
        start = max(
            (e.end() for e in _SENTENCE_END.finditer(masked, 0, m.start())), default=0
        )
        end_match = _SENTENCE_END.search(masked, m.end())
        end = end_match.start() if end_match else len(masked)
        lead = masked[start : m.start()]
        clause = _CLAUSE_BREAK.split(lead)[-1]
        forbidden = bool(
            _NEGATION.search(clause) or _NEVER_BARE.search(masked[m.end() : end])
        )
        sentence = _LIST_MARK.sub("", lead).lstrip()
        ordered = not (
            _OTHER_ACTOR.match(sentence) or _RUN_NOUN.match(masked[m.end() : end])
        )
        yield m.group(1), forbidden, ordered


def role_commands(text: str, paths: list[str], red: list[str]) -> list[str]:
    """Backticked commands a role file has its role run, placeholders filled.

    A span counts when its first word after any leading VAR=value
    assignments is one of _COMMANDS, some mention of it is an order to the
    role, and no mention forbids it (see _mentions). A placeholder naming red
    tests, e.g. `<each red test path>`, is filled with `red`, and the
    command dropped when `red` is empty; any other is filled with `paths`.
    """
    forbidden: set[str] = set()
    ordered: list[str] = []
    for span, forbids, orders in _mentions(text):
        words = span.split()
        while words and _ASSIGNMENT.match(words[0]):
            words.pop(0)
        if not words or words[0] not in _COMMANDS:
            continue
        if forbids:
            forbidden.add(span)
        elif orders:
            ordered.append(span)
    found = []
    for span in dict.fromkeys(ordered):
        if span in forbidden:
            continue
        if _RED_PLACEHOLDER.search(span):
            if not red:
                continue
            found.append(_PLACEHOLDER.sub(lambda _: " ".join(red), span))
        else:
            found.append(_PLACEHOLDER.sub(lambda _: " ".join(paths), span))
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


def run_hook(
    hook: str, command: str, cwd: Path, agent_type: str | None = None
) -> HookResult:
    """Feed `command` to `hook` as Bash PreToolUse input, in its own process group.

    The payload mirrors what Claude Code sends. A session started with
    `claude --agent <role>` also carries `agent_type: <role>`, and hooks may
    act on it (no-foreground-tests.py lets agent sessions run tests in the
    foreground), so `agent_type`, when given, is sent too; leaving it out
    would report blocks the dispatched session never meets.
    """
    payload: dict[str, object] = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command, "run_in_background": False},
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
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
    """Check 4: no Bash hook blocks a command the step's role itself runs."""

    def found(problem: str) -> Finding:
        return Finding(step.id, "hooks", problem, "hook")

    findings = []
    # The step's own check is not fed: Foreman runs it in foreman/verify.py,
    # outside any agent session, so no hook sees it.
    commands: list[str] = []
    # The role's text where cfg.backend finds it; checks 5 and 6 report a
    # missing or unreadable role file.
    role = role_lookup(group, cfg).find(step.role)
    if role is not None:
        try:
            commands = role_commands(role.read(), step.paths, red_paths(step, group))
        except _UNREADABLE:
            pass
    hooks: list[str] = []
    for path in (*USER_SETTINGS, cfg.target_repo / ".claude/settings.json"):
        try:
            hooks += [h for h in bash_hooks(path) if h not in hooks]
        except (OSError, ValueError, AttributeError, TypeError) as e:
            findings.append(found(f"cannot read settings {path}: {e}"))
    # claude-local dispatches `claude -p --agent <role>`, whose hook input names
    # the role as agent_type; feed the hooks the same, so a hook that exempts
    # agent sessions is judged as the dispatched session meets it.
    agent_type = step.role if cfg.backend == backends.ClaudeLocal.name else None
    with tempfile.TemporaryDirectory(prefix="preflight-hooks-") as cwd:
        for command in dict.fromkeys(commands):
            for hook in hooks:
                r = run_hook(hook, command, Path(cwd), agent_type)
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


@dataclass(frozen=True)
class RoleFile:
    name: str  # where the file is, for findings
    read: Callable[[], str]  # the role's instructions; raises _UNREADABLE


# What RoleFile.read raises when the file is there but cannot be read.
_UNREADABLE = (OSError, ValueError, KeyError, TypeError, ForemanError)


def _codex_instructions(path: Path) -> str:
    """The developer_instructions CodexLocal sends from role file `path`."""
    with path.open("rb") as f:
        instructions = tomllib.load(f)["developer_instructions"]
    if not isinstance(instructions, str):
        raise TypeError("developer_instructions must be a string")
    return instructions


@dataclass(frozen=True)
class RoleLookup:
    where: str  # where cfg.backend looks, for findings
    find: Callable[[str], RoleFile | None]  # a role's file, or None if missing


def role_lookup(group: Group, cfg: Config) -> RoleLookup:
    """Where cfg.backend finds each role's file in `group`'s worktree.

    claude-local runs `claude --agent <role>` in the worktree, which finds
    <role>.md in the user agents folder or <worktree>/.claude/agents/<role>.md.
    The worktree is the kept one if it exists, else the one
    git.make_worktree will add from HEAD, so a role file not committed at
    HEAD is missing. codex-local reads <role>.toml from its agents folder
    wherever it runs, and sends its developer_instructions.
    """
    if cfg.backend not in USER_AGENTS:
        raise ConfigError(f"unknown backend {cfg.backend!r}")
    home = USER_AGENTS[cfg.backend]
    if cfg.backend == backends.CodexLocal.name:

        def find(role: str) -> RoleFile | None:
            path = home / f"{role}.toml"
            if not path.is_file():
                return None
            return RoleFile(str(path), lambda: _codex_instructions(path))

        return RoleLookup(str(home), find)
    wt = git.worktree_path(cfg, group.id)

    def find(role: str) -> RoleFile | None:
        rel = f".claude/agents/{role}.md"
        path = home / f"{role}.md"
        if path.is_file():
            return RoleFile(str(path), path.read_text)
        if wt.is_dir():
            path = wt / rel
            return RoleFile(str(path), path.read_text) if path.is_file() else None
        if git.in_head(cfg.target_repo, rel):
            return RoleFile(
                f"{rel} at HEAD", lambda: git.read_head(cfg.target_repo, rel)
            )
        return None

    tree = f"worktree {wt}" if wt.is_dir() else "HEAD of the target repo"
    return RoleLookup(f"{home} or .claude/agents/ in {tree}", find)


def check_installed(step: Step, group: Group, cfg: Config) -> list[Finding]:
    """Check 5: each dispatched role is where cfg.backend finds it, per role_lookup."""
    lookup = role_lookup(group, cfg)
    return [
        Finding(
            step.id,
            "installed",
            f"role {role} not installed for {cfg.backend}: not in {lookup.where}",
            "role author",
        )
        for role in dispatched_roles(step, group, cfg)
        if lookup.find(role) is None
    ]


_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^(`{3,}|~{3,})")


def report_example(text: str) -> str | None:
    """The report example a role file documents, or None if it documents none.

    The example is the first fenced code block in the section headed
    `Report` (any heading level), which runs to the next heading of the same
    or a higher level. The fence lines themselves are not part of it.
    """
    lines = text.splitlines()
    level = None
    fence = None
    body: list[str] = []
    for line in lines:
        if fence is not None:
            if re.fullmatch(rf"\s*{fence[0]}{{{len(fence)},}}\s*", line):
                return "\n".join(body) + "\n"
            body.append(line)
            continue
        heading = _HEADING.match(line)
        if heading:
            if level is not None and len(heading.group(1)) <= level:
                return None
            if level is None and heading.group(2) == "Report":
                level = len(heading.group(1))
            continue
        opened = _FENCE.match(line)
        if opened and level is not None:
            fence = opened.group(1)
    return None


def example_parsers(role: str, group: Group) -> list[Callable[[str], object]]:
    """The reports parsers loop applies to `role`'s output in `group`.

    loop._dispatch reads every report with reports.parse; loop._step also
    checks a red step's report with reports.red, and loop._judge reads the
    juror's with reports.verdict.
    """
    parsers: list[Callable[[str], object]] = [reports.parse]
    if any(s.role == role and isinstance(s.form, forms.Red) for s in group.steps):
        parsers.append(reports.red)
    if role == "juror":
        parsers.append(reports.verdict)
    return parsers


def check_examples(step: Step, group: Group, cfg: Config) -> list[Finding]:
    """Check 6: each installed dispatched role's report example parses as loop reads it."""

    def found(problem: str) -> Finding:
        return Finding(step.id, "examples", problem, "role author")

    lookup = role_lookup(group, cfg)
    findings = []
    for role in dispatched_roles(step, group, cfg):
        file = lookup.find(role)
        if file is None:
            continue  # check 5 reports a missing role
        try:
            text = file.read()
        except _UNREADABLE as e:
            findings.append(found(f"cannot read role file {file.name}: {e}"))
            continue
        example = report_example(text)
        if example is None:
            findings.append(found(f"role file {file.name} documents no report example"))
            continue
        for parser in example_parsers(role, group):
            try:
                parser(example)
            except ConfigError as e:
                findings.append(
                    found(
                        f"role file {file.name}: report example fails"
                        f" reports.{parser.__name__}: {e}"
                    )
                )
                break
    return findings


CHECKS: tuple[Check, ...] = (
    check_verbatim,
    check_scope,
    check_hooks,
    check_installed,
    check_examples,
)


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
