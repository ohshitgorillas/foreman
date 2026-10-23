"""Harness-owned check: format, then the check command, in the worktree."""

from dataclasses import dataclass
from pathlib import Path

from foreman import run
from foreman.config import Config
from foreman.run import Result
from foreman.steps import Step


@dataclass(frozen=True)
class Verdict:
    green: bool
    result: Result


def verify(cfg: Config, wt: Path) -> Verdict:
    """Run format_cmd, then check_cmd, in wt. A failed format is red and final."""
    formatted = run.run(cfg.format_cmd, wt)
    if formatted.code != 0:
        return Verdict(False, formatted)
    checked = run.run(cfg.check_cmd, wt)
    return Verdict(checked.code == 0, checked)


def check_step(step: Step, wt: Path) -> Verdict | None:
    """Run step.check in wt; None when it is empty, green when the code is expect_code."""
    if not step.check:
        return None
    result = run.run(step.check, wt)
    return Verdict(result.code == step.expect_code, result)
