Status: approved.

## Why

The g7 build stopped 28 times; 18 of the 22 that can be judged had no defect behind them, and 6 cannot be judged because nothing was kept (`docs/research/stops-g7.md:9`). Pre-flight stops (`docs/research/stops-g7.md:36-43`) already warn and go on (`foreman/cli.py:80-85`), and a BLOCKED red report is already read (`foreman/reports.py:198-203`). Seven stops sit in leads and role prompts, outside `foreman/` (`docs/research/stops-g7.md:31-46`, rows 12, 15, 19, 23, 25, 26, 28). What still jams inside Foreman, by cause:

- **Red paperwork is the gate, not the tests.** A red step whose check failed as expected (`foreman/loop.py:517`) is still rejected when its report carries `LEFT`, `UNTESTABLE` or `CONTRADICTS` (`foreman/reports.py:198-203`), lacks the agent's own `RUN red`/`RUN rerun` lines or their exact summary text (`foreman/reports.py:246-259`, `foreman/reports.py:117-132`), or carries a malformed envelope record (`foreman/reports.py:174-197`). Stops 1, 6, 9 and 10 were tests red as intended (`docs/research/stops-g7.md:15`, `:20`, `:23`, `:24`). Two homes judge "red": the step check (`foreman/verify.py:32-37`) and the agent's RUN records (`foreman/reports.py:246-259`).
- **A refusal is answered with the same brief.** `_answer` reruns the identical render (`foreman/loop.py:133-163`, `foreman/loop.py:499-512`); a steering or overload refusal of fixed text recurs, then a juror is asked, whose verdicts (`foreman/reports.py:77`) name no brief-form fault. Stops 3, 4, 14 and 28 (`docs/research/stops-g7.md:17-18`, `:33`, `:46`). A red brief the cut refuses is dispatched whole anyway (`foreman/loop.py:455-461`).
- **Halts with nothing logged.** No step check, group check, format or view command has a timeout (`foreman/verify.py:25`, `foreman/verify.py:28`, `foreman/verify.py:36`, `foreman/views.py:20`), so the timeout stops at `foreman/loop.py:546-547` and `foreman/loop.py:1080-1081` never fire and a hung command hangs Foreman. A kill exits through `foreman/cli.py:160-161` and writes only a temp alert file (`foreman/cli.py:176-183`), never `events.jsonl`. Stops 7 and 11 (`docs/research/stops-g7.md:21`, `:25`).
- **Kept reports are overwritten.** `_dispatch` keeps each attempt under one name (`foreman/loop.py:78`), so a rerun (`foreman/loop.py:163`, `foreman/loop.py:1124`) erases the report it answers. Stops 5 and 8 (`docs/research/stops-g7.md:19`, `:22`).
- **A refused finding batch stops outright** through `_refusal_stops` (`foreman/loop.py:98-111`, called at `foreman/loop.py:1267`), outside the one rerun counter `_round` (`foreman/loop.py:114-130`).

Copies of shared concerns, 26:

- Process running and cleanup, 3: `foreman/run.py:38-50` kills only the direct child on timeout; `foreman/preflight.py:365-382` and `foreman/cli.py:153-159` start processes outside `run.py`.
- Command timeouts, 5 literal or missing: `foreman/judge.py:22`, `foreman/preflight.py:194`, `foreman/verify.py:166`, and none at `foreman/verify.py:25-36` and `foreman/views.py:20`.
- Stop recording, 4: `_rule` (`foreman/loop.py:34-38`), `_owner_stop` (`foreman/loop.py:642-648`), `_refusal_stops` (`foreman/loop.py:98-111`), and the alert file (`foreman/cli.py:176-183`); no stop carries a kind.
- Refusal markers, 2: `foreman/reports.py:13-14` and the literals at `foreman/reports.py:200`.
- Dead stop class, 1: `Escalated` (`foreman/errors.py:20-23`) is raised nowhere.
- Test helpers, 11 outside `tests/conftest.py:170-208`: Step/Group built by hand at `tests/test_preflight_hooks.py:84-108`, `tests/test_preflight_hooks.py:318-344`, `tests/test_steps.py:12-24`, `tests/test_views.py:10`, `tests/test_verify.py:53`; red report text at `tests/test_reports.py:109`, `tests/test_preflight.py:86`, `tests/test_loop.py:952`; verdict text at `tests/test_reports.py:424`, `tests/test_preflight.py:78`, `tests/test_loop.py:27`.

## Modules

The modules and homes of `harness-core-design.md`, `group-run-design.md`, `test-wrong-design.md`, `brief-forms-design.md` and `red-overload-design.md` stand except where this file changes them. Only changed modules are listed.

`foreman/run.py`: the one process runner. Every child starts in its own session; a timeout kills its whole process group and returns `TIMEOUT_CODE`. Replaces `foreman/preflight.py:365-382` and `foreman/cli.py:153-159`.

```python
def run(cmd: list[str], cwd: Path, timeout: float | None = None,
        env: dict[str, str] | None = None, stdin: str | None = None,
        shell: bool = False) -> Result: ...
    # start_new_session=True; on timeout os.killpg(pgid, SIGKILL), then reap
def spawn_detached(cmd: list[str]) -> int: ...
    # own session, stdio to /dev/null; returns the pid; for the exit watcher only
```

`foreman/config.py`: adds the timeout for every command that is not an agent, an optional key like `juror_rounds` (`foreman/config.py:46-47`).

```python
@dataclass(frozen=True)
class Config:
    ...                         # existing fields unchanged
    check_seconds: int = 600    # step check, group check, format, views, hooks, collection,
                                # red run; unmeasured starting value
    python_cmd: list[str] = field(default_factory=lambda: ["uv", "run", "python"])
                                # the target repo's interpreter, for verify.red
```

`foreman/verify.py`: the one judge of whether tests are red. Every command passes `cfg.check_seconds`. Replaces the RUN-record checks at `foreman/reports.py:117-132` and `foreman/reports.py:246-259`.

```python
@dataclass(frozen=True)
class RedRun:
    test_id: str
    outcome: str      # "assertion" | "not-implemented" | "not-raised" | "passed" | "error" | "missing"
    summary: str      # pytest's one-line longrepr summary, verbatim

def red(cfg: Config, wt: Path, ids: tuple[str, ...]) -> tuple[RedRun, ...]: ...
    # runs cfg.python_cmd with a pytest plugin over exactly ids, outcomes from
    # pytest_runtest_logreport, never from terminal text; an id not collected is "missing"
def verify(cfg: Config, wt: Path) -> Verdict: ...           # format_cmd, check_cmd under check_seconds
def check_step(cfg: Config, step: Step, wt: Path) -> Verdict | None: ...   # under check_seconds
```

`foreman/reports.py`: reads a red report's claims only: each `BEHAVIOR` id with its number, and each note. A note never rejects the report. `_REJECTED` and `_OVERLOADED` are the only refusal markers.

```python
@dataclass(frozen=True)
class RedClaims:
    ids: tuple[tuple[int, str], ...]     # (behavior number, test id), report order
    notes: tuple[str, ...]               # LEFT, UNTESTABLE and CONTRADICTS lines, verbatim

def red(text: str) -> RedClaims: ...
    # ConfigError only for: no SHAPE red .. END TESTS block, a malformed BEHAVIOR line,
    # an invalid or duplicate test id, or a refusal marker inside the block
```

`foreman/events.py`: every stop is one `stop` event with a kind, and every dispatch attempt is kept.

```python
STOP_KINDS = ("timeout", "infrastructure", "steering", "overload", "reviewer refused",
              "findings", "owner", "evidence", "no change", "killed", "crashed")

def stopped(self, group_id: str | None, kind: str, exit_code: int, text: str) -> None: ...
    # EventLog method: one "stop" event; kind outside STOP_KINDS is ValueError
def keep_report(self, name: str, text: str) -> Path: ...
    # EventLog method: writes <name>-attempt-<n>.txt, n from 1 counted from kept files,
    # and <name>.txt as the latest; returns the attempt's path
```

`foreman/errors.py`: `Escalated` goes. Each `ForemanError` subclass names its stop kind.

```python
class ForemanError(Exception):
    exit_code: int = 1
    kind: str = "infrastructure"     # a member of events.STOP_KINDS
```

`foreman/cli.py`: writes exactly one `stop` event for every exit that is not a success: a `ForemanError` with its `kind`, a SIGTERM or SIGHUP as `killed`, any other exception as `crashed`. The exit watcher starts through `run.spawn_detached`.

`foreman/preflight.py`: `run_hook` runs through `run.run(..., shell=True, timeout=cfg.check_seconds)`; `HOOK_SECONDS` goes. `foreman/judge.py`: `ASK_SECONDS` goes; `default_ask` passes `cfg.dispatch_seconds`.

`foreman/loop.py`:

```python
def _rule(log: EventLog, group: Group, wt: Path, kept: str, why: str,
          kind: str) -> RulingNeeded: ...
    # notify_dispatcher("ruling_needed", ...) as today; the cli writes the stop event
def _dispatch(...) -> reports.Report: ...
    # unchanged signature; keeps the agent's output through log.keep_report
def _red_evidence(cfg: Config, step: Step, name: str, wt: Path,
                  log: EventLog) -> tuple[reports.RedEvidence, ...]: ...
    # reports.red over every part; verify.red over the claimed ids;
    # accepted when every claimed id is red ("assertion", "not-implemented", "not-raised")
    # and every behavior number has an id or a note; notes kept as <name>-notes
    # and added to the reports of any judgment on the step or its builds
def _refused(cfg: Config, group: Group, index: int, wt: Path, log: EventLog,
             backend: Backend, report: reports.Report, kept: str) -> None: ...
    # the one handler of a step's refused report; the Refusals table says what it does
```

A claimed id that is not red, or a behavior with neither id nor note, goes to `_answer` as the `red report` row does today. `run_upheld` answers a refused batch through `_round` the way `_review` answers a reviewer (`foreman/loop.py:1118-1124`); `_refusal_stops` goes.

#### Refusals

| Report or event | Handling |
|---|---|
| `REJECTED: STEERING` from any step | `_rule(..., kind="steering")`, naming the kept attempt and the quoted lines; no rerun, no juror; replaces the `refused` row of `brief-forms-design.md:223` |
| `OVERLOADED:` from a red step | `overload.cut_red` again with `behavior_limit` set to half the refused part's behavior count, rounded down, at least 1; the parts dispatch as a cut red step does; a part of one behavior refused `OVERLOADED:` stops with `kind="overload"` |
| `OVERLOADED:` from any other step | `_rule(..., kind="overload")` |
| `cut_red` refuses before dispatch | `_rule(..., kind="overload")` naming every `Overload.line()` and the step; nothing is dispatched; replaces the whole-form dispatch at `foreman/loop.py:455-461` |

## Homes

| Concern | Home | Forbidden elsewhere |
|---|---|---|
| Process running and cleanup | `foreman/run.py`: `run` with process-group kill, `spawn_detached` | importing `subprocess`; `os.killpg`; a child left in Foreman's process group |
| Command timeouts | `Config.dispatch_seconds` for agents, `Config.check_seconds` for check, format, view, hook, collection and red-run commands | a literal seconds value; one of those commands run with no timeout |
| Whether tests are red | `verify.red` over claimed ids, and `verify.check_step` | reading red outcomes, summaries or `RUN` lines from an agent's report |
| Red report claims | `reports.red` returning `RedClaims` | rejecting a report for a note; a second reader of `BEHAVIOR` lines |
| Refusal markers | `reports._REJECTED`, `reports._OVERLOADED` | the literal strings anywhere else in `foreman/` |
| A step's refused report | `loop._refused`, per the Refusals table | rerunning an identical refused brief; a juror asked about a refusal |
| Rerun counting | `loop._round` for steps, reviewers and finding batches | `_refusal_stops`; a second counter |
| Stop recording | `EventLog.stopped`, called only from `cli`, with `ForemanError.kind` or `killed`/`crashed` | an exit with no `stop` event; a stop kind outside `STOP_KINDS`; the alert file as the only record |
| Kept dispatch output | `EventLog.keep_report`, called only from `loop._dispatch` | `log.keep` of an agent's output; overwriting an earlier attempt |
| Test helpers | `tests/conftest.py`: `make_step`, `make_red`, `make_group`, `red_report`, `verdict_text` | `Step(`, `Group(`, `SHAPE red` or `VERDICT ` text built in a test file |

```python
# tests/conftest.py
def red_report(*behaviors: tuple[int, str], notes: tuple[str, ...] = ()) -> str: ...
    # a complete test-writer red report claiming each (number, id), with notes
def verdict_text(kind: str = "build wrong", action: str = "retry", step: str | None = "s1",
                 reasons: tuple[str, ...] = ("r",), evidence: tuple[str, ...] = ("a.py:1 | c",),
                 failures: tuple[str, ...] = ()) -> str: ...
```

## Order

`builder` is the roster agent whose writes are `foreman/`, `tests/`, `pyproject.toml`, `Makefile` and `foreman.example.toml` (`docs/superpowers/specs/harness-core-design.md:178`). Steps land in order on one branch; each is one dispatch with one done-check.

| Step | Paths | Agent | Done-check |
|---|---|---|---|
| 1 | `tests/conftest.py`, `tests/test_reports.py`, `tests/test_preflight.py`, `tests/test_loop.py` | `builder` | `uv run pytest -q tests/test_reports.py tests/test_preflight.py tests/test_loop.py` |
| 2 | `tests/test_preflight_hooks.py`, `tests/test_steps.py`, `tests/test_views.py`, `tests/test_verify.py` | `builder` | `uv run pytest -q tests/test_preflight_hooks.py tests/test_steps.py tests/test_views.py tests/test_verify.py` |
| 3 | `foreman/run.py`, `tests/test_run.py` | `builder` | `uv run pytest -q tests/test_run.py` |
| 4 | `foreman/config.py`, `foreman.example.toml`, `tests/conftest.py`, `tests/test_config.py` | `builder` | `uv run pytest -q tests/test_config.py` |
| 5 | `foreman/verify.py`, `foreman/views.py`, `tests/test_verify.py`, `tests/test_views.py` | `builder` | `uv run pytest -q tests/test_verify.py tests/test_views.py` |
| 6 | `foreman/preflight.py`, `foreman/judge.py`, `tests/test_preflight_hooks.py`, `tests/test_judge.py` | `builder` | `uv run pytest -q tests/test_preflight_hooks.py tests/test_judge.py` |
| 7 | `foreman/events.py`, `foreman/errors.py`, `tests/test_events.py` | `builder` | `uv run pytest -q tests/test_events.py` |
| 8 | `foreman/cli.py`, `tests/test_cli.py` | `builder` | `uv run pytest -q tests/test_cli.py` |
| 9 | `foreman/reports.py`, `tests/test_reports.py` | `builder` | `uv run pytest -q tests/test_reports.py` |
| 10 | `foreman/loop.py`, `tests/test_loop.py` | `builder` | `uv run pytest -q tests/test_loop.py` |
| 11 | `foreman/loop.py`, `tests/test_loop.py` | `builder` | `uv run pytest -q tests/test_loop.py` |

- Steps 1 and 2 move tests onto the conftest helpers and change no assertion.
- Step 5 passes `cfg.check_seconds` from `verify` and `views.make`, changes `check_step` to take `cfg`, and adds `verify.red`.
- Step 7 adds `STOP_KINDS`, `stopped`, `keep_report` and `ForemanError.kind`, and removes `Escalated`.
- Step 10: `_rule` takes a kind at every call; `_dispatch` keeps through `keep_report`; `_red_evidence` reads claims and runs `verify.red`.
- Step 11: `_refused` and the Refusals table; `run_upheld` through `_round`; `_refusal_stops` goes.

## Decisions for the owner

5. **The seven lead and role-prompt stops** (`docs/research/stops-g7.md:31-46`, rows 12, 15, 19, 23, 25, 26, 28). Recommend: a separate design over the roles and dispatch rules, since none of it is in `foreman/`; this design then leaves those stops as they are. Alternative: widen this design to the role prompts; it then needs `role-author` steps and paths this survey did not read.
