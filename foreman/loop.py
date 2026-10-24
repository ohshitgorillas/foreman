"""Runs groups: dispatch order, checks, reviews, commit and cleanup."""

import json
from dataclasses import asdict
from pathlib import Path

from foreman import backends, brief, forms, git, reports, steps, verify, views
from foreman.backends import Backend
from foreman.config import Config, Reviewer
from foreman.errors import (
    ConfigError,
    Escalated,
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


def _text(result: Result) -> str:
    """Output, then any stderr after a blank line so it never joins a findings block."""
    out = (
        result.out if result.out.endswith("\n") or not result.out else result.out + "\n"
    )
    return out + (("\n" + result.err) if result.err else "")


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
    """Keep and record every returned dispatch; overruns and refusals stop the run."""
    log.keep(f"{name}-brief", brief_text)
    job = backend.start(brief_text, wt, role)
    result = backend.wait(job)
    path = log.keep(name, _text(result))
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
            if log.kept(f"{name}-evidence") != _producer_identity(producer):
                raise ConfigError("red producer has no current successful evidence")
            evidence = _red_evidence(producer, log.kept(name))
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
        except ConfigError as error:
            raise _rule(log, group, wt, f"{name}.txt", str(error)) from error
    return ids


def _steps(
    cfg: Config, group: Group, wt: Path, log: EventLog, backend: Backend, first: int
) -> None:
    # Invalidate before any views, dispatch or check can fail on a rerun.
    for producer in group.steps[first:]:
        if isinstance(producer.form, forms.Red):
            log.keep(f"{group.id}-{producer.id}-evidence", "")
    for index in range(first, len(group.steps)):
        step = group.steps[index]
        if step.views:
            made = views.make(cfg, group.id, step, wt)
            path = log.keep(f"{group.id}-{step.id}-views", _text(made.result))
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
                    f"{group.id}-{step.id}-views.txt",
                    f"step {step.id} generated view missing or not a single line: {value!r}",
                )
            view_paths[source] = value
        red_ids = _red_ids(group, index, wt, log)
        before = git.snapshot(wt)
        log.keep(f"{group.id}-{step.id}-before", before)
        text = brief.render(
            step.form, paths=step.paths, view_paths=view_paths, red_ids=red_ids
        )
        report = _dispatch(
            group, wt, log, backend, f"{group.id}-{step.id}", text, step.role
        )
        if isinstance(step.form, forms.Red):
            outside = [
                p
                for p in git.changed_since(wt, before)
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
            test = _test_owner(group, index, report, wt, log)
            text = (
                f"kept {group.id}-{step.id}.txt; stopped step {step.id}; "
                f"test step {test.id}; worktree {wt}"
            )
            log.notify_dispatcher("test_wrong", group.id, text)
            raise WrongTest(text)
        verdict = verify.check_step(step, wt)
        if verdict is not None:
            path = log.keep(f"{group.id}-{step.id}-check", _text(verdict.result))
            if not verdict.green:
                raise _rule(log, group, wt, str(path), f"step {step.id} check missed")
        if isinstance(step.form, forms.Red):
            name = f"{group.id}-{step.id}"
            try:
                _red_evidence(step, log.kept(name))
            except ConfigError as error:
                raise _rule(log, group, wt, f"{name}.txt", str(error)) from error
            log.keep(f"{name}-evidence", _producer_identity(step))


def _test_owner(
    group: Group, index: int, report: reports.Report, wt: Path, log: EventLog
) -> Step:
    """Require every wrong-test line to name the same earlier step."""
    owned, unowned = reports.route_tests(report.test_wrong, group)
    earlier = [step for step in group.steps[:index] if step.id in owned]
    if unowned or len(owned) != 1 or len(earlier) != 1:
        raise _rule(
            log,
            group,
            wt,
            f"{group.id}-{group.steps[index].id}.txt",
            "wrong tests must belong to exactly one earlier step",
        )
    return earlier[0]


def _gate(cfg: Config, group: Group, wt: Path, log: EventLog, backend: Backend) -> None:
    verdict = verify.verify(cfg, wt)
    path = log.keep(f"{group.id}-check", _text(verdict.result))
    if verdict.green:
        return
    owned, unowned = reports.route(reports.lines(verdict.result), wt, group)
    if unowned:
        raise _rule(
            log, group, wt, str(path), f"{len(unowned)} red lines name no group path"
        )
    if not owned:
        raise _rule(log, group, wt, str(path), "red output names no path")
    for step in group.steps:
        if step.id in owned:
            _dispatch(
                group,
                wt,
                log,
                backend,
                f"{group.id}-{step.id}-finding",
                brief.finding(owned[step.id]),
                step.role,
            )
    verdict = verify.verify(cfg, wt)
    path = log.keep(f"{group.id}-check-2", _text(verdict.result))
    if not verdict.green:
        text = f"red twice; kept {path}; worktree {wt}"
        log.notify_dispatcher("red_twice", group.id, text)
        raise Escalated(text)


def _review(
    group: Group, wt: Path, log: EventLog, backend: Backend, reviewers: list[Reviewer]
) -> None:
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
    for role, lines in upheld.items():
        owned, unowned = reports.route(lines, wt, group)
        placed = {ln for ls in owned.values() for ln in ls} | set(unowned)
        stray = unowned + [ln for ln in lines if ln not in placed]
        if stray:
            raise _rule(
                log,
                group,
                wt,
                f"{group.id}-review-{role}",
                f"{len(stray)} upheld lines name no group path",
            )
        for step in group.steps:
            if step.id in owned:
                _dispatch(
                    group,
                    wt,
                    log,
                    backend,
                    f"{group.id}-{step.id}-upheld-{role}",
                    brief.finding(owned[step.id]),
                    step.role,
                )
    return _check_review_land(cfg, group, wt, log, backend)


def run_retest(
    cfg: Config,
    group: Group,
    text: str,
    log: EventLog,
    backend: Backend | None = None,
) -> str:
    """Fix tests with Void's verbatim brief and rerun from the stopped step."""
    wt = git.worktree(cfg, group.id)
    for index, stopped in enumerate(group.steps):
        try:
            report = reports.parse(log.kept(f"{group.id}-{stopped.id}"))
        except ConfigError:
            continue
        if report.test_wrong:
            break
    else:
        raise ConfigError(f"no kept wrong-test step for {group.id}")
    test = _test_owner(group, index, report, wt, log)
    backend = backend or backends.get(cfg)
    if text:
        name = f"{group.id}-{test.id}-retest"
        if test.views:
            made = views.make(cfg, group.id, test, wt)
            path = log.keep(f"{name}-views", _text(made.result))
            if not made.green:
                raise _rule(log, group, wt, str(path), f"step {test.id} views failed")
        _dispatch(group, wt, log, backend, name, text, test.role)
        verdict = verify.check_step(test, wt)
        if verdict is not None:
            path = log.keep(f"{name}-check", _text(verdict.result))
            if not verdict.green:
                raise _rule(log, group, wt, str(path), f"step {test.id} check missed")
    git.restore(wt, log.kept(f"{group.id}-{stopped.id}-before"), stopped.paths)
    _steps(cfg, group, wt, log, backend, index)
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
