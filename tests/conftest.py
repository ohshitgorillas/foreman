"""Shared fixtures."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from foreman import run
from foreman.backends import Job
from foreman.config import Config
from foreman.run import Result, run_ok


def identify(repo: Path) -> None:
    """Give a repository or worktree a git identity."""
    run_ok(["git", "config", "user.name", "Test"], repo)
    run_ok(["git", "config", "user.email", "test@example.com"], repo)
    run_ok(["git", "config", "commit.gpgsign", "false"], repo)


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """A throwaway git repository with one initial commit."""
    repo = tmp_path / "target"
    repo.mkdir()
    run_ok(["git", "init", "-q", "-b", "main"], repo)
    identify(repo)
    (repo / "README.md").write_text("initial\n")
    run_ok(["git", "add", "README.md"], repo)
    run_ok(["git", "commit", "-q", "-m", "initial"], repo)
    return repo


@pytest.fixture
def cfg(tmp_path: Path, tmp_repo: Path) -> Config:
    return Config(
        target_repo=tmp_repo,
        worktree_root=tmp_path / "worktrees",
        check_cmd=["make", "check"],
        format_cmd=["ruff", "format"],
        reviewers=[],
        backend="claude-local",
        state_dir=tmp_path / "state",
        view_cmd=["stubgen"],
        view_root=tmp_path / "views",
    )


@pytest.fixture
def green_cfg(cfg: Config) -> Config:
    """cfg whose check and format commands both succeed."""
    return replace(cfg, check_cmd=["true"], format_cmd=["true"])


@pytest.fixture
def conf_file(green_cfg: Config, tmp_path: Path) -> Path:
    """green_cfg written as a foreman.toml, every required key included."""
    path = tmp_path / "foreman.toml"
    path.write_text(
        "".join(
            f"{key} = {json.dumps([str(x) for x in v] if isinstance(v, list) else str(v))}\n"
            for key, v in vars(green_cfg).items()
            if key != "reviewers"
        )
    )
    return path


@pytest.fixture
def fake_run(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[tuple[list[str], Path]], dict[str, int]]:
    """Fake run.run: records (cmd, cwd); exit codes are looked up by argv[0]."""
    calls: list[tuple[list[str], Path]] = []
    codes: dict[str, int] = {}

    def fake(cmd, cwd, timeout=None, env=None):
        calls.append((cmd, cwd))
        return Result(cmd, codes.get(cmd[0], 0), f"out {cmd[0]}", "", 0.0)

    monkeypatch.setattr(run, "run", fake)
    return calls, codes


class FakeBackend:
    """A Backend that writes each start's files into the worktree, not an agent.

    The nth start writes files[n] and its wait returns outputs[n].
    """

    name = "fake"

    def __init__(
        self,
        files: list[dict[str, str]] | None = None,
        outputs: list[str] | None = None,
    ) -> None:
        self.files = files or []
        self.outputs = outputs or []
        self.briefs: list[str] = []
        self.agents: list[str] = []
        self._out: dict[str, str] = {}

    def start(self, brief: str, worktree: Path, agent: str) -> Job:
        n = len(self.briefs)
        self.agents.append(agent)
        self.briefs.append(brief)
        for rel, text in (self.files[n] if n < len(self.files) else {}).items():
            target = worktree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        handle = f"fake-{n + 1}"
        self._out[handle] = self.outputs[n] if n < len(self.outputs) else ""
        return Job(self.name, worktree.name, worktree, handle)

    def wait(self, job: Job) -> Result:
        return Result(["fake"], 0, self._out[job.handle], "", 0.0)


@pytest.fixture
def fake_backend() -> type[FakeBackend]:
    """The FakeBackend class; call it with the files to write."""
    return FakeBackend
