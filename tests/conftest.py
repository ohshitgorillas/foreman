"""Shared fixtures."""

import subprocess
from pathlib import Path

import pytest

from foreman.config import Config


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """A throwaway git repository with one initial commit."""
    repo = tmp_path / "target"
    repo.mkdir()
    env = [
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "README.md").write_text("initial\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", *env, "commit", "-q", "-m", "initial"], cwd=repo, check=True)
    return repo


@pytest.fixture
def cfg(tmp_path: Path, tmp_repo: Path) -> Config:
    return Config(
        target_repo=tmp_repo,
        worktree_root=tmp_path / "worktrees",
        check_cmd=["make", "check"],
        format_cmd=["ruff", "format"],
        standing_lines=[],
        backend="claude-local",
        state_dir=tmp_path / "state",
    )
