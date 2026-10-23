from dataclasses import replace
from pathlib import Path

import pytest

from foreman import git, loop
from foreman.config import Config, Reviewer
from foreman.errors import Escalated, FindingsToRule, ForemanError, RulingNeeded
from foreman.events import EventLog
from foreman.run import run_ok
from foreman.steps import Group, Step

RED_UNTIL_FIXED = [
    "sh",
    "-c",
    "if grep -q fixed a.py 2>/dev/null; then exit 0; fi; echo a.py:1 bad; exit 1",
]


def _step(sid: str, paths: list[str], **kw: object) -> Step:
    return Step(sid, "builder", f"brief {sid}", paths, kw.get("check", []), 0)  # type: ignore[arg-type]


def _group(*steps: Step, gid: str = "g1") -> Group:
    return Group(gid, list(steps), "design.md", [])


@pytest.fixture
def origin(cfg: Config, tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    run_ok(["git", "init", "-q", "--bare", str(bare)], tmp_path)
    run_ok(["git", "remote", "add", "origin", str(bare)], cfg.target_repo)
    return bare


@pytest.fixture
def log(green_cfg: Config) -> EventLog:
    return EventLog(green_cfg)


def test_run_group_commits_pushes_and_cleans(
    green_cfg, origin, log, fake_backend, capsys
) -> None:
    g = _group(_step("s1", ["a.py"]), _step("s2", ["b.py"]))
    backend = fake_backend([{"a.py": "1"}, {"b.py": "2", "stray.txt": "x"}])
    commit = loop.run_group(green_cfg, g, log, backend)
    assert run_ok(["git", "rev-parse", "foreman/g1"], origin).out.strip() == commit
    subject = run_ok(["git", "log", "-1", "--format=%s", commit], origin).out.strip()
    assert subject == "Group g1: steps s1, s2"
    files = run_ok(["git", "show", "--name-only", "--format=", commit], origin)
    assert sorted(files.out.split()) == ["a.py", "b.py"]
    assert backend.briefs == ["brief s1", "brief s2"]
    assert not (green_cfg.worktree_root / "g1").exists()
    assert log.done() == {"g1"}
    assert log.kept("g1-s1") == ""


def test_refusal_is_a_ruling_and_keeps_worktree(green_cfg, log, fake_backend, capsys):
    g = _group(_step("s1", ["a.py"]))
    backend = fake_backend(outputs=["REJECTED: STEERING too much"])
    with pytest.raises(RulingNeeded):
        loop.run_group(green_cfg, g, log, backend)
    err = capsys.readouterr().err
    assert "[ruling_needed] g1:" in err and "g1-s1.txt" in err
    assert str(green_cfg.worktree_root / "g1") in err
    assert (green_cfg.worktree_root / "g1").exists()


def test_overload_is_a_ruling(green_cfg, log, fake_backend) -> None:
    g = _group(_step("s1", ["a.py"]))
    with pytest.raises(RulingNeeded):
        loop.run_group(green_cfg, g, log, fake_backend(outputs=["OVERLOADED: 9"]))


def test_missed_step_check_is_a_ruling(green_cfg, log, fake_backend) -> None:
    g = _group(_step("s1", ["a.py"], check=["false"]))
    with pytest.raises(RulingNeeded):
        loop.run_group(green_cfg, g, log, fake_backend([{"a.py": "1"}]))


def test_step_check_matching_expect_code_passes(
    green_cfg, origin, log, fake_backend
) -> None:
    g = _group(_step("s1", ["a.py"], check=["true"]))
    loop.run_group(green_cfg, g, log, fake_backend([{"a.py": "1"}]))
    assert log.done() == {"g1"}


def test_red_gate_sends_finding_to_owning_step(
    green_cfg, origin, log, fake_backend
) -> None:
    cfg = replace(green_cfg, check_cmd=RED_UNTIL_FIXED)
    g = _group(_step("s1", ["a.py"]), _step("s2", ["b.py"]))
    backend = fake_backend([{"a.py": "1"}, {"b.py": "2"}, {"a.py": "fixed"}])
    loop.run_group(cfg, g, log, backend)
    assert backend.briefs[2] == "a.py:1 bad"
    assert backend.agents[2] == "builder"
    assert log.done() == {"g1"}


def test_red_twice_escalates_and_keeps_worktree(
    green_cfg, log, fake_backend, capsys
) -> None:
    cfg = replace(green_cfg, check_cmd=RED_UNTIL_FIXED)
    g = _group(_step("s1", ["a.py"]))
    with pytest.raises(Escalated):
        loop.run_group(cfg, g, log, fake_backend([{"a.py": "1"}, {"a.py": "2"}]))
    assert "[red_twice] g1:" in capsys.readouterr().err
    assert (cfg.worktree_root / "g1").exists()


def test_red_line_outside_group_is_a_ruling(green_cfg, log, fake_backend) -> None:
    cfg = replace(green_cfg, check_cmd=["sh", "-c", "echo other.py:3 bad; exit 1"])
    g = _group(_step("s1", ["a.py"]))
    with pytest.raises(RulingNeeded):
        loop.run_group(cfg, g, log, fake_backend([{"a.py": "1"}]))


def test_red_without_path_is_a_ruling(green_cfg, log, fake_backend) -> None:
    cfg = replace(green_cfg, check_cmd=["sh", "-c", "echo broken; exit 1"])
    g = _group(_step("s1", ["a.py"]))
    with pytest.raises(RulingNeeded):
        loop.run_group(cfg, g, log, fake_backend([{"a.py": "1"}]))


def test_nothing_changed_is_a_ruling(green_cfg, log, fake_backend) -> None:
    g = _group(_step("s1", ["a.py"]))
    with pytest.raises(RulingNeeded):
        loop.run_group(green_cfg, g, log, fake_backend())
    assert (green_cfg.worktree_root / "g1").exists()


def _reviewed(cfg: Config) -> Config:
    return replace(cfg, reviewers=[Reviewer("diff-reviewer", ["a.py"], True)])


def test_reviewer_findings_raise_and_are_kept(
    green_cfg, log, fake_backend, capsys
) -> None:
    cfg = _reviewed(green_cfg)
    g = _group(_step("s1", ["a.py"]))
    backend = fake_backend([{"a.py": "1"}], ["", "FINDINGS 1\n  a.py:1 wrong\n"])
    with pytest.raises(FindingsToRule):
        loop.run_group(cfg, g, log, backend)
    assert backend.briefs[1] == "working tree against HEAD\nspec: design.md"
    assert backend.agents[1] == "diff-reviewer"
    assert "a.py:1 wrong" in log.kept("g1-review-diff-reviewer")
    err = capsys.readouterr().err
    assert "[findings] g1:" in err and "g1-review-diff-reviewer" in err
    assert (cfg.worktree_root / "g1").exists()


def test_clean_review_lands(green_cfg, origin, log, fake_backend) -> None:
    cfg = _reviewed(green_cfg)
    g = _group(_step("s1", ["a.py"]))
    backend = fake_backend([{"a.py": "1"}], ["", "FINDINGS 0\n"])
    loop.run_group(cfg, g, log, backend)
    assert log.done() == {"g1"}


def test_reviewer_skipped_when_paths_unchanged(
    green_cfg, origin, log, fake_backend
) -> None:
    cfg = replace(green_cfg, reviewers=[Reviewer("r", ["docs"], False)])
    g = _group(_step("s1", ["a.py"]))
    backend = fake_backend([{"a.py": "1"}])
    loop.run_group(cfg, g, log, backend)
    assert len(backend.briefs) == 1


def _stopped_for_findings(green_cfg, log, fake_backend) -> Config:
    cfg = _reviewed(green_cfg)
    g = _group(_step("s1", ["a.py"]))
    backend = fake_backend([{"a.py": "1"}], ["", "FINDINGS 1\n  a.py:1 wrong\n"])
    with pytest.raises(FindingsToRule):
        loop.run_group(cfg, g, log, backend)
    return cfg


def test_upheld_sends_lines_then_lands(green_cfg, origin, log, fake_backend) -> None:
    cfg = _stopped_for_findings(green_cfg, log, fake_backend)
    g = _group(_step("s1", ["a.py"]))
    backend = fake_backend([{"a.py": "2"}], ["", "FINDINGS 0\n"])
    commit = loop.run_upheld(cfg, g, "a.py:1 wrong\n", log, backend)
    assert backend.briefs[0] == "a.py:1 wrong"
    assert run_ok(["git", "rev-parse", "foreman/g1"], origin).out.strip() == commit
    assert log.done() == {"g1"}
    assert not (cfg.worktree_root / "g1").exists()


def test_upheld_nothing_lands_directly(green_cfg, origin, log, fake_backend) -> None:
    cfg = _stopped_for_findings(green_cfg, log, fake_backend)
    g = _group(_step("s1", ["a.py"]))
    backend = fake_backend()
    loop.run_upheld(cfg, g, "", log, backend)
    assert backend.briefs == []
    assert log.done() == {"g1"}


def test_upheld_line_in_no_review_raises(green_cfg, log, fake_backend) -> None:
    cfg = _stopped_for_findings(green_cfg, log, fake_backend)
    g = _group(_step("s1", ["a.py"]))
    with pytest.raises(ForemanError):
        loop.run_upheld(cfg, g, "a.py:9 invented\n", log, fake_backend())


def test_upheld_unowned_line_is_a_ruling(green_cfg, log, fake_backend) -> None:
    cfg = _reviewed(green_cfg)
    g = _group(_step("s1", ["a.py"]))
    backend = fake_backend([{"a.py": "1"}], ["", "FINDINGS 1\n  z.py:1 elsewhere\n"])
    with pytest.raises(FindingsToRule):
        loop.run_group(cfg, g, log, backend)
    with pytest.raises(RulingNeeded):
        loop.run_upheld(cfg, g, "z.py:1 elsewhere\n", log, fake_backend())


def test_upheld_without_kept_worktree_raises(green_cfg, log, fake_backend) -> None:
    with pytest.raises(ForemanError):
        loop.run_upheld(
            green_cfg, _group(_step("s1", ["a.py"])), "", log, fake_backend()
        )


def test_run_section_runs_in_order_and_notifies_owner(
    green_cfg, origin, log, fake_backend, capsys
) -> None:
    g1 = _group(_step("s1", ["a.py"]), gid="g1")
    g2 = Group("g2", [_step("s2", ["b.py"])], "d.md", ["g1"])
    backend = fake_backend([{"a.py": "1"}, {"b.py": "2"}])
    commits = loop.run_section(green_cfg, [g2, g1], log, backend)
    assert len(commits) == 2
    assert backend.briefs == ["brief s1", "brief s2"]
    assert log.done() == {"g1", "g2"}
    assert "[milestone_done]" in capsys.readouterr().err


def test_run_section_stop_propagates_without_owner_signal(
    green_cfg, log, fake_backend, capsys
) -> None:
    g1 = _group(_step("s1", ["a.py"]))
    with pytest.raises(RulingNeeded):
        loop.run_section(green_cfg, [g1], log, fake_backend(outputs=["OVERLOADED: x"]))
    assert "milestone_done" not in capsys.readouterr().err
    assert git.worktree(green_cfg, "g1").exists()
