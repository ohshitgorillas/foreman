Status: proposed, for the owner's approval.

## Why

A builder that believes a red test wrong writes nothing more, puts the test on `TEST WRONG` and returns (`docs/research/gauntlet-roles/core-builder.md:49`, `docs/research/gauntlet-roles/core-builder.md:51`, `docs/research/gauntlet-roles/core-builder.md:59`, `docs/research/gauntlet-roles/seam-builder.md:57`, `docs/research/gauntlet-roles/gatesmith.md:44`, `docs/research/gauntlet-roles/gatesmith.md:49`). Each line is `<test path>::<name> | <why> | <docs or spec line>`, under an indented block (`docs/research/gauntlet-roles/core-builder.md:88-89`, `docs/research/gauntlet-roles/seam-builder.md:92-93`) or after a `TEST WRONG <n>:` header (`docs/research/gauntlet-roles/gatesmith.md:75`).

Three gaps, 0 copies:

- `reports.parse` reads `REJECTED`, `OVERLOADED` and `FINDINGS` and no `TEST WRONG` (`foreman/reports.py:26-42`). Its `FINDINGS` header pattern ends the line (`foreman/reports.py:14`), so gatesmith's `FINDINGS <n>:` header (`docs/research/gauntlet-roles/gatesmith.md:74`) goes unread too: same cause.
- `loop._steps` runs `verify.check_step` straight after the dispatch (`foreman/loop.py:61-68`), so a builder's `TEST WRONG` surfaces as `step <id> check missed`, or, for a step with no check, goes on to the group gate.
- The one command that continues a kept group is `upheld` (`foreman/cli.py:21-24`, `foreman/loop.py:152-196`), and it starts at the gate, never at a step.

Copies the pieces touch:

- Step and group builders in test files: `tests/test_loop.py:20`, `tests/test_loop.py:32`, `tests/test_reports.py:18`, `tests/test_reports.py:22`, `tests/test_steps.py:9`, `tests/test_steps.py:22`, `tests/test_views.py:8`, `tests/test_brief.py:7`, `tests/test_verify.py:49`: 9.
- The bare `origin` remote fixture: `tests/test_loop.py:37`, `tests/test_cli.py:24`: 2.
- Reading the file a CLI command names: `foreman/cli.py:54-57`, which `retest` would copy.

A builder's `TEST WRONG` stops the group for Void, the dispatcher. Void rules each line and hands back a brief for the test step that owns the tests; Foreman sends it verbatim, restores the stopped step's paths to what they held before it ran, and runs the group again from the stopped step.

## Modules

The modules and homes of `docs/superpowers/specs/harness-core-design.md` and `docs/superpowers/specs/group-run-design.md` stand except where this file changes them. Only changed modules are listed.

`foreman/errors.py`: adds the stop for a wrong test. Its name keeps pytest's `Test*` class collection off it. Replaces nothing.

```python
class WrongTest(ForemanError): exit_code = 8   # a builder reported TEST WRONG; Void, the dispatcher, rules and runs `retest`
```

`foreman/events.py`: adds the signal. Replaces nothing.

```python
DISPATCHER_SIGNALS = ("findings", "ruling_needed", "red_twice", "test_wrong")
```

`foreman/reports.py`: adds the `TEST WRONG` block and the routing of test ids. One private block reader serves `findings` and `test_wrong`. Replaces `foreman/reports.py:35-41`.

```python
@dataclass(frozen=True)
class Report:
    kind: str
    findings: list[str]                                        # lines under "FINDINGS <n>" or "FINDINGS <n>:", verbatim without indent
    test_wrong: list[str] = field(default_factory=list)        # lines under "TEST WRONG <n>" or "TEST WRONG <n>:", verbatim without indent

def _block(lines: list[str], header: str) -> list[str]: ...
    # the indented lines after the first "<header> <n>" line, a trailing ":..." on it allowed, up to the first blank or unindented line
def route_tests(lines: list[str], group: Group) -> tuple[dict[str, list[str]], list[str]]: ...
    # step id -> each line, verbatim, whose leading "<test path>::" steps.owner maps to it; and the lines whose test path no step owns
```

`foreman/git.py`: adds the snapshot and restore of a step's paths. Replaces nothing.

```python
def snapshot(wt: Path) -> str: ...
    # a tree object of every file in wt, tracked or untracked; wt's index, HEAD and files untouched
def restore(wt: Path, tree: str, paths: list[str]) -> None: ...
    # each file under paths set to its content in tree; a file under paths absent from tree removed
```

`foreman/loop.py`: adds the stop to `run_group` 2 and the continuation. Replaces nothing.

```python
def run_retest(cfg: Config, group: Group, text: str, log: EventLog, backend: Backend | None = None) -> str: ...
    # continues a group stopped by WrongTest: text, Void's brief for the test step, goes to it verbatim;
    # then the stopped step and every later step run again; returns and raises as run_group
```

`run_group` 2 becomes, for each step: views as before; `log.keep("<group id>-<step id>-before", git.snapshot(wt))`; `start`, `wait`, `reports.read`, `rejected` or `overloaded` raises `RulingNeeded`. Then, when `report.test_wrong` is set: `reports.route_tests(report.test_wrong, group)`; an unowned line, or lines owned by anything but exactly one step earlier in the group, raise `RulingNeeded`; otherwise `log.notify_dispatcher("test_wrong", group.id, ...)` naming the kept report, the stopped step, the test step and the worktree, then raise `WrongTest`. Only then `verify.check_step`. `_steps` takes the index of its first step, 0 from `run_group`. Only a step dispatch reads `test_wrong`; finding, upheld and review dispatches do not.

`run_retest`, in order:

1. `git.worktree(cfg, group.id)`.
2. The stopped step: the first step in group order whose kept `<group id>-<step id>` text, through `reports.parse`, has `test_wrong`; none raises `ConfigError`. The test step: its one owner under `reports.route_tests`.
3. When `text` is not empty: the test step's `views`, when set, through `views.make`, kept as `<group id>-<test id>-retest-views`, red raises `RulingNeeded`; `start(text, wt, test.role)`, kept as `<group id>-<test id>-retest`, read as in `run_group` 2; `verify.check_step(test, wt)`, kept as `<group id>-<test id>-retest-check`, red raises `RulingNeeded`.
4. `git.restore(wt, log.kept("<group id>-<stopped id>-before"), stopped.paths)`.
5. `run_group` 2 from the stopped step on, then `run_group` 3, 4 and 5. A second `TEST WRONG` stops again as `WrongTest`.

`foreman/cli.py`: adds `python -m foreman retest queues/<milestone>.toml GROUP FILE` (FILE holds Void's brief for the test step, verbatim; empty skips the fix), calling `loop.run_retest`. `upheld` and `retest` read FILE through one private `_read`, which raises `ConfigError` when FILE is unreadable. Replaces `foreman/cli.py:54-57`.

```python
def _read(path: Path) -> str: ...
```

`tests/conftest.py`: adds the step and group builders and the `origin` fixture. Replaces `tests/test_loop.py:20`, `tests/test_loop.py:32`, `tests/test_loop.py:37`, `tests/test_reports.py:18`, `tests/test_reports.py:22`, `tests/test_steps.py:9`, `tests/test_steps.py:22`, `tests/test_views.py:8`, `tests/test_brief.py:7`, `tests/test_verify.py:49`, `tests/test_cli.py:24`.

```python
def make_step(sid: str = "s1", paths: list[str] | None = None, **fields: object) -> Step: ...
    # role "builder", brief f"brief {sid}", paths ["a.py"], check [], expect_code 0, views []; fields override any of them
def make_group(*steps: Step, gid: str = "g1", **fields: object) -> Group: ...
    # design "design.md", after []; fields override either

@pytest.fixture
def origin(cfg: Config, tmp_path: Path) -> Path: ...   # a bare repository added as cfg.target_repo's origin remote
```

## Homes

| Concern | Home | Forbidden elsewhere |
|---|---|---|
| Reading agent text | `foreman/reports.py`, one `_block` for every report block | matching `TEST WRONG`, `FINDINGS` or a `<test path>::` test id anywhere else; a second block reader |
| Wrong-test stop | `WrongTest` in `foreman/errors.py`, signalled by `notify_dispatcher("test_wrong", ...)` | any other exception, exit code or signal for a builder's `TEST WRONG`; a `TEST WRONG` read as a missed step check |
| Ruling a test wrong, and the fix brief | Void, the dispatcher, handing the brief to `python -m foreman retest` | Foreman composing, wrapping or appending to a test-fix brief; Foreman dispatching a fix Void has not handed |
| Where a stopped group resumes | `loop.run_retest`, from the kept step reports | a step id taken from argv or kept in any other file |
| Snapshot and restore of a step's paths | `git.snapshot`, `git.restore`, the tree id kept through `log.keep` | git calls, copies or deletes of step paths in any other module |
| File a CLI command names | `cli._read` | `read_text` of a CLI argument anywhere else |
| Test helpers | `tests/conftest.py`: `make_step`, `make_group`, `origin` | `Step(`, `Group(` or a bare origin remote built in a test file, save in `tests/test_steps.py` tests of `load` and `lint` themselves |

## Order

`builder` is the roster agent whose writes are `foreman/`, `tests/`, `pyproject.toml`, `Makefile` and `foreman.example.toml` (`docs/superpowers/specs/harness-core-design.md:178`). Steps land in order on one branch; each step's done-check is its own test files, step 7 takes `make check`. Steps 1 and 2 move tests onto the conftest helpers and change no assertion.

| Step | Paths | Agent |
|---|---|---|
| 1 | `tests/conftest.py`, `tests/test_loop.py`, `tests/test_cli.py` | `builder` |
| 2 | `tests/test_reports.py`, `tests/test_steps.py`, `tests/test_views.py`, `tests/test_brief.py`, `tests/test_verify.py` | `builder` |
| 3 | `foreman/errors.py`, `foreman/events.py`, `tests/test_events.py` | `builder` |
| 4 | `foreman/reports.py`, `tests/test_reports.py` | `builder` |
| 5 | `foreman/git.py`, `tests/test_git.py` | `builder` |
| 6 | `foreman/loop.py`, `tests/test_loop.py` | `builder` |
| 7 | `foreman/cli.py`, `tests/test_cli.py` | `builder` |

## Decisions for the owner

1. Who writes the test-fix brief. Recommend: Void writes it in the test-writer's shape and Foreman carries it verbatim; cost: one brief by Void per round, time unmeasured. Alternative: Foreman composes a `repair` brief in `foreman/brief.py`; cost: `repair` keeps every claim and expected value byte for byte (`docs/research/gauntlet-roles/test-writer.md:42`), so it cannot fix a test that contradicts a document, and core-builder's `TEST WRONG` line carries no failure summary line (`docs/research/gauntlet-roles/core-builder.md:89`) where `repair` requires one (`docs/research/gauntlet-roles/test-writer.md:28`).
2. The stopped step's paths before it runs again. Recommend: `git.snapshot` before every step dispatch and `git.restore` before the rerun; cost: one snapshot per step, time unmeasured. Alternative: leave them as the builder left them; cost: a build left in place by a `TEST WRONG` after building (`docs/research/gauntlet-roles/core-builder.md:51`) makes the fresh builder's red run see passing tests, which it reports as `TEST WRONG` again (`docs/research/gauntlet-roles/core-builder.md:49`).
3. `TEST WRONG` lines owned by more than one earlier step. Recommend: `RulingNeeded`, so FILE is one brief for one test step; cost: a group with two red steps cannot use `retest` for both at once. Alternative: FILE carries one brief per test step; cost: a FILE format and its reader in `foreman/reports.py`.
4. gatesmith's `TEST WRONG <n>:` header. Recommend: read the indented lines after it, as for the other roles; cost: anything gatesmith writes after the colon on the header line is not read. Alternative: also split the header line's remainder into items; cost: its separator is undocumented (`docs/research/gauntlet-roles/gatesmith.md:75`), so the split is a guess.
5. A second `TEST WRONG` from the same step after `retest`. Recommend: it stops as `WrongTest` again, each round through Void; cost: no bound on rounds. Alternative: it raises `Escalated` with `red_twice`, as the gate does (`foreman/loop.py:94-99`); cost: a second, different wrong test also escalates.
