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

`foreman/errors.py`: adds the stop for Void's ruling and the stop for reviewer findings; `Escalated` goes to Void. Replaces nothing.

```python
class Escalated(ForemanError): exit_code = 5       # red twice; Void, the dispatcher, acts
class RulingNeeded(ForemanError): exit_code = 6    # an agent refused or overloaded, a step check missed, red output or an upheld finding named no group path, or nothing changed; Void, the dispatcher, rules
class FindingsToRule(ForemanError): exit_code = 7  # reviewers filed findings; Void, the dispatcher, upholds or declines each
```

`foreman/events.py`: adds kept text, the done set, the dispatcher signals and the two stream writes; `milestone_done` is the one owner signal. Replaces `foreman/events.py:9`, `foreman/cli.py:28-32`, `foreman/cli.py:57-59`, `foreman/cli.py:100`, `foreman/cli.py:105`, `foreman/cli.py:112`, `foreman/cli.py:115-116`.

```python
OWNER_SIGNALS = ("milestone_done",)
DISPATCHER_SIGNALS = ("findings", "ruling_needed", "red_twice")

class EventLog:
    def keep(self, name: str, text: str) -> Path: ...   # writes state_dir/<name>.txt, emits "kept", returns the path
    def kept(self, name: str) -> str: ...               # the text keep(name, ...) wrote; raises ConfigError when absent
    def done(self) -> set[str]: ...                     # group ids with a "group_done" event
    def notify_dispatcher(self, signal: str, group_id: str, text: str) -> None: ...
        # signal in DISPATCHER_SIGNALS; emits it and writes "[<signal>] <group_id>: <text>" to stderr

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

`foreman/reports.py` (new): the one reader of text other programs print or Void hands back: an agent's report, a gate run's output, and the upheld findings. Replaces the unread replies at `foreman/cli.py:48`, `foreman/cli.py:53`.

```python
@dataclass(frozen=True)
class Report:
    kind: str              # "done" | "rejected" (a line starting "REJECTED: STEERING") | "overloaded" (a line starting "OVERLOADED:")
    findings: list[str]    # the lines under a "FINDINGS <n>" header, verbatim without indent

def parse(text: str) -> Report: ...
def read(result: Result) -> Report: ...                      # parse(result.out)
def lines(result: Result) -> list[str]: ...                  # every output line of a gate run, verbatim
def route(lines: list[str], wt: Path, group: Group) -> tuple[dict[str, list[str]], list[str]]: ...
    # step id -> each line, verbatim, whose path:line (relative, or absolute under wt) steps.owner maps to it;
    # and the lines naming a path:line no step owns
def upheld(text: str, kept: dict[str, Report]) -> dict[str, list[str]]: ...
    # reviewer role -> each line of text, verbatim, that is one of that reviewer's kept findings; raises ConfigError for a line in none
```

`foreman/git.py`: adds the lookup of a kept worktree. Replaces nothing.

```python
def worktree(cfg: Config, group_id: str) -> Path: ...   # the path make_worktree creates for group_id; raises ForemanError when absent
```

`make_worktree` and `worktree` build the path in one private function.

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
    # returns the commit hash; raises RulingNeeded, FindingsToRule or Escalated and leaves the worktree in place
def run_upheld(cfg: Config, group: Group, text: str, log: EventLog, backend: Backend | None = None) -> str: ...
    # continues the group in its kept worktree from the findings Void upheld (text); returns and raises as run_group
def run_section(cfg: Config, groups: list[Group], log: EventLog, backend: Backend | None = None) -> list[str]: ...
    # runs each ready group (next_ready over log.done()) until none is ready; notifies milestone_done when every group is done
```

`run_group`, in order; every agent output and check output goes to `log.keep`; every `RulingNeeded` raise follows `log.notify_dispatcher("ruling_needed", group.id, ...)`, and every signal names the kept file and the worktree:

1. `git.make_worktree(cfg, group.id)`.
2. Each step: `start(step.brief, wt, step.role)`, `wait`. `reports.read`: `rejected` or `overloaded` raises `RulingNeeded`. `verify.check_step`: red raises `RulingNeeded`.
3. `verify.verify`. Red: `reports.route(reports.lines(result), wt, group)`; unowned lines, or no owned line, raise `RulingNeeded`; otherwise each owning step in group order gets `start(brief.finding(lines), wt, step.role)`, read as in 2; `verify.verify` again; red calls `log.notify_dispatcher("red_twice", group.id, ...)` and raises `Escalated`.
4. Each `cfg.reviewers` entry whose paths hold a changed path: `start(brief.review(reviewer, group), wt, reviewer.role)`, read as in 2, its output kept as `<group id>-review-<reviewer role>`. Any finding from any reviewer: `log.notify_dispatcher("findings", group.id, ...)` naming each kept review and the worktree, then raise `FindingsToRule`.
5. Commit the changed paths under the group's step paths as `Group <id>: steps <ids>` (none changed raises `RulingNeeded`), push, `git.remove_worktree`, emit `group_done`.

`run_upheld`, in order:

1. `git.worktree(cfg, group.id)`.
2. `reports.upheld(text, kept)`, `kept` mapping each reviewer role with a kept `<group id>-review-<role>` to `reports.parse(log.kept(...))`. No upheld line: go to 4.
3. Each reviewer's upheld lines: `reports.route`; unowned lines raise `RulingNeeded`; each owning step in group order gets `start(brief.finding(lines), wt, step.role)`, one brief per reviewer, read as in `run_group` 2. Then `run_group` 3 and 4.
4. `run_group` 5.

`foreman/cli.py`: `python -m foreman run queues/<milestone>.toml [--group ID]`, `python -m foreman upheld queues/<milestone>.toml GROUP FILE` (FILE holds the findings Void upheld, each line verbatim from a kept review) and `python -m foreman status`; parses argv, lints, calls `loop`, maps every `ForemanError` to its exit code. Holds no loop, writes no stream and sends no signal.

```python
def main(argv: list[str] | None = None) -> int: ...
def entry() -> NoReturn: ...
```

`tests/conftest.py`: adds `identify(repo: Path) -> None` (git identity through `run.run_ok`), the `fake_run` fixture (records `(cmd, cwd)`, exit codes looked up by `argv[0]`), and the `green_cfg` fixture (`cfg` with `check_cmd` and `format_cmd` set to `["true"]`). `FakeBackend(files, outputs)` writes each start's files and returns each start's output in turn; its `resume` goes. `tmp_repo` creates its repository through `run.run_ok`.

## Homes

| Concern | Home | Forbidden elsewhere |
|---|---|---|
| Output, kept text, owner and dispatcher signals | `foreman/events.py` | `sys.stdout`, `sys.stderr`, `print`, files written or read under `state_dir` |
| Owner signal | `log.notify_owner("milestone_done", ...)` in `loop.run_section` | any other owner signal; a stop reaching the owner except as Void forwards it |
| Stops Void rules on | `RulingNeeded` and `Escalated` in `foreman/errors.py`, signalled by `notify_dispatcher("ruling_needed", ...)` and `notify_dispatcher("red_twice", ...)` | any other exception, exit code or signal for a refusal, an overload, a missed step check, an unowned line, nothing changed or red twice |
| Reviewer findings stop | `FindingsToRule` in `foreman/errors.py`, signalled by `notify_dispatcher("findings", ...)` | reviewer findings in an owner signal; any other exception or exit code for them |
| Upholding or declining reviewer findings | Void, the dispatcher, handing the upheld lines to `python -m foreman upheld` | Foreman upholding, declining or dispatching a reviewer finding Void has not upheld |
| Reading agent, gate and upheld text | `foreman/reports.py` | matching `REJECTED`, `OVERLOADED`, `FINDINGS`, `path:line` or upheld lines anywhere else |
| Kept worktree lookup | `git.worktree` | building a worktree path anywhere else |
| Path ownership | `steps.owner` | mapping a path to a step or role anywhere else |
| Step briefs | the `brief` field of `queues/<milestone>.toml`, written by Void, the dispatcher, in the role's shape | Foreman editing, wrapping or appending to a step brief |
| Briefs Foreman composes | `foreman/brief.py` | brief text built in any other module; brief templates |
| Step and group checks | `foreman/verify.py` | running `step.check`, `check_cmd` or `format_cmd` elsewhere |
| Reviewer roster | `Config.reviewers` | reviewer role names or reviewed path lists in code |
| Run order and cleanup decisions | `foreman/loop.py` | dispatch order or `git.remove_worktree` calls in `foreman/cli.py` |
| Test helpers | `tests/conftest.py` | git identity, `run.run` fakes, green configs or `subprocess` in a test file |

## Order

`builder` is the roster agent whose writes are `foreman/`, `tests/`, `pyproject.toml`, `Makefile` and `foreman.example.toml` (`docs/superpowers/specs/harness-core-design.md:178`). Steps 1-10 land on one branch; each step's done-check is its own test files, step 10's is `make check`.

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
| 9 | `foreman/git.py`, `tests/test_git.py`, `foreman/loop.py`, `tests/test_loop.py` | `builder` |
| 10 | `foreman/cli.py`, `tests/test_cli.py` | `builder` |
| 11 | `queues/m1.toml` | Void, the dispatcher |

Step 11 rewrites the step file in the group format, each step's brief in its role's shape. The first real run is one Gauntlet group through `python -m foreman run`, by the owner.
