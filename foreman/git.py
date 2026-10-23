"""Git, worktrees and cleanup: the only module that runs git."""

from pathlib import Path
from tempfile import TemporaryDirectory

from foreman.config import Config
from foreman.errors import ForemanError
from foreman.run import run, run_ok


def _indexed(wt: Path, index: Path, *args: str) -> str:
    """Run git against a private index, leaving the worktree index alone."""
    result = run(
        ["git", "--literal-pathspecs", *args],
        wt,
        env={"GIT_INDEX_FILE": str(index)},
    )
    if result.code != 0:
        raise ForemanError(f"{result.cmd} exited {result.code}: {result.err.strip()}")
    return result.out


def snapshot(wt: Path) -> str:
    """Capture working files, including untracked and ignored files, as a tree."""
    with TemporaryDirectory() as directory:
        index = Path(directory) / "index"
        _indexed(wt, index, "read-tree", "--empty")
        _indexed(wt, index, "add", "--all", "--force", "--", ".")
        return _indexed(wt, index, "write-tree").strip()


def restore(wt: Path, tree: str, paths: list[str]) -> None:
    """Restore only the named paths, removing files absent from the snapshot."""
    if not paths:
        return
    with TemporaryDirectory() as directory:
        index = Path(directory) / "index"
        _indexed(wt, index, "read-tree", tree)
        # Against this index, only files absent from the snapshot are untracked.
        _indexed(wt, index, "clean", "-fdx", "--", *paths)
        present = _indexed(wt, index, "ls-files", "-z", "--", *paths)
        if present:
            _indexed(
                wt,
                index,
                "restore",
                "--source",
                tree,
                "--worktree",
                "--",
                *present.rstrip("\0").split("\0"),
            )


def _branch(step_id: str) -> str:
    return f"foreman/{step_id}"


def _path(cfg: Config, group_id: str) -> Path:
    return cfg.worktree_root / group_id


def make_worktree(cfg: Config, step_id: str) -> Path:
    """Create a worktree for the step on branch foreman/<step_id> from HEAD."""
    wt = _path(cfg, step_id)
    if wt.exists():
        raise ForemanError(f"worktree already exists: {wt}")
    cfg.worktree_root.mkdir(parents=True, exist_ok=True)
    run_ok(
        ["git", "worktree", "add", "-B", _branch(step_id), str(wt), "HEAD"],
        cfg.target_repo,
    )
    return wt


def worktree(cfg: Config, group_id: str) -> Path:
    """The kept worktree make_worktree created for group_id; raises when absent."""
    wt = _path(cfg, group_id)
    if not wt.is_dir():
        raise ForemanError(f"no worktree for {group_id}: {wt}")
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
