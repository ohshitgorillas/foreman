"""Runs groups: dispatch order, checks, reviews, commit and cleanup."""

import json
import re
from dataclasses import asdict
from pathlib import Path

from foreman import (
    backends,
    batches,
    brief,
    forms,
    git,
    overload,
    reports,
    steps,
    verify,
    views,
)
from foreman.backends import Backend
from foreman.config import Config
from foreman.errors import (
    BackendError,
    ConfigError,
    FindingsToRule,
    ForemanError,
    RulingNeeded,
    WrongTest,
)
from foreman.events import EventLog
from foreman.run import TIMEOUT_CODE, Result
from foreman.steps import Group, Step, next_ready


def _rule(log: EventLog, group: Group, wt: Path, kept: str, why: str) -> RulingNeeded:
    """Signal Void and return the stop to raise; the text names the kept file and worktree."""
    text = f"{why}; kept {kept}; worktree {wt}"
    log.notify_dispatcher("ruling_needed", group.id, text)
    return RulingNeeded(text)


def _stdout(result: Result) -> str:
    """Keep stdout with a terminal newline when nonempty."""
    return (
        result.out if result.out.endswith("\n") or not result.out else result.out + "\n"
    )


def _text(result: Result) -> str:
    """Output, then any stderr after a blank line so it never joins a findings block."""
    return _stdout(result) + (("\n" + result.err) if result.err else "")


def _dispatch(
    group: Group,
    wt: Path,
    log: EventLog,
    backend: Backend,
    name: str,
    brief_text: str,
    role: str,
    *,
    load: int | None = None,
) -> reports.Report:
    """Keep and record every returned dispatch; an overrun stops the run.

    A refused report is recorded as refused and returned for the caller to handle
    as the Mismatches table says. A start that fails is marked `<name>-unstarted`
    so a reserved round that never reached an agent is told apart from one that
    returned.
    """
    log.keep(f"{name}-brief", brief_text)
    try:
        job = backend.start(brief_text, wt, role)
    except BackendError as error:
        log.keep(f"{name}-unstarted", str(error))
        raise
    result = backend.wait(job)
    path = log.keep(name, _stdout(result))
    log.keep(f"{name}-stderr", result.err)
    report = None if result.code == TIMEOUT_CODE else reports.read(result)
    outcome = (
        "overran" if report is None else "done" if report.kind == "done" else "refused"
    )
    log.dispatched(
        group.id,
        name,
        role,
        load=load,
        seconds=result.seconds,
        tokens=backend.tokens(job),
        outcome=outcome,
    )
    if report is None:
        raise _rule(log, group, wt, str(path), f"{role} timed out")
    return report


def _refusal_stops(
    cfg: Config,
    group: Group,
    wt: Path,
    log: EventLog,
    name: str,
    role: str,
    report: reports.Report,
) -> None:
    """Keep the old stop for callers whose Mismatches rows land in later steps."""
    if report.kind != "done":
        raise _rule(
            log, group, wt, str(cfg.state_dir / f"{name}.txt"), f"{role} {report.kind}"
        )


def _round(cfg: Config, log: EventLog, name: str) -> str | None:
    """The one rerun counter: reserve `<name>-answer-<n>`, counted from kept keys.

    Returns the reserved key, or None once `cfg.answer_rounds` are used.
    """
    number = 1
    while True:
        key = f"{name}-answer-{number}"
        try:
            log.kept(key)
        except ConfigError:
            break
        number += 1
    if number > cfg.answer_rounds:
        return None
    log.keep(key, "")
    return key


def _answer(
    cfg: Config,
    group: Group,
    index: int,
    wt: Path,
    log: EventLog,
    backend: Backend,
    kind: str,
    kept: str,
    *,
    juror_reasons: tuple[str, ...] = (),
) -> None:
    """Rerun a step as a fresh agent from its before-snapshot, or judge past the cap.

    `kept` is the kept report the mismatch was read from; past `answer_rounds` it is
    the judgment's failure report. `juror_reasons` keeps a retried build's rerun
    brief identical to the brief it answers.
    """
    step = group.steps[index]
    name = f"{group.id}-{step.id}"
    log.mismatched(group.id, name, kind, f"step {step.id} {kind}; kept {kept}")
    if _round(cfg, log, name) is None:
        judgment = _judgment(cfg, group, index, wt, log, failure=kept)
        _judge(cfg, group, step.id, judgment, wt, log, backend)
        return
    try:
        before = log.kept(f"{name}-before")
    except ConfigError as error:
        raise _rule(log, group, wt, kept, str(error)) from error
    git.restore(wt, before, step.paths)
    _step(cfg, group, index, wt, log, backend, juror_reasons=juror_reasons)


def _producer_identity(step: Step) -> str:
    """Canonical producer definition; evidence never migrates to another form."""
    return json.dumps(
        {
            "role": step.role,
            "shape": step.form.shape,
            "form": asdict(step.form),
            "paths": step.paths,
            "views": step.views,
        },
        sort_keys=True,
    )


def _parts(name: str, log: EventLog, step: Step) -> list[tuple[str, int]]:
    """Each kept report name of a red step with its behavior count, in order.

    A missing `<name>-parts` reads as the whole step in one report; `step` gives
    its behavior count.
    """
    try:
        raw = log.kept(f"{name}-parts")
    except ConfigError:
        assert isinstance(step.form, forms.Red)
        return [(name, len(step.form.behaviors))]
    try:
        counts = json.loads(raw)
    except ValueError as error:
        raise ConfigError(f"{name}-parts is not JSON: {error}") from error
    if (
        not isinstance(counts, list)
        or not counts
        or any(type(n) is not int or n < 1 for n in counts)
    ):
        raise ConfigError(f"{name}-parts is not a list of behavior counts")
    if len(counts) == 1:
        return [(name, counts[0])]
    return [(f"{name}-part-{k}", n) for k, n in enumerate(counts, 1)]


_RED_OUTCOMES = {"assertion", "not-implemented", "not-raised"}
_NOTE_NUMBER = re.compile(r"(?:LEFT|UNTESTABLE|CONTRADICTS) ([1-9][0-9]*)\b")


def _red_claims(step: Step, name: str, log: EventLog) -> tuple[tuple[int, str], ...]:
    """The (behavior, test id) of every claimed id of a red step, in report order.

    The claims come from reports.red over every part, and every behavior needs an id
    or a numbered note. The notes are kept as `<name>-notes`. Nothing runs here: a
    later build makes these tests pass, so only `_red_evidence` runs verify.red.
    """
    assert isinstance(step.form, forms.Red)
    parts = _parts(name, log, step)
    if sum(n for _, n in parts) != len(step.form.behaviors):
        raise ConfigError("red parts do not cover the producer form's behaviors")
    claimed: list[tuple[int, str]] = []
    notes: list[str] = []
    offset = 0
    for part, count in parts:
        claims = reports.red(log.kept(part))
        covered = {number for number, _ in claims.ids}
        for note in claims.notes:
            match = _NOTE_NUMBER.match(note)
            if match:
                covered.add(int(match.group(1)))
        for number, test_id in claims.ids:
            if not 1 <= number <= count:
                raise ConfigError(f"red behavior index outside producer form: {number}")
            if test_id.partition("::")[0] not in step.paths:
                raise ConfigError(f"red test outside producer paths: {test_id}")
            claimed.append((number + offset, test_id))
        missing = [n for n in range(1, count + 1) if n not in covered]
        if missing:
            raise ConfigError(
                f"red report: behavior {missing[0] + offset} has neither a test id"
                " nor a LEFT, UNTESTABLE or CONTRADICTS note"
            )
        notes.extend(claims.notes)
        offset += count
    ids = [test_id for _, test_id in claimed]
    if len(set(ids)) != len(ids):
        raise ConfigError("red report: a test id is claimed twice across parts")
    log.keep(f"{name}-notes", "\n".join(notes))
    return tuple(claimed)


def _red_evidence(
    cfg: Config, step: Step, name: str, wt: Path, log: EventLog
) -> tuple[tuple[int, str], ...]:
    """The claims of a red step, each id checked red by verify.red.

    Only the step that accepts the red report calls this, before any build runs.
    """
    claimed = _red_claims(step, name, log)
    ids = [test_id for _, test_id in claimed]
    if ids:
        for result in verify.red(cfg, wt, tuple(ids)):
            if result.outcome not in _RED_OUTCOMES:
                raise ConfigError(
                    f"red report: {result.test_id} is not red:"
                    f" {result.outcome} {result.summary!r}"
                )
    return claimed


def _current_red_evidence(
    cfg: Config, step: Step, name: str, wt: Path, log: EventLog
) -> tuple[tuple[int, str], ...]:
    """Resolve one successful generation, including any preserved repair baseline."""
    if log.kept(f"{name}-evidence") != _producer_identity(step):
        raise ConfigError("red producer has no current successful evidence")
    evidence = _red_claims(step, name, log)
    preserved = log.kept(f"{name}-repair-evidence")
    if preserved:
        ids = tuple(test_id for _, test_id in evidence)
        current = verify.capture_repair(cfg, wt, ids)
        if json.dumps(asdict(current), sort_keys=True) != preserved:
            raise RulingNeeded("repair differs from preserved producer evidence")
    return evidence


def _settle(
    group: Group,
    wt: Path,
    log: EventLog,
    found: dict[str, str],
    unresolved: dict[str, str] | None,
) -> None:
    """Hand unresolved producers to the caller's collector, or stop on the first."""
    if unresolved is not None:
        for name, reason in found.items():
            unresolved.setdefault(name, reason)
    elif found:
        name, reason = next(iter(found.items()))
        raise _rule(log, group, wt, f"{name}.txt", reason)


def _red_ids(
    cfg: Config,
    group: Group,
    index: int,
    wt: Path,
    log: EventLog,
    *,
    unresolved: dict[str, str] | None = None,
) -> list[str]:
    """Resolve a build's selected ids, producer by producer.

    A producer whose evidence does not resolve contributes no ids. Given an
    `unresolved` collector, its kept report name and reason go there for the
    Mismatches table's `evidence` rows; without one, the first stops. A selection
    the queue could not have declared always stops.
    """
    step = group.steps[index]
    ids: list[str] = []
    found: dict[str, str] = {}
    if not isinstance(step.form, forms.Build):
        return ids
    for selection in step.form.red:
        name = f"{group.id}-{selection.step}"
        producer = next(
            (s for s in group.steps[:index] if s.id == selection.step), None
        )
        if (
            producer is None
            or producer.role != "test-writer"
            or not isinstance(producer.form, forms.Red)
        ):
            raise _rule(
                log,
                group,
                wt,
                f"{name}.txt",
                "reference is not an earlier red producer",
            )
        if selection.files is not None and (
            not selection.files
            or len(set(selection.files)) != len(selection.files)
            or not set(selection.files) <= set(producer.paths)
        ):
            raise _rule(log, group, wt, f"{name}.txt", "invalid red file filter")
        try:
            evidence = _current_red_evidence(cfg, producer, name, wt, log)
            selected = [
                test_id
                for _, test_id in evidence
                if selection.files is None
                or test_id.partition("::")[0] in selection.files
            ]
            if not selected:
                raise ConfigError("red selection contains no new ids")
            if set(ids).intersection(selected):
                raise ConfigError("overlapping red test ids")
        except (ConfigError, RulingNeeded) as error:
            found.setdefault(name, str(error))
            continue
        ids.extend(selected)
    _settle(group, wt, log, found, unresolved)
    return ids


def _group_red_ids(
    cfg: Config,
    group: Group,
    wt: Path,
    log: EventLog,
    *,
    unresolved: dict[str, str] | None = None,
) -> list[str]:
    """Resolve complete-group evidence through the same producer/build checks."""
    produced: dict[str, list[str]] = {}
    found: dict[str, str] = {}
    for candidate in group.steps:
        if isinstance(candidate.form, forms.Red):
            name = f"{group.id}-{candidate.id}"
            try:
                evidence = _current_red_evidence(cfg, candidate, name, wt, log)
            except (ConfigError, RulingNeeded) as error:
                found.setdefault(name, str(error))
                continue
            produced[name] = [test_id for _, test_id in evidence]
    for index in range(len(group.steps)):
        _red_ids(cfg, group, index, wt, log, unresolved=found)
    _settle(group, wt, log, found, unresolved)
    # A producer a build cannot resolve contributes none of its ids.
    ids: list[str] = []
    for name, produced_ids in produced.items():
        if name not in found:
            ids.extend(i for i in produced_ids if i not in ids)
    return ids


def _queue_identity(group: Group) -> str:
    return json.dumps(asdict(group), sort_keys=True)


def _steps(
    cfg: Config,
    group: Group,
    wt: Path,
    log: EventLog,
    backend: Backend,
    first: int,
    *,
    restore: bool = False,
) -> None:
    """Run steps from first; `restore` puts rerun steps back to their kept snapshots."""
    # Invalidate before any views, dispatch or check can fail on a rerun.
    for producer in group.steps[first:]:
        if isinstance(producer.form, forms.Red):
            log.keep(f"{group.id}-{producer.id}-evidence", "")
    if restore:
        kept = []
        for index in range(first, len(group.steps)):
            try:
                kept.append(
                    (index, log.kept(f"{group.id}-{group.steps[index].id}-before"))
                )
            except ConfigError:
                # A step that never started has no snapshot; it runs as the tree is.
                continue
        # Latest first, so a path shared with an earlier step ends at its snapshot.
        for index, snapshot in reversed(kept):
            git.restore(wt, snapshot, group.steps[index].paths)
    for index in range(first, len(group.steps)):
        _step(cfg, group, index, wt, log, backend)


def _view_paths(
    cfg: Config, group: Group, step: Step, wt: Path, log: EventLog, name: str
) -> dict[str, str]:
    """Generate and validate views through the same path for steps and repairs."""
    if step.views:
        made = views.make(cfg, group.id, step, wt)
        path = log.keep(f"{name}-views", _text(made.result))
        if not made.green:
            raise _rule(log, group, wt, str(path), f"step {step.id} views failed")
    view_paths = {}
    for source in step.views:
        view = views.path(cfg, group.id, source)
        value = str(view)
        if not view.is_file() or any(c in value for c in "\r\n\0"):
            raise _rule(
                log,
                group,
                wt,
                f"{name}-views.txt",
                f"step {step.id} generated view missing or not a single line: {value!r}",
            )
        view_paths[source] = value
    return view_paths


def _step(
    cfg: Config,
    group: Group,
    index: int,
    wt: Path,
    log: EventLog,
    backend: Backend,
    *,
    juror_reasons: tuple[str, ...] = (),
) -> None:
    """Execute one step without replacing its original restoration point."""
    step = group.steps[index]
    view_paths = _view_paths(cfg, group, step, wt, log, f"{group.id}-{step.id}")
    name = f"{group.id}-{step.id}"
    # Kept before evidence resolves, so a verdict on an unstarted build can restore.
    before = git.snapshot(wt)
    try:
        log.kept(f"{name}-before")
    except ConfigError:
        log.keep(f"{name}-before", before)
    unresolved: dict[str, str] = {}
    red_ids = _red_ids(cfg, group, index, wt, log, unresolved=unresolved)
    for producer, reason in unresolved.items():
        try:
            log.kept(f"{producer}-brief")
        except ConfigError:
            # A producer that never dispatched leaves no report to judge.
            raise _rule(log, group, wt, f"{producer}.txt", reason) from None
    if unresolved:
        # The Mismatches table's `evidence` row: judge the build before it starts.
        judgment = _judgment(cfg, group, index, wt, log, dispatched=False)
        _judge(cfg, group, step.id, judgment, wt, log, backend)
        return
    cut = None
    if isinstance(step.form, forms.Red):
        cut = overload.cut_red(
            step.form, step.paths, view_paths, cfg.page_chars, cfg.red_behaviors
        )
        if cut.refused:
            # The Mismatches table's `overload` row: the form is dispatched whole.
            lines = "; ".join(o.line() for o in cut.refused)
            log.mismatched(
                group.id, name, "overload", f"step {step.id} red brief {lines}"
            )
            cut = overload.Cut((step.form,), ())

    def render(form: forms.Form) -> str:
        return brief.render(
            form,
            paths=step.paths,
            view_paths=view_paths,
            red_ids=red_ids,
            juror_reasons=juror_reasons,
        )

    reported = name
    if cut is None:
        report = _dispatch(group, wt, log, backend, name, render(step.form), step.role)
    else:
        assert isinstance(step.form, forms.Red)
        counts = [len(part.behaviors) for part in cut.parts]
        if len(cut.parts) > 1:
            log.keep(f"{name}-brief", render(step.form))
        log.keep(f"{name}-parts", json.dumps(counts))
        for k, part in enumerate(cut.parts, 1):
            reported = name if len(cut.parts) == 1 else f"{name}-part-{k}"
            report = _dispatch(
                group, wt, log, backend, reported, render(part), step.role
            )
            if report.test_wrong or report.kind != "done":
                # Parts that never run leave no report, not an earlier run's.
                for later in range(k + 1, len(cut.parts) + 1):
                    log.keep(f"{name}-part-{later}", "")
                break
    if isinstance(step.form, forms.Red):
        outside = [
            p
            for p in git.changed_since(wt, before, include_ignored=False)
            if not steps.holds(step.paths, p)
        ]
        if outside:
            _restore_outside(group, wt, log, name, before, outside, f"{reported}.txt")
    if report.kind != "done":
        kept = str(cfg.state_dir / f"{reported}.txt")
        _answer(
            cfg,
            group,
            index,
            wt,
            log,
            backend,
            "refused",
            kept,
            juror_reasons=juror_reasons,
        )
        return
    if report.test_wrong:
        judgment = _judgment(cfg, group, index, wt, log)
        _judge(cfg, group, step.id, judgment, wt, log, backend)
        return
    if not _check_step(cfg, group, index, wt, log, backend):
        return
    if isinstance(step.form, forms.Red):
        try:
            _red_evidence(cfg, step, name, wt, log)
        except ConfigError as error:
            # Keep the rejection itself: a cut step has no single report to name.
            kept = str(log.keep(f"{name}-red-report", str(error)))
            _answer(cfg, group, index, wt, log, backend, "red report", kept)
            return
        log.keep(f"{name}-evidence", _producer_identity(step))
        log.keep(f"{name}-repair-evidence", "")


def _check_step(
    cfg: Config,
    group: Group,
    index: int,
    wt: Path,
    log: EventLog,
    backend: Backend,
    *,
    completed: bool = False,
) -> bool:
    """Check a dispatched or repaired build through the same judgment path."""
    step = group.steps[index]
    verdict = verify.check_step(cfg, step, wt)
    if verdict is not None:
        path = log.keep(f"{group.id}-{step.id}-check", _text(verdict.result))
        if verdict.result.code == TIMEOUT_CODE:
            raise _rule(log, group, wt, str(path), f"step {step.id} check timed out")
        if not verdict.green:
            judgment = _judgment(cfg, group, index, wt, log, failure=str(path))
            _judge(cfg, group, step.id, judgment, wt, log, backend, completed=completed)
            return False
    return True


def _judgment(
    cfg: Config,
    group: Group,
    index: int | None,
    wt: Path,
    log: EventLog,
    *,
    failure: str | None = None,
    dispatched: bool = True,
) -> forms.Judgment:
    """Resolve a failed step or completed group through the same evidence path.

    A producer whose evidence does not resolve is logged as an `evidence`
    mismatch and its ids are left out; its kept report stays among the reports.
    `dispatched=False` judges a step that has kept no report of its own.
    """
    unresolved: dict[str, str] = {}
    if index is None:
        name = f"{group.id}-group-check"
        candidates = group.steps
        red_ids = _group_red_ids(cfg, group, wt, log, unresolved=unresolved)
    else:
        step = group.steps[index]
        name = f"{group.id}-{step.id}"
        red_ids = _red_ids(cfg, group, index, wt, log, unresolved=unresolved)
        selected = (
            {r.step for r in step.form.red}
            if isinstance(step.form, forms.Build)
            else set()
        )
        candidates = [
            s for s in group.steps[: index + 1] if s.id in selected or s.id == step.id
        ]
    for producer, reason in unresolved.items():
        log.mismatched(
            group.id,
            producer,
            "evidence",
            f"producer evidence unresolved: {reason}; kept {producer}",
        )
    blocks = []
    paths = []
    try:
        for candidate in candidates:
            key = f"{group.id}-{candidate.id}"
            block = (
                candidate.form.step
                if isinstance(candidate.form, (forms.Build, forms.Stub))
                else log.kept(f"{key}-brief")
            )
            blocks.append(block)
            names = (
                [n for n, _ in _parts(key, log, candidate)]
                if isinstance(candidate.form, forms.Red)
                else [key]
            )
            for n in names:
                try:
                    text = log.kept(n)
                except ConfigError:
                    if n == key and (dispatched or n != name):
                        raise
                    continue
                if text or n == key:
                    paths.append(str(log.keep(n, text)))
        if failure is not None and failure not in paths:
            paths.append(failure)
        judgment = forms.Judgment(
            group.design,
            tuple(s.id for s in candidates),
            "\n".join(blocks),
            tuple(red_ids),
            tuple(paths),
        )
        problems = forms.validate_judgment(judgment, spec_dir=cfg.spec_dir)
        if problems:
            raise ConfigError("; ".join(problems))
    except (ConfigError, RulingNeeded) as error:
        # A step judged before it dispatched has no report; its producer's names it.
        kept = name if dispatched or not unresolved else next(iter(unresolved))
        raise _rule(log, group, wt, f"{kept}.txt", str(error)) from error
    log.keep(f"{name}-judgment", json.dumps(asdict(judgment)))
    return judgment


def _owner_stop(
    group: Group, judgment: forms.Judgment, wt: Path, log: EventLog, why: str
) -> WrongTest:
    log.keep(f"{group.id}-owner-judgment", json.dumps(asdict(judgment)))
    text = f"{why}; kept {', '.join(judgment.reports)}; worktree {wt}"
    log.notify_owner("test_wrong", group.id, text)
    return WrongTest(text)


def _judge(
    cfg: Config,
    group: Group,
    context: str,
    judgment: forms.Judgment,
    wt: Path,
    log: EventLog,
    backend: Backend,
    *,
    completed: bool = False,
) -> None:
    """Reserve attempts before launch so restarts cannot reset the cap.

    A reserved round counts unless its dispatch is marked unstarted.
    """
    log.keep(
        f"{group.id}-pending-judgment",
        json.dumps(
            {
                "context": context,
                "queue": _queue_identity(group),
                "completed": completed or context == "group-check",
                "judgment": asdict(judgment),
            }
        ),
    )
    log.keep(f"{group.id}-owner-judgment", "")
    while True:
        number = 1
        used = 0
        while True:
            name = f"{group.id}-{context}-juror-{number}"
            try:
                log.kept(name)
            except ConfigError:
                break
            try:
                log.kept(f"{name}-unstarted")
            except ConfigError:
                used += 1
            number += 1
        if used >= cfg.juror_rounds:
            raise _owner_stop(group, judgment, wt, log, "juror rounds exhausted")
        text = brief.render(judgment, paths=[], view_paths={}, red_ids=[])
        path = log.keep(name, "")
        report = _dispatch(group, wt, log, backend, name, text, "juror")
        if report.kind != "done":
            problem = f"juror {report.kind}"
        else:
            try:
                verdict = reports.verdict(log.kept(name))
            except ConfigError as error:
                problem = str(error)
            else:
                problem = _outside_judgment(group, judgment, verdict)
        if problem is None:
            break
        # The Mismatches table's `verdict` row: the round is used; the next follows.
        log.mismatched(group.id, name, "verdict", f"{problem}; kept {path}")
    _follow_verdict(
        cfg,
        group,
        judgment,
        wt,
        log,
        backend,
        verdict,
        completed=completed or context == "group-check",
    )


def _target(
    group: Group, judgment: forms.Judgment, verdict: reports.Verdict
) -> int | None:
    """The group index of the verdict's step when it is a judgment candidate."""
    return next(
        (
            i
            for i, s in enumerate(group.steps)
            if s.id == verdict.step and s.id in judgment.steps
        ),
        None,
    )


def _outside_judgment(
    group: Group, judgment: forms.Judgment, verdict: reports.Verdict
) -> str | None:
    """Why a verdict's evidence or target lies outside its judgment, if it does."""
    for evidence in verdict.evidence:
        path = evidence.partition(" | ")[0].rpartition(":")[0]
        if path.startswith("/") and path not in judgment.reports:
            return "verdict evidence is not a judgment report"
    if verdict.action != "owner" and _target(group, judgment, verdict) is None:
        return "verdict target is not a judgment candidate"
    return None


def _follow_verdict(
    cfg: Config,
    group: Group,
    judgment: forms.Judgment,
    wt: Path,
    log: EventLog,
    backend: Backend,
    verdict: reports.Verdict,
    *,
    completed: bool = False,
) -> None:
    """Follow a decoded verdict; never substitute a local fault decision."""
    problem = _outside_judgment(group, judgment, verdict)
    if problem is not None:
        raise _rule(log, group, wt, ", ".join(judgment.reports), problem)
    if verdict.action == "owner":
        raise _owner_stop(group, judgment, wt, log, "; ".join(verdict.reasons))
    target = _target(group, judgment, verdict)
    assert target is not None
    step = group.steps[target]
    if verdict.kind == "build wrong" and verdict.action == "retry":
        # Before the group completes, replay only through the judged step; later
        # steps have not run and stay in normal group order.
        judged = max(i for i, s in enumerate(group.steps) if s.id in judgment.steps)
        replay = [
            index
            for index in _dependent_steps(group, target)
            if completed or index <= judged
        ]
        snapshots = []
        try:
            for index in replay:
                snapshots.append(log.kept(f"{group.id}-{group.steps[index].id}-before"))
        except ConfigError as error:
            raise _rule(
                log, group, wt, ", ".join(judgment.reports), str(error)
            ) from error
        for index in replay:
            if isinstance(group.steps[index].form, forms.Red):
                log.keep(f"{group.id}-{group.steps[index].id}-evidence", "")
        for index, snapshot in reversed(list(zip(replay, snapshots))):
            git.restore(wt, snapshot, group.steps[index].paths)
        for index in replay:
            _step(
                cfg,
                group,
                index,
                wt,
                log,
                backend,
                # Only a build brief defines reasons; other roles get their own brief.
                juror_reasons=(
                    verdict.reasons
                    if index == target and isinstance(step.form, forms.Build)
                    else ()
                ),
            )
        return
    if verdict.kind == "tests wrong" and verdict.action == "repair":
        _repair(
            cfg,
            group,
            target,
            judgment,
            wt,
            log,
            backend,
            verdict,
            completed=completed,
        )
        return
    if verdict.kind == "tests wrong" and verdict.action == "redo red":
        _redo_red(cfg, group, target, judgment, wt, log, backend, completed=completed)
        return
    raise _rule(
        log,
        group,
        wt,
        ", ".join(judgment.reports),
        "tests-wrong transitions await step 19a",
    )


def _dependent_steps(group: Group, target: int) -> list[int]:
    """Follow declared input and output dependencies in group order."""
    replay = [target]
    affected_paths = list(group.steps[target].paths)
    affected_ids = {group.steps[target].id}
    for index in range(target + 1, len(group.steps)):
        later = group.steps[index]
        dependencies = later.paths + later.views
        # Compare both directions for directory-sized path declarations.
        overlaps = any(
            steps.holds(affected_paths, path) for path in dependencies
        ) or any(steps.holds(dependencies, path) for path in affected_paths)
        consumes = isinstance(later.form, forms.Build) and any(
            selection.step in affected_ids for selection in later.form.red
        )
        if overlaps or consumes:
            replay.append(index)
            affected_paths.extend(later.paths)
            affected_ids.add(later.id)
    return replay


def _redo_red(
    cfg: Config,
    group: Group,
    target: int,
    judgment: forms.Judgment,
    wt: Path,
    log: EventLog,
    backend: Backend,
    *,
    completed: bool = False,
) -> None:
    """Replace producer evidence and replay its started dependent builds."""
    producer = group.steps[target]
    name = f"{group.id}-{producer.id}"
    kept = ", ".join(judgment.reports)
    if producer.role != "test-writer" or not isinstance(producer.form, forms.Red):
        raise _rule(log, group, wt, kept, "redo red target is not a red producer")
    try:
        before = log.kept(f"{name}-before")
    except ConfigError as error:
        raise _rule(log, group, wt, kept, str(error)) from error
    dependents = []
    affected = set(_dependent_steps(group, target)) if completed else set()
    for index in range(target + 1, len(group.steps)):
        step = group.steps[index]
        consumes = isinstance(step.form, forms.Build) and any(
            selection.step == producer.id for selection in step.form.red
        )
        if index not in affected and not consumes:
            continue
        try:
            snapshot = log.kept(f"{group.id}-{step.id}-before")
        except ConfigError:
            # A dependent not yet dispatched remains in normal group order.
            continue
        dependents.append((index, snapshot))
    # All build resolutions consult this marker; no stale ids survive a failed
    # restoration, view generation, dispatch, report validation or red check.
    log.keep(f"{name}-evidence", "")
    log.keep(f"{name}-repair-evidence", "")
    for index, _ in dependents:
        if isinstance(group.steps[index].form, forms.Red):
            log.keep(f"{group.id}-{group.steps[index].id}-evidence", "")
    for index, snapshot in reversed(dependents):
        git.restore(wt, snapshot, group.steps[index].paths)
    git.restore(wt, before, producer.paths)
    _step(cfg, group, target, wt, log, backend)
    for index, _ in dependents:
        _step(cfg, group, index, wt, log, backend)


def _repair(
    cfg: Config,
    group: Group,
    target: int,
    judgment: forms.Judgment,
    wt: Path,
    log: EventLog,
    backend: Backend,
    verdict: reports.Verdict,
    *,
    completed: bool = False,
) -> None:
    """Repair machinery against preserved producer evidence, then retry its build.

    A refused repair or rejected repair evidence is the Mismatches table's
    `repair evidence` row: the next judgment attempt opens on the same context.
    """
    producer = group.steps[target]
    name = f"{group.id}-{producer.id}"
    kept = ", ".join(judgment.reports)
    if producer.role != "test-writer" or not isinstance(producer.form, forms.Red):
        raise _rule(log, group, wt, kept, "repair target is not a red producer")
    stopped = next(
        (
            i
            for i in reversed(range(len(group.steps)))
            if group.steps[i].id in judgment.steps
            and isinstance(group.steps[i].form, forms.Build)
        ),
        None,
    )
    if stopped is None and not completed:
        raise _rule(log, group, wt, kept, "repair has no stopped build")
    try:
        if log.kept(f"{name}-evidence") != _producer_identity(producer):
            raise ConfigError("repair producer has no current successful evidence")
        ids = tuple(test_id for _, test_id in _red_claims(producer, name, log))
        if not completed:
            assert stopped is not None
            build = group.steps[stopped]
            before_build = log.kept(f"{group.id}-{build.id}-before")
        original = verify.capture_repair(cfg, wt, ids)
        fingerprint = json.dumps(asdict(original), sort_keys=True)
        try:
            preserved = log.kept(f"{name}-repair-evidence")
        except ConfigError:
            preserved = ""
        if not preserved:
            log.keep(f"{name}-repair-evidence", fingerprint)
        elif preserved != fingerprint:
            raise RulingNeeded("repair differs from preserved producer evidence")
    except (ConfigError, RulingNeeded) as error:
        raise _rule(log, group, wt, kept, str(error)) from error
    repair_name = f"{name}-repair"
    view_paths = _view_paths(cfg, group, producer, wt, log, repair_name)
    form = forms.parse(
        "test-writer",
        "repair",
        {"sources": producer.views, "failures": list(verdict.failures)},
        where=repair_name,
        builders=cfg.builders,
        spec_dir=cfg.spec_dir,
    )
    text = brief.render(form, paths=producer.paths, view_paths=view_paths, red_ids=[])
    before_repair = git.snapshot(wt)
    report = _dispatch(group, wt, log, backend, repair_name, text, "test-writer")
    repair_path = str(cfg.state_dir / f"{repair_name}.txt")
    if report.kind != "done":
        _next_judgment(
            cfg,
            group,
            judgment,
            wt,
            log,
            backend,
            producer,
            f"test-writer repair {report.kind}; kept {repair_path}",
            completed=completed,
        )
        return
    outside = [
        p
        for p in git.changed_since(wt, before_repair, include_ignored=False)
        if not steps.holds(producer.paths, p)
    ]
    if outside:
        _restore_outside(
            group, wt, log, repair_name, before_repair, outside, f"{repair_name}.txt"
        )
    try:
        verify.validate_repair(cfg, original, wt)
    except RulingNeeded as error:
        _next_judgment(
            cfg,
            group,
            judgment,
            wt,
            log,
            backend,
            producer,
            f"{error}; kept {repair_path}",
            completed=completed,
        )
        return
    if completed:
        for index, step in enumerate(group.steps):
            if isinstance(step.form, forms.Build) and any(
                selection.step == producer.id for selection in step.form.red
            ):
                _check_step(cfg, group, index, wt, log, backend, completed=True)
    else:
        assert stopped is not None
        git.restore(wt, before_build, build.paths)
        _step(cfg, group, stopped, wt, log, backend)


def _restore_outside(
    group: Group,
    wt: Path,
    log: EventLog,
    name: str,
    before: str,
    outside: list[str],
    kept: str,
) -> None:
    """Log output outside a dispatch's declared paths and put it back.

    The Mismatches table's `outside paths` row: each path returns to the
    snapshot taken before the dispatch, and the build goes on.
    """
    log.mismatched(
        group.id,
        name,
        "outside paths",
        f"{name} output outside declared paths: {', '.join(outside)}; kept {kept}",
    )
    git.restore(wt, before, outside)


def _next_judgment(
    cfg: Config,
    group: Group,
    judgment: forms.Judgment,
    wt: Path,
    log: EventLog,
    backend: Backend,
    producer: Step,
    why: str,
    *,
    completed: bool,
) -> None:
    """Log a rejected repair and judge the same context again.

    The context is the one `_judge` or `run_retest` kept with this judgment. The
    repair's writes stay in the tree for the juror; a later repair still checks
    them against the preserved producer evidence.
    """
    name = f"{group.id}-{producer.id}-repair"
    log.mismatched(group.id, name, "repair evidence", why)
    try:
        pending = json.loads(log.kept(f"{group.id}-pending-judgment"))
        if pending["judgment"] != json.loads(json.dumps(asdict(judgment))):
            raise ValueError("pending judgment is not this judgment")
        context = pending["context"]
        if not isinstance(context, str):
            raise TypeError("pending judgment has no context")
    except (ConfigError, KeyError, TypeError, ValueError) as error:
        raise _rule(log, group, wt, ", ".join(judgment.reports), str(error)) from error
    _judge(cfg, group, context, judgment, wt, log, backend, completed=completed)


def _gate(cfg: Config, group: Group, wt: Path, log: EventLog, backend: Backend) -> None:
    while True:
        verdict = verify.verify(cfg, wt)
        path = log.keep(f"{group.id}-check", _text(verdict.result))
        if verdict.result.code == TIMEOUT_CODE:
            raise _rule(log, group, wt, str(path), "combined-tree check timed out")
        if verdict.green:
            return
        judgment = _judgment(cfg, group, None, wt, log, failure=str(path))
        _judge(cfg, group, "group-check", judgment, wt, log, backend)


def _review(
    cfg: Config, group: Group, wt: Path, log: EventLog, backend: Backend
) -> None:
    """Dispatch each reviewer whose paths changed; findings stop for Void's ruling.

    A refused reviewer report is logged and the reviewer dispatched again, fresh,
    with the same text under a round from `_round`; past `answer_rounds` it stops.
    """
    log.keep(f"{group.id}-review-queue", _queue_identity(group))
    changed = git.changed_paths(wt)
    kept: list[str] = []
    for reviewer in cfg.reviewers:
        if not any(steps.holds(reviewer.paths, c) for c in changed):
            continue
        name = f"{group.id}-review-{reviewer.role}"
        form = group.reviews.get(reviewer.role)
        if form is None:
            form = forms.parse(
                reviewer.role,
                "review",
                {"scope": "working-tree"},
                where=f"groups.{group.id}.reviews.{reviewer.role}",
                builders=cfg.builders,
                spec_dir=cfg.spec_dir,
            )
        text = brief.render(form, paths=[], view_paths={}, red_ids=[])
        log.keep(f"{name}-handled", "")
        report = _dispatch(group, wt, log, backend, name, text, reviewer.role)
        while report.kind != "done":
            kept_report = str(cfg.state_dir / f"{name}.txt")
            why = f"{reviewer.role} {report.kind}"
            log.mismatched(group.id, name, "refused", f"{why}; kept {kept_report}")
            if _round(cfg, log, name) is None:
                raise _rule(log, group, wt, kept_report, why)
            report = _dispatch(group, wt, log, backend, name, text, reviewer.role)
        if report.findings:
            kept.append(name)
    if kept:
        text = f"reviews {', '.join(kept)}; worktree {wt}"
        log.notify_dispatcher("findings", group.id, text)
        raise FindingsToRule(text)


def _land(cfg: Config, group: Group, wt: Path, log: EventLog) -> str:
    roots = [p for s in group.steps for p in s.paths]
    paths = [c for c in git.changed_paths(wt) if steps.holds(roots, c)]
    if not paths:
        raise _rule(log, group, wt, "none", "nothing changed under the group's paths")
    ids = ", ".join(s.id for s in group.steps)
    commit = git.commit_paths(wt, paths, f"Group {group.id}: steps {ids}")
    git.push(wt)
    git.remove_worktree(cfg, wt)
    views.clear(cfg, group.id)
    log.emit("group_done", group.id, commit=commit)
    return commit


def run_group(cfg: Config, group: Group, log: EventLog, backend: Backend) -> str:
    """Run a group to its commit; stops leave the worktree in place."""
    wt = git.make_worktree(cfg, group.id)
    log.emit("group_started", group.id)
    _steps(cfg, group, wt, log, backend, 0)
    return _check_review_land(cfg, group, wt, log, backend)


def run_resume(
    cfg: Config,
    group: Group,
    step_id: str,
    log: EventLog,
    backend: Backend | None = None,
) -> str:
    """Rerun a kept group from step_id on its worktree; earlier steps' state stays."""
    first = next((i for i, s in enumerate(group.steps) if s.id == step_id), None)
    if first is None:
        known = ", ".join(s.id for s in group.steps)
        raise ConfigError(f"group {group.id} has no step {step_id!r} (steps: {known})")
    try:
        wt = git.worktree(cfg, group.id)
    except ForemanError as error:
        raise ConfigError(
            f"group {group.id} has no kept worktree to resume ({error});"
            " start it with foreman run --group"
        ) from error
    backend = backend or backends.get(cfg)
    log.emit("group_started", group.id)
    _steps(cfg, group, wt, log, backend, first, restore=True)
    return _check_review_land(cfg, group, wt, log, backend)


def _check_review_land(
    cfg: Config, group: Group, wt: Path, log: EventLog, backend: Backend
) -> str:
    _gate(cfg, group, wt, log, backend)
    _review(cfg, group, wt, log, backend)
    return _land(cfg, group, wt, log)


def run_upheld(
    cfg: Config,
    group: Group,
    text: str,
    log: EventLog,
    backend: Backend | None = None,
) -> str:
    """Continue a kept group from the findings Void upheld."""
    backend = backend or backends.get(cfg)
    wt = git.worktree(cfg, group.id)
    try:
        if log.kept(f"{group.id}-review-queue") != _queue_identity(group):
            raise ConfigError("review queue changed; rerun producer evidence")
    except ConfigError as error:
        raise _rule(
            log, group, wt, f"{group.id}-review-queue.txt", str(error)
        ) from error
    _group_red_ids(cfg, group, wt, log)
    kept: dict[str, reports.Report] = {}
    for reviewer in cfg.reviewers:
        try:
            kept[reviewer.role] = reports.parse(
                log.kept(f"{group.id}-review-{reviewer.role}")
            )
        except ConfigError:
            continue
    upheld = reports.upheld(text, kept)
    if not upheld:
        return _land(cfg, group, wt, log)
    pending = []
    for role, lines in upheld.items():
        name = f"{group.id}-review-{role}"
        source = forms.ReviewerSource(role, name)
        raw = log.kept(name)
        try:
            handled = log.kept(f"{name}-handled")
        except ConfigError:
            handled = ""
        if handled == raw:
            continue
        cut, unowned = batches.cut(lines, source, group, wt, cfg.batch_budget)
        if unowned:
            raise _rule(
                log,
                group,
                wt,
                name,
                f"{len(unowned)} upheld lines name no group path",
            )
        prepared = []
        for number, batch in enumerate(cut, 1):
            key = f"{name}-batch-{number}"
            if batch.source != source or any(
                line not in kept[role].findings or line not in lines
                for line in batch.lines
            ):
                raise _rule(log, group, wt, name, "batch is outside its upheld source")
            try:
                form = forms.parse(
                    batch.role,
                    "finding",
                    {
                        "source": {"kind": source.kind, **asdict(source)},
                        "lines": list(batch.lines),
                    },
                    where=key,
                    builders=cfg.builders,
                    spec_dir=cfg.spec_dir,
                )
            except ConfigError as error:
                raise _rule(log, group, wt, name, str(error)) from error
            text = brief.render(form, paths=[], view_paths={}, red_ids=[])
            prepared.append((key, batch, text))
        pending.append((name, raw, prepared))
    for name, raw, prepared in pending:
        for key, batch, text in prepared:
            report = _dispatch(
                group, wt, log, backend, key, text, batch.role, load=batch.load
            )
            _refusal_stops(cfg, group, wt, log, key, batch.role, report)
        log.keep(f"{name}-handled", raw)
    return _check_review_land(cfg, group, wt, log, backend)


def run_retest(
    cfg: Config,
    group: Group,
    text: str,
    log: EventLog,
    backend: Backend | None = None,
) -> str:
    """Continue an owner stop through the same decoded verdict transition."""
    verdict = reports.verdict(text)
    wt = git.worktree(cfg, group.id)
    try:
        pending = json.loads(log.kept(f"{group.id}-pending-judgment"))
        owner = json.loads(log.kept(f"{group.id}-owner-judgment"))
        raw = pending["judgment"]
        if owner != raw or not isinstance(pending["completed"], bool):
            raise ValueError("no matching owner stop")
        judgment = forms.Judgment(
            raw["spec"],
            tuple(raw["steps"]),
            raw["step"],
            tuple(raw["red_ids"]),
            tuple(raw["reports"]),
        )
        problems = forms.validate_judgment(judgment, spec_dir=cfg.spec_dir)
        if problems:
            raise ValueError("; ".join(problems))
        context = pending["context"]
        index = next(
            (i for i, step in enumerate(group.steps) if step.id == context), None
        )
        if context != "group-check" and index is None:
            raise ValueError("stopped step is absent from the group")
    except (KeyError, TypeError, ValueError) as error:
        raise ConfigError(
            f"invalid kept owner judgment for {group.id}: {error}"
        ) from error
    # Check the loaded queue and evidence before any transition can restore files.
    # A producer rerun must establish a fresh generation; an old owner stop cannot
    # authorize new forms or bless selectors from a replacement report.
    try:
        if pending.get("queue") != _queue_identity(group):
            raise ConfigError("owner judgment queue changed; rerun producer evidence")
        if context == "group-check":
            current_ids = _group_red_ids(cfg, group, wt, log, unresolved={})
        else:
            assert index is not None
            current_ids = _red_ids(cfg, group, index, wt, log, unresolved={})
        if tuple(current_ids) != judgment.red_ids:
            raise ConfigError("owner judgment red selectors changed; refresh judgment")
    except (ConfigError, RulingNeeded) as error:
        raise _rule(log, group, wt, ", ".join(judgment.reports), str(error)) from error
    log.keep(f"{group.id}-retest-verdict", text)
    backend = backend or backends.get(cfg)
    _follow_verdict(
        cfg,
        group,
        judgment,
        wt,
        log,
        backend,
        verdict,
        completed=pending["completed"],
    )
    log.keep(f"{group.id}-owner-judgment", "")
    log.keep(f"{group.id}-pending-judgment", "")
    if not pending["completed"]:
        assert index is not None
        _steps(cfg, group, wt, log, backend, index + 1)
    return _check_review_land(cfg, group, wt, log, backend)


def run_section(
    cfg: Config,
    groups: list[Group],
    log: EventLog,
    backend: Backend | None = None,
) -> list[str]:
    """Run each ready group until none is ready; notify when every group is done."""
    backend = backend or backends.get(cfg)
    commits: list[str] = []
    while (group := next_ready(groups, log.done())) is not None:
        commits.append(run_group(cfg, group, log, backend))
    if all(g.id in log.done() for g in groups):
        log.notify_owner("milestone_done", None, f"{len(groups)} groups done")
    return commits
