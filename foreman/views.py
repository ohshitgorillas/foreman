"""The .pyi views a step reads of code in its worktree."""

import shutil
from pathlib import Path

from foreman import run
from foreman.config import Config
from foreman.steps import Step
from foreman.verify import Verdict


def path(cfg: Config, group_id: str, source: str) -> Path:
    """Where stubgen writes the view of a source in a package."""
    return cfg.view_root / group_id / Path(source).with_suffix(".pyi")


def make(cfg: Config, group_id: str, step: Step, wt: Path) -> Verdict:
    """Run view_cmd for step.views in wt; green when every view exists."""
    out = cfg.view_root / group_id
    result = run.run(
        [*cfg.view_cmd, "-o", str(out), *step.views], wt, cfg.check_seconds
    )
    made = all(path(cfg, group_id, s).is_file() for s in step.views)
    return Verdict(result.code == 0 and made, result)


def clear(cfg: Config, group_id: str) -> None:
    """Remove the group's views when present."""
    shutil.rmtree(cfg.view_root / group_id, ignore_errors=True)
