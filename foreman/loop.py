"""Runs groups: dispatch order, checks, reviews, commit and cleanup."""

from pathlib import Path

from foreman import backends, brief, git, reports, steps, verify, views
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
from foreman.run import Result
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
) -> reports.Report:
    """Start a fresh agent, keep its output, and read it; a refusal or overload is a ruling."""
    result = backend.wait(backend.start(brief_text, wt, role))
    path = log.keep(name, _text(result))
    report = reports.read(result)
    if report.kind != "done":
        raise _rule(log, group, wt, str(path), f"{role} {report.kind}")
    return report


def _steps(
    cfg: Config, group: Group, wt: Path, log: EventLog, backend: Backend, first: int
) -> None:
    for index in range(first, len(group.steps)):
        step = group.steps[index]
        if step.views:
            made = views.make(cfg, group.id, step, wt)
            path = log.keep(f"{group.id}-{step.id}-views", _text(made.result))
            if not made.green:
                raise _rule(log, group, wt, str(path), f"step {step.id} views failed")
        log.keep(f"{group.id}-{step.id}-before", git.snapshot(wt))
        text = step.brief
        if step.red:
            ids = reports.failed_ids(log.kept(f"{group.id}-{step.red}-check"))
            if not ids:
                raise _rule(
                    log,
                    group,
                    wt,
                    f"{group.id}-{step.red}-check.txt",
                    f"step {step.id}: step {step.red} check named no failing tests",
                )
            text = "\n".join([step.brief, *ids])
        report = _dispatch(
            group, wt, log, backend, f"{group.id}-{step.id}", text, step.role
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
        report = _dispatch(
            group, wt, log, backend, name, brief.review(reviewer, group), reviewer.role
        )
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
