"""Pre-flight checks that hand a judgment to an agent.

Each check builds one prompt, passes it to `ask`, and parses the answer
strictly: one `PROBLEM: <text> | <fixer>` line per problem, or `NONE`. An
answer that does not parse, a timeout or a failed call is a finding, never
a pass. Tests replace `ask` with a fake; the default runs `claude -p`.
"""

import dataclasses
import re
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path

from foreman import forms, preflight
from foreman.config import Config
from foreman.errors import ForemanError
from foreman.preflight import Finding
from foreman.run import TIMEOUT_CODE, run
from foreman.steps import Group, Step

ASK_SECONDS = 300
ASK_CMD = ["claude", "-p", "--output-format", "text"]


class AskError(ForemanError):
    """The agent could not be asked or gave no answer."""


# The directory the default `ask` runs in; each check sets it to the target repo.
_cwd: ContextVar[Path] = ContextVar("judge_cwd")


def default_ask(prompt: str) -> str:
    """Run `claude -p` with `prompt` on stdin in the target repo; its stdout."""
    try:
        result = run(ASK_CMD, _cwd.get(), timeout=ASK_SECONDS, stdin=prompt)
    except ForemanError as e:
        raise AskError(str(e)) from e
    if result.code == TIMEOUT_CODE:
        raise AskError(f"agent timed out after {ASK_SECONDS}s")
    if result.code != 0:
        tail = _one_line(result.err) or "no stderr"
        raise AskError(f"agent exited {result.code}: {tail}")
    return result.out


ask: Callable[[str], str] = default_ask

_PROBLEM = re.compile(r"PROBLEM: (?P<text>[^|]*[^|\s][^|]*?) \| (?P<fixer>[^|]+)")

_ANSWER_FORMAT = """\
Answer with nothing but one line per problem, in exactly this form:
PROBLEM: <one-line problem, no "|" character> | <fixer>
where <fixer> is {fixers}. If there is no problem, answer with the single line:
NONE"""


def _one_line(text: str, limit: int = 200) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 3] + "..."


def parse(answer: str, fixers: tuple[str, ...]) -> list[tuple[str, str]] | None:
    """(problem, fixer) pairs from an answer, or None if it does not parse.

    The answer is `NONE` alone, or one or more `PROBLEM:` lines whose fixer
    is one of `fixers`. Only whitespace around the whole answer and at line
    ends is forgiven.
    """
    lines = [line.rstrip() for line in answer.strip().splitlines()]
    if lines == ["NONE"]:
        return []
    pairs = []
    for line in lines:
        m = _PROBLEM.fullmatch(line)
        if m is None or m["fixer"] not in fixers:
            return None
        pairs.append((m["text"].strip(), m["fixer"]))
    return pairs or None


def _judge(
    step: Step, check: str, cfg: Config, prompt: str, fixers: tuple[str, ...]
) -> list[Finding]:
    """Ask the agent `prompt` in the target repo and turn its answer into findings."""
    token = _cwd.set(cfg.target_repo)
    try:
        answer = ask(prompt)
    except ForemanError as e:
        return [
            Finding(
                step.id, check, f"agent call failed: {_one_line(str(e))}", "Foreman"
            )
        ]
    finally:
        _cwd.reset(token)
    pairs = parse(answer, fixers)
    if pairs is None:
        shown = _one_line(answer) or "empty answer"
        return [
            Finding(step.id, check, f"agent answer unparseable: {shown}", "Foreman")
        ]
    return [Finding(step.id, check, text, fixer) for text, fixer in pairs]


_OBSERVABLE_FIXERS = ("architect",)

_OBSERVABLE_PROMPT = """\
You are reviewing the behavior lines of a test step before any test is written.
For each behavior line below, decide whether it states a concrete outcome that a
test could observe and check, or whether it leaves undefined what counts as the
outcome happening. Judge each line against the design documents that follow:
the group's design first, then each design it cites. A line is a problem when
neither the line nor any of the designs defines what a test would have to set up
or observe. For example, "Failed cleanup raises ProcessError" is a problem when
no design says what makes cleanup fail.

Behavior lines:
{behaviors}

{designs}

Name the behavior line in each problem. {answer}
"""

_DESIGN_BLOCK = """\
Design document {design}:
<<<DESIGN
{text}
DESIGN>>>"""


def check_observable(step: Step, group: Group, cfg: Config) -> list[Finding]:
    """Check 2: each behavior of a red step states an outcome a test can observe."""
    if not isinstance(step.form, forms.Red):
        return []

    def found(problem: str) -> list[Finding]:
        return [Finding(step.id, "observable", problem, "Foreman")]

    if not group.design:
        return found(f"group {group.id} names no design file")
    path = cfg.target_repo / group.design
    try:
        text = path.read_text()
    except OSError as e:
        return found(f"cannot read design {group.design}: {e.strerror}")
    designs = {group.design: text}
    for rel, cited in preflight._cited(text, path, cfg.target_repo):
        designs.setdefault(rel, cited)
    prompt = _OBSERVABLE_PROMPT.format(
        behaviors="\n".join(f"- {b.text}" for b in step.form.behaviors),
        designs="\n\n".join(
            _DESIGN_BLOCK.format(design=rel, text=t) for rel, t in designs.items()
        ),
        answer=_ANSWER_FORMAT.format(fixers='"architect"'),
    )
    return _judge(step, "observable", cfg, prompt, _OBSERVABLE_FIXERS)


_RULES_FIXERS = ("role author", "owner ruling")

_RULES_PROMPT = """\
You are checking a step before it is dispatched to the role "{role}". Below are
the role's own rules (its role file) and the step. Decide whether doing the
step's work needs an edit or a command that the role's rules forbid for a step
of this shape: for example, a "moved" step that must change a file comment
outside any test, when the rules confine that shape to test code. Report only
conflicts between the step's work and the rules.

Use fixer "role author" when the rules forbid what the step needs, and
"owner ruling" when the rules leave open whether the step's work is allowed.

Role file {rel}:
<<<ROLE
{role_text}
ROLE>>>

Step {id}:
shape: {shape}
paths:
{paths}
form:
{form}

{answer}
"""


def _render(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, tuple):
        return "\n".join(f"    - {_render(v)}" for v in value) or "    (none)"
    if dataclasses.is_dataclass(value):
        return "; ".join(
            f"{f.name}: {_render(getattr(value, f.name))}"
            for f in dataclasses.fields(value)
        )
    return repr(value)


def _form_text(form: object) -> str:
    """The step's form, field by field; a build step's `step` text in full."""
    lines = []
    for f in dataclasses.fields(form):
        value = getattr(form, f.name)
        sep = "\n" if isinstance(value, tuple) and value else " "
        lines.append(f"  {f.name}:{sep}{_render(value)}")
    return "\n".join(lines)


def check_rules(step: Step, group: Group, cfg: Config) -> list[Finding]:
    """Check 3, judgment half: the step's work stays within its role's own rules."""
    # The role's instructions where cfg.backend finds them, codex-local's
    # developer_instructions included; checks 5 and 6 report a missing or
    # unreadable role file.
    role = preflight.role_lookup(group, cfg).find(step.role)
    if role is None:
        return []
    try:
        role_text = role.read()
    except preflight._UNREADABLE:
        return []
    prompt = _RULES_PROMPT.format(
        role=step.role,
        rel=role.name,
        role_text=role_text,
        id=step.id,
        shape=step.form.shape,
        paths="\n".join(f"  - {p}" for p in step.paths),
        form=_form_text(step.form),
        answer=_ANSWER_FORMAT.format(fixers='"role author" or "owner ruling"'),
    )
    return _judge(step, "scope", cfg, prompt, _RULES_FIXERS)
