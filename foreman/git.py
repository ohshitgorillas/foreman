"""Git, worktrees and cleanup: the only module that runs git."""

from pathlib import Path

from foreman.config import Config
from foreman.errors import ForemanError
from foreman.run import run_ok


def _branch(step_id: str) -> str:
    return f"foreman/{step_id}"


def make_worktree(cfg: Config, step_id: str) -> Path:
    """Create a worktree for the step on branch foreman/<step_id> from HEAD."""
    wt = cfg.worktree_root / step_id
    if wt.exists():
        raise ForemanError(f"worktree already exists: {wt}")
    cfg.worktree_root.mkdir(parents=True, exist_ok=True)
    run_ok(
        ["git", "worktree", "add", "-B", _branch(step_id), str(wt), "HEAD"],
        cfg.target_repo,
    )
    return wt


def remove_worktree(cfg: Config, wt: Path) -> None:
    """Remove the worktree and its administrative record."""
    run_ok(["git", "worktree", "remove", "--force", str(wt)], cfg.target_repo)
    run_ok(["git", "worktree", "prune"], cfg.target_repo)


def changed_paths(wt: Path) -> list[str]:
    """Paths modified, added, deleted or untracked in the worktree."""
    out = run_ok(["git", "status", "--porcelain", "-z", "-uall"], wt).out
    entries = out.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(entries) and entries[i]:
        status, path = entries[i][:2], entries[i][3:]
        paths.append(path)
        i += 2 if "R" in status or "C" in status else 1  # skip the origin path
    return paths


def head(wt: Path) -> str:
    """The commit hash HEAD points at in the worktree."""
    return run_ok(["git", "rev-parse", "HEAD"], wt).out.strip()


def commit_paths(wt: Path, paths: list[str], message: str) -> str:
    """Commit only the given paths; return the new commit hash."""
    if not paths:
        raise ForemanError("commit_paths needs at least one path")
    run_ok(["git", "add", "--", *paths], wt)
    run_ok(["git", "commit", "--only", "-m", message, "--", *paths], wt)
    return head(wt)


def push(wt: Path) -> None:
    """Push the worktree's branch to origin."""
    run_ok(["git", "push", "origin", "HEAD"], wt)
