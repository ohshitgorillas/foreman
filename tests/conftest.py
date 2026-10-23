"""Shared fixtures."""

from pathlib import Path

import pytest

from foreman.config import Config


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        target_repo=tmp_path / "target",
        worktree_root=tmp_path / "worktrees",
        check_cmd=["make", "check"],
        format_cmd=["ruff", "format"],
        standing_lines=[],
        backend="claude-local",
        state_dir=tmp_path / "state",
    )
