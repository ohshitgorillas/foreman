Status: proposed, for the owner's approval.

## Why

Gauntlet lands a section as commit groups: each step goes to its role in that role's fixed brief shape, the group's full check runs once at the end, a red check goes back as a `finding` brief of gate lines, reviewers read the group's diff, and the group is one commit (`docs/research/first-run-2026-10-23.md:7`). Foreman runs one step per worktree, check and commit (`docs/research/first-run-2026-10-23.md:8`). Its shared concerns have 22 copies or second sources:

- Brief text, 3 sources: the verbatim `Step.brief` (`foreman/steps.py:22`, `foreman/brief.py:23-24`), the template (`foreman/brief.py:30-39`), and Foreman's own resume text (`foreman/brief.py:42-51`), which Gauntlet's roles refuse as steering (`docs/research/first-run-2026-10-23.md:5-6`).
- The check, 2 sources: `Step.done_check` (`foreman/steps.py:17`) is never run, only printed (`foreman/cli.py:58`); `Config.check_cmd` runs for every step (`foreman/verify.py:22`).
- Output outside `foreman/events.py`, 4: the report files `foreman/cli.py:28-32`, and stream writes `foreman/cli.py:100`, `foreman/cli.py:105`, `foreman/cli.py:112`.
- Agent replies saved but never read, 2: `foreman/cli.py:48`, `foreman/cli.py:53`; a refusal surfaces as "changed none of its paths" (`foreman/cli.py:62-63`).
- Cleanup on escalation, 1: the worktree is removed when red twice (`foreman/cli.py:68-69`), losing the work (`docs/research/first-run-2026-10-23.md:6`).
- Test helpers, 10: git identity set three ways (`tests/conftest.py:18-29`, `tests/test_cli.py:24-29`, `tests/test_git.py:17-20`); `run.run` faked twice (`tests/test_verify.py:11-22`, `tests/test_backends.py:11-20`); a green `Config` built four times (`tests/test_cli.py:33-36`, `tests/test_cli.py:61-66`, `tests/test_cli.py:80`, `tests/test_verify.py:57`); `subprocess` imported outside `foreman/run.py` (`tests/conftest.py:3`).

The unit of a run becomes the group. Every brief a role receives is either a step brief carried verbatim from the step file, or a `finding` or review brief Foreman composes in one home. Agent and gate text is read in one home.

## Modules

The core modules and homes of `docs/superpowers/specs/harness-core-design.md:16-174` stand except where this file changes them. Only changed and new modules are listed.

`foreman/errors.py`: adds the owner-ruling failure. Replaces nothing.

```python
class RulingNeeded(ForemanError): exit_code = 6   # an agent refused or overloaded, a step check missed, reviewers filed findings, or red output named no group path
```

`foreman/events.py`: adds kept text, the done set and the two stream writes. Replaces `foreman/cli.py:28-32`, `foreman/cli.py:100`, `foreman/cli.py:105`, `foreman/cli.py:112`.

```python
class EventLog:
    def keep(self, name: str, text: str) -> Path: ...   # writes state_dir/<name>.txt, emits "kept", returns the path
    def done(self) -> set[str]: ...                     # group ids with a "group_done" event

def say(text: str) -> None: ...    # one line to stdout
def fail(text: str) -> None: ...   # "foreman: <text>" to stderr, usable before a Config exists
```

`foreman/config.py`: adds the reviewer roster; drops `standing_lines`. Replaces nothing.

```python
@dataclass(frozen=True)
class Reviewer:
    role: str            # e.g. "diff-reviewer"
    paths: list[str]     # runs when the group changed a path equal to or under one of these
    spec: bool           # its brief adds "spec: <Group.design>"

@dataclass(frozen=True)
class Config:
    target_repo: Path
    worktree_root: Path
    check_cmd: list[str]       # the group check, e.g. ["make", "check"]
    format_cmd: list[str]
    backend: str
    state_dir: Path
    reviewers: list[Reviewer]  # [[reviewers]] tables in foreman.toml, in run order
```

`foreman/steps.py`: loads groups and steps, lints them, picks the next ready group, names the step that owns a path. A step file holds `[groups.<id>]` tables (`steps`, `design`, `after`) and `[steps.<id>]` tables (`role`, `brief`, `paths`, `check`, `expect_code`). Replaces `foreman/steps.py:13-22` (`agent`, `done_check`, `design_ref`, `size`, `depends_on`).

```python
@dataclass(frozen=True)
class Step:
    id: str
    role: str              # the target repo's role
    brief: str             # sent verbatim; required
    paths: list[str]       # 1 to 5
    check: list[str]       # one argv; empty means the step runs no check
    expect_code: int       # the check's passing exit code, 0 by default

@dataclass(frozen=True)
class Group:
    id: str
    steps: list[Step]      # dispatched in this order
    design: str            # design file path for reviewer spec: lines
    after: list[str]       # group ids that land first

def load(path: Path) -> list[Group]: ...                        # raises ConfigError
def lint(groups: list[Group]) -> list[str]: ...                 # paths 1-5, brief set, each step in one group, ids unique, no unknown or cyclic `after`
def next_ready(groups: list[Group], done: set[str]) -> Group | None: ...
def owner(group: Group, path: str) -> Step | None: ...          # the last step in group order whose paths hold path
```

`foreman/reports.py` (new): the one reader of text other programs print: an agent's report and a gate run's output. Replaces the unread replies at `foreman/cli.py:48`, `foreman/cli.py:53`.

```python
@dataclass(frozen=True)
class Report:
    kind: str              # "done" | "rejected" (a line starting "REJECTED: STEERING") | "overloaded" (a line starting "OVERLOADED:")
    findings: list[str]    # the lines under a "FINDINGS <n>" header, verbatim without indent

def read(result: Result) -> Report: ...
def gate_lines(result: Result, wt: Path, group: Group) -> tuple[dict[str, list[str]], list[str]]: ...
    # step id -> each output line, verbatim, whose path:line (relative, or absolute under wt) steps.owner maps to it;
    # and the lines naming a path:line no step owns
```

`foreman/verify.py`: adds the per-step check. Replaces `foreman/cli.py:58` as the use of a step's check.

```python
def verify(cfg: Config, wt: Path) -> Verdict: ...               # unchanged: format_cmd, then check_cmd
def check_step(step: Step, wt: Path) -> Verdict | None: ...     # None when step.check is empty; green when the code equals step.expect_code
```

`foreman/brief.py`: the briefs Foreman composes; nothing else. Removes `render`, `render_resume` and `foreman/templates/`. Replaces `foreman/brief.py:21-51`.

```python
def finding(lines: list[str]) -> str: ...                    # the lines verbatim, one per line, nothing else
def review(reviewer: Reviewer, group: Group) -> str: ...     # "working tree against HEAD", then "spec: <group.design>" when reviewer.spec
```

`foreman/backends.py`: every dispatch is a fresh agent; `resume` goes. Replaces `foreman/backends.py:27`, `foreman/backends.py:72-77`.

```python
class Backend(Protocol):
    name: str
    def start(self, brief: str, worktree: Path, role: str) -> Job: ...
    def wait(self, job: Job) -> Result: ...
```

`foreman/loop.py` (new): runs groups. Replaces `foreman/cli.py:22-69`.

```python
def run_group(cfg: Config, group: Group, log: EventLog, backend: Backend) -> str: ...
    # returns the commit hash; raises RulingNeeded or Escalated and leaves the worktree in place
def run_section(cfg: Config, groups: list[Group], log: EventLog, backend: Backend | None = None) -> list[str]: ...
    # runs each ready group (next_ready over log.done()) until none is ready; notifies milestone_done when every group is done
```

`run_group`, in order; every agent output and check output goes to `log.keep`, and every raise names the kept file and the worktree in its owner signal:

1. `git.make_worktree(cfg, group.id)`.
2. Each step: `start(step.brief, wt, step.role)`, `wait`. `reports.read`: `rejected` or `overloaded` raises `RulingNeeded`. `verify.check_step`: red raises `RulingNeeded`.
3. `verify.verify`. Red: `reports.gate_lines`; unowned lines, or no owned line, raise `RulingNeeded`; otherwise each owning step in group order gets `start(brief.finding(lines), wt, step.role)`, read as in 2; `verify.verify` again; red notifies `red_twice` and raises `Escalated`.
4. Each `cfg.reviewers` entry whose paths hold a changed path: `start(brief.review(reviewer, group), wt, reviewer.role)`, read as in 2; any finding raises `RulingNeeded`.
5. Commit the changed paths under the group's step paths as `Group <id>: steps <ids>` (none changed raises `RulingNeeded`), push, `git.remove_worktree`, emit `group_done`.

`foreman/cli.py`: `python -m foreman run queues/<milestone>.toml [--group ID]` and `python -m foreman status`; parses argv, lints, calls `loop`, maps every `ForemanError` to its exit code. Holds no loop and writes no stream.

```python
def main(argv: list[str] | None = None) -> int: ...
def entry() -> NoReturn: ...
```

`tests/conftest.py`: adds `identify(repo: Path) -> None` (git identity through `run.run_ok`), the `fake_run` fixture (records `(cmd, cwd)`, exit codes looked up by `argv[0]`), and the `green_cfg` fixture (`cfg` with `check_cmd` and `format_cmd` set to `["true"]`). `FakeBackend(files, outputs)` writes each start's files and returns each start's output in turn; its `resume` goes. `tmp_repo` creates its repository through `run.run_ok`.

## Homes

| Concern | Home | Forbidden elsewhere |
|---|---|---|
| Output, kept text, owner signals | `foreman/events.py` | `sys.stdout`, `sys.stderr`, `print`, files written under `state_dir` |
| Owner-ruling failure | `RulingNeeded` in `foreman/errors.py` | any other exception or exit code for a refusal, a missed step check or reviewer findings |
| Reading agent and gate text | `foreman/reports.py` | matching `REJECTED`, `OVERLOADED`, `FINDINGS` or `path:line` anywhere else |
| Path ownership | `steps.owner` | mapping a path to a step or role anywhere else |
| Step briefs | the `brief` field of `queues/<milestone>.toml`, written by Void, the dispatcher, in the role's shape | Foreman editing, wrapping or appending to a step brief |
| Briefs Foreman composes | `foreman/brief.py` | brief text built in any other module; brief templates |
| Step and group checks | `foreman/verify.py` | running `step.check`, `check_cmd` or `format_cmd` elsewhere |
| Reviewer roster | `Config.reviewers` | reviewer role names or reviewed path lists in code |
| Run order and cleanup decisions | `foreman/loop.py` | dispatch order or `git.remove_worktree` calls in `foreman/cli.py` |
| Test helpers | `tests/conftest.py` | git identity, `run.run` fakes, green configs or `subprocess` in a test file |

## Order

`builder` is the roster agent whose writes are `foreman/`, `tests/`, `pyproject.toml`, `Makefile` and `foreman.example.toml` (`docs/superpowers/specs/harness-core-design.md:178`). Steps 1-9 land on one branch; each step's done-check is its own test files, step 9's is `make check`.

| Step | Paths | Agent |
|---|---|---|
| 1 | `foreman/errors.py`, `foreman/events.py`, `tests/test_events.py` | `builder` |
| 2 | `tests/conftest.py`, `tests/test_git.py`, `tests/test_verify.py`, `tests/test_backends.py` | `builder` |
| 3 | `foreman/config.py`, `tests/test_config.py`, `foreman.example.toml` | `builder` |
| 4 | `foreman/steps.py`, `tests/test_steps.py` | `builder` |
| 5 | `foreman/reports.py`, `tests/test_reports.py` | `builder` |
| 6 | `foreman/verify.py`, `tests/test_verify.py` | `builder` |
| 7 | `foreman/brief.py`, `tests/test_brief.py`, `foreman/templates/brief.md`, `foreman/templates/resume.md` (both deleted) | `builder` |
| 8 | `foreman/backends.py`, `tests/test_backends.py` | `builder` |
| 9 | `foreman/loop.py`, `tests/test_loop.py`, `foreman/cli.py`, `tests/test_cli.py` | `builder` |
| 10 | `queues/m1.toml` | Void, the dispatcher |

Step 10 rewrites the step file in the group format, each step's brief in its role's shape. The first real run is one Gauntlet group through `python -m foreman run`, by the owner.

## Decisions for the owner

1. **Who writes each step's brief.** Recommend: Void writes it verbatim in the step file, in the role's shape; cost: Void writes one brief per step by hand. Alternative: Foreman renders each shape (`stub`, `red`, module, `wire`, ...) from step fields; cost: a Python copy of every role's brief grammar (`docs/research/gauntlet-roles/gatesmith.md:23-33`, `docs/research/gauntlet-roles/test-writer.md:26-32`), which drifts when a role changes.
2. **A red group check.** Recommend: one `finding` round, the gate lines routed by `steps.owner` to the owning step's role, then escalate; cost: `reports.gate_lines` depends on gates printing `path:line`, unmeasured on Gauntlet's gates, and output with no owned line goes to the owner. Alternative: every red group check goes straight to the owner; cost: an owner action per red check, however mechanical.
3. **Reviewer findings.** Recommend: any finding raises `RulingNeeded` with the kept report, since a `finding` brief carries only upheld findings (`docs/research/gauntlet-roles/test-writer.md:27`); cost: an owner ruling per round with findings. Alternative: Foreman forwards every finding as upheld to the owning role and rechecks; cost: no one upholds a finding before it is built.
4. **The worktree on escalation.** Recommend: kept, its path in the owner signal; cost: a rerun of the group fails at `foreman/git.py:17-18` until the owner removes it. Alternative: removed as today; cost: the group's work is lost (`docs/research/first-run-2026-10-23.md:6`).
5. **The free template brief and `Backend.resume`.** Recommend: removed, every brief verbatim or composed in `foreman/brief.py`, every dispatch fresh; cost: a target repo with free-form roles needs every brief written into its step file. Alternative: kept beside step briefs for such targets; cost: two brief sources and a resume path Gauntlet never uses.
