Status: proposed, for the owner's approval. Questions below block the affected shapes; this document does not amend a Gauntlet role.
The design is approved for build.

## Why

An unrestricted brief costs a dispatch per typo or extra line before `REJECTED: STEERING` reaches Foreman. Steps carry a closed form (`foreman/steps.py:17`) rendered by `brief.render` (`foreman/brief.py:10-69`). Two unchecked text paths remain, 2 copies: `brief.finding` (`foreman/brief.py:77-79`), which carries gate and upheld findings (`foreman/loop.py:234`, `foreman/loop.py:343`), and `brief.review` (`foreman/brief.py:82-87`), which reads the removed `Reviewer.spec`.

A second gap is the boundary between red and build. The read-only `~/dev/foreman/queues/m2.toml` sends all of `tests/test_core_errors.py` to step 3. Core-builder runs every test named and treats a passing test as `TEST WRONG` before building. Step 2 adds tests to an existing file; its old passing tests are not red evidence. M2 step 3e has the same problem in `tests/test_core_config.py`, and its consumers must select only their own new tests across the three files that step writes.

The non-juror forms follow their role contracts. This design defines the juror's job, brief and verdict grammar. The juror compares the approved specification, tests and failure evidence and identifies a spec defect, a test defect, an implementation defect or insufficient evidence. Its contract has one home in this design's Juror section; no existing juror contract supplies fields or verdict meanings.

The installed roles follow the current contracts: test-writer allows a red `design:` before the first behavior, required for a run taken from a design's Order; test-reviewer allows optional `spec:` paths; diff-reviewer allows scope only. Forms follow these contracts.

The unit of authoring becomes a form. The step file names a role, a shape and the fields of that shape. Foreman validates the whole file before making a worktree or dispatching any agent, then renders only those fields. A build references earlier red steps and receives their reported new pytest test ids, never a whole file merely because it contains new tests. Foreman neither invents cases nor decides which old test should be replaced.

Finding dispatch is cut by owning step, never by load, in 2 copies: the gate path (`foreman/loop.py:219-236`) and the upheld path (`foreman/loop.py:323-345`) each route lines through `reports.route` (`foreman/reports.py:196-211`) and send one `brief.finding` dispatch per owning step. Nothing records what a dispatch costs: `Result.seconds` is measured (`foreman/run.py:51-53`) and dropped after `backend.wait` (`foreman/loop.py:48-50`), and neither adapter reads a token count (`foreman/backends.py:63-67`, `foreman/backends.py:128-132`). Foreman cuts finding batches mechanically by measured load and logs every dispatch's load, wall time, tokens and outcome, so the budget is tuned from that record.

A builder's `TEST WRONG` goes to the dispatcher in 3 places: the stop in `_steps` (`foreman/loop.py:175-182`), through the `test_wrong` dispatcher signal (`foreman/events.py:12`) and `WrongTest` (`foreman/errors.py:44-47`); `run_retest`, which sends the dispatcher's verbatim brief to the test step (`foreman/loop.py:349-384`); and `retest` (`foreman/cli.py:25-30`, `foreman/cli.py:69-71`). A read-only juror replaces that adjudication. The existing continuation tests cover dispatcher-written fixes (`tests/test_loop.py:343`, `tests/test_loop.py:408`), and the report tests cover wrong-test blocks (`tests/test_reports.py:47`, `tests/test_reports.py:57`); these consumers move onto the shared judgment contract. There are 0 juror codecs in the dispatch renderer (`foreman/brief.py:19-63`) and report reader (`foreman/reports.py:152-174`).

Three more paths bypass judgment: an unexpected step-check result stops for the dispatcher (`foreman/loop.py:183-187`); a failed group check dispatches findings by path (`foreman/loop.py:214-236`); a repeated failed group check escalates (`foreman/loop.py:237-242`). All issues that prevent a green tree with tests and implementation together go to the same juror, who decides what is wrong. The check runner already distinguishes expected red from a missed check (`foreman/verify.py:28-33`) and reports formatter and group-check failures (`foreman/verify.py:18-25`); it remains the only check runner.

Dispatch timeout has 2 uncapped adapter calls (`foreman/backends.py:52`, `foreman/backends.py:118`), both using the existing process runner. Its timeout parameter and `TIMEOUT_CODE` are already the one timeout mechanism (`foreman/run.py:10`, `foreman/run.py:28-46`); there is no second timer or process runner to add. Config has no dispatch or batch limits (`foreman/config.py:17-27`); the shared config fixture and serializer must carry the new integer fields (`tests/conftest.py:37-48`, `tests/conftest.py:58-68`).

## Modules

The modules and homes of `harness-core-design.md`, `group-run-design.md` and `test-wrong-design.md` stand except where changed here. No new third-party dependency. Paths in this section are relative to this Foreman checkout.

`foreman/forms.py` (`foreman/forms.py:536-544`): frozen form values, the closed role/shape registry, parsing and structural validation. Replaces the unrestricted brief value, not the role's judgment of behavior or correctness. It has no subprocesses, source-code reader or renderer.

```python
def parse(role: str, shape: str, fields: object, *, where: str) -> Form: ...
    # closed tagged union; ConfigError identifies the table, field and violated rule
def validate_step(form: Form, paths: list[str], views: list[str]) -> list[str]: ...
def validate_group(group: Group) -> list[str]: ...
    # origins, earlier red-step references, scope/view relationships

@dataclass(frozen=True)
class Judgment:
    spec: str                  # explicit approved design path; required at judgment time
    steps: tuple[str, ...]     # candidate group step ids, in group order
    step: str                  # their approved-step blocks, in the same order
    red_ids: tuple[str, ...]   # relevant resolved ids; empty when the failure names none
    reports: tuple[str, ...]   # kept failure output and relevant agent/producer reports
    role: ClassVar[str] = "juror"
    shape: ClassVar[str] = "judgment"

def validate_judgment(form: Judgment) -> list[str]: ...
    # structural framing, distinct candidate steps, specification/report references and distinct red ids
```

`Form` means distinct immutable records for the shapes below, not a dictionary of optional strings. Origin, behavior, changed-document line, finding source and red-step selection have distinct records too. `Judgment` is the juror's form, built by `loop` from the failed step or combined group and never loaded from a step file; `Form` includes it. `Group` in the signature is a type-only reference; forms does not import the step loader at runtime.

`.claude/agents/juror.md` (new contract implementation): reads one judgment and returns an evidence-backed verdict without repository writes; replaces dispatcher adjudication at `foreman/loop.py:175-187`, automatic gate repair at `foreman/loop.py:214-242` and dispatcher-written retest briefs at `foreman/loop.py:349-384`.

```text
input: judgment envelope defined in the Juror section
output: one VERDICT/ACTION envelope defined in the Juror section
writes: none
```

`foreman/steps.py`: `Step.brief: str` becomes `Step.form: Form`. Each step requires `role`, `shape`, `form`, `paths`; `check`, `expect_code`, `views` retain their defaults and execution meaning. A group retains `steps`, `after`, `design`, and gains `reviews`, keyed by reviewer role, for the two reviewer forms below. `design` remains documentary metadata; it never implicitly becomes a brief line. `load` calls all form and existing graph/view/path checks before returning, including checks formerly left to the CLI's separate `lint` call. `lint` remains the shared validator for programmatically constructed groups. Invalid shape/field data raises `ConfigError`; CLI compatibility must not make a bad file dispatchable.

`foreman/brief.py`: the sole renderer for steps, reviews, finding batches and judgments. Replaces `finding(lines)` and `review(reviewer, group)` with rendering validated values. It uses `views.path` for a declared source's view and accepts resolved red ids as data. It never reparses a role file or adds group purpose, a spec, commands, report prose or standing lines.

```python
def render(form: Form, *, paths: list[str], view_paths: dict[str, str],
           red_ids: list[str], juror_reasons: tuple[str, ...] = ()) -> str: ...
    # reasons are runtime verdict data for a build retry only; never a queue suffix
```

`foreman/reports.py`: the sole reader of test-writer's red report, alongside its existing finding and wrong-test readers. Adds ordered red evidence: new test id, behavior number, and both `RUN red` / `RUN rerun` outcomes and summaries. Extract ids from `BEHAVIOR <n> | <test id> | ...`, not from every `RUN` line: file-wide runs include old passing tests. Require `SHAPE red` and terminal `END TESTS`, `BLOCKED none`, no `LEFT`, `UNTESTABLE` or `CONTRADICTS`, and one matching new id in each run with outcome `assertion` or `not-implemented` and identical summary text. The report's quoted `BRIEF` and `DIFF` are not sources of report records; read the report envelope, stopping structured-field interpretation at `DIFF`/`COMMANDS`. Ambiguous or duplicate records fail closed. The shape's report must be complete before accepting evidence. It also reads each finding's primary anchor as a worktree-relative path and line, and one juror verdict. `anchor` replaces `finding_anchor` (`foreman/reports.py:214-220`).

```python
def anchor(line: str, wt: Path) -> tuple[str, int] | None: ...
    # the primary path:line only (reviewer field two or a gate prefix), relative to wt; None without one

@dataclass(frozen=True)
class Verdict:
    kind: str                  # "spec wrong" | "tests wrong" | "build wrong" | "undetermined"
    action: str                # "owner" | "repair" | "redo red" | "retry"
    step: str | None           # candidate step to fix; None for owner actions
    reasons: tuple[str, ...]   # nonempty, ordered reasons
    evidence: tuple[str, ...]  # nonempty path:line | claim records
    failures: tuple[str, ...]  # required for repair, empty otherwise

def verdict(text: str) -> Verdict: ...
    # the Juror section's grammar; exactly one complete verdict, else ConfigError
    # repair requires failure summaries acceptable as test-writer's failure: evidence
```

`foreman/batches.py` (new): finding weights and batch cutting, mechanically, never by the dispatcher. A finding's weight is the line count of the innermost function or class enclosing its anchor, measured from the Python AST; a module-level anchor, a non-Python file or a file that does not parse weighs the whole file. Findings in one enclosing unit always share a batch. Batches never mix receiving roles or sources. Within a role, units keep the order of their first finding; each unit joins the current batch while its load stays within the budget and it has at most five distinct anchor files, else starts the next; a unit over budget goes alone. The file cap applies even to an over-budget unit. A batch's load is the sum of its units' weights. Replaces upheld-finding routing at `foreman/loop.py:323-345` and `foreman/reports.py:196-211`; failed gate routing at `foreman/loop.py:219-236` belongs to judgment.

```python
@dataclass(frozen=True)
class Unit:
    path: str      # worktree-relative file
    start: int     # first line of the enclosing def or class; 1 for the whole file
    weight: int    # its line count

@dataclass(frozen=True)
class Batch:
    role: str                       # the receiving role, from the anchor's owning step
    source: forms.FindingSource
    lines: tuple[str, ...]          # findings, verbatim, in source order
    load: int                       # summed unit weights

def unit(wt: Path, path: str, line: int) -> Unit: ...
def cut(lines: list[str], source: forms.FindingSource, group: Group, wt: Path,
        budget: int) -> tuple[list[Batch], list[str]]: ...
    # batches per receiving role under budget and at most five distinct anchor files;
    # and the lines whose anchor no step owns
```

`foreman/events.py`: adds the dispatch record, and `test_wrong` becomes an owner signal. Combined-tree failures use judgment instead of the dispatcher signal `red_twice` (`foreman/events.py:12`). The owner tunes the budget from these records; Foreman never changes its own configuration.

```python
OWNER_SIGNALS = ("milestone_done", "test_wrong")
DISPATCHER_SIGNALS = ("findings", "ruling_needed")

def dispatched(self, group_id: str, name: str, role: str, *, load: int | None,
               seconds: float, tokens: int | None, outcome: str) -> None: ...
    # EventLog method: one "dispatch" event; outcome is "done", "overran" or "refused"
    # no batch means load=None, serialized as JSON null; never zero or an omitted field
```

`foreman/errors.py`: `WrongTest` is the owner stop for a spec defect, an undetermined judgment or exhausted juror rounds.

```python
class WrongTest(ForemanError): exit_code = 8   # spec defect, undetermined judgment or juror cap; owner acts
```

`foreman/backends.py`: each adapter reports the token count of a finished job and passes required `Config.dispatch_seconds` to `run.run(timeout=...)`. Both adapters remain fresh-agent launches. `ClaudeLocal` runs `claude -p --output-format json`, returns the result text as `Result.out` and reads its usage totals. `CodexLocal` runs `exec --json`, reads usage from its event stream and retains its existing last-message file as `Result.out` (`foreman/backends.py:99-131`). Structured transport records never become report prose. `tokens(job)` remains available after `wait(job)`; missing usage returns `None`, never a guessed count. Incomplete structured output from a timed-out process must still return `TIMEOUT_CODE`, preserving available text and usage, so logging and the timeout stop occur.

```python
class ClaudeLocal:
    def __init__(self, command: list[str] | None = None,
                 args: list[str] | None = None, *, dispatch_seconds: int) -> None: ...

class CodexLocal:
    def __init__(self, command: list[str] | None = None,
                 agents_dir: Path | None = None, *, dispatch_seconds: int) -> None: ...

class Backend(Protocol):
    def start(self, brief: str, worktree: Path, role: str) -> Job: ...
    def wait(self, job: Job) -> Result: ...
    def tokens(self, job: Job) -> int | None: ...   # total tokens the waited job reported; None when it reported none

def get(cfg: Config) -> Backend: ...   # passes cfg.dispatch_seconds to either adapter
```

`foreman/loop.py`: renders before each dispatch. After a red step and its configured check succeed, validates its report and keeps it through `EventLog.keep` as today; the ordered evidence is reconstructible from that kept report. Before a build, resolves the explicit red references, validates and orders ids, and renders them into the brief. Missing/malformed evidence is `RulingNeeded`, with the kept report path, before the builder starts. Existing view generation, snapshots and commit flow remain their homes.

`_dispatch` is the one dispatch path. After each agent returns it keeps its output and calls `log.dispatched` with the batch's measured load, `Result.seconds`, `backend.tokens(job)` and the outcome. A dispatch carrying no batch logs `load: null`. `run.TIMEOUT_CODE` takes precedence: log `overran` and stop with `RulingNeeded`, naming the kept report and worktree. Otherwise a rejected or overloaded report logs `refused` before stopping with `RulingNeeded`; a completed dispatch logs `done`. No later dispatch follows a timeout.

Upheld reviewer findings go through `batches.cut` with `Config.batch_budget`, using the reviewer's kept round `<group>-review-<role>` as source. A line no step owns stops with `RulingNeeded`. Each batch becomes a typed finding form through `forms.parse` with its source, every line a member of that kept source, and is dispatched to its role as `<source key>-batch-<n>`. The round is handled once every batch lands; then checks, reviews and landing continue. Gate finding forms remain available as evidence; a failed combined-tree check goes to the juror before any fix dispatch.

Every issue that prevents a green tree after tests and implementation are combined opens a judgment: `TEST WRONG`, unexpected step-check results, and failed formatter or group checks, including checks after repairs, retries and upheld findings. Expected red outcomes that satisfy a producer's configured check remain successful evidence. A failure needs neither a test id nor a parseable finding anchor to reach the juror. Refusals, malformed transport and infrastructure timeouts retain their existing stops.

The judgment carries explicit `Group.design`, candidate group step ids and their approved-step blocks, relevant resolved test ids when available, and kept failure, agent and producer reports. For a step failure include its build and selected producers; for a group failure include the completed group steps. Test ids are resolved per producer, without requiring one producer for the whole judgment. Missing specification or stale evidence stops with `RulingNeeded` before dispatch. The juror names the step responsible for the fix; Foreman validates that target against the supplied candidates and follows the verdict without deciding fault itself.

One step's run is a private `_step`, so a single step reruns without replacing its original pre-build snapshot. Juror attempts use monotonically numbered kept keys `<group>-<context>-juror-<n>`, where context is the failing step id or `group-check`, reserved with `EventLog.keep` before launch and counted across process restarts, including malformed or refused attempts. `Config.juror_rounds` caps attempts per failing step and per group-check context; retries do not reset the count. At the cap, Foreman calls `log.notify_owner("test_wrong", ...)`, naming the kept evidence and worktree, and raises `WrongTest`. Otherwise it dispatches `juror` with `Judgment`, keeps its output, decodes `reports.verdict` and follows the verdict:

- `spec wrong / owner`: immediately notifies the owner and raises `WrongTest`, preserving the worktree and evidence. No test is weakened and no specification is edited automatically.
- `tests wrong / repair`: targets the named test producer and renders only the verdict's verbatim `FAILURE` summaries as test-writer `failure:` evidence. Its paths, views, original ids and assertions are preserved; machinery alone changes. Validate the repair against the producer's preserved evidence and the combined tree, rather than expecting repaired tests against built code to fail red again.
- `tests wrong / redo red`: restores the named producer and its dependent builds from their original snapshots, invalidates the producer's evidence before dispatch, reruns its original red form and check, then resolves new evidence and restarts those builds in group order. The approved behavior claims do not change. Dependent build resolutions are invalidated.
- `build wrong / retry`: restores the named build's paths from `<group>-<step>-before` and retries the same builder with the reasons rendered after its red ids. Later completed steps whose writes depend on those restored paths rerun in group order before checking the combined tree.
- `undetermined / owner`: stops for the owner with the missing or conflicting evidence. It grants no automatic repair or specification change.

Only these kind/action pairs are legal. An infrastructure timeout, a refused juror or malformed verdict stops with `RulingNeeded`; no guessed verdict follows. A remaining combined-tree failure starts the next bounded juror attempt. After the fix, rerun affected build checks and the group check; reviews and landing proceed only when green. A group-check failure resumes at that check after the fix, without replaying unrelated completed steps. A failure with no supported fix target returns `undetermined / owner`. Specification changes require the owner's approval and a newly validated queue/evidence set; an owner's verdict file cannot silently amend claims.

```python
def _dispatch(group: Group, wt: Path, log: EventLog, backend: Backend,
              name: str, brief_text: str, role: str, *,
              load: int | None = None) -> reports.Report: ...
    # one kept report and dispatch record; TIMEOUT_CODE logs overran and raises RulingNeeded

def _step(cfg: Config, group: Group, index: int, wt: Path, log: EventLog,
          backend: Backend, *, juror_reasons: tuple[str, ...] = ()) -> None: ...
    # executes one step; retries preserve its original pre-build snapshot

def _follow_verdict(cfg: Config, group: Group, judgment: forms.Judgment, wt: Path,
                    log: EventLog, backend: Backend,
                    verdict: reports.Verdict) -> None: ...
    # sole handler for a decoded juror or owner verdict; validates its target, restores and dispatches through _step

def run_retest(cfg: Config, group: Group, text: str, log: EventLog,
               backend: Backend | None = None) -> str: ...
    # reports.verdict(text) reads the owner's verdict in the Juror section's grammar;
    # follows the same verdict handler as a juror; returns and raises as run_group
```

`foreman/config.py`: reviewer roster entries become just `role` and `paths` (selection/order). Reject `spec`, even `false`, with a migration error. Only the two reviewed role contracts are registered here; a roster entry naming a role without a supplied contract, such as `spec-reviewer` or `gatesmith`, fails load with a diagnostic and is never reinterpreted. Adds required `batch_budget` and `dispatch_seconds`, and optional `juror_rounds`; a bool, non-integer or value below 1 is a `ConfigError`. The example sets `batch_budget = 300` enclosing-unit lines, an unmeasured starting value tuned by the owner from the dispatch record, and `dispatch_seconds = 600`, the ten-minute agent cap. Required keys have no loader fallback. Shared `cfg` and config-file fixtures carry integer values, not quoted numbers (`tests/conftest.py:37-68`).

```python
@dataclass(frozen=True)
class Config:
    ...                         # existing fields unchanged
    batch_budget: int           # required; example 300, unmeasured, tuned from dispatch records
    dispatch_seconds: int       # required; example 600, the backend run timeout
    juror_rounds: int = 2       # rounds per failing step or group-check context before the owner arbitrates
```

`foreman/cli.py`: all run/continue commands receive fully validated groups, matched against the loaded configuration before dispatch. `retest STEP_FILE GROUP FILE` is the owner's continuation of a group stopped by `WrongTest`, calling `loop.run_retest`; FILE holds the owner's verdict in the juror's verdict grammar. `reports.verdict` reads it, and the same verdict handler follows it without dispatcher adjudication. Empty, malformed or ambiguous content raises `ConfigError` before dispatch; an empty file no longer skips a repair. Keep the supplied verdict with `EventLog.keep`. No raw brief reaches a role through it.

## Forms and exact rendering

The templates below fix every form serialization, including the juror brief and verdict.

For every template: encode UTF-8; join the listed lines with one LF byte (`\n`); no final LF, heading, Markdown fence, indentation or blank separator is added. Preserve array order and field contents. A field called a **line** is a nonempty string without CR, LF or NUL or surrounding whitespace. A **block** is verbatim approved-step text: LF permitted, CR/NUL and leading/trailing LF forbidden; preserve internal blank lines and indentation. No escaping, trimming, wrapping, interpolation or prose cleanup. TOML decoding precedes these rules. Placeholders below are definitions, not literal angle brackets in dispatched text.

Paths are repository-relative POSIX file paths without traversal, absolute roots, whitespace or control characters, except generated absolute scratchpad `.pyi` paths. A test id has a `tests/` `.py` file prefix followed by `::` and its exact pytest selector, including class/parameter components. Do not split a selector at whitespace inside a pytest parameter name or interpret it as shell text. Unsupported path spelling is an explicit validation error, not normalization. The form stores view **sources**, not hand-copied `.pyi` filenames: `V(source)` is exactly `str(views.path(cfg, group.id, source))`. The generated path must also fit a single brief line.

An **origin** is `{ design = "docs/superpowers/specs/<file>.md", steps = ["<Order id>", ...] }`, with nonempty, distinct line-valued ids. `O(origin)` is the design path followed by `, Order step ` and its single id, or `, Order steps ` and ids joined with `, `. A port has exactly one origin id. Origin attests where text came from; Foreman does not open Gauntlet's design to decide whether a step is approved or text is verbatim.

### Test-writer

| `shape` | Required `form` fields | Optional fields | Rendered lines, in order |
|---|---|---|---|
| `red` | `behaviors`: nonempty array of `{ text: line, source: .py path }` | `origin`; each behavior may have one `replaces: test id` | `red`; if present, `design: ` + `O(origin)`; for each behavior, `behavior: ` + text, `view: ` + `V(source)`, then `replaces: ` + id when present |
| `port` | `origin`, `sources`: array of .py paths | none | `port`; `design: ` + `O(origin)`; one `files: ` + path for each `Step.paths`; one `view: ` + `V(source)` for each source |
| `repair` | `sources`; `failures`: nonempty array of verbatim summary lines | none | `repair`; one `files: ` + path per `Step.paths`; one `view: ` + `V(source)` per source; one `failure: ` + summary per failure |
| `moved` | `changes`: nonempty array of `{ anchor: document path:line, before: line, after: line }`; `sources` | none | `moved`; one `lines: <anchor> | before: "<before>" | after: "<after>"` per change; then `files:` and `view:` lines as for port |
| `finding` | `source`, `lines` as defined below | none | Each finding line verbatim; no `finding` heading |

`Step.paths` are the one-to-five writable test files, never a line freely supplied to the renderer. For red they bound permitted output and routing but are **not** rendered: red has no `files:` slot. The role itself chooses where the named documents put tests. A red output outside those declared paths stops before the next dispatch; this needs output evidence, not a claim that its brief imposes a new red scope rule.

Each behavior has exactly one source, even when several behaviors share the same source and view. The distinct union of red behavior sources, or the other shapes' `sources`, must equal `Step.views`; each generated view must exist before dispatch. For port/repair/moved, `sources` may be empty only for files importing no own-code module; completeness is a dispatcher attestation, since Foreman does not analyze imports. `replaces` identifies an existing test, once across a run; its file must be covered by `Step.paths`. A design-derived red run requires an explicit origin. No red origin is inherited from `Group.design` merely because the group has one. For M2 the origin is required, not optional in practice.

Embedded `"` or ` | ` in moved before/after text is refused, never escaped. Findings and failure summaries remain evidence, not slots for instructions. Free behavior text and approved step text still require human provenance; structural validation cannot prove that a sentence is an owner's behavior rather than steering.

### Core-builder and seam-builder

| `shape` | Required `form` fields | Optional fields | Rendered lines, in order |
|---|---|---|---|
| `stub` | `step`: approved-step block | `exclude`: nonempty path array | `stub`; step block; one `Step.paths` path per line; then exclusion line if present |
| `build` | `step`: approved-step block; `red`: nonempty array of red-step selections | `exclude` | step block; one `Step.paths` path per line; one resolved new test id per line; on retry, each juror reason verbatim; then exclusion line if present |
| `rewrite` | `line`: one verbatim mutant-hunter `REWRITE` line | `exclude` | REWRITE line; then exclusion line if present |
| `finding` | `source`, `lines` as defined below | `exclude` | finding lines verbatim; then exclusion line if present |

Only a `build` retry accepts `render`'s runtime `juror_reasons`, obtained from `reports.verdict` for that stopped step. They are ordered verbatim lines after all red ids and before exclusions. Queue forms have no reasons field or raw suffix; other shapes reject nonempty reasons. The retry template includes these lines; no queue author supplies them.

One exclusion line is exactly `exclude: ` followed by its paths joined with one ASCII space. No `build`, `step:`, `scope:`, `red:`, `tests:`, `design:` or command line is added to a build brief. An approved-step block carries the design's citation, its Order row and the constructor/interface declarations that Order step lists; it is never an appendix for arbitrary signatures or instructions.

For core stub and build, writable files must be under `gauntlet/core/` or exactly `gauntlet/__init__.py`. A step that writes a core package initializer lists it as one of its own paths, counted toward the five-file limit and rendered like any other path. Seam paths and exclusions must be under `gauntlet/seams/`. Core exclusions must narrow its allowed scope. No exclusion widens scope or adds another shape's field.

For rewrite/finding, `Step.paths` describe ownership/landing limits and are **not** rendered as a scope list. The role fixes scope from its evidence, with the expansion rules stated in its contract. A declared path cannot authorize an extra write through the brief, and a write outside the group's paths does not land. Stub/rewrite/finding have no red field or test selector line.

### Reviewers

Review forms live in the step file under `[groups.<id>.reviews.<role>]`, separate from writable steps. A configured reviewer is selected by its roster paths as today. Its form defaults to `{ scope = "working-tree" }` when absent; group entries naming an unconfigured reviewer are errors at configuration/queue preflight. The field set is closed by reviewer role:

| Role | Fields | Rendering |
|---|---|---|
| `diff-reviewer` | `scope`: `working-tree` or `range`; `range` required only for `range` | Working tree is the empty string (zero bytes), explicitly legal under its contract; range is its exact value, one line |
| `test-reviewer` | Same scope/range; optional `specs`: ordered distinct array of paths under `docs/superpowers/specs/` | Working tree starts with `working tree`; range starts with its exact value; append one `spec: ` + path per spec |

A range is one single-line `A..B` or `A...B` expression with nonempty revision operands, no whitespace/control characters and no leading `-`. Validation does not resolve git revisions before the target worktree exists. Automatic group reviews require `working-tree`, because Foreman is reviewing an uncommitted group; range forms are available only for an explicit review operation, not a way to omit the group's changes. There is no new standalone review CLI in this design. Optional test specs are explicit fields, never appended because a generic flag or group design happens to exist. A diff-reviewer form with `specs`, empty or not, fails load.

### Juror

`.claude/agents/juror.md` is a read-only role prompt implementing this section. It receives one issue preventing a green combined tree, reads the supplied specification, candidate steps, tests, implementation and kept failure reports, and decides who got it wrong. It returns one complete verdict and names the step to fix for an automatic action. It writes no repository file, changes no approval and dispatches no repair. Existing role files and historical juror contracts are not inputs to this contract.

The brief grammar is exactly the following sequence, with placeholder lines expanded and no fence:

```text
judgment
spec: <repository-relative approved design path>
step: <first candidate step id>
<one step: line for every remaining candidate>
step-lines: <N>
<N literal approved-step lines>
<zero or more red: lines, one per relevant resolved test id>
report: <kept failure report path>
<one report: line for each remaining relevant agent or producer report>
end judgment
```

`N` is the positive decimal count of LF-separated lines in `Judgment.step`, without leading zeros. Exactly N following lines belong to that block, including internal blank lines. The block carries candidate steps' approved text in their listed order; each step is identified by its approved Order row. The framing prevents a line in the block from becoming a field. Candidate ids are distinct and nonempty. Relevant `red:` ids are distinct and keep producer/build order; they may be absent for a failure without selectors. The spec and test ids use the path/id rules above. At least one failure report is required; additional reports include relevant builder and producer evidence. Kept report paths are generated by `EventLog.keep`, may be absolute under the configured state directory, and must fit one line. They are evidence references, never shell commands or user-supplied queue suffixes. Validation belongs to `forms`, text generation to `brief`; the juror reads the files at those references. Foreman neither embeds report prose as instructions nor parses role files.

The verdict grammar is exactly:

```text
VERDICT <kind>
ACTION <action>
<STEP candidate-id for repair, redo red or retry; absent for owner>
REASON <first reason>
<zero or more further REASON lines>
EVIDENCE <path:line> | <claim>
<zero or more further EVIDENCE lines>
<one or more FAILURE lines only for repair>
END JUDGMENT
```

Kinds and actions are literal lower-case strings from this table. `STEP <id>` names exactly one candidate: a test producer for repair/redo red or a build for retry. Reasons, claims and failure summaries satisfy the line rule above. Each evidence record has a repository-relative file or one of the judgment's exact kept report paths and a positive line number, followed by the first literal ` | ` delimiter and a nonempty claim. At least one reason and one evidence record are mandatory; preserve their order. `FAILURE <summary>` contains the verbatim observed summary acceptable to test-writer's repair form, never a suggested fix phrased as an observation.

| Kind | Action | Required basis |
|---|---|---|
| `spec wrong` | `owner` | An approved requirement contradicts owner intent or another authoritative requirement, or leaves the needed behavior undecidable. Cite the specification and conflicting evidence; propose the needed clarification in the reasons. |
| `tests wrong` | `repair` | The approved claim is clear and correct; test machinery misrepresents it. Cite test and specification anchors and observed failure summaries. Original ids and assertions remain valid. |
| `tests wrong` | `redo red` | The test's asserted behavior contradicts the clear approved claim, or cannot be fixed while preserving ids/assertions. Cite test and specification anchors. Rerun the producer with its unchanged approved behaviors. |
| `build wrong` | `retry` | The tests or check correctly expose an implementation defect, or the builder's rejection is wrong. Cite the approved requirement, failure evidence and the build responsible. |
| `undetermined` | `owner` | The supplied evidence cannot support one of the other kinds. Cite the available evidence and identify what is missing. |

The juror does not classify a correct but inconvenient specification as wrong merely because a builder disagrees. When owner intent is unavailable or authority conflicts cannot be resolved from the supplied material, it returns `undetermined`. The report's evidence supports the judgment; Foreman validates syntax, reference membership and permitted transitions without adjudicating the substantive claims.

`reports.verdict` accepts precisely this ordered envelope: UTF-8 and LF separators, with at most one final LF permitted for kept-report transport (`foreman/loop.py:29-34`); no preamble, fences, unknown fields, blank separators, duplicate singleton fields, extra terminal text or second verdict. Evidence anchors must exist in the target worktree or the judgment's exact kept reports before a verdict is followed; test anchors must belong to a supplied producer, and specification anchors to the declared specification or requirements it explicitly cites. Repair requires at least one failure summary; every other action forbids failures. Parsing checks shape; `loop._follow_verdict` checks the target step, action and evidence against the kept judgment before any restore or dispatch.

The owner's `retest` FILE uses the same envelope and context checks. There is one decoder and one transition handler. Reasons enter a build retry only through the runtime renderer argument; repair summaries enter only through a typed repair form. A spec-wrong or undetermined verdict preserves the owner stop.

### Findings and their provenance

A finding form's `source` is `{ kind = "reviewer", role = "test-reviewer" | "diff-reviewer", round = "<kept review key>" }` or `{ kind = "gate", run = "<kept gate key>" }`. `lines` is a nonempty array of verbatim lines. The source record is validation/provenance data and never renders. Static forms are structurally checked on load; before dispatch each line must also be an exact member of the cited kept report/run and, for reviewer findings, the dispatcher's upheld set. A missing kept source at load is not an error for a future runtime form. A reference that cannot be resolved at dispatch stops there.

Test-writer accepts test-reviewer findings anchored under `tests/`, with category, defect, failure scenario, docs citation and fix evidence in the role's report format, or gate lines anchored under `tests/`. Builders accept diff-reviewer findings with exactly four ` | `-separated fields: category, writable `path:line`, defect, `fails when: <scenario>`. The seven builder categories are `shortcut`, `gate-gaming`, `misplaced`, `hand-rolled`, `drift`, `shell-text parsing`, `leftover`. Preserve the complete evidence; a quoted reuse target inside a defect is not a second finding anchor. Reject extra fields, empty scenario, unsupported category or an anchor outside the receiver's permitted writes. Reviewer and gate evidence never mix in one form, nor do different review rounds or gate runs.

Finding briefs use these same form constructors and renderer, replacing the unchecked `brief.finding` path. Route on the finding's primary anchor, not the first incidental path inside a quotation. Preserve order and verbatim lines. `batches.cut` splits each source's findings into batches per receiving role under the configured load budget and five distinct anchor files; the dispatcher never chooses a batch. The builder and test-writer contracts accept a batch that is part of one round or run, and a round is handled once every batch lands. A failed combined-tree gate first goes to judgment; findings alone do not decide who must fix it. Never truncate, merge sources or add explanation to a brief.

## Red evidence into a build

A build's `form.red` is an ordered list of records `{ step = "<earlier red step id>" }`, optionally with `files = ["tests/<file>.py", ...]`. It is not a list of test names guessed by the dispatcher. Each reference must name a `test-writer` red step earlier in the same group; no forward/self/cyclic reference, arbitrary report filename or reference to port/repair/moved. An optional file filter must be a nonempty distinct subset of that producer's declared paths. An absent filter selects all its new ids. This supports M2 3e's separate core and seam consumers and step 32 consuming steps 30, 31 and 31a. Cross-group report lifetime is not introduced.

At dispatch, parse the current successful producer report as above; select its `BEHAVIOR` ids whose file prefix matches the optional filter. Every id must have a positive behavior index within that producer's form, a canonical test-file prefix covered by its paths, and valid red/rerun evidence. Reject zero selected ids, malformed selectors, duplicate ids within a producer or overlap across selected producers. Order producers by the form, ids within each producer by their `BEHAVIOR` report order. Validate that every reported new id has run evidence, even an id excluded by the consumer's file filter. Never include `KEPT`, `REPLACED`, `RUN ... passed`, or an old id merely because its file was run.

For example, if step 2 reports new ids `tests/test_core_errors.py::test_process_error_fields` and `tests/test_core_errors.py::test_launch_error_hierarchy` in `BEHAVIOR`, with matching red/rerun evidence, step 3's last lines are exactly those two ids, in that order. These example names illustrate the protocol; the queue does not prescribe them. `tests/test_core_errors.py` alone is never a fallback. The builder then executes only those selectors in its existing red/green pytest command, leaving the file's old passing tests outside that initial red check. Foreman's configured whole-file done-check may still run the whole file; its result does not supply ids.

Core-builder and seam-builder take red tests by file path or pytest node id. A build carries the selected node ids; Foreman never falls back to a whole file or invents a `red ids:` line.

Keep the raw producer report and the rendered dispatch text (including empty review text) with `EventLog.keep`; use the current group/step report key and a distinct `<group>-<step>-brief` key. Evidence is loaded from the latest successful execution of the referenced producer in this group worktree; rebuilding a process in memory must not lose it. A rerun overwrites that producer's kept report only with its new execution and invalidates later resolutions; rejected, partial or failed reruns cannot leave an earlier success usable. No separate permanent id ledger.

After a juror's `tests wrong / repair` verdict, the repair may update machinery but cannot add/remove/rename the original red ids or change their assertions. Re-read and validate those selectors against the preserved producer evidence after repair. A stopped builder reruns against its restored pre-build snapshot; a completed group resumes affected checks and its group check after the fix. A missing selector or changed membership stops with `RulingNeeded`. After a `tests wrong / redo red` verdict, the producer reruns as a red step and its evidence replaces the old before dependent builds restart. Never try to infer a new red set from a repair's all-green `RUN after` lines. The old text of a report from a different queue/group definition is not trusted: keep a canonical serialization of the producer form, paths and views with the evidence and require it to match the loaded queue on continuation; differences require a producer rerun.

## Load-time checks and migration

`steps.load` reads and validates **all** groups, including ones after the selected group. It returns no partial usable result. Before EventLog creation, worktree creation, view generation or backend start, reject:

- Unknown/missing top-level, group, step, form, behavior, origin, source, selection or review keys; wrong TOML types, bools where integers are expected, invalid discriminants, unsupported role/shape pairs. A field from another shape is an error even when empty. There is no `extra`, appendix, template override or raw suffix slot.
- Empty required strings/arrays, invalid line/block/path syntax, more than five writable files, duplicate paths or ids, files outside the role's writes, invalid exclusions or red-test fields on non-build shapes. Preserve the existing one-check argv, expected-code, unique-group, membership, dependency-cycle and view-order checks, now in the load boundary. Forms do not assert that a syntactically short step satisfies the role's time cap.
- A view not declared for its source, a source not produced by an earlier step under the existing view rule, a missing design origin for a design-derived red/port step, non-spec origins/specs, invalid producer order/shape/file filters, and a review spec field on diff-reviewer. Existence of generated views, report evidence, git ranges and adjudication remain runtime checks.
- Any free-text `brief` key, whether alone, beside a form, blank or nonblank: `steps.<id>.brief: free-text briefs removed; use role, shape and form`. No dual-format mode or automatic text-to-fields parser. Likewise `reviewers.<index>.spec: removed; put explicit specs in the group's test-reviewer review form`. Errors include the queue/config path and exact table/field; they are not agent refusals.

The loader checks structure and syntactic evidence, not whether a behavior was truly quoted, the plan approved, every own-code import viewed, a test actually new in git, or a finding upheld correctly beyond its kept membership. Those attestations remain the dispatcher's responsibility. Unexpected report output stops before the next dispatch, not at load when the output does not exist yet.

### The existing M2 queue

Only the external `~/dev/foreman/queues/m2.toml` is the migration source; it is not edited by this design. It currently has `g1` and steps `1`, `2`, `3`. The M2 Order is amended before the dispatcher translates it:

| Table | Translation |
|---|---|
| `groups.g1` | Keep steps, design and after. Optionally add `[groups.g1.reviews.test-reviewer]` with `scope = "working-tree"`, `specs = ["docs/superpowers/specs/m2-launcher-kinds-design.md"]`; diff-reviewer uses its empty working-tree default. No spec is inferred. |
| `steps.1` | Keep core-builder and errors.py paths; `shape = "stub"`. `form.step` carries the existing approved Order step 1 row plus the two constructor declarations already in its brief, with their exact current indentation and blank lines. Remove the opening `stub` and final path from stored text: the renderer supplies those. |
| `steps.2` | Keep test-writer, paths, views, check and `expect_code = 1`; `shape = "red"`. Set `form.origin = { design = "docs/superpowers/specs/m2-launcher-kinds-design.md", steps = ["2"] }`. Two behavior records preserve exactly the current sentences: `New errors preserve safe data and hierarchy.` and `Errors carry safe operation identifiers and bounded reasons, not credentials, request bodies, inherited environments or arbitrary process output.` Both sources are `gauntlet/core/errors.py`. No replaces. The generated view resolves to `/Users/atom/dev/foreman-views/g1/gauntlet/core/errors.pyi` under the existing run's configuration. |
| `steps.3` | Keep core-builder, errors.py scope and whole-file passing check; `shape = "build"`. `form.step` contains the existing citation `docs/superpowers/specs/m2-launcher-kinds-design.md, Order step 3` and row `3 build | core-builder | gauntlet/core/errors.py | Typed-error constructor and hierarchy tests must pass.` in that order. Set `form.red = [{ step = "2" }]`; remove the standalone scope and test-file lines from stored text. Scope comes from paths; red selectors come from step 2's report. |

Thus step 2 renders `red\ndesign: docs/superpowers/specs/m2-launcher-kinds-design.md, Order step 2\nbehavior: New errors preserve safe data and hierarchy.\nview: /Users/atom/dev/foreman-views/g1/gauntlet/core/errors.pyi\nbehavior: Errors carry safe operation identifiers and bounded reasons, not credentials, request bodies, inherited environments or arbitrary process output.\nview: /Users/atom/dev/foreman-views/g1/gauntlet/core/errors.pyi` (the displayed `\n` denotes one LF). The origin is added because the current contract requires it.

For the rest of the amended M2 Order, stub/red/build rows use their matching forms and explicit origins; `3d declaration` is a core-builder `stub`. Step 3e is one red producer with three declared test files and the required view sources. Step 3f selects only new ids in `tests/test_core_config.py` and `tests/test_core_codec.py`; a seam-builder build step in the same group as 3e selects its new ids in `tests/test_seams_config.py`. Step 32 selects steps 30, 31 and 31a explicitly. Ports 3g, 7 and 29c that need helper code or new fixture tests split into a red step and a build for the helper, then a pure port. A port has a design origin, paths and sources, never a build-style red list.

For example, step 3's candidate replacement table is concrete TOML (the existing group table remains):

```toml
[steps.3]
role = "core-builder"
shape = "build"
paths = ["gauntlet/core/errors.py"]
check = ["uv", "run", "pytest", "-q", "tests/test_core_errors.py"]

[steps.3.form]
step = """docs/superpowers/specs/m2-launcher-kinds-design.md, Order step 3
3 build | core-builder | gauntlet/core/errors.py | Typed-error constructor and hierarchy tests must pass."""
red = [{ step = "2" }]
```

M2 `28 dependency` and the checked-in `queues/m1.toml` belong to gatesmith, which stays unsupported until separately designed. Do not invent aliases. Old queues fail with actionable diagnostics until translated; no promise that all old queues keep running through a generic free-text escape hatch.

## Homes

| Concern | Home | Forbidden elsewhere |
|---|---|---|
| Shape records and structural grammar | `foreman/forms.py` | Arbitrary brief dictionaries, fallback raw text, runtime role-file parsing, independent shape validators in CLI/loop |
| Group relationships and load boundary | `foreman/steps.py`, calling forms validators | A CLI-only lint requirement that lets another caller load malformed queues |
| All dispatched text | `foreman/brief.py`, including runtime juror reasons after build red ids | Appending lines in loop/backends; inheriting specs from group metadata; a second finding or continuation renderer; raw queue reason suffixes |
| Generated view identity | Existing `foreman/views.py:12` (`path`) | Hard-coded scratchpad paths in queue forms or another suffix/root calculation |
| Red-report extraction, finding anchors and juror verdicts | `foreman/reports.py` | Parsing BEHAVIOR/RUN or verdict text in loop; extracting ids from pytest output, git diffs or guessed names |
| Finding weight, routing to a role and batch cutting | `foreman/batches.py`, under `Config.batch_budget` and five distinct anchor files | Per-step finding dispatch in loop; `ast` imported elsewhere; the dispatcher choosing batch membership; a second packing rule |
| Dispatch record | `EventLog.dispatched`, called only from `loop._dispatch`; no batch has null load | Load, wall time, tokens or outcome logged elsewhere; zero or omitted load for no batch; Foreman changing its own budget |
| Token counts | Each adapter's `Backend.tokens`, from Claude JSON usage or Codex JSON events | Reading token counts from agent output in loop or events; a second token parser |
| Dispatch timeout | `Config.dispatch_seconds`, passed by adapters to existing `foreman/run.py:run` | A timer in loop; another process runner; unbounded adapter calls; timeout treated as success |
| Specification/test adjudication | This design's Juror section, implemented by `.claude/agents/juror.md` | Importing an existing juror contract; juror rules or verdict meanings maintained independently in another document |
| Combined-tree failure and following a judgment | `forms.Judgment`, rendered by `brief.render`; `reports.verdict` decodes juror output and owner FILE; `loop._follow_verdict` follows either | Dispatcher adjudication; automatic gate repair before judgment; a TEST WRONG-only trigger; a second verdict codec/handler; Foreman inventing failure evidence or editing specifications; a literal round cap |
| Step and combined-tree checks | Existing `foreman/verify.py:18` (`verify`) and `foreman/verify.py:27` (`check_step`), called by loop | Check commands run in a verdict decoder, CLI or juror; expected producer red treated as a combined-tree failure |
| Specification defect, undecidable judgment or exhausted juror rounds | `WrongTest` and `notify_owner("test_wrong", ...)` | Automatic test weakening or specification edits; dispatcher decisions substituted for the owner |
| Evidence lifetime and dispatch resolution | `foreman/loop.py` through existing `EventLog.keep/kept` | Another id store, file-level fallback, cross-group report reuse |
| Reviewer selection | `foreman/config.py` roster; per-group review forms in step file | Generic `spec` boolean or unvalidated role-specific suffix |
| Approval, brief provenance and legal-shape rulings | Owner and dispatcher | Foreman deciding that a passing test is red, that a role grammar changed, or that an Order mismatch authorizes a new shape |

## Order

Each row is one dispatch, one agent, at most five written files and exactly one done-check. Rows land in order; no row carries a whole-tree run. `builder` is the roster agent whose writes are `foreman/`, `tests/`, `pyproject.toml`, `Makefile` and `foreman.example.toml` (`docs/superpowers/specs/harness-core-design.md:178`), and the owner authorizes it to build this Order. `role-author` writes the juror prompt; the existing prompt-writing home names it for the builder role (`docs/superpowers/specs/harness-core-design.md:169`, `docs/superpowers/specs/harness-core-design.md:178`), and this Order assigns it the juror prompt too. Its prompt follows the Juror section. Each row's done-check covers its described behavior, including regression cases, rather than merely reproducing a renderer implementation. No code, tests, role or queue changes are part of this document.

| Step | Agent | Paths written | Work | One done-check |
|---|---|---|---|---|
| 1 | `builder` | `foreman/forms.py`, `tests/test_forms.py` | Typed records and field validation for the five test-writer shapes; origin/behavior/source records. | `uv run pytest -q tests/test_forms.py` |
| 2 | `builder` | `foreman/forms.py`, `tests/test_forms.py` | Builder shapes, exclusions, provenance batches and two review contracts; reject mixed sources and extra fields. | `uv run pytest -q tests/test_forms.py` |
| 3 | `builder` | `foreman/brief.py`, `tests/test_brief.py` | Exact renderers for every registered shape, empty diff review, views and resolved-id inputs; existing entry points remain only until callers move. | `uv run pytest -q tests/test_brief.py` |
| 4 | `builder` | `foreman/steps.py`, `foreman/forms.py`, `tests/test_steps.py` | Closed queue loading, whole-file validation, review tables and earlier-red/filter relationships; reject legacy brief. | `uv run pytest -q tests/test_steps.py` |
| 5 | `builder` | `tests/conftest.py`, `tests/test_views.py`, `tests/test_verify.py` | Move shared Step/Group constructors and these consumers onto form values; retain assertions. | `uv run pytest -q tests/test_views.py tests/test_verify.py` |
| 6 | `builder` | `foreman/config.py`, `foreman.example.toml`, `tests/test_config.py` | Remove spec boolean, validate reviewer roles and document the explicit group review form. | `uv run pytest -q tests/test_config.py` |
| 7 | `builder` | `foreman/reports.py`, `tests/test_reports.py` | Red envelope/evidence reader and primary finding anchors; passing old tests, partial/ambiguous reports cannot become red ids. | `uv run pytest -q tests/test_reports.py` |
| 8 | `builder` | `foreman/loop.py`, `tests/test_loop.py` | Render step/review forms, keep exact dispatched text and enforce generated-view/output scope checks. | `uv run pytest -q tests/test_loop.py` |
| 9 | `builder` | `foreman/loop.py`, `tests/test_loop.py` | Resolve filtered/multiple red producers, persist/invalidate evidence, and stop before builder start on invalid evidence. | `uv run pytest -q tests/test_loop.py` |
| 10 | `builder` | `foreman/config.py`, `foreman.example.toml`, `tests/test_config.py`, `tests/conftest.py` | Required `batch_budget` (example 300, unmeasured, tuned from dispatch records) and `dispatch_seconds` (example 600); optional `juror_rounds` defaulting to 2; reject bools, non-integers and values below 1. Update shared cfg and config serialization. | `uv run pytest -q tests/test_config.py` |
| 11 | `builder` | `foreman/reports.py`, `tests/test_reports.py` | `anchor` returns the primary anchor's worktree-relative path and line, replacing `finding_anchor`. | `uv run pytest -q tests/test_reports.py` |
| 12 | `builder` | `foreman/batches.py`, `tests/test_batches.py` | AST unit weights with whole-file fallback, role routing, per-role packing under budget, shared units, five distinct anchor files, over-budget units alone, unowned lines returned. | `uv run pytest -q tests/test_batches.py` |
| 13 | `builder` | `foreman/backends.py`, `tests/conftest.py`, `tests/test_backends.py` | Structured Claude JSON and Codex JSON-event usage, preserved report text and post-wait `Backend.tokens`; both adapters pass required `dispatch_seconds` to run; timeout and FakeBackend coverage. | `uv run pytest -q tests/test_backends.py` |
| 14 | `builder` | `foreman/events.py`, `foreman/errors.py`, `tests/test_events.py` | `EventLog.dispatched`; `test_wrong` becomes an owner signal; remove `red_twice`; `WrongTest` covers spec defects, undecidable judgments and the juror cap. | `uv run pytest -q tests/test_events.py` |
| 15 | `builder` | `foreman/loop.py`, `tests/test_loop.py` | `_dispatch` logs load, wall time, tokens and outcome for every dispatch; no batch logs null load; timeout logs overran and stops with RulingNeeded before another dispatch. | `uv run pytest -q tests/test_loop.py` |
| 16 | `builder` | `foreman/loop.py`, `tests/test_loop.py` | Upheld findings go out as typed finding forms in cut batches, each line a member of its kept source; the round is handled once every batch lands. Gate failures await the judgment path. | `uv run pytest -q tests/test_loop.py` |
| 17a | `role-author` | `.claude/agents/juror.md` | Write the read-only juror prompt from the Juror contract; no historical juror input. | Dispatcher checks the prompt against every brief field, verdict pair and write prohibition in the Juror section. |
| 17 | `builder` | `foreman/forms.py`, `foreman/brief.py`, `tests/test_forms.py`, `tests/test_brief.py` | `Judgment` candidate steps, optional red ids, spec and failure/evidence reports, length-framed approved-step block and exact juror rendering; runtime build-retry reasons after red ids, never queue suffixes. | `uv run pytest -q tests/test_forms.py tests/test_brief.py` |
| 18 | `builder` | `foreman/reports.py`, `tests/test_reports.py` | `verdict` reads exactly one verdict with kind/action/target constraints, reasons, anchored evidence and repair-only failure summaries in the Juror grammar, for juror output and owner FILE; anything else fails closed. | `uv run pytest -q tests/test_reports.py` |
| 19 | `builder` | `foreman/loop.py`, `tests/test_loop.py` | One-step `_step`; kept judgment context, bounded juror attempts, spec-wrong/undetermined owner stops and build-wrong retry. | `uv run pytest -q tests/test_loop.py` |
| 19a | `builder` | `foreman/loop.py`, `tests/test_loop.py` | Tests-wrong repair/redo-red transitions, original snapshots, identity/assertion preservation and dependent evidence invalidation; remaining combined-tree failures return to judgment. | `uv run pytest -q tests/test_loop.py` |
| 19b | `builder` | `foreman/loop.py`, `tests/test_loop.py` | Unexpected step checks and formatter/group-check failures use the same judgment path, including failures with no selector or finding anchor and multiple producer contexts; expected red stays successful evidence. | `uv run pytest -q tests/test_loop.py` |
| 19c | `builder` | `foreman/loop.py`, `tests/test_loop.py` | Follow the juror's group-check target, resume affected checks after repair/retry/redo red, preserve completed unrelated steps and count repeated failures toward the kept group-check cap; landing requires green. | `uv run pytest -q tests/test_loop.py` |
| 20 | `builder` | `foreman/loop.py`, `foreman/cli.py`, `tests/test_loop.py`, `tests/test_cli.py` | The owner's `retest` FILE decoded by reports.verdict after the juror cap, followed through the shared verdict handler; reject empty/malformed content; queue/config preflight. | `uv run pytest -q tests/test_loop.py tests/test_cli.py` |
| 21 | `builder` | `foreman/loop.py`, `tests/test_loop.py` | Preserved red selectors, queue identity and evidence refresh checks on continuation. | `uv run pytest -q tests/test_loop.py` |
| 22 | `builder` | `foreman/brief.py`, `foreman/reports.py`, `tests/test_brief.py`, `tests/test_reports.py` | Remove `brief.finding`, `brief.review` and `reports.route` after all callers move. | `uv run pytest -q tests/test_brief.py tests/test_reports.py` |

Intermediate rows may temporarily leave other consumers awaiting their listed migration; they are not runnable milestone releases. After row 22 the dispatcher runs the repository's normal final gate as a separate bounded verification step, not a second done-check appended to a builder brief. If measured size makes a row exceed its dispatch cap, re-cut it under 7.1f before dispatch. Queue migration is the dispatcher's work after the M2 Order amendment, outside `builder`'s writes; no row edits the read-only external queue or role sources.
