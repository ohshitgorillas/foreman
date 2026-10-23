"""Command line and the step loop: the only module that exits the process."""

import argparse
import sys
from pathlib import Path
from typing import NoReturn

from foreman import backends, brief, config, git, steps, verify
from foreman.config import Config
from foreman.errors import (
    EXIT_OK,
    ConfigError,
    Escalated,
    ForemanError,
    OverloadedStep,
)
from foreman.events import EventLog
from foreman.run import Result
from foreman.steps import Step


def _in_scope(step: Step, changed: list[str]) -> list[str]:
    """Changed paths that are one of the step's paths or lie beneath one."""
    roots = [p.rstrip("/") for p in step.paths]
    return [c for c in changed if any(c == r or c.startswith(r + "/") for r in roots)]


def _keep(cfg: Config, step: Step, attempt: int, result: Result) -> None:
    """Save the agent's own report so a stopped step can be read afterwards."""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    out = cfg.state_dir / f"{step.id}-{attempt}.out"
    out.write_text(result.out + result.err)


def run_step(
    cfg: Config,
    step: Step,
    log: EventLog,
    backend: backends.Backend | None = None,
) -> str:
    """Run one step end to end; return the commit hash. Raises Escalated when red twice."""
    backend = backend or backends.get(cfg)
    wt = git.make_worktree(cfg, step.id)
    try:
        log.emit("step_started", step.id)
        text = brief.render(cfg, step, git.head(wt), {})
        job = backend.start(text, wt, step.agent)
        _keep(cfg, step, 1, backend.wait(job))
        verdict = verify.verify(cfg, wt)
        if not verdict.green:
            log.emit("step_red", step.id, attempt=1)
            job = backend.resume(job, brief.render_resume(step, verdict.result))
            _keep(cfg, step, 2, backend.wait(job))
            verdict = verify.verify(cfg, wt)
        if not verdict.green:
            log.emit("step_red", step.id, attempt=2)
            log.notify_owner(
                "red_twice", step.id, f"red after resume: {' '.join(step.done_check)}"
            )
            raise Escalated(f"step {step.id} is red twice")
        paths = _in_scope(step, git.changed_paths(wt))
        if not paths:
            raise ForemanError(f"step {step.id} changed none of its paths")
        commit = git.commit_paths(wt, paths, f"Step {step.id}")
        git.push(wt)
        log.emit("step_done", step.id, commit=commit)
        return commit
    finally:
        git.remove_worktree(cfg, wt)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="foreman")
    p.add_argument("--config", type=Path, default=Path("foreman.toml"))
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run one step of a milestone's step file")
    r.add_argument("step_file", type=Path)
    r.add_argument("--step", dest="step_id")
    sub.add_parser("status", help="print the one-line status")
    return p


def _select(all_steps: list[Step], step_id: str | None) -> Step:
    if step_id is None:
        chosen = steps.next_ready(all_steps, set())
        if chosen is None:
            raise ConfigError("no step is ready")
        return chosen
    for s in all_steps:
        if s.id == step_id:
            return s
    raise ConfigError(f"unknown step {step_id!r}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        cfg = config.load(args.config)
    except ForemanError as e:
        sys.stderr.write(f"foreman: {e}\n")
        return e.exit_code
    log = EventLog(cfg)
    try:
        if args.command == "status":
            sys.stdout.write(log.status() + "\n")
            return EXIT_OK
        all_steps = steps.load(args.step_file)
        problems = steps.lint(all_steps)
        if problems:
            raise OverloadedStep("; ".join(problems))
        step = _select(all_steps, args.step_id)
        sys.stdout.write(run_step(cfg, step, log) + "\n")
        return EXIT_OK
    except ForemanError as e:
        if not isinstance(e, Escalated):
            log.notify_owner("ruling_needed", None, str(e))
        return e.exit_code


def entry() -> NoReturn:
    sys.exit(main())
