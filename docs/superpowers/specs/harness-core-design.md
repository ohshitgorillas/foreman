Status: proposed, for the owner's approval.

# Foreman: harness core and first version

## Why

The tree holds no harness code: no `foreman/`, `gauntlet/`, `gates/`, `tests/`, `pyproject.toml` or `Makefile`. Code copies: 0. Every shared concern is a copy carried by hand in each brief or by each agent, 4 of them:

- Standing lines (format, focused suites, copyright) pasted into every brief: `docs/research/void-sketch.md:18`.
- Pings each brief must remember to send: `docs/research/void-sketch.md:28`.
- The overload check, run separately by every agent and by the dispatcher: `docs/research/dispatch-rules.md:2`.
- `make check` run by the agent, then again by hand before commit: `docs/research/void-sketch.md:3`, `docs/research/void-sketch.md:31`.

The core below gives each of these one home before any piece exists, so none is written twice. The first version runs one Gauntlet step from a milestone's step file end to end on one backend: lint, brief, launch, verify, resume once, commit the step's paths, escalate when red twice (`docs/research/void-sketch.md:5-37`). Budget routing, the reviewer pass, cloud, Codex and API adapters come after it.

## Modules

Python 3.12. Package `foreman/` uses the standard library only; TOML is read with `tomllib`. Tests run under pytest, a development-only dependency declared in `pyproject.toml`.

Core (every piece builds on these):

`foreman/errors.py`: the one exception type per failure kind and the one exit-code table. Replaces: nothing (new).

```python
class ForemanError(Exception):
    exit_code: int = 1

class ConfigError(ForemanError): exit_code = 2      # bad or missing foreman.toml
class OverloadedStep(ForemanError): exit_code = 3   # step file fails the lint
class BackendError(ForemanError): exit_code = 4     # agent could not start or vanished
class Escalated(ForemanError): exit_code = 5        # red twice; owner must act

EXIT_OK = 0
```

`foreman/config.py`: loads `foreman.toml` once into a frozen value; nothing else reads the file or the environment. Replaces: nothing (new).

```python
@dataclass(frozen=True)
class Config:
    target_repo: Path        # the repository being built (Gauntlet)
    worktree_root: Path      # where step worktrees are made
    check_cmd: list[str]     # e.g. ["make", "check"]
    format_cmd: list[str]    # e.g. ["ruff", "format"]
    standing_lines: list[str]
    backend: str             # "claude-local" in the first version
    state_dir: Path          # holds events.jsonl

def load(path: Path) -> Config: ...   # raises ConfigError
```

`foreman/run.py`: the only caller of `subprocess`; runs one command with a timeout and captures its output. Replaces: nothing (new).

```python
@dataclass(frozen=True)
class Result:
    cmd: list[str]
    code: int
    out: str
    err: str
    seconds: float

def run(cmd: list[str], cwd: Path, timeout: float | None = None,
        env: dict[str, str] | None = None) -> Result: ...
def run_ok(cmd: list[str], cwd: Path, timeout: float | None = None) -> Result: ...  # raises ForemanError on non-zero
```

`foreman/events.py`: the one output channel: appends JSON lines to `state_dir/events.jsonl`, prints the one-line status, and raises the three owner signals. Replaces the per-brief pings (`docs/research/void-sketch.md:28`) and the human surface (`docs/research/void-sketch.md:36-37`).

```python
OWNER_SIGNALS = ("ruling_needed", "red_twice", "milestone_done")

class EventLog:
    def __init__(self, cfg: Config) -> None: ...
    def emit(self, kind: str, step_id: str | None = None, **fields: object) -> None: ...
    def notify_owner(self, signal: str, step_id: str | None, text: str) -> None: ...  # emit + stderr
    def status(self) -> str: ...          # one line, from the log
```

`foreman/git.py`: worktree lifecycle and the path-limited commit; the only module that runs `git`, and the only cleanup path. Replaces the whole-index step commit (`docs/research/void-sketch.md:34`).

```python
def make_worktree(cfg: Config, step_id: str) -> Path: ...
def remove_worktree(cfg: Config, wt: Path) -> None: ...
def changed_paths(wt: Path) -> list[str]: ...
def commit_paths(wt: Path, paths: list[str], message: str) -> str: ...  # git commit --only -- paths; returns hash
def push(wt: Path) -> None: ...
```

Pieces (each built on the core):

`foreman/steps.py`: reads a milestone's step file, lints every step against the overload test, picks the next ready step. A step file is `queues/<milestone>.toml` in Foreman, one per milestone, written by Void, the dispatcher, from Gauntlet's plan, and holding one `[steps.<id>]` table per step whose keys are the other `Step` fields; `foreman/` never parses Gauntlet's plan files. Replaces the overload check each agent runs (`docs/research/dispatch-rules.md:2`) as the one dispatcher-side lint.

```python
@dataclass(frozen=True)
class Step:
    id: str
    paths: list[str]          # 5 or fewer
    done_check: list[str]     # exactly one command
    design_ref: str
    depends_on: list[str]
    size: str

def load(path: Path) -> list[Step]: ...                 # queues/<milestone>.toml
def lint(steps: list[Step]) -> list[str]: ...         # problems; empty means clean
def next_ready(steps: list[Step], done: set[str]) -> Step | None: ...
```

`foreman/brief.py`: renders a brief and a resume brief from one template each. Replaces the hand-edited template and pasted standing lines (`docs/research/void-sketch.md:3`, `docs/research/void-sketch.md:18`).

```python
def render(cfg: Config, step: Step, head: str, earlier: dict[str, str]) -> str: ...
def render_resume(step: Step, failures: Result) -> str: ...   # quotes the exact failing output
```

`foreman/backends.py`: one adapter interface and the first adapter, local Claude Code (`claude -p`) on the Max plan. `get` returns the adapter `Config.backend` names; the first version has no budget ledger and no routing. Replaces nothing (new).

```python
@dataclass
class Job:
    backend: str
    step_id: str
    worktree: Path
    handle: str               # session id or pid

class Backend(Protocol):
    name: str
    def start(self, brief: str, worktree: Path) -> Job: ...
    def wait(self, job: Job) -> Result: ...
    def resume(self, job: Job, brief: str) -> Job: ...

class ClaudeLocal:            # `claude -p` in the worktree
    name = "claude-local"

def get(cfg: Config) -> Backend: ...
```

`foreman/verify.py`: harness-owned check: format, then the check command, in the worktree. Replaces the agent-run and hand-run checks (`docs/research/void-sketch.md:31`).

```python
@dataclass(frozen=True)
class Verdict:
    green: bool
    result: Result

def verify(cfg: Config, wt: Path) -> Verdict: ...
```

`foreman/cli.py` and `foreman/__main__.py`: `python -m foreman run queues/<milestone>.toml [--step ID]` and `python -m foreman status`; the loop: lint, worktree, brief, start, wait, verify, resume once when red, escalate when red twice, commit the step's paths when green, push, remove the worktree. Maps every `ForemanError` to its exit code; nothing else calls `sys.exit`.

```python
def run_step(cfg: Config, step: Step, log: EventLog) -> str: ...   # returns commit hash; raises Escalated
def main(argv: list[str] | None = None) -> int: ...
```

Test helpers: `tests/conftest.py` holds the pytest fixtures `tmp_repo` (a throwaway git repository with one commit), `cfg` (a `Config` pointing at it) and `FakeBackend` (a `Backend` that writes given files into the worktree).

## Homes

| Concern | Home | Forbidden elsewhere |
|---|---|---|
| Errors and exit codes | `foreman/errors.py` | new exception classes for these kinds; `sys.exit` outside `foreman/cli.py`; literal exit codes |
| Config | `foreman/config.py` | reading `foreman.toml`, `os.environ` or hard-coded target paths and commands |
| Process running | `foreman/run.py` | importing `subprocess` or `os.system` |
| Output and owner signals | `foreman/events.py` | `print`, logging setup, any other file written as a log, any other way of reaching the owner |
| Git, worktrees, cleanup | `foreman/git.py` | running `git` (it goes through `run.run` from here only); deleting worktrees; staging the whole index |
| Step file and overload lint | `foreman/steps.py` | parsing step files; checking file counts or done-check counts |
| Step lists | `queues/<milestone>.toml`, one per milestone, written by Void, the dispatcher, from Gauntlet's plan | steps kept in any other file; `foreman/` reading Gauntlet's plan files; any roster agent writing `queues/` |
| Builder role | `.claude/agents/builder.md`, written by `role-author` | `builder`'s writes defined in any other file; any other agent writing `.claude/agents/builder.md` |
| Backend choice | `Config.backend`, resolved by `backends.get` | budget ledgers, meter readings or routing rules in the first version |
| Brief text and standing lines | `foreman/brief.py` | brief or resume text built anywhere else |
| Agent launch | `foreman/backends.py` | starting an agent process outside a `Backend` |
| Format and check | `foreman/verify.py` | running `format_cmd` or `check_cmd` |
| Test helpers | `tests/conftest.py` | temp repos, fake configs or fake backends defined in a test file |

## Order

`role-author` is a roster agent whose write in this design is `.claude/agents/builder.md`. `builder` is a roster agent whose writes are `foreman/`, `tests/`, `pyproject.toml`, `Makefile` and `foreman.example.toml`.

| Step | Paths | Agent |
|---|---|---|
| 0 | `.claude/agents/builder.md` | `role-author` |
| 1 | `pyproject.toml`, `Makefile`, `foreman/__init__.py`, `foreman/errors.py`, `foreman/config.py` | `builder` |
| 2 | `foreman/run.py`, `foreman/events.py`, `tests/conftest.py`, `tests/test_run.py`, `tests/test_events.py` | `builder` |
| 3 | `foreman/git.py`, `tests/test_git.py`, `tests/test_config.py` | `builder` |
| 4 | `foreman/steps.py`, `tests/test_steps.py` | `builder` |
| 5 | `foreman/brief.py`, `foreman/templates/brief.md`, `foreman/templates/resume.md`, `tests/test_brief.py` | `builder` |
| 6 | `foreman/backends.py`, `tests/test_backends.py` | `builder` |
| 7 | `foreman/verify.py`, `tests/test_verify.py` | `builder` |
| 8 | `foreman/cli.py`, `foreman/__main__.py`, `tests/test_cli.py`, `foreman.example.toml` | `builder` |

Step 0 is one dispatch that writes `builder`'s role with the writes above, and lands before step 1. Steps 1-3 are the core; no piece starts before step 3 lands. Step 8's done-check is `make check`. Before a milestone's first run, Void, the dispatcher, writes its `queues/<milestone>.toml` from Gauntlet's plan; the first real run is one Gauntlet step through `python -m foreman run`, by the owner.

## Decisions for the owner

1. **Where `role-author` is installed.** The tracked roster holds no `role-author` role, so step 0 has no agent to run it. Recommend: the owner installs `role-author` with writes limited to `.claude/agents/` before step 0; cost: one role file, the owner's time unmeasured. Alternative: step 0 goes to a `role-author` installed outside this repository; cost: its write scope is bound nowhere in this repository.
