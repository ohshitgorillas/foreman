Status: proposed, for the owner's approval. Questions below block the affected shapes; this document does not amend a Gauntlet role.

## Why

`foreman/steps.py:16-24` carries a step's `brief` as unrestricted text. `foreman/loop.py:_steps` dispatches it unchanged. `foreman/brief.py:review` adds `working tree against HEAD` and, when `Reviewer.spec` is true, `spec: <Group.design>`. Neither operation checks the receiving role's grammar. A typo or extra line costs a dispatch before `REJECTED: STEERING` reaches Foreman.

A second gap is the boundary between red and build. The read-only `~/dev/foreman/queues/m2.toml` sends all of `tests/test_core_errors.py` to step 3. Core-builder runs every test named and treats a passing test as `TEST WRONG` before building. Step 2 adds tests to an existing file; its old passing tests are not red evidence. M2 step 3e has the same problem in `tests/test_core_config.py`, and its consumers must select only their own new tests across the three files that step writes.

The authority for accepted content is the **The brief** section of each current role in `~/dev/gauntlet/.claude/worktrees/m1-build/.claude/agents/`: `test-writer.md`, `test-reviewer.md`, `diff-reviewer.md`, `core-builder.md`, `seam-builder.md`. The needed sequence is **Order** in that worktree's `docs/superpowers/specs/m2-launcher-kinds-design.md`. These are read-only sources for this design, not runtime inputs. The copies under Foreman's `docs/research/gauntlet-roles/` are not substituted for them.

The current contracts differ from the reported refusals: test-writer explicitly allows a red `design:` before the first behavior, required for a run taken from a design's Order; test-reviewer explicitly allows optional `spec:` paths. Diff-reviewer allows scope only. Forms must follow the current contracts, and the dispatcher must resolve the installed-role mismatch before another run (question 1).

The unit of authoring becomes a form. The step file names a role, a shape and the fields of that shape. Foreman validates the whole file before making a worktree or dispatching any agent, then renders only those fields. A build references earlier red steps and receives their reported new pytest test ids, never a whole file merely because it contains new tests. Foreman neither invents cases nor decides which old test should be replaced.

## Modules

The modules and homes of `harness-core-design.md`, `group-run-design.md` and `test-wrong-design.md` stand except where changed here. No new third-party dependency. Paths in this section are relative to this Foreman checkout.

`foreman/forms.py` (new): frozen form values, the closed role/shape registry, parsing and structural validation. Replaces the unrestricted brief value, not the role's judgment of behavior or correctness. It has no subprocesses, source-code reader or renderer.

```python
def parse(role: str, shape: str, fields: object, *, where: str) -> Form: ...
    # closed tagged union; ConfigError identifies the table, field and violated rule
def validate_step(form: Form, paths: list[str], views: list[str]) -> list[str]: ...
def validate_group(group: Group) -> list[str]: ...
    # origins, earlier red-step references, scope/view relationships
```

`Form` means distinct immutable records for the shapes below, not a dictionary of optional strings. Origin, behavior, changed-document line, finding source and red-step selection have distinct records too. `Group` in the signature is a type-only reference; forms does not import the step loader at runtime.

`foreman/steps.py`: `Step.brief: str` becomes `Step.form: Form`. Each step requires `role`, `shape`, `form`, `paths`; `check`, `expect_code`, `views` retain their defaults and execution meaning. A group retains `steps`, `after`, `design`, and gains `reviews`, keyed by reviewer role, for the two reviewer forms below. `design` remains documentary metadata; it never implicitly becomes a brief line. `load` calls all form and existing graph/view/path checks before returning, including checks formerly left to the CLI's separate `lint` call. `lint` remains the shared validator for programmatically constructed groups. Invalid shape/field data raises `ConfigError`; CLI compatibility must not make a bad file dispatchable.

`foreman/brief.py`: the sole renderer for steps, reviews, findings and retest forms. Replaces `finding(lines)` and `review(reviewer, group)` with rendering validated values. It uses `views.path` for a declared source's view and accepts resolved red ids as data. It never reparses a role file or adds group purpose, a spec, commands, report prose or standing lines.

```python
def render(form: Form, *, paths: list[str], view_paths: dict[str, str],
           red_ids: list[str]) -> str: ...
```

`foreman/reports.py`: the sole reader of test-writer's red report, alongside its existing finding and wrong-test readers. Adds ordered red evidence: new test id, behavior number, and both `RUN red` / `RUN rerun` outcomes and summaries. Extract ids from `BEHAVIOR <n> | <test id> | ...`, not from every `RUN` line: file-wide runs include old passing tests. Require `SHAPE red` and terminal `END TESTS`, `BLOCKED none`, no `LEFT`, `UNTESTABLE` or `CONTRADICTS`, and one matching new id in each run with outcome `assertion` or `not-implemented` and identical summary text. The report's quoted `BRIEF` and `DIFF` are not sources of report records; read the report envelope, stopping structured-field interpretation at `DIFF`/`COMMANDS`. Ambiguous or duplicate records fail closed. The shape's report must be complete before accepting evidence.

`foreman/loop.py`: renders before each dispatch. After a red step and its configured check succeed, validates its report and keeps it through `EventLog.keep` as today; the ordered evidence is reconstructible from that kept report. Before a build, resolves the explicit red references, validates and orders ids, and renders them into the brief. Missing/malformed evidence is `RulingNeeded`, with the kept report path, before the builder starts. Existing view generation, snapshots, `TEST WRONG` stop and commit flow remain their homes.

`foreman/config.py`: reviewer roster entries become just `role` and `paths` (selection/order). Reject `spec`, even `false`, with a migration error. Only the two reviewed role contracts are registered here. Do not silently translate the example's `spec-reviewer`, whose contract was not supplied; question 8 covers it.

`foreman/cli.py`: all run/continue commands receive fully validated groups. `retest`'s nonempty FILE becomes a TOML form envelope (`role`, `shape`, `form`), checked against the selected test step before dispatch; its existing empty-file meaning, skip the repair, remains. A role's legal `repair` form requires adjudication against built code; Foreman must not turn `TEST WRONG` prose into a diagnosis or assume every repair is legal here. See question 7.

## Forms and exact rendering

This is the proposed serialization for review. Where a role specifies content but leaves spelling, separators or evidence interpretation open, the relevant question below must be answered before that part is built. Exact bytes are nevertheless specified here so an approval has a concrete target.

For every template: encode UTF-8; join the listed lines with one LF byte (`\n`); no final LF, heading, Markdown fence, indentation or blank separator is added. Preserve array order and field contents. A field called a **line** is a nonempty string without CR, LF or NUL or surrounding whitespace. A **block** is verbatim approved-step text: LF permitted, CR/NUL and leading/trailing LF forbidden; preserve internal blank lines and indentation. No escaping, trimming, wrapping, interpolation or prose cleanup. TOML decoding precedes these rules. Placeholders below are definitions, not literal angle brackets in dispatched text.

Paths are repository-relative POSIX file paths without traversal, absolute roots, whitespace or control characters, except generated absolute scratchpad `.pyi` paths. A test id has a `tests/` `.py` file prefix followed by `::` and its exact pytest selector, including class/parameter components. Do not split a selector at whitespace inside a pytest parameter name or interpret it as shell text. Unsupported path spelling is an explicit validation error, not normalization. The form stores view **sources**, not hand-copied `.pyi` filenames: `V(source)` is exactly `str(views.path(cfg, group.id, source))`. The generated path must also fit a single brief line.

An **origin** is `{ design = "docs/superpowers/specs/<file>.md", steps = ["<Order id>", ...] }`, with nonempty, distinct line-valued ids. `O(origin)` is the design path followed by `, Order step ` and its single id, or `, Order steps ` and ids joined with `, `. A port has exactly one origin id. Question 3 asks approval of that spelling. Origin attests where text came from; Foreman does not open Gauntlet's design to decide whether a step is approved or text is verbatim.

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

The role leaves the exact multi-file and quoted-change syntax open (question 3). Under the candidate moved template, embedded `"` or ` | ` in before/after text is refused until the owner settles quoting, rather than escaped without a contract. Findings and failure summaries remain evidence, not slots for instructions. Free behavior text and approved step text still require human provenance; structural validation cannot prove that a sentence is an owner's behavior rather than steering.

### Core-builder and seam-builder

| `shape` | Required `form` fields | Optional fields | Rendered lines, in order |
|---|---|---|---|
| `stub` | `step`: approved-step block | `exclude`: nonempty path array | `stub`; step block; one `Step.paths` path per line; then exclusion line if present |
| `build` | `step`: approved-step block; `red`: nonempty array of red-step selections | `exclude` | step block; one `Step.paths` path per line; one resolved new test id per line; then exclusion line if present |
| `rewrite` | `line`: one verbatim mutant-hunter `REWRITE` line | `exclude` | REWRITE line; then exclusion line if present |
| `finding` | `source`, `lines` as defined below | `exclude` | finding lines verbatim; then exclusion line if present |

One exclusion line is exactly `exclude: ` followed by its paths joined with one ASCII space. No `build`, `step:`, `scope:`, `red:`, `tests:`, `design:` or command line is added to a build brief. An approved-step block can itself contain its plan citation and declarations where the approved step lists them; it cannot be used as an appendix for arbitrary signatures or instructions. Questions 3 and 4 cover the open serialization and plan-text boundaries.

For core build, writable files must be under `gauntlet/core/` or exactly `gauntlet/__init__.py`; core stub paths must be under `gauntlet/core/`. The role alone grants creation of absent `gauntlet/__init__.py` and `gauntlet/core/__init__.py` in a stub; Foreman's declared paths must account for any such writes when counting five and landing changes, without adding them as illegal stub-path lines. Question 4 asks how to represent these conditional paths. Seam paths and exclusions must be under `gauntlet/seams/`. Core exclusions must narrow its allowed scope. No exclusion widens scope or adds another shape's field.

For rewrite/finding, `Step.paths` describe ownership/landing limits and are **not** rendered as a scope list. The role fixes scope from its evidence, with the expansion rules stated in its contract. A declared path cannot silently authorize an extra write through the brief; question 6 addresses scope expansion. Stub/rewrite/finding have no red field or test selector line.

### Reviewers

Review forms live in the step file under `[groups.<id>.reviews.<role>]`, separate from writable steps. A configured reviewer is selected by its roster paths as today. Its form defaults to `{ scope = "working-tree" }` when absent; group entries naming an unconfigured reviewer are errors at configuration/queue preflight. The field set is closed by reviewer role:

| Role | Fields | Rendering |
|---|---|---|
| `diff-reviewer` | `scope`: `working-tree` or `range`; `range` required only for `range` | Working tree is the empty string (zero bytes), explicitly legal under its contract; range is its exact value, one line |
| `test-reviewer` | Same scope/range; optional `specs`: ordered distinct array of paths under `docs/superpowers/specs/` | Working tree starts with `working tree`; range starts with its exact value; append one `spec: ` + path per spec |

A range is one single-line `A..B` or `A...B` expression with nonempty revision operands, no whitespace/control characters and no leading `-`. Validation does not resolve git revisions before the target worktree exists. Automatic group reviews require `working-tree`, because Foreman is reviewing an uncommitted group; range forms are available only for an explicit review operation, not a way to omit the group's changes. There is no new standalone review CLI in this design. Optional test specs are explicit fields, never appended because a generic flag or group design happens to exist. A diff-reviewer form with `specs`, empty or not, fails load.

### Findings and their provenance

A finding form's `source` is `{ kind = "reviewer", role = "test-reviewer" | "diff-reviewer", round = "<kept review key>" }` or `{ kind = "gate", run = "<kept gate key>" }`. `lines` is a nonempty array of verbatim lines. The source record is validation/provenance data and never renders. Static forms are structurally checked on load; before dispatch each line must also be an exact member of the cited kept report/run and, for reviewer findings, the dispatcher's upheld set. A missing kept source at load is not an error for a future runtime form. A reference that cannot be resolved at dispatch stops there.

Test-writer accepts test-reviewer findings anchored under `tests/`, with category, defect, failure scenario, docs citation and fix evidence in the role's report format, or gate lines anchored under `tests/`. Builders accept diff-reviewer findings with exactly four ` | `-separated fields: category, writable `path:line`, defect, `fails when: <scenario>`. The seven builder categories are `shortcut`, `gate-gaming`, `misplaced`, `hand-rolled`, `drift`, `shell-text parsing`, `leftover`. Preserve the complete evidence; a quoted reuse target inside a defect is not a second finding anchor. Reject extra fields, empty scenario, unsupported category or an anchor outside the receiver's permitted writes. Reviewer and gate evidence never mix in one form, nor do different review rounds or gate runs.

Generated gate/upheld briefs use these same form constructors and renderer, replacing the current unchecked `brief.finding` path. Route on the finding's primary anchor, not the first incidental path inside a quotation. Preserve order and verbatim lines. The roles require every applicable finding from one source; Foreman's current split by owning step can fragment one round. Question 6 must settle collection by role versus split dispatches before changing this routing. Until settled, refuse an unrepresentable batch with `RulingNeeded` and keep it; do not truncate, merge sources or add explanation to a brief.

## Red evidence into a build

A build's `form.red` is an ordered list of records `{ step = "<earlier red step id>" }`, optionally with `files = ["tests/<file>.py", ...]`. It is not a list of test names guessed by the dispatcher. Each reference must name a `test-writer` red step earlier in the same group; no forward/self/cyclic reference, arbitrary report filename or reference to port/repair/moved. An optional file filter must be a nonempty distinct subset of that producer's declared paths. An absent filter selects all its new ids. This supports M2 3e's separate core and seam consumers and step 32 consuming steps 30, 31 and 31a. Cross-group report lifetime is not introduced here; question 5 covers a sequence that needs it.

At dispatch, parse the current successful producer report as above; select its `BEHAVIOR` ids whose file prefix matches the optional filter. Every id must have a positive behavior index within that producer's form, a canonical test-file prefix covered by its paths, and valid red/rerun evidence. Reject zero selected ids, malformed selectors, duplicate ids within a producer or overlap across selected producers. Order producers by the form, ids within each producer by their `BEHAVIOR` report order. Validate that every reported new id has run evidence, even an id excluded by the consumer's file filter. Never include `KEPT`, `REPLACED`, `RUN ... passed`, or an old id merely because its file was run.

For example, if step 2 reports new ids `tests/test_core_errors.py::test_process_error_fields` and `tests/test_core_errors.py::test_launch_error_hierarchy` in `BEHAVIOR`, with matching red/rerun evidence, step 3's last lines are exactly those two ids, in that order. These example names illustrate the protocol; the queue does not prescribe them. `tests/test_core_errors.py` alone is never a fallback. The builder then executes only those selectors in its existing red/green pytest command, leaving the file's old passing tests outside that initial red check. Foreman's configured whole-file done-check may still run the whole file; its result does not supply ids.

This dispatch is blocked on question 2: the two builder contracts say “paths” under `tests/`, not explicitly pytest node ids. A working pytest selector does not prove a legal role brief. Until the owner accepts that reading or updates the contracts, Foreman must stop before the affected build; it must not fall back to a full file or invent a `red ids:` line.

Keep the raw producer report and the rendered dispatch text (including empty review text) with `EventLog.keep`; use the current group/step report key and a distinct `<group>-<step>-brief` key. Evidence is loaded from the latest successful execution of the referenced producer in this group worktree; rebuilding a process in memory must not lose it. A rerun overwrites that producer's kept report only with its new execution and invalidates later resolutions; rejected, partial or failed reruns cannot leave an earlier success usable. No separate permanent id ledger.

On `retest`, an adjudicated repair may update machinery but cannot add/remove/rename the original red ids. Re-read and validate those selectors against the preserved producer evidence after repair, and rerun the stopped builder against the restored pre-build snapshot as today. A missing selector or changed membership stops for the dispatcher. If tests/claims must change beyond repair, rerun the producer as a red step and replace its evidence before building; question 7 settles the continuation interface. Never try to infer a new red set from a repair's all-green `RUN after` lines. The old text of a report from a different queue/group definition is not trusted: keep a canonical serialization of the producer form, paths and views with the evidence and require it to match the loaded queue on continuation; differences require a producer rerun.

## Load-time checks and migration

`steps.load` reads and validates **all** groups, including ones after the selected group. It returns no partial usable result. Before EventLog creation, worktree creation, view generation or backend start, reject:

- Unknown/missing top-level, group, step, form, behavior, origin, source, selection or review keys; wrong TOML types, bools where integers are expected, invalid discriminants, unsupported role/shape pairs. A field from another shape is an error even when empty. There is no `extra`, appendix, template override or raw suffix slot.
- Empty required strings/arrays, invalid line/block/path syntax, more than five writable files, duplicate paths or ids, files outside the role's writes, invalid exclusions or red-test fields on non-build shapes. Preserve the existing one-check argv, expected-code, unique-group, membership, dependency-cycle and view-order checks, now in the load boundary. Forms do not assert that a syntactically short step satisfies the role's time cap.
- A view not declared for its source, a source not produced by an earlier step under the existing view rule, a missing design origin for a design-derived red/port step, non-spec origins/specs, invalid producer order/shape/file filters, and a review spec field on diff-reviewer. Existence of generated views, report evidence, git ranges and adjudication remain runtime checks.
- Any free-text `brief` key, whether alone, beside a form, blank or nonblank: `steps.<id>.brief: free-text briefs removed; use role, shape and form`. No dual-format mode or automatic text-to-fields parser. Likewise `reviewers.<index>.spec: removed; put explicit specs in the group's test-reviewer review form`. Errors include the queue/config path and exact table/field; they are not agent refusals.

The loader checks structure and syntactic evidence, not whether a behavior was truly quoted, the plan approved, every own-code import viewed, a test actually new in git, or a finding upheld correctly beyond its kept membership. Those attestations remain the dispatcher's responsibility. Unexpected report output stops before the next dispatch, not at load when the output does not exist yet.

### The existing M2 queue

Only the external `~/dev/foreman/queues/m2.toml` is the migration source; it is not edited by this design. It currently has `g1` and steps `1`, `2`, `3`. The dispatcher translates it after the relevant owner rulings:

| Table | Translation |
|---|---|
| `groups.g1` | Keep steps, design and after. Optionally add `[groups.g1.reviews.test-reviewer]` with `scope = "working-tree"`, `specs = ["docs/superpowers/specs/m2-launcher-kinds-design.md"]`; diff-reviewer uses its empty working-tree default. No spec is inferred. |
| `steps.1` | Keep core-builder and errors.py paths; `shape = "stub"`. `form.step` carries the existing approved Order step 1 row plus the two constructor declarations already in its brief, with their exact current indentation and blank lines. Remove the opening `stub` and final path from stored text: the renderer supplies those. Whether those declarations belong to the approved-step text is question 4. |
| `steps.2` | Keep test-writer, paths, views, check and `expect_code = 1`; `shape = "red"`. Set `form.origin = { design = "docs/superpowers/specs/m2-launcher-kinds-design.md", steps = ["2"] }`. Two behavior records preserve exactly the current sentences: `New errors preserve safe data and hierarchy.` and `Errors carry safe operation identifiers and bounded reasons, not credentials, request bodies, inherited environments or arbitrary process output.` Both sources are `gauntlet/core/errors.py`. No replaces. The generated view resolves to `/Users/atom/dev/foreman-views/g1/gauntlet/core/errors.pyi` under the existing run's configuration. |
| `steps.3` | Keep core-builder, errors.py scope and whole-file passing check; `shape = "build"`. `form.step` contains the existing citation `docs/superpowers/specs/m2-launcher-kinds-design.md, Order step 3` and row `3 build | core-builder | gauntlet/core/errors.py | Typed-error constructor and hierarchy tests must pass.` in that order, subject to question 4. Set `form.red = [{ step = "2" }]`; remove the standalone scope and test-file lines from stored text. Scope comes from paths; red selectors come from step 2's report. |

Thus step 2 renders `red\ndesign: docs/superpowers/specs/m2-launcher-kinds-design.md, Order step 2\nbehavior: New errors preserve safe data and hierarchy.\nview: /Users/atom/dev/foreman-views/g1/gauntlet/core/errors.pyi\nbehavior: Errors carry safe operation identifiers and bounded reasons, not credentials, request bodies, inherited environments or arbitrary process output.\nview: /Users/atom/dev/foreman-views/g1/gauntlet/core/errors.pyi` (the displayed `\n` denotes one LF). The origin is added because the current contract requires it; question 1 must resolve the previous refusal before this is dispatched.

For the rest of M2 Order, stub/red/build rows use their matching forms and explicit origins. Step 3e is one red producer with three declared test files and the required view sources. Step 3f selects only new ids in `tests/test_core_config.py` and `tests/test_core_codec.py`; the seam-config consumer needs its own approved step and new ids from `tests/test_seams_config.py`, which the current Order does not assign to a builder. Step 32 selects steps 30, 31 and 31a explicitly. Port rows 3g, 7 and 29c have a design origin, paths and sources, never a build-style red list. Whether their required helper construction is legal port work is question 5, not permission to disguise it as another shape.

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

M2 `3d declaration` is **not** one of core-builder's four legal shapes; M2 `28 dependency` belongs to gatesmith, outside the supplied contracts. Do not invent aliases. Similarly the checked-in `queues/m1.toml` includes gatesmith and needs a separately approved contract/form migration. Old queues fail with actionable diagnostics until translated; no promise that all old queues keep running through a generic free-text escape hatch.

## Homes

| Concern | Home | Forbidden elsewhere |
|---|---|---|
| Shape records and structural grammar | `foreman/forms.py` | Arbitrary brief dictionaries, fallback raw text, runtime role-file parsing, independent shape validators in CLI/loop |
| Group relationships and load boundary | `foreman/steps.py`, calling forms validators | A CLI-only lint requirement that lets another caller load malformed queues |
| All dispatched text | `foreman/brief.py` | Appending lines in loop/backends; inheriting specs from group metadata; a second finding or continuation renderer |
| Generated view identity | Existing `foreman/views.py:path` | Hard-coded scratchpad paths in queue forms or another suffix/root calculation |
| Red-report extraction and finding anchors | `foreman/reports.py` | Parsing BEHAVIOR/RUN text in loop; extracting ids from pytest output, git diffs or guessed names |
| Evidence lifetime and dispatch resolution | `foreman/loop.py` through existing `EventLog.keep/kept` | Another id store, file-level fallback, cross-group report reuse without an approved lifetime |
| Reviewer selection | `foreman/config.py` roster; per-group review forms in step file | Generic `spec` boolean or unvalidated role-specific suffix |
| Approval, brief provenance and legal-shape rulings | Owner and dispatcher | Foreman deciding that a passing test is red, that a role grammar changed, or that an Order mismatch authorizes a new shape |

## Order

Each row is one dispatch, one agent, at most five written files and exactly one done-check. Rows land in order; no row carries a whole-tree run. The existing Foreman `builder` role only names the three earlier designs in its reads/brief rule. **Before dispatching these rows**, the owner must authorize this design for that role (question 8); the table does not grant that authority. Each row's done-check covers its described behavior, including regression cases, rather than merely reproducing a renderer implementation. No code, tests, role or queue changes are part of this document's commit.

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
| 10 | `builder` | `foreman/loop.py`, `foreman/reports.py`, `tests/test_loop.py`, `tests/test_reports.py` | Generated gate/upheld finding forms, source membership and the owner-settled batch routing. | `uv run pytest -q tests/test_loop.py tests/test_reports.py` |
| 11 | `builder` | `foreman/cli.py`, `tests/test_cli.py` | Preflight queue/config matching and parse the owner-settled typed retest envelope. | `uv run pytest -q tests/test_cli.py` |
| 12 | `builder` | `foreman/loop.py`, `tests/test_loop.py` | Preserved red selectors, queue identity and evidence refresh checks on continuation. | `uv run pytest -q tests/test_loop.py` |
| 13 | `builder` | `foreman/brief.py`, `tests/test_brief.py` | Remove superseded unchecked finding/review APIs after all callers move. | `uv run pytest -q tests/test_brief.py` |

Intermediate rows may temporarily leave other consumers awaiting their listed migration; they are not runnable milestone releases. After row 13 the dispatcher runs the repository's normal final gate as a separate bounded verification step, not a second done-check appended to a builder brief. If measured size makes a row exceed its dispatch cap, re-cut it under 7.1f before dispatch. Queue migration is the dispatcher's work after the rulings, outside `builder`'s writes; no row edits the read-only external queue or role sources.

## Questions for the owner

1. **Which installed contracts will the next run use?** The current source permits red `design:` in its required position and test-reviewer `spec:` under the spec directory. Were today's refused lines sent to older installations, the wrong role, or in the wrong position/path? Resolve the installation/brief evidence and bind the runtime roster to these contracts before enabling those fields. No role edits are authorized by this design.
2. **Are pytest node ids legal red test paths?** Do core-builder and seam-builder, as written, accept `tests/file.py::test_name`, class selectors and parameterized node ids wherever they say “red test path”? If not, will the owner amend both contracts to permit exact new-test selectors? This is required to solve additions to existing files; whole-file fallback would reproduce `TEST WRONG`.
3. **Approve the concrete spelling where contracts only specify content?** In particular: `O(origin)` for one/many Order ids; one `files:` per path; the moved before/after quotation syntax and embedded-quote policy; bare step/scope/red-selector lines for builders; space-separated exclusions. Diff-reviewer's zero-byte working-tree brief is explicitly legal, and test-reviewer's `working tree` is explicitly legal. The proposed byte templates for the unspecified pieces remain conditional on this ruling.
4. **What is verbatim approved-step text for M2?** May a stub's Order row be followed by the design's constructor/interface declarations, as the existing queue does, and may a build carry its citation plus Order row? The rows alone do not list the signatures a stub needs. How should conditional core package initializer writes be declared/countable without rendering forbidden stub paths? Settle this before translating step 1; Foreman must not scrape a design to manufacture approval or a second step.
5. **Resolve the M2 Order/role mismatches.** What legal shape covers `3d declaration` without red tests? Are 3g, 7 and 29c legal ports when they require helper implementation or new fixture tests, given port's preservation rules and requirement that existing tests pass before work? Which approved step builds seam config for 3e's third file? Does its red producer need a cross-group lifetime, or can producer/consumers stay in one group? Gatesmith's dependency and existing M1 briefs need a separate supplied contract and form design before those queues can load; they are not implicitly supported here.
6. **How are whole-source finding batches scoped?** Builders require every upheld diff-reviewer finding from one round or every gate finding from one run; test-writer likewise takes one source. Is Foreman's per-owner subset a legal batch, or must it aggregate by receiving role (possibly over five files, requiring an owner recut)? When a finding/rewrite role may expand scope beyond the named file, how must the dispatcher declare landing paths without adding a forbidden scope list? The runtime must stop on an unresolved batch instead of silently dropping lines or widening ownership.
7. **Which typed continuation is authorized after TEST WRONG?** May `retest` accept only an adjudicated repair with preserved original ids, or also an explicitly rerun red form when claims/membership must change? A repair requires built code and preserves assertions; a stopped builder may have written nothing. Settle what built state and adjudication are required, and how a producer rerun replaces evidence. Until then affected continuations stop; no raw brief bypass remains.
8. **Authorize the Foreman builder and roster migration.** The current local builder explicitly allows only the prior three designs. Who will authorize it to build this Order? The example also names `spec-reviewer`, with no supplied legal contract, and queues/m1 uses gatesmith. Should unsupported roster entries be removed by the dispatcher, or have separately reviewed forms added? This design proposes rejecting them, not silently reinterpreting them as the reviewed Gauntlet roles.

## Owner rulings (2026-10-23)

1. Settled: the refused `design:` and `spec:` lines came from stale installed roles; the installed Codex roles were refreshed from the current Gauntlet role files.
2. Yes: core-builder and seam-builder take red tests by file path or pytest node id (Gauntlet b6801ce).
3. Approved: the concrete spellings as proposed.
4. Yes: an approved-step block may carry the design's citation, its Order row and the constructor/interface declarations that Order step lists. A step that writes a core package initializer lists it as one of its own paths, counted toward the file limit.
5. The M2 Order is amended before its queue is translated. `3d declaration` becomes a core-builder `stub`. Ports 3g, 7 and 29c that need helper code or new fixture tests are split: a red step and a build for the helper first, then a pure port. 3e's seam-config tests get their own seam-builder build step selecting `tests/test_seams_config.py`, in the same group as 3e; no cross-group report lifetime is introduced. Gatesmith steps and `queues/m1.toml` stay unsupported until separately designed.
8. The owner authorizes Foreman's builder to build this design's Order. Roster entries without a supplied contract (`spec-reviewer`, `gatesmith`) are rejected at load with a diagnostic, never reinterpreted.
