Status: proposed, for the owner's approval.

## Why

Gauntlet lands a section as commit groups: each step goes to its role in that role's fixed brief shape, the group's full check runs once at the end, a red check goes back as a `finding` brief of gate lines, reviewers read the group's diff, and the group is one commit (`docs/research/first-run-2026-10-23.md:7`). Gauntlet's roles refuse any brief outside their fixed shapes as steering (`docs/research/first-run-2026-10-23.md:5-6`).

The group run stands in the tree, each concern in its one home: step briefs carried verbatim (`foreman/steps.py:16`), composed briefs (`foreman/brief.py:7`, `foreman/brief.py:12`), checks (`foreman/verify.py:18`, `foreman/verify.py:27`), output and kept text (`foreman/events.py:15`), agent and gate text (`foreman/reports.py:26`, `foreman/reports.py:64`, `foreman/reports.py:88`), run order and cleanup (`foreman/loop.py:52-61`, `foreman/loop.py:115-125`), test helpers (`tests/conftest.py:14`, `tests/conftest.py:35-50`, `tests/conftest.py:54`, `tests/conftest.py:69`). One copy stands: `tests/test_cli.py:16-25` writes `foreman.toml` text by hand in a test file, and every config key this design adds reaches it.

One gap stands. A `red` brief gives `test-writer`, per behavior, one `view:`, a `stubgen` `.pyi` of the module under test (`docs/research/gauntlet-roles/test-writer.md:26`), read for names and signatures (`docs/research/gauntlet-roles/test-writer.md:19`) and imported by its dotted path under the `stubgen` output (`docs/research/gauntlet-roles/test-writer.md:39`). The code it views is what the group's `stub` step writes (`docs/research/gauntlet-roles/core-builder.md:43`, `docs/research/gauntlet-roles/seam-builder.md:49`, `docs/research/gauntlet-roles/gatesmith.md:43`). `loop._steps` starts each step as soon as the one before it returns (`foreman/loop.py:52-61`), and nothing in `foreman/` writes a view, so no `.pyi` exists when the red step starts: 1 gap, 0 copies.

The unit of a run is the group. Every brief a role receives is either a step brief carried verbatim from the step file, or a `finding` or review brief Foreman composes in one home. Agent and gate text is read in one home. A step that names views gets them made, from the code earlier steps of its group wrote, before it is dispatched.

## Modules

The core modules and homes of `docs/superpowers/specs/harness-core-design.md:16-174` stand except where this file changes them. Only changed and new modules are listed.

`foreman/errors.py`: adds the stop for Void's ruling and the stop for reviewer findings; `Escalated` goes to Void. Replaces nothing.

```python
class Escalated(ForemanError): exit_code = 5       # red twice; Void, the dispatcher, acts
class RulingNeeded(ForemanError): exit_code = 6    # an agent refused or overloaded, a view was not made, a step check missed, red output or an upheld finding named no group path, or nothing changed; Void, the dispatcher, rules
class FindingsToRule(ForemanError): exit_code = 7  # reviewers filed findings; Void, the dispatcher, upholds or declines each
```

`foreman/events.py`: adds kept text, the done set, the dispatcher signals and the two stream writes; `milestone_done` is the one owner signal. Replaces nothing.

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

`foreman/config.py`: adds the reviewer roster and the view command and root; drops `standing_lines`. Replaces nothing. `view_cmd` and `view_root` are required keys of `foreman.toml`.

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
    view_cmd: list[str]        # the stubgen argv, e.g. ["uv", "run", "stubgen"]; views.make appends "-o <dir>" and the sources
    view_root: Path            # outside every worktree; a group's views live under view_root/<group id>/
```

`foreman/steps.py`: loads groups and steps, lints them, picks the next ready group, names the step that owns a path. A step file holds `[groups.<id>]` tables (`steps`, `design`, `after`) and `[steps.<id>]` tables (`role`, `brief`, `paths`, `check`, `expect_code`, `views`). Replaces nothing.

```python
@dataclass(frozen=True)
class Step:
    id: str
    role: str              # the target repo's role
    brief: str             # sent verbatim; required
    paths: list[str]       # 1 to 5
    check: list[str]       # one argv; empty means the step runs no check
    expect_code: int       # the check's passing exit code, 0 by default
    views: list[str]       # .py sources, relative to the worktree, viewed before this step; empty by default

@dataclass(frozen=True)
class Group:
    id: str
    steps: list[Step]      # dispatched in this order
    design: str            # design file path for reviewer spec: lines
    after: list[str]       # group ids that land first

def load(path: Path) -> list[Group]: ...                        # raises ConfigError
def lint(groups: list[Group]) -> list[str]: ...                 # paths 1-5, brief set, each step in one group, ids unique, no unknown or cyclic `after`,
    # each view a .py source held (steps.holds) by the paths of an earlier step in its group
def next_ready(groups: list[Group], done: set[str]) -> Group | None: ...
def owner(group: Group, path: str) -> Step | None: ...          # the last step in group order whose paths hold path
```

`foreman/reports.py` (new): the one reader of text other programs print or Void hands back: an agent's report, a gate run's output, and the upheld findings. Replaces nothing.

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

`foreman/verify.py`: adds the per-step check. Replaces nothing.

```python
def verify(cfg: Config, wt: Path) -> Verdict: ...               # unchanged: format_cmd, then check_cmd
def check_step(step: Step, wt: Path) -> Verdict | None: ...     # None when step.check is empty; green when the code equals step.expect_code
```

`foreman/views.py` (new): the `.pyi` views a step reads of code its group already wrote; the one runner of `view_cmd` and the one writer under `view_root`. Replaces nothing.

```python
def path(cfg: Config, group_id: str, source: str) -> Path: ...
    # view_root/<group_id>/<source with .py replaced by .pyi>: where stubgen writes the view of a source in a package
def make(cfg: Config, group_id: str, step: Step, wt: Path) -> Verdict: ...
    # runs view_cmd + ["-o", view_root/<group_id>] + step.views in wt through run.run;
    # green when the code is 0 and path(cfg, group_id, s) exists for every s in step.views
def clear(cfg: Config, group_id: str) -> None: ...    # removes view_root/<group_id> when present
```

A red step's brief names each view by its `views.path`, written there by Void, the dispatcher; Foreman adds nothing to it.

`foreman/brief.py`: the briefs Foreman composes; nothing else. Holds no `render`, `render_resume` or `foreman/templates/`. Replaces nothing.

```python
def finding(lines: list[str]) -> str: ...                    # the lines verbatim, one per line, nothing else
def review(reviewer: Reviewer, group: Group) -> str: ...     # "working tree against HEAD", then "spec: <group.design>" when reviewer.spec
```

`foreman/backends.py`: every dispatch is a fresh agent; no `resume`. Replaces nothing.

```python
class Backend(Protocol):
    name: str
    def start(self, brief: str, worktree: Path, role: str) -> Job: ...
    def wait(self, job: Job) -> Result: ...
```

`foreman/loop.py` (new): runs groups. Replaces nothing.

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
2. Each step: when `step.views` is set, `views.make(cfg, group.id, step, wt)`, its output kept as `<group id>-<step id>-views`; red raises `RulingNeeded`. Then `start(step.brief, wt, step.role)`, `wait`. `reports.read`: `rejected` or `overloaded` raises `RulingNeeded`. `verify.check_step`: red raises `RulingNeeded`.
3. `verify.verify`. Red: `reports.route(reports.lines(result), wt, group)`; unowned lines, or no owned line, raise `RulingNeeded`; otherwise each owning step in group order gets `start(brief.finding(lines), wt, step.role)`, read as in 2; `verify.verify` again; red calls `log.notify_dispatcher("red_twice", group.id, ...)` and raises `Escalated`.
4. Each `cfg.reviewers` entry whose paths hold a changed path: `start(brief.review(reviewer, group), wt, reviewer.role)`, read as in 2, its output kept as `<group id>-review-<reviewer role>`. Any finding from any reviewer: `log.notify_dispatcher("findings", group.id, ...)` naming each kept review and the worktree, then raise `FindingsToRule`.
5. Commit the changed paths under the group's step paths as `Group <id>: steps <ids>` (none changed raises `RulingNeeded`), push, `git.remove_worktree`, `views.clear(cfg, group.id)`, emit `group_done`.

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

`tests/conftest.py`: adds `identify(repo: Path) -> None` (git identity through `run.run_ok`), the `fake_run` fixture (records `(cmd, cwd)`, exit codes looked up by `argv[0]`), and the `green_cfg` fixture (`cfg` with `check_cmd` and `format_cmd` set to `["true"]`). `cfg` sets `view_cmd` to `["stubgen"]` and `view_root` to `tmp_path / "views"`. The `conf_file` fixture writes `green_cfg` as a `foreman.toml`, every required key included, and returns its path; `tests/test_cli.py` takes it in place of its `conf` fixture (`tests/test_cli.py:16-25`). `FakeBackend(files, outputs)` writes each start's files and returns each start's output in turn; its `resume` goes. `tmp_repo` creates its repository through `run.run_ok`.

## Homes

| Concern | Home | Forbidden elsewhere |
|---|---|---|
| Output, kept text, owner and dispatcher signals | `foreman/events.py` | `sys.stdout`, `sys.stderr`, `print`, files written or read under `state_dir` |
| Owner signal | `log.notify_owner("milestone_done", ...)` in `loop.run_section` | any other owner signal; a stop reaching the owner except as Void forwards it |
| Stops Void rules on | `RulingNeeded` and `Escalated` in `foreman/errors.py`, signalled by `notify_dispatcher("ruling_needed", ...)` and `notify_dispatcher("red_twice", ...)` | any other exception, exit code or signal for a refusal, an overload, a view not made, a missed step check, an unowned line, nothing changed or red twice |
| Reviewer findings stop | `FindingsToRule` in `foreman/errors.py`, signalled by `notify_dispatcher("findings", ...)` | reviewer findings in an owner signal; any other exception or exit code for them |
| Upholding or declining reviewer findings | Void, the dispatcher, handing the upheld lines to `python -m foreman upheld` | Foreman upholding, declining or dispatching a reviewer finding Void has not upheld |
| Reading agent, gate and upheld text | `foreman/reports.py` | matching `REJECTED`, `OVERLOADED`, `FINDINGS`, `path:line` or upheld lines anywhere else |
| Kept worktree lookup | `git.worktree` | building a worktree path anywhere else |
| Path ownership | `steps.owner` | mapping a path to a step or role anywhere else |
| Step briefs | the `brief` field of `queues/<milestone>.toml`, written by Void, the dispatcher, in the role's shape | Foreman editing, wrapping or appending to a step brief |
| Briefs Foreman composes | `foreman/brief.py` | brief text built in any other module; brief templates |
| Step and group checks | `foreman/verify.py` | running `step.check`, `check_cmd` or `format_cmd` elsewhere |
| Views of a group's code | `foreman/views.py`, from the `views` of a step | running `view_cmd`, building a view path, or writing or removing under `view_root` anywhere else; a view written inside a worktree; Foreman adding a `view:` line to a step brief |
| Reviewer roster | `Config.reviewers` | reviewer role names or reviewed path lists in code |
| Run order and cleanup decisions | `foreman/loop.py` | dispatch order or `git.remove_worktree` calls in `foreman/cli.py` |
| Test helpers | `tests/conftest.py` | git identity, `run.run` fakes, green configs, config values written by hand or `subprocess` in a test file |

## Order

`builder` is the roster agent whose writes are `foreman/`, `tests/`, `pyproject.toml`, `Makefile` and `foreman.example.toml` (`docs/superpowers/specs/harness-core-design.md:178`). Steps 1-14 land on one branch; each step's done-check is its own test files, steps 10 and 14 take `make check`. Steps 11-14 land before step 15.

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
| 11 | `foreman/config.py`, `tests/test_config.py`, `foreman.example.toml`, `tests/conftest.py`, `tests/test_cli.py` | `builder` |
| 12 | `foreman/steps.py`, `tests/test_steps.py` | `builder` |
| 13 | `foreman/views.py`, `tests/test_views.py` | `builder` |
| 14 | `foreman/loop.py`, `tests/test_loop.py` | `builder` |
| 15 | `queues/m1.toml` | Void, the dispatcher |

Step 11 adds `view_cmd` and `view_root` to `Config`, `foreman.example.toml` and the `cfg` fixture, adds `conf_file`, and moves `tests/test_cli.py` onto it. Step 15 rewrites the step file in the group format, each step's brief in its role's shape; each red step lists its `views`, and its brief's `view:` lines name their `views.path`. The first real run is one Gauntlet group through `python -m foreman run`, by the owner.

## Decisions for the owner

1. **Where views are written.** Recommend: under `Config.view_root`, outside every worktree; cost: one more required key, and each red brief's `view:` lines carry absolute paths Void builds from `foreman.toml`, a wrong one surfacing only when `test-writer` cannot read it. Alternative: under a directory inside the group's worktree that git ignores; cost: `git.changed_paths` (`foreman/git.py:45-55`) lists the views unless the target repository ignores that directory, and `check_cmd` may run over them; neither cost measured.
2. **Whether `view_cmd` and `view_root` are required keys.** Recommend: required; cost: the owner's `foreman.toml` gains two lines before the next run, and every config written by a test carries them. Alternative: optional, empty by default, with `views.make` raising `ConfigError` for a step with views under an empty `view_cmd`; cost: a second default path in `config.load` and a stop found only mid-run, after earlier steps of the group ran.
