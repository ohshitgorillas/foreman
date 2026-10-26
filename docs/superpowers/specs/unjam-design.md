Status: approved.

## Why

The g7 build stopped 28 times; 18 of the 22 that can be judged had no defect behind them, and 6 cannot be judged because nothing was kept (`docs/research/stops-g7.md:9`). Pre-flight stops (`docs/research/stops-g7.md:36-43`) warn and go on (`foreman/cli.py:95-100`). Seven stops sit in leads and role prompts, outside `foreman/` (`docs/research/stops-g7.md:31-46`, rows 12, 15, 19, 23, 25, 26, 28). The causes inside Foreman, and where each is answered:

- **Red paperwork, not the tests, gated a red step.** Stops 1, 6, 9 and 10 were tests red as intended (`docs/research/stops-g7.md:15`, `:20`, `:23`, `:24`). The step check (`foreman/verify.py:107-113`, called at `foreman/loop.py:668`) and `verify.red` (`foreman/verify.py:71-95`, called at `foreman/loop.py:340`) judge red; `reports.red` reads claims only (`foreman/reports.py:119-162`).
- **A refusal was answered with the same brief.** Stops 3, 4, 14 and 28 (`docs/research/stops-g7.md:17-18`, `:33`, `:46`). `_refused` handles a step's refusal (`foreman/loop.py:125-189`); a red brief the cut refuses stops before dispatch (`foreman/loop.py:581-595`).
- **Kept reports were overwritten.** Stops 5 and 8 (`docs/research/stops-g7.md:19`, `:22`). `keep_report` keeps every attempt (`foreman/events.py:102-109`), called from `_dispatch_kept` (`foreman/loop.py:105-106`).
- **A refused finding batch stopped outright.** `run_upheld` answers it through `_round` (`foreman/loop.py:1448-1468`, `foreman/loop.py:192-208`).

Copies of shared concerns left in the tree, 3, all test helpers outside `tests/conftest.py:170-262`: red report text at `tests/test_reports.py:108-113` and `tests/test_preflight.py:87`; verdict text at `tests/test_cli.py:31-34`.

## Modules

The modules and homes of `harness-core-design.md`, `group-run-design.md`, `test-wrong-design.md`, `brief-forms-design.md` and `red-overload-design.md` stand except where this file changes them. Only changed modules are listed.

`foreman/run.py`: the one process runner. Every child starts in its own session; a timeout kills its whole process group and returns `TIMEOUT_CODE`. Replaces every other process runner; its callers include `run_hook` (`foreman/preflight.py:362`) and the exit watcher (`foreman/cli.py:191`).

```python
def run(cmd: list[str], cwd: Path, timeout: float | None = None,
        env: dict[str, str] | None = None, stdin: str | None = None,
        shell: bool = False) -> Result: ...
    # start_new_session=True; on timeout os.killpg(pgid, SIGKILL), then reap
def spawn_detached(cmd: list[str]) -> int: ...
    # own session, stdio to /dev/null; returns the pid; for the exit watcher only
```

`foreman/config.py`: adds the timeout for every command that is not an agent, an optional key like `juror_rounds` (`foreman/config.py:30`, `foreman/config.py:52`).

```python
@dataclass(frozen=True)
class Config:
    ...                         # existing fields unchanged
    check_seconds: int = 600    # step check, group check, format, views, hooks, collection,
                                # red run; unmeasured starting value
    python_cmd: list[str] = field(default_factory=lambda: ["uv", "run", "python"])
                                # the target repo's interpreter, for verify.red and
                                # capture_repair's collection
```

`foreman/verify.py`: the one judge of whether tests are red. Every command passes `cfg.check_seconds`. Replaces reading `RUN` records from an agent's report; `reports.red` (`foreman/reports.py:119-162`) reads none.

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
def capture_repair(cfg: Config, wt: Path, red_ids: tuple[str, ...]) -> RepairSnapshot: ...
    # collects through cfg.python_cmd, never sys.executable; signature unchanged
```

`foreman/reports.py`: reads a red report's claims only: each `BEHAVIOR` id with its number, and each note. A note never rejects the report. Every parser in `reports.py` skips the lines it does not read, `IGNORED` and `CHOSEN` lines among them, and never rejects a report for carrying one. `_REJECTED` and `_OVERLOADED` are the only refusal markers.

```python
@dataclass(frozen=True)
class RedClaims:
    ids: tuple[tuple[int, str], ...]     # (behavior number, test id), report order
    notes: tuple[str, ...]               # LEFT, UNTESTABLE and CONTRADICTS lines, verbatim

def red(text: str) -> RedClaims: ...
    # ConfigError only for: no SHAPE red .. END TESTS block, a malformed BEHAVIOR line,
    # an invalid or duplicate test id, or any line in the block starting _REJECTED
    # or _OVERLOADED; a block with no BEHAVIOR lines gives empty ids
```

`foreman/events.py`: every stop is one `stop` event with a kind, and every dispatch attempt is kept.

```python
STOP_KINDS = ("timeout", "infrastructure", "steering", "overload", "reviewer refused",
              "findings", "owner", "evidence", "no change", "killed", "crashed")

def stopped(self, group_id: str | None, kind: str, exit_code: int, text: str) -> None: ...
    # EventLog method: one "stop" event; kind outside STOP_KINDS is ValueError
def keep_report(self, name: str, text: str) -> Path: ...
    # EventLog method: writes <name>-attempt-<n>.txt, n from 1 counted from kept files,
    # and <name>.txt as the latest; returns the attempt's path. An agent's stdout is
    # kept as <name>, its stderr as <name>-stderr, each with attempt files
```

`foreman/errors.py`: `Escalated` goes. Each `ForemanError` subclass names its stop kind.

```python
class ForemanError(Exception):
    exit_code: int = 1
    kind: str = "infrastructure"     # a member of events.STOP_KINDS
```

`foreman/cli.py`: writes exactly one `stop` event for every exit that is not a success once `config.load` has returned: a `ForemanError` with its `kind`, a non-zero `SystemExit` as `killed`, any other exception, `KeyboardInterrupt` included, as `crashed`. The only source of a non-zero `SystemExit` during a run is the SIGTERM and SIGHUP handler, which exits `128 + signal`. An exit before `config.load` returns, a missing or unreadable `foreman.toml` among them, writes no `stop` event: there is no state directory. The event's group is the one the command names (`run --group`, `upheld`, `retest`, `resume`); a whole-section `run` records `None`. The exit watcher starts through `run.spawn_detached`.

`foreman/preflight.py`: `run_hook` runs through `run.run(..., shell=True, timeout=cfg.check_seconds)`; `HOOK_SECONDS` goes. `foreman/judge.py`: `ASK_SECONDS` goes; `default_ask` passes `cfg.dispatch_seconds`.

`foreman/loop.py`:

```python
def _rule(log: EventLog, group: Group, wt: Path, kept: str, why: str,
          kind: str) -> RulingNeeded: ...
    # ValueError for a kind outside STOP_KINDS; notify_dispatcher("ruling_needed", ...);
    # the cli writes the stop event with the returned error's kind
def _dispatch_kept(group: Group, wt: Path, log: EventLog, backend: Backend, name: str,
                   brief_text: str, role: str, *,
                   load: int | None = None) -> tuple[reports.Report, Path]: ...
    # keeps stdout and stderr through log.keep_report; returns the report and the
    # -attempt-<n>.txt path keep_report returned for stdout
def _dispatch(...) -> reports.Report: ...
    # unchanged signature; _dispatch_kept(...)[0]
def _red_claims(step: Step, name: str,
                log: EventLog) -> tuple[tuple[int, str], ...]: ...
    # (behavior number, test id) over every part, behavior numbers offset by part;
    # runs nothing. ConfigError when a behavior number has neither an id nor a note
    # covering it, or a test id is claimed in two parts. Keeps the notes as
    # <name>-notes on every call; they are added to the reports of any judgment on
    # the step or its builds
def _red_evidence(cfg: Config, step: Step, name: str, wt: Path,
                  log: EventLog) -> tuple[tuple[int, str], ...]: ...
    # _red_claims, then verify.red over the claimed ids; ConfigError unless every id
    # is red ("assertion", "not-implemented", "not-raised"). Called only when the
    # step's red report is accepted, before any build runs
def _refused(cfg: Config, group: Group, index: int, wt: Path, log: EventLog,
             backend: Backend, report: reports.Report, reported: str,
             kept: str) -> None: ...
    # the one handler of a step's refused report, per the Refusals table; `reported`
    # is the dispatched name (<name> or <name>-part-<k>) and finds the refused part;
    # returns only after a red recut, having rewritten <name>-parts; raises otherwise
```

A note covers behavior `n` when it starts `LEFT n`, `UNTESTABLE n` or `CONTRADICTS n`; a note with no number covers nothing. `_current_red_evidence` and `_repair` read claims through `_red_claims` and never run `verify.red`.

A claimed id that is not red, or a behavior with neither id nor note, goes to `_answer` as the `red report` row does. `run_upheld` answers a refused batch through `_round` the way `_review` answers a reviewer (`foreman/loop.py:1280-1291`); past `answer_rounds` a batch stops as `overload` when its last report is overloaded, else as `steering`.

`_step` dispatches a cut red step's parts from the counts in `<name>-parts`, re-reading them after each refused part: after a recut the sub-parts take the refused part's position, and the later parts follow them.

#### Kind at each `_rule` call

| Kind | Calls |
|---|---|
| `timeout` | a dispatch overrun (`foreman/loop.py:121`); a step check timeout (`foreman/loop.py:696`); a group check timeout (`foreman/loop.py:1244`) |
| `evidence` | unresolved red-producer evidence (`foreman/loop.py:378`, `foreman/loop.py:575`) |
| `reviewer refused` | the reviewer refusal loop past `answer_rounds` (`foreman/loop.py:1288`) |
| `no change` | nothing changed under the group's paths (`foreman/loop.py:1304`) |
| `infrastructure` | a kept-text read failure; a view failure; a judgment parse error |
| `steering` | every other call, matching `RulingNeeded.kind` (`foreman/errors.py:25`) |

#### Refusals

Every refusal stop, of a step, a red part, a reviewer or an upheld batch, names the kept `<name>-attempt-<n>.txt` file `_dispatch_kept` returned, never `<name>.txt`.

| Report or event | Handling |
|---|---|
| `REJECTED: STEERING` from any step | `_rule(..., kind="steering")`, naming the kept attempt and quoting its non-blank lines, joined and capped at 500 characters; no rerun, no juror; replaces the `refused` row of `brief-forms-design.md:223` |
| `OVERLOADED:` from a red step | `overload.cut_red` again with `behavior_limit` set to half the refused part's behavior count, rounded down, at least 1; `_refused` rewrites `<name>-parts` and returns, and `_step` dispatches the parts; a part of one behavior refused `OVERLOADED:`, or a recut that refuses, stops with `kind="overload"` |
| `OVERLOADED:` from any other step | `_rule(..., kind="overload")` |
| `cut_red` refuses before dispatch | `_step` stops with `_rule(..., kind="overload")` naming every `Overload.line()` and `<name>-brief.txt`; nothing is dispatched |

## Homes

| Concern | Home | Forbidden elsewhere |
|---|---|---|
| Process running and cleanup | `foreman/run.py`: `run` with process-group kill, `spawn_detached` | importing `subprocess`; `os.killpg`; a child left in Foreman's process group |
| Command timeouts | `Config.dispatch_seconds` for agents, `Config.check_seconds` for check, format, view, hook, collection and red-run commands | a literal seconds value; one of those commands run with no timeout |
| Whether tests are red | `verify.red` over claimed ids, and `verify.check_step` | reading red outcomes, summaries or `RUN` lines from an agent's report |
| Target interpreter | `Config.python_cmd`, for every Python command run in the target repository | `sys.executable` in such a command |
| Red report claims | `reports.red` returning `RedClaims`, read across parts only by `loop._red_claims` | rejecting a report for a note, or for an `IGNORED` or `CHOSEN` line; a second reader of `BEHAVIOR` lines; a second reader of a step's parts' claims |
| Stop kind of a ruling | `loop._rule`'s `kind`, per the Kind at each `_rule` call table | setting `RulingNeeded.kind` anywhere else |
| Refusal markers | `reports._REJECTED`, `reports._OVERLOADED` | the literal strings anywhere else in `foreman/` |
| A step's refused report | `loop._refused`, per the Refusals table | rerunning an identical refused brief; a juror asked about a refusal |
| Rerun counting | `loop._round` for steps, reviewers and finding batches | `_refusal_stops`; a second counter |
| Stop recording | `EventLog.stopped`, called only from `cli` once `config.load` has returned, with `ForemanError.kind` or `killed`/`crashed`, and the group the command names or `None` | an exit after `config.load` returns with no `stop` event; a stop kind outside `STOP_KINDS`; a group the command did not name; the alert file as the only record |
| Kept dispatch output | `EventLog.keep_report`, called only from `loop._dispatch_kept`, for an agent's stdout and stderr | `log.keep` of an agent's output; overwriting an earlier attempt |
| Test helpers | `tests/conftest.py`: `make_step`, `make_red`, `make_group`, `red_report`, `verdict_text`; in `tests/test_loop.py`, an autouse fake of `verify.red` | `Step(`, `Group(`, `SHAPE red` or `VERDICT ` text built in a test file; a loop test that runs the real `verify.red` |

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
| 9 | `foreman/reports.py`, `foreman/loop.py`, `tests/test_reports.py`, `tests/test_loop.py` | `builder` | `uv run pytest -q tests/test_reports.py tests/test_loop.py` |
| 10 | `foreman/loop.py`, `tests/test_loop.py`, `tests/test_cli.py` | `builder` | `uv run pytest -q tests/test_loop.py tests/test_cli.py` |
| 11 | `foreman/loop.py`, `tests/test_loop.py` | `builder` | `uv run pytest -q tests/test_loop.py` |
| 12 | `foreman/verify.py`, `tests/test_verify.py` | `builder` | `uv run pytest -q tests/test_verify.py` |

- Steps 1 and 2 move tests onto the conftest helpers and change no assertion.
- Step 5 passes `cfg.check_seconds` from `verify` and `views.make`, changes `check_step` to take `cfg`, and adds `verify.red`.
- Step 7 adds `STOP_KINDS`, `stopped`, `keep_report` and `ForemanError.kind`, and removes `Escalated`.
- Step 9: `reports.red` returns `RedClaims`; every parser skips `IGNORED` and `CHOSEN` lines; `_red_claims` reads claims and notes across parts; `_red_evidence` runs `verify.red` at red acceptance; `_current_red_evidence` and `_repair` use `_red_claims`; `tests/test_loop.py` fakes `verify.red` autouse.
- Step 10: `_rule` takes a kind at every call, per the Kind at each `_rule` call table, and raises `ValueError` outside `STOP_KINDS`; `_dispatch_kept` keeps stdout and stderr through `keep_report` and returns the attempt path; `tests/test_cli.py::test_stop_writes_one_stop_event_with_its_kind` expects `"no change"`.
- Step 11: `_refused` and the Refusals table, with every refusal stop naming the kept attempt; `_step` dispatches recut parts from `<name>-parts`; `run_upheld` through `_round`; `_refusal_stops` goes.
- Step 12: `capture_repair` collects through `cfg.python_cmd`.

## Decisions for the owner

5. **The seven lead and role-prompt stops** (`docs/research/stops-g7.md:31-46`, rows 12, 15, 19, 23, 25, 26, 28). Recommend: a separate design over the roles and dispatch rules, since none of it is in `foreman/`; this design then leaves those stops as they are. Alternative: widen this design to the role prompts; it then needs `role-author` steps and paths this survey did not read.
