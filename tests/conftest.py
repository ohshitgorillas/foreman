"""Shared fixtures."""

import subprocess
from pathlib import Path

import pytest

from foreman.backends import Job
from foreman.config import Config
from foreman.run import Result


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


class FakeBackend:
    """A Backend that writes given files into the worktree instead of an agent."""

    name = "fake"

    def __init__(
        self,
        files: dict[str, str] | None = None,
        resume_files: dict[str, str] | None = None,
    ) -> None:
        self.files = files or {}
        self.resume_files = resume_files or {}
        self.briefs: list[str] = []
        self._n = 0

    def _write(self, files: dict[str, str], worktree: Path) -> None:
        for rel, text in files.items():
            target = worktree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)

    def start(self, brief: str, worktree: Path) -> Job:
        self.briefs.append(brief)
        self._n += 1
        self._write(self.files, worktree)
        return Job(self.name, worktree.name, worktree, f"fake-{self._n}")

    def wait(self, job: Job) -> Result:
        return Result(["fake"], 0, "", "", 0.0)

    def resume(self, job: Job, brief: str) -> Job:
        self.briefs.append(brief)
        self._write(self.resume_files, job.worktree)
        return job


@pytest.fixture
def fake_backend() -> type[FakeBackend]:
    """The FakeBackend class; call it with the files to write."""
    return FakeBackend
