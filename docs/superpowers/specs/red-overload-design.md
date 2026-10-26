Status: proposed, for the owner's approval.
The design is approved for build.

## Why

Rule 7.1f (`docs/research/dispatch-rules.md:2`) refuses a brief with more than one done-check or commit, work past the dispatch cap, more than about a page, more than five files in scope, a mandated heavy run, or a missing ruling. Foreman renders a red brief and dispatches it with no check of the rendered text (`foreman/loop.py:247-256`). 7.1f holds only in pieces, 6 places:

- Five files: `MAX_PATHS` and its load lint (`foreman/steps.py:11`, `foreman/steps.py:187-190`), read again by the batch cutter (`foreman/batches.py:77`). A sixth path fails load with exit 2 (`tests/test_cli.py:91-99`).
- One done-check: `Step.check` is one argv (`foreman/steps.py:20`, `foreman/steps.py:197-202`). A red form has no command field (`foreman/forms.py:50-55`) and renders no command line (`foreman/brief.py:22-34`), so a red brief cannot mandate a run.
- Dispatch cap: the backend timeout `Config.dispatch_seconds` (`foreman/config.py:28`) stops an overrun after the fact (`foreman/loop.py:80-81`). Nothing predicts it. The one red step cut for size so far was cut by hand, into steps 8a-8e of 3, 1, 3, 6 and 5 behaviors (`queues/m2-g7.toml:97-222`).
- Missing ruling: the agent-judged `judge.check_rules` (`foreman/judge.py:214-236`).
- The agent's own refusal: `OVERLOADED:` read at `foreman/reports.py:14`, stopped as `RulingNeeded` at `foreman/loop.py:82-83`.
- `OverloadedStep` (`foreman/errors.py:14-17`), exit 3, is raised nowhere.

The page test has 0 homes. No module measures a brief, and none cuts a red brief along its behaviors.

Copies the pieces touch: test files build their own red steps, other steps and groups instead of using `make_step` and `make_group` (`tests/conftest.py:165`, `tests/conftest.py:181`). There are 7: `tests/test_preflight.py:45`, `tests/test_preflight.py:50`, `tests/test_preflight.py:54`, `tests/test_judge.py:19`, `tests/test_judge.py:24`, `tests/test_judge.py:30`, `tests/test_judge.py:35`.

Before a red brief is dispatched, Foreman checks its rendered text against the measurable parts of 7.1f: five files, one page, and a red brief's behavior count as the measure of the dispatch cap. When it fails only the page or behaviors test, Foreman cuts the brief into consecutive runs of whole behaviors. Each run passes the check and goes to its own fresh dispatch, and their reports together are the step's red evidence. When no cut passes, Foreman refuses before any dispatch. The tests for this land red before the module is built.

## Modules

The modules and homes of `harness-core-design.md`, `group-run-design.md`, `test-wrong-design.md` and `brief-forms-design.md` stand except where this file changes them. Only changed and new modules are listed.

`foreman/overload.py` (new): Foreman's 7.1f check on a brief it is about to dispatch, and the cut of a red brief. It imports `forms`, `brief` and `errors` only. It replaces `steps.MAX_PATHS` (`foreman/steps.py:11`). Files in scope are the step's written paths, `Step.paths` (`foreman/steps.py:19`); views do not count. Foreman predicts no run time: the behaviors test is its measure of the dispatch cap, and `Config.dispatch_seconds` stays the stop.

```python
MAX_FILES = 5   # 7.1f: more than five files in scope

@dataclass(frozen=True)
class Overload:
    test: str    # "files" | "page" | "behaviors"
    found: int
    limit: int
    def line(self) -> str: ...   # "OVERLOADED: <test> <found>, more than <limit>"

@dataclass(frozen=True)
class Cut:
    parts: tuple[forms.Red, ...]      # dispatched in order; empty when refused
    refused: tuple[Overload, ...]     # empty when parts is not

def files(paths: list[str]) -> Overload | None: ...       # len(paths) > MAX_FILES
def page(text: str, limit: int) -> Overload | None: ...   # len(text) > limit, in characters
def behaviors(form: forms.Red, limit: int) -> Overload | None: ...   # len(form.behaviors) > limit
def check(text: str, paths: list[str], page_limit: int) -> list[Overload]: ...
def cut_red(form: forms.Red, paths: list[str], view_paths: dict[str, str],
            page_limit: int, behavior_limit: int) -> Cut: ...
    # renders through brief.render. Passing: parts == (form,).
    # Failing only page, behaviors or both: behaviors taken in order, each part grows while
    # its render passes page and its count stays within behavior_limit,
    # and every part keeps form.origin.
    # Refused: files fails, or a single behavior's own render fails page;
    # refused names every failing Overload.
```

`foreman/config.py`: adds the page and behaviors limits, optional keys like `juror_rounds` (`foreman/config.py:90`, `foreman/config.py:101-105`). A config without a key loads with its default. A bool, a non-integer or a value below 1 is a `ConfigError`.

```python
@dataclass(frozen=True)
class Config:
    ...                       # existing fields unchanged
    page_chars: int = 3000    # 7.1f "about a page": characters of a rendered brief; unmeasured starting value
    red_behaviors: int = 6    # 7.1f dispatch cap: behaviors per red brief; unmeasured starting value,
                              # the largest red step in queues/m2-g7.toml (8d)
```

`foreman/steps.py`: drops `MAX_PATHS`. `lint`'s five-file problem comes from `overload.files(s.paths)` and keeps its current text. `foreman/batches.py:77` reads `overload.MAX_FILES`.

`foreman/preflight.py`: adds the early report, in `CHECKS` after `check_verbatim` (`foreman/preflight.py:645-651`). `"dispatcher"` joins the fixers.

```python
def check_overload(step: Step, group: Group, cfg: Config) -> list[Finding]: ...
    # red steps only: cut_red(step.form, step.paths, {s: str(views.path(cfg, group.id, s)) for s in step.views},
    # cfg.page_chars, cfg.red_behaviors); one Finding(step.id, "overload", o.line(), "dispatcher") per refused Overload
```

`foreman/loop.py`: `_step` cuts a red form before its snapshot and dispatch. Refused: `_rule` raises `RulingNeeded`, naming every `Overload.line()` and the step, before any dispatch. `--override-preflight` passes the preflight finding (`foreman/cli.py:91-94`) and never this refusal; an owner's order for an oversize step is a queue change. One part: dispatch as today, under `<group>-<step>`. Several parts:

- The whole render is kept as `<group>-<step>-brief`.
- Each part goes through `_dispatch` as `<group>-<step>-part-<k>`, `k` from 1.
- `<group>-<step>-parts` keeps the parts' behavior counts as JSON before the first dispatch, and `[n]` for one part.
- The out-of-paths check (`foreman/loop.py:257-270`) runs once against the snapshot taken before the first part. `_check_step` runs once after the last part.

Non-red forms are not cut. The one reader of a red step's evidence replaces the three direct reads at `foreman/loop.py:120`, `foreman/loop.py:280` and `foreman/loop.py:637`. `_judgment` (`foreman/loop.py:339-347`) takes a red candidate's report paths from `_parts`.

```python
def _parts(name: str, log: EventLog) -> list[tuple[str, int]]: ...
    # each kept report name of a red step with its behavior count, in order;
    # a missing <name>-parts reads as [(name, len(behaviors))]
def _red_evidence(step: Step, name: str, log: EventLog) -> tuple[reports.RedEvidence, ...]: ...
    # reports.red of every part; each part's behavior numbers checked against its own count,
    # then offset by the earlier parts' counts; test files checked against step.paths
```

`foreman/errors.py`: `OverloadedStep` and exit 3 go. Foreman's refusal is `RulingNeeded`, like the agent's own (`docs/superpowers/specs/group-run-design.md:194`).

`tests/conftest.py`: adds the red-step builder. Replaces `tests/test_preflight.py:45` and `tests/test_judge.py:19`. `make_step` and `make_group` replace `tests/test_preflight.py:50`, `tests/test_preflight.py:54`, `tests/test_judge.py:24`, `tests/test_judge.py:30` and `tests/test_judge.py:35`.

```python
def make_red(sid: str = "r1", texts: tuple[str, ...] = ("A behavior.",),
             paths: list[str] | None = None, source: str = "gauntlet/core/a.py",
             **fields: object) -> Step: ...
    # role "test-writer", forms.Red with one Behavior(text, source) per text, paths ["tests/test_a.py"],
    # check [], views [source]; fields override any of them
```

## Homes

| Concern | Home | Forbidden elsewhere |
|---|---|---|
| 7.1f check on a brief Foreman dispatches, and the cut of a red brief | `foreman/overload.py` | measuring a brief's length; cutting a red form; dispatching a red brief that `cut_red` has not passed |
| Five-file limit | `overload.MAX_FILES`, through `overload.files` over `Step.paths` | `steps.MAX_PATHS`; a literal 5 for a file count; counting views as files |
| Page limit | `Config.page_chars`, counted in characters of the rendered brief | a literal page size; a line count |
| Dispatch-cap measure | `Config.red_behaviors`, through `overload.behaviors`; `Config.dispatch_seconds` as the stop | a literal behavior count; a run-time prediction |
| Foreman's refusal | `_rule` raising `RulingNeeded`, with `notify_dispatcher("ruling_needed", ...)` naming each `Overload.line()` | `OverloadedStep`, exit 3, or any other exception, exit code or signal for it; a flag that lets a refused red brief dispatch |
| Early overload report | `preflight.check_overload` in `CHECKS` | another queue-time overload check |
| Red evidence of a step, split or not | `loop._red_evidence` over `loop._parts` | `reports.red` called on a red step's kept report anywhere else in `loop` |
| One done-check, no mandated run | `Step.check` as one argv (`foreman/steps.py:197-202`); no command field in `forms.Red` | a check, command or gate line in a red form or its render |
| Missing ruling | `judge.check_rules` and the agent's own `OVERLOADED:` refusal | Foreman deciding mechanically that a ruling is missing |
| Test helpers | `tests/conftest.py`: `make_red`, `make_step`, `make_group` | `Step(` or `Group(` in `tests/test_preflight.py` or `tests/test_judge.py` |

## Order

`builder` is the roster agent whose writes are `foreman/`, `tests/`, `pyproject.toml`, `Makefile` and `foreman.example.toml` (`docs/superpowers/specs/harness-core-design.md:178`). The steps land in order on one branch. Each step is one dispatch with one done-check.

| Step | Paths | Agent | Done-check |
|---|---|---|---|
| 1 | `tests/conftest.py`, `foreman/overload.py`, `tests/test_overload.py` | `builder` | `uv run pytest -q tests/test_overload.py` exits 1, and every failure is `NotImplementedError` |
| 2 | `foreman/overload.py` | `builder` | `uv run pytest -q tests/test_overload.py` passes |
| 3 | `foreman/steps.py`, `foreman/batches.py`, `tests/test_steps.py`, `tests/test_batches.py` | `builder` | `uv run pytest -q tests/test_steps.py tests/test_batches.py` |
| 4 | `foreman/config.py`, `foreman.example.toml`, `tests/conftest.py`, `tests/test_config.py` | `builder` | `uv run pytest -q tests/test_config.py` |
| 5 | `tests/test_preflight.py`, `tests/test_judge.py` | `builder` | `uv run pytest -q tests/test_preflight.py tests/test_judge.py` |
| 6 | `foreman/preflight.py`, `tests/test_preflight.py` | `builder` | `uv run pytest -q tests/test_preflight.py` |
| 7 | `foreman/loop.py`, `tests/test_loop.py` | `builder` | `uv run pytest -q tests/test_loop.py` |
| 8 | `foreman/errors.py` | `builder` | `make check` |

Step 1 is the red step:

- `make_red` is real.
- In `foreman/overload.py`, `MAX_FILES`, `Overload` and `Cut` are real, and every function body raises `NotImplementedError`.
- `tests/test_overload.py` holds the behaviors of `files`, `page`, `behaviors`, `check` and `cut_red`.

Step 4 adds `page_chars` and `red_behaviors` to `Config` and `foreman.example.toml`.

Step 5 moves the builders onto the conftest helpers and changes no assertion.
