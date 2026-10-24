from pathlib import Path

import pytest

from foreman.config import Config
from foreman.errors import ForemanError
from foreman.git import (
    changed_paths,
    changed_since,
    commit_paths,
    make_worktree,
    push,
    remove_worktree,
    restore,
    snapshot,
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


def test_snapshot_preserves_index_head_and_working_files(cfg: Config) -> None:
    wt = make_worktree(cfg, "snapshot")
    (wt / "README.md").write_text("staged\n")
    run_ok(["git", "add", "README.md"], wt)
    (wt / "README.md").write_text("working\n")
    (wt / "new file").write_bytes(b"\x00\xff\n")
    (wt / ".gitignore").write_text("ignored\n")
    (wt / "ignored").write_text("ignored content\n")
    (wt / "executable").write_text("#!/bin/sh\n")
    (wt / "executable").chmod(0o755)
    (wt / "link").symlink_to("new file")
    index = Path(run_ok(["git", "rev-parse", "--git-path", "index"], wt).out.strip())
    before_index = index.read_bytes()
    before_head = run_ok(["git", "rev-parse", "HEAD"], wt).out
    before_files = run_ok(["git", "status", "--porcelain", "-uall"], wt).out

    tree = snapshot(wt)

    assert run_ok(["git", "cat-file", "-t", tree], wt).out == "tree\n"
    assert run_ok(["git", "show", f"{tree}:README.md"], wt).out == "working\n"
    assert run_ok(["git", "show", f"{tree}:ignored"], wt).out == "ignored content\n"
    assert index.read_bytes() == before_index
    assert run_ok(["git", "rev-parse", "HEAD"], wt).out == before_head
    assert run_ok(["git", "status", "--porcelain", "-uall"], wt).out == before_files
    assert (wt / "new file").read_bytes() == b"\x00\xff\n"
    assert (wt / "link").readlink() == Path("new file")
    assert (wt / "executable").stat().st_mode & 0o111

    (wt / "new file").unlink()
    (wt / "link").unlink()
    (wt / "executable").chmod(0o644)
    restore(wt, tree, ["new file", "link", "executable"])
    assert (wt / "new file").read_bytes() == b"\x00\xff\n"
    assert (wt / "link").readlink() == Path("new file")
    assert (wt / "executable").stat().st_mode & 0o111


def test_restore_directory_and_absent_files_keeps_other_changes(cfg: Config) -> None:
    wt = make_worktree(cfg, "restore")
    (wt / "owned").mkdir()
    (wt / "owned" / "before").write_text("earlier step\n")
    (wt / "README.md").unlink()
    tree = snapshot(wt)
    (wt / "owned" / "before").unlink()
    (wt / "owned" / "new").write_text("staged new\n")
    run_ok(["git", "add", "owned/new"], wt)
    (wt / "owned" / ".gitignore").write_text("ignored\n")
    (wt / "owned" / "ignored").write_text("new ignored\n")
    (wt / "README.md").write_text("recreated\n")
    (wt / "outside").write_text("test fix\n")
    (wt / "owned-sibling").write_text("keep\n")
    staged = run_ok(["git", "diff", "--cached"], wt).out
    before_head = run_ok(["git", "rev-parse", "HEAD"], wt).out

    restore(wt, tree, ["owned", "README.md", "never-existed"])

    assert sorted(p.name for p in (wt / "owned").iterdir()) == ["before"]
    assert (wt / "owned" / "before").read_text() == "earlier step\n"
    assert not (wt / "README.md").exists()
    assert (wt / "outside").read_text() == "test fix\n"
    assert (wt / "owned-sibling").read_text() == "keep\n"
    assert run_ok(["git", "diff", "--cached"], wt).out == staged
    assert run_ok(["git", "rev-parse", "HEAD"], wt).out == before_head


def test_restore_empty_paths_changes_nothing(cfg: Config) -> None:
    wt = make_worktree(cfg, "empty")
    tree = snapshot(wt)
    (wt / "README.md").write_text("keep\n")
    restore(wt, tree, [])
    assert (wt / "README.md").read_text() == "keep\n"


def test_restore_literal_path_and_file_directory_swaps(cfg: Config) -> None:
    wt = make_worktree(cfg, "swaps")
    (wt / "literal[1]").write_text("literal\n")
    (wt / "literal1").write_text("other\n")
    (wt / "directory").mkdir()
    (wt / "directory" / "child").write_text("child\n")
    tree = snapshot(wt)
    (wt / "literal[1]").unlink()
    (wt / "literal[1]").mkdir()
    (wt / "literal[1]" / "new").write_text("new\n")
    (wt / "literal1").write_text("keep\n")
    (wt / "directory" / "child").unlink()
    (wt / "directory").rmdir()
    (wt / "directory").write_text("file now\n")

    restore(wt, tree, ["literal[1]", "directory"])

    assert (wt / "literal[1]").read_text() == "literal\n"
    assert (wt / "literal1").read_text() == "keep\n"
    assert (wt / "directory" / "child").read_text() == "child\n"


def test_restore_invalid_tree_leaves_files_alone(cfg: Config) -> None:
    wt = make_worktree(cfg, "invalid")
    (wt / "new").write_text("keep\n")
    with pytest.raises(ForemanError):
        restore(wt, "not-a-tree", ["new"])
    assert (wt / "new").read_text() == "keep\n"


def test_changed_since_uses_snapshot_instead_of_head_or_index(cfg: Config) -> None:
    wt = make_worktree(cfg, "since")
    (wt / "README.md").write_text("staged\n")
    run_ok(["git", "add", "README.md"], wt)
    (wt / "README.md").write_text("working\n")
    (wt / ".gitignore").write_text("ignored*\n")
    (wt / "ignored-existing").write_text("earlier step\n")
    (wt / "untracked-existing").write_text("earlier step\n")
    tree = snapshot(wt)
    assert changed_since(wt, tree) == []

    # Staging alone changes neither the working files nor their snapshot diff.
    run_ok(["git", "add", "README.md"], wt)
    assert changed_since(wt, tree) == []
    (wt / "README.md").write_text("initial\n")
    assert changed_since(wt, tree) == ["README.md"]


def test_changed_since_captures_all_working_changes_without_side_effects(
    cfg: Config,
) -> None:
    wt = make_worktree(cfg, "changes")
    (wt / ".gitignore").write_text("ignored*\n")
    (wt / "ignored-modified").write_text("before\n")
    (wt / "ignored-deleted").write_text("before\n")
    (wt / "renamed-from").write_text("same content\n")
    (wt / "mode").write_text("executable\n")
    (wt / "link").symlink_to("README.md")
    tree = snapshot(wt)

    (wt / "README.md").unlink()
    (wt / "ignored-modified").write_text("after\n")
    (wt / "ignored-deleted").unlink()
    (wt / "ignored-added").write_bytes(b"\x00\xff\n")
    (wt / "renamed-from").rename(wt / "renamed-to")
    (wt / "mode").chmod(0o755)
    (wt / "link").unlink()
    (wt / "link").symlink_to("mode")
    unusual = 'new [file]\twith\n"quotes".py'
    (wt / unusual).write_text("new\n")
    (wt / "staged").write_text("staged\n")
    run_ok(["git", "add", "staged"], wt)
    index = Path(run_ok(["git", "rev-parse", "--git-path", "index"], wt).out.strip())
    before_index = index.read_bytes()
    before_head = run_ok(["git", "rev-parse", "HEAD"], wt).out

    paths = changed_since(wt, tree)

    assert sorted(paths) == sorted(
        [
            "README.md",
            "ignored-modified",
            "ignored-deleted",
            "ignored-added",
            "renamed-from",
            "renamed-to",
            "mode",
            "link",
            unusual,
            "staged",
        ]
    )
    assert index.read_bytes() == before_index
    assert run_ok(["git", "rev-parse", "HEAD"], wt).out == before_head
    assert snapshot(wt) != tree
    assert (wt / "ignored-added").read_bytes() == b"\x00\xff\n"
    assert (wt / "ignored-modified").read_text() == "after\n"
    assert not (wt / "ignored-deleted").exists()
    assert not (wt / "README.md").exists()
    assert not (wt / "renamed-from").exists()
    assert (wt / "renamed-to").read_text() == "same content\n"
    assert (wt / "mode").stat().st_mode & 0o111
    assert (wt / "link").readlink() == Path("mode")
    assert (wt / unusual).read_text() == "new\n"
    assert (wt / "staged").read_text() == "staged\n"


def test_changed_since_invalid_tree_raises(cfg: Config) -> None:
    wt = make_worktree(cfg, "invalid-since")
    with pytest.raises(ForemanError):
        changed_since(wt, "not-a-tree")
