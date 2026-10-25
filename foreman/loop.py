"""Runs groups: dispatch order, checks, reviews, commit and cleanup."""

import json
from dataclasses import asdict
from pathlib import Path

from foreman import backends, batches, brief, forms, git, reports, steps, verify, views
from foreman.backends import Backend
from foreman.config import Config, Reviewer
from foreman.errors import (
    BackendError,
    ConfigError,
    FindingsToRule,
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
    """Keep and record every returned dispatch; overruns and refusals stop the run.

    A start that fails is marked `<name>-unstarted` so a reserved round that never
    reached an agent is told apart from one that returned.
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
    if report.kind != "done":
        raise _rule(log, group, wt, str(path), f"{role} {report.kind}")
    return report


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


def _red_evidence(step: Step, text: str) -> tuple[reports.RedEvidence, ...]:
    evidence = reports.red(text)
    assert isinstance(step.form, forms.Red)
    for item in evidence:
        if not 1 <= item.behavior <= len(step.form.behaviors):
            raise ConfigError(
                f"red behavior index outside producer form: {item.behavior}"
            )
        if item.test_id.partition("::")[0] not in step.paths:
            raise ConfigError(f"red test outside producer paths: {item.test_id}")
    return evidence


def _current_red_evidence(
    step: Step, name: str, wt: Path, log: EventLog
) -> tuple[reports.RedEvidence, ...]:
    """Resolve one successful generation, including any preserved repair baseline."""
    if log.kept(f"{name}-evidence") != _producer_identity(step):
        raise ConfigError("red producer has no current successful evidence")
    evidence = _red_evidence(step, log.kept(name))
    preserved = log.kept(f"{name}-repair-evidence")
    if preserved:
        ids = tuple(item.test_id for item in evidence)
        current = verify.capture_repair(wt, ids)
        if json.dumps(asdict(current), sort_keys=True) != preserved:
            raise RulingNeeded("repair differs from preserved producer evidence")
    return evidence


def _red_ids(group: Group, index: int, wt: Path, log: EventLog) -> list[str]:
    step = group.steps[index]
    if not isinstance(step.form, forms.Build):
        return []
    ids: list[str] = []
    for selection in step.form.red:
        name = f"{group.id}-{selection.step}"
        try:
            producer = next(
                (s for s in group.steps[:index] if s.id == selection.step), None
            )
            if (
                producer is None
                or producer.role != "test-writer"
                or not isinstance(producer.form, forms.Red)
            ):
                raise ConfigError("reference is not an earlier red producer")
            if selection.files is not None and (
                not selection.files
                or len(set(selection.files)) != len(selection.files)
                or not set(selection.files) <= set(producer.paths)
            ):
                raise ConfigError("invalid red file filter")
            evidence = _current_red_evidence(producer, name, wt, log)
            selected = [
                item.test_id
                for item in evidence
                if selection.files is None
                or item.test_id.partition("::")[0] in selection.files
            ]
            if not selected:
                raise ConfigError("red selection contains no new ids")
            if set(ids).intersection(selected):
                raise ConfigError("overlapping red test ids")
            ids.extend(selected)
        except (ConfigError, RulingNeeded) as error:
            raise _rule(log, group, wt, f"{name}.txt", str(error)) from error
    return ids


def _group_red_ids(group: Group, wt: Path, log: EventLog) -> list[str]:
    """Resolve complete-group evidence through the same producer/build checks."""
    ids: list[str] = []
    for candidate in group.steps:
        if isinstance(candidate.form, forms.Red):
            name = f"{group.id}-{candidate.id}"
            try:
                evidence = _current_red_evidence(candidate, name, wt, log)
            except (ConfigError, RulingNeeded) as error:
                raise _rule(log, group, wt, f"{name}.txt", str(error)) from error
            for item in evidence:
                if item.test_id not in ids:
                    ids.append(item.test_id)
    for index in range(len(group.steps)):
        _red_ids(group, index, wt, log)
    return ids


def _queue_identity(group: Group) -> str:
    return json.dumps(asdict(group), sort_keys=True)


def _steps(
    cfg: Config, group: Group, wt: Path, log: EventLog, backend: Backend, first: int
) -> None:
    # Invalidate before any views, dispatch or check can fail on a rerun.
    for producer in group.steps[first:]:
        if isinstance(producer.form, forms.Red):
            log.keep(f"{group.id}-{producer.id}-evidence", "")
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
    red_ids = _red_ids(group, index, wt, log)
    before = git.snapshot(wt)
    try:
        log.kept(f"{group.id}-{step.id}-before")
    except ConfigError:
        log.keep(f"{group.id}-{step.id}-before", before)
    text = brief.render(
        step.form,
        paths=step.paths,
        view_paths=view_paths,
        red_ids=red_ids,
        juror_reasons=juror_reasons,
    )
    report = _dispatch(
        group, wt, log, backend, f"{group.id}-{step.id}", text, step.role
    )
    if isinstance(step.form, forms.Red):
        outside = [
            p
            for p in git.changed_since(wt, before, include_ignored=False)
            if not steps.holds(step.paths, p)
        ]
        if outside:
            raise _rule(
                log,
                group,
                wt,
                f"{group.id}-{step.id}.txt",
                f"step {step.id} red output outside declared paths: {', '.join(outside)}",
            )
    if report.test_wrong:
        judgment = _judgment(group, index, wt, log)
        _judge(cfg, group, step.id, judgment, wt, log, backend)
        return
    if not _check_step(cfg, group, index, wt, log, backend):
        return
    if isinstance(step.form, forms.Red):
        name = f"{group.id}-{step.id}"
        try:
            _red_evidence(step, log.kept(name))
        except ConfigError as error:
            raise _rule(log, group, wt, f"{name}.txt", str(error)) from error
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
    verdict = verify.check_step(step, wt)
    if verdict is not None:
        path = log.keep(f"{group.id}-{step.id}-check", _text(verdict.result))
        if verdict.result.code == TIMEOUT_CODE:
            raise _rule(log, group, wt, str(path), f"step {step.id} check timed out")
        if not verdict.green:
            judgment = _judgment(group, index, wt, log, failure=str(path))
            _judge(cfg, group, step.id, judgment, wt, log, backend, completed=completed)
            return False
    return True


def _judgment(
    group: Group,
    index: int | None,
    wt: Path,
    log: EventLog,
    *,
    failure: str | None = None,
) -> forms.Judgment:
    """Resolve a failed step or completed group through the same evidence path."""
    if index is None:
        name = f"{group.id}-group-check"
        candidates = group.steps
        red_ids = _group_red_ids(group, wt, log)
    else:
        step = group.steps[index]
        name = f"{group.id}-{step.id}"
        red_ids = _red_ids(group, index, wt, log)
        selected = (
            {r.step for r in step.form.red}
            if isinstance(step.form, forms.Build)
            else set()
        )
        candidates = [
            s for s in group.steps[: index + 1] if s.id in selected or s.id == step.id
        ]
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
            paths.append(str(log.keep(key, log.kept(key))))
        if failure is not None:
            paths.append(failure)
        judgment = forms.Judgment(
            group.design,
            tuple(s.id for s in candidates),
            "\n".join(blocks),
            tuple(red_ids),
            tuple(paths),
        )
        problems = forms.validate_judgment(judgment)
        if problems:
            raise ConfigError("; ".join(problems))
    except (ConfigError, RulingNeeded) as error:
        raise _rule(log, group, wt, f"{name}.txt", str(error)) from error
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
    log.keep(name, "")
    _dispatch(group, wt, log, backend, name, text, "juror")
    try:
        verdict = reports.verdict(log.kept(name))
    except ConfigError as error:
        raise _rule(log, group, wt, f"{name}.txt", str(error)) from error
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
    for evidence in verdict.evidence:
        path = evidence.partition(" | ")[0].rpartition(":")[0]
        if path.startswith("/") and path not in judgment.reports:
            raise _rule(
                log,
                group,
                wt,
                ", ".join(judgment.reports),
                "verdict evidence is not a judgment report",
            )
    if verdict.action == "owner":
        raise _owner_stop(group, judgment, wt, log, "; ".join(verdict.reasons))
    target = next(
        (
            i
            for i, s in enumerate(group.steps)
            if s.id == verdict.step and s.id in judgment.steps
        ),
        None,
    )
    if target is None:
        raise _rule(
            log,
            group,
            wt,
            ", ".join(judgment.reports),
            "verdict target is not a judgment candidate",
        )
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
            cfg, group, target, judgment, wt, log, backend, verdict, completed=completed
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
    """Repair machinery against preserved producer evidence, then retry its build."""
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
        ids = tuple(item.test_id for item in _red_evidence(producer, log.kept(name)))
        if not completed:
            assert stopped is not None
            build = group.steps[stopped]
            before_build = log.kept(f"{group.id}-{build.id}-before")
        original = verify.capture_repair(wt, ids)
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
    )
    text = brief.render(form, paths=producer.paths, view_paths=view_paths, red_ids=[])
    before_repair = git.snapshot(wt)
    _dispatch(group, wt, log, backend, repair_name, text, "test-writer")
    outside = [
        p
        for p in git.changed_since(wt, before_repair, include_ignored=False)
        if not steps.holds(producer.paths, p)
    ]
    if outside:
        raise _rule(
            log,
            group,
            wt,
            f"{repair_name}.txt",
            f"repair output outside declared paths: {', '.join(outside)}",
        )
    try:
        verify.validate_repair(original, wt)
    except RulingNeeded as error:
        raise _rule(log, group, wt, f"{repair_name}.txt", str(error)) from error
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


def _gate(cfg: Config, group: Group, wt: Path, log: EventLog, backend: Backend) -> None:
    while True:
        verdict = verify.verify(cfg, wt)
        path = log.keep(f"{group.id}-check", _text(verdict.result))
        if verdict.result.code == TIMEOUT_CODE:
            raise _rule(log, group, wt, str(path), "combined-tree check timed out")
        if verdict.green:
            return
        judgment = _judgment(group, None, wt, log, failure=str(path))
        _judge(cfg, group, "group-check", judgment, wt, log, backend)


def _review(
    group: Group, wt: Path, log: EventLog, backend: Backend, reviewers: list[Reviewer]
) -> None:
    log.keep(f"{group.id}-review-queue", _queue_identity(group))
    changed = git.changed_paths(wt)
    kept: list[str] = []
    for reviewer in reviewers:
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
            )
        text = brief.render(form, paths=[], view_paths={}, red_ids=[])
        log.keep(f"{name}-handled", "")
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


def _check_review_land(
    cfg: Config, group: Group, wt: Path, log: EventLog, backend: Backend
) -> str:
    _gate(cfg, group, wt, log, backend)
    _review(group, wt, log, backend, cfg.reviewers)
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
    _group_red_ids(group, wt, log)
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
                )
            except ConfigError as error:
                raise _rule(log, group, wt, name, str(error)) from error
            text = brief.render(form, paths=[], view_paths={}, red_ids=[])
            prepared.append((key, batch, text))
        pending.append((name, raw, prepared))
    for name, raw, prepared in pending:
        for key, batch, text in prepared:
            _dispatch(group, wt, log, backend, key, text, batch.role, load=batch.load)
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
        problems = forms.validate_judgment(judgment)
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
            current_ids = _group_red_ids(group, wt, log)
        else:
            assert index is not None
            current_ids = _red_ids(group, index, wt, log)
        if tuple(current_ids) != judgment.red_ids:
            raise ConfigError("owner judgment red selectors changed; refresh judgment")
    except (ConfigError, RulingNeeded) as error:
        raise _rule(log, group, wt, ", ".join(judgment.reports), str(error)) from error
    log.keep(f"{group.id}-retest-verdict", text)
    backend = backend or backends.get(cfg)
    _follow_verdict(
        cfg, group, judgment, wt, log, backend, verdict, completed=pending["completed"]
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
