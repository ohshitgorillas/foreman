from pathlib import Path

import pytest

from foreman.config import Config
from foreman.errors import ForemanError
from foreman.git import (
    changed_paths,
    commit_paths,
    make_worktree,
    push,
    remove_worktree,
    worktree,
)
from foreman.run import run_ok
from tests.conftest import identify


def test_make_worktree_creates_checkout(cfg: Config) -> None:
    wt = make_worktree(cfg, "s1")
    assert wt == cfg.worktree_root / "s1"
    assert (wt / "README.md").read_text() == "initial\n"
    branch = run_ok(["git", "branch", "--show-current"], wt).out.strip()
    assert branch == "foreman/s1"


def test_make_worktree_twice_raises(cfg: Config) -> None:
    make_worktree(cfg, "s1")
    with pytest.raises(ForemanError):
        make_worktree(cfg, "s1")


def test_remove_worktree_then_recreate(cfg: Config) -> None:
    wt = make_worktree(cfg, "s1")
    (wt / "dirty.txt").write_text("x")
    remove_worktree(cfg, wt)
    assert not wt.exists()
    listing = run_ok(["git", "worktree", "list"], cfg.target_repo).out
    assert str(wt.resolve()) not in listing
    assert make_worktree(cfg, "s1").exists()


def test_changed_paths(cfg: Config) -> None:
    wt = make_worktree(cfg, "s1")
    assert changed_paths(wt) == []
    (wt / "README.md").write_text("changed\n")
    (wt / "sub").mkdir()
    (wt / "sub" / "new.py").write_text("x = 1\n")
    assert sorted(changed_paths(wt)) == ["README.md", "sub/new.py"]


def test_changed_paths_rename(cfg: Config) -> None:
    wt = make_worktree(cfg, "s1")
    run_ok(["git", "mv", "README.md", "MOVED.md"], wt)
    assert changed_paths(wt) == ["MOVED.md"]


def test_commit_paths_commits_only_named(cfg: Config) -> None:
    wt = make_worktree(cfg, "s1")
    identify(wt)
    (wt / "a.txt").write_text("a")
    (wt / "b.txt").write_text("b")
    (wt / "README.md").write_text("edited\n")
    run_ok(["git", "add", "b.txt"], wt)  # staged but not named
    h = commit_paths(wt, ["a.txt"], "step s1")
    assert h == run_ok(["git", "rev-parse", "HEAD"], wt).out.strip()
    files = run_ok(["git", "show", "--name-only", "--format=", "HEAD"], wt)
    assert files.out.split() == ["a.txt"]
    assert sorted(changed_paths(wt)) == ["README.md", "b.txt"]
    assert run_ok(["git", "log", "-1", "--format=%s"], wt).out.strip() == "step s1"


def test_commit_paths_rejects_empty(cfg: Config) -> None:
    wt = make_worktree(cfg, "s1")
    with pytest.raises(ForemanError):
        commit_paths(wt, [], "nothing")


def test_push_to_origin(cfg: Config, tmp_path: Path) -> None:
    bare = tmp_path / "origin.git"
    run_ok(["git", "init", "-q", "--bare", str(bare)], tmp_path)
    run_ok(["git", "remote", "add", "origin", str(bare)], cfg.target_repo)
    wt = make_worktree(cfg, "s1")
    identify(wt)
    (wt / "a.txt").write_text("a")
    h = commit_paths(wt, ["a.txt"], "step s1")
    push(wt)
    assert run_ok(["git", "rev-parse", "foreman/s1"], bare).out.strip() == h


def test_push_without_remote_raises(cfg: Config) -> None:
    wt = make_worktree(cfg, "s1")
    with pytest.raises(ForemanError):
        push(wt)


def test_worktree_finds_kept_checkout(cfg: Config) -> None:
    wt = make_worktree(cfg, "g1")
    assert worktree(cfg, "g1") == wt


def test_worktree_absent_raises(cfg: Config) -> None:
    with pytest.raises(ForemanError):
        worktree(cfg, "g1")


def test_worktree_after_remove_raises(cfg: Config) -> None:
    remove_worktree(cfg, make_worktree(cfg, "g1"))
    with pytest.raises(ForemanError):
        worktree(cfg, "g1")
