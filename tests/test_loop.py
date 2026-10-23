from dataclasses import replace

import pytest
from conftest import make_group, make_step

from foreman import git, loop
from foreman.config import Config, Reviewer
from foreman.errors import (
    ConfigError,
    Escalated,
    FindingsToRule,
    ForemanError,
    RulingNeeded,
    WrongTest,
)
from foreman.events import EventLog
from foreman.run import run_ok

RED_UNTIL_FIXED = [
    "sh",
    "-c",
    "if grep -q fixed a.py 2>/dev/null; then exit 0; fi; echo a.py:1 bad; exit 1",
]


@pytest.fixture
def log(green_cfg: Config) -> EventLog:
    return EventLog(green_cfg)


def test_run_group_commits_pushes_and_cleans(
    green_cfg, origin, log, fake_backend, capsys
) -> None:
    g = make_group(make_step("s1", ["a.py"]), make_step("s2", ["b.py"]))
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
    g = make_group(make_step("s1", ["a.py"]))
    backend = fake_backend(outputs=["REJECTED: STEERING too much"])
    with pytest.raises(RulingNeeded):
        loop.run_group(green_cfg, g, log, backend)
    err = capsys.readouterr().err
    assert "[ruling_needed] g1:" in err and "g1-s1.txt" in err
    assert str(green_cfg.worktree_root / "g1") in err
    assert (green_cfg.worktree_root / "g1").exists()


def test_overload_is_a_ruling(green_cfg, log, fake_backend) -> None:
    g = make_group(make_step("s1", ["a.py"]))
    with pytest.raises(RulingNeeded):
        loop.run_group(green_cfg, g, log, fake_backend(outputs=["OVERLOADED: 9"]))


def test_missed_step_check_is_a_ruling(green_cfg, log, fake_backend) -> None:
    g = make_group(make_step("s1", ["a.py"], check=["false"]))
    with pytest.raises(RulingNeeded):
        loop.run_group(green_cfg, g, log, fake_backend([{"a.py": "1"}]))


def test_step_check_matching_expect_code_passes(
    green_cfg, origin, log, fake_backend
) -> None:
    g = make_group(make_step("s1", ["a.py"], check=["true"]))
    loop.run_group(green_cfg, g, log, fake_backend([{"a.py": "1"}]))
    assert log.done() == {"g1"}


def test_red_gate_sends_finding_to_owning_step(
    green_cfg, origin, log, fake_backend
) -> None:
    cfg = replace(green_cfg, check_cmd=RED_UNTIL_FIXED)
    g = make_group(make_step("s1", ["a.py"]), make_step("s2", ["b.py"]))
    backend = fake_backend([{"a.py": "1"}, {"b.py": "2"}, {"a.py": "fixed"}])
    loop.run_group(cfg, g, log, backend)
    assert backend.briefs[2] == "a.py:1 bad"
    assert backend.agents[2] == "builder"
    assert log.done() == {"g1"}


def test_red_twice_escalates_and_keeps_worktree(
    green_cfg, log, fake_backend, capsys
) -> None:
    cfg = replace(green_cfg, check_cmd=RED_UNTIL_FIXED)
    g = make_group(make_step("s1", ["a.py"]))
    with pytest.raises(Escalated):
        loop.run_group(cfg, g, log, fake_backend([{"a.py": "1"}, {"a.py": "2"}]))
    assert "[red_twice] g1:" in capsys.readouterr().err
    assert (cfg.worktree_root / "g1").exists()


def test_red_line_outside_group_is_a_ruling(green_cfg, log, fake_backend) -> None:
    cfg = replace(green_cfg, check_cmd=["sh", "-c", "echo other.py:3 bad; exit 1"])
    g = make_group(make_step("s1", ["a.py"]))
    with pytest.raises(RulingNeeded):
        loop.run_group(cfg, g, log, fake_backend([{"a.py": "1"}]))


def test_red_without_path_is_a_ruling(green_cfg, log, fake_backend) -> None:
    cfg = replace(green_cfg, check_cmd=["sh", "-c", "echo broken; exit 1"])
    g = make_group(make_step("s1", ["a.py"]))
    with pytest.raises(RulingNeeded):
        loop.run_group(cfg, g, log, fake_backend([{"a.py": "1"}]))


def test_nothing_changed_is_a_ruling(green_cfg, log, fake_backend) -> None:
    g = make_group(make_step("s1", ["a.py"]))
    with pytest.raises(RulingNeeded):
        loop.run_group(green_cfg, g, log, fake_backend())
    assert (green_cfg.worktree_root / "g1").exists()


def _reviewed(cfg: Config) -> Config:
    return replace(cfg, reviewers=[Reviewer("diff-reviewer", ["a.py"], True)])


def test_reviewer_findings_raise_and_are_kept(
    green_cfg, log, fake_backend, capsys
) -> None:
    cfg = _reviewed(green_cfg)
    g = make_group(make_step("s1", ["a.py"]))
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
    g = make_group(make_step("s1", ["a.py"]))
    backend = fake_backend([{"a.py": "1"}], ["", "FINDINGS 0\n"])
    loop.run_group(cfg, g, log, backend)
    assert log.done() == {"g1"}


def test_reviewer_skipped_when_paths_unchanged(
    green_cfg, origin, log, fake_backend
) -> None:
    cfg = replace(green_cfg, reviewers=[Reviewer("r", ["docs"], False)])
    g = make_group(make_step("s1", ["a.py"]))
    backend = fake_backend([{"a.py": "1"}])
    loop.run_group(cfg, g, log, backend)
    assert len(backend.briefs) == 1


def _stopped_for_findings(green_cfg, log, fake_backend) -> Config:
    cfg = _reviewed(green_cfg)
    g = make_group(make_step("s1", ["a.py"]))
    backend = fake_backend([{"a.py": "1"}], ["", "FINDINGS 1\n  a.py:1 wrong\n"])
    with pytest.raises(FindingsToRule):
        loop.run_group(cfg, g, log, backend)
    return cfg


def test_upheld_sends_lines_then_lands(green_cfg, origin, log, fake_backend) -> None:
    cfg = _stopped_for_findings(green_cfg, log, fake_backend)
    g = make_group(make_step("s1", ["a.py"]))
    backend = fake_backend([{"a.py": "2"}], ["", "FINDINGS 0\n"])
    commit = loop.run_upheld(cfg, g, "a.py:1 wrong\n", log, backend)
    assert backend.briefs[0] == "a.py:1 wrong"
    assert run_ok(["git", "rev-parse", "foreman/g1"], origin).out.strip() == commit
    assert log.done() == {"g1"}
    assert not (cfg.worktree_root / "g1").exists()


def test_upheld_nothing_lands_directly(green_cfg, origin, log, fake_backend) -> None:
    cfg = _stopped_for_findings(green_cfg, log, fake_backend)
    g = make_group(make_step("s1", ["a.py"]))
    backend = fake_backend()
    loop.run_upheld(cfg, g, "", log, backend)
    assert backend.briefs == []
    assert log.done() == {"g1"}


def test_upheld_line_in_no_review_raises(green_cfg, log, fake_backend) -> None:
    cfg = _stopped_for_findings(green_cfg, log, fake_backend)
    g = make_group(make_step("s1", ["a.py"]))
    with pytest.raises(ForemanError):
        loop.run_upheld(cfg, g, "a.py:9 invented\n", log, fake_backend())


def test_upheld_unowned_line_is_a_ruling(green_cfg, log, fake_backend) -> None:
    cfg = _reviewed(green_cfg)
    g = make_group(make_step("s1", ["a.py"]))
    backend = fake_backend([{"a.py": "1"}], ["", "FINDINGS 1\n  z.py:1 elsewhere\n"])
    with pytest.raises(FindingsToRule):
        loop.run_group(cfg, g, log, backend)
    with pytest.raises(RulingNeeded):
        loop.run_upheld(cfg, g, "z.py:1 elsewhere\n", log, fake_backend())


def test_upheld_without_kept_worktree_raises(green_cfg, log, fake_backend) -> None:
    with pytest.raises(ForemanError):
        loop.run_upheld(
            green_cfg, make_group(make_step("s1", ["a.py"])), "", log, fake_backend()
        )


def test_run_section_runs_in_order_and_notifies_owner(
    green_cfg, origin, log, fake_backend, capsys
) -> None:
    g1 = make_group(make_step("s1", ["a.py"]), gid="g1")
    g2 = make_group(make_step("s2", ["b.py"]), gid="g2", design="d.md", after=["g1"])
    backend = fake_backend([{"a.py": "1"}, {"b.py": "2"}])
    commits = loop.run_section(green_cfg, [g2, g1], log, backend)
    assert len(commits) == 2
    assert backend.briefs == ["brief s1", "brief s2"]
    assert log.done() == {"g1", "g2"}
    assert "[milestone_done]" in capsys.readouterr().err


def test_run_section_stop_propagates_without_owner_signal(
    green_cfg, log, fake_backend, capsys
) -> None:
    g1 = make_group(make_step("s1", ["a.py"]))
    with pytest.raises(RulingNeeded):
        loop.run_section(green_cfg, [g1], log, fake_backend(outputs=["OVERLOADED: x"]))
    assert "milestone_done" not in capsys.readouterr().err
    assert git.worktree(green_cfg, "g1").exists()


VIEW_CMD = [
    "sh",
    "-c",
    'test -f a.py && mkdir -p "$2" && touch "$2/a.pyi"',
    "sh",
]


def _viewing(cfg: Config) -> Config:
    return replace(cfg, view_cmd=VIEW_CMD)


def test_views_made_after_earlier_step_and_cleared_on_land(
    green_cfg, origin, log, fake_backend
) -> None:
    cfg = _viewing(green_cfg)
    g = make_group(make_step("s1", ["a.py"]), make_step("s2", ["b.py"], views=["a.py"]))
    seen: list[bool] = []

    class Spy(fake_backend):  # type: ignore[valid-type, misc]
        def start(self, brief, worktree, agent):
            seen.append((cfg.view_root / "g1" / "a.pyi").exists())
            return super().start(brief, worktree, agent)

    loop.run_group(cfg, g, log, Spy([{"a.py": "1"}, {"b.py": "2"}]))
    assert seen == [False, True]
    assert not (cfg.view_root / "g1").exists()
    assert log.kept("g1-s2-views") == ""


def test_failed_views_are_a_ruling_before_dispatch(
    green_cfg, log, fake_backend, capsys
) -> None:
    cfg = _viewing(green_cfg)
    g = make_group(make_step("s1", ["b.py"], views=["a.py"]))
    backend = fake_backend()
    with pytest.raises(RulingNeeded):
        loop.run_group(cfg, g, log, backend)
    assert backend.briefs == []
    assert "[ruling_needed] g1:" in capsys.readouterr().err
    assert (cfg.worktree_root / "g1").exists()


@pytest.mark.parametrize("check", [[], ["false"]])
def test_wrong_test_stops_before_check_and_gate(
    green_cfg, log, fake_backend, capsys, check
):
    cfg = replace(green_cfg, check_cmd=["false"])
    g = make_group(
        make_step("tests", ["tests"], role="test-writer"),
        make_step("build", ["a.py"], check=check),
        make_step("later", ["b.py"]),
    )
    output = "TEST WRONG 1:\n  tests/test_a.py::test_a | wrong | spec:1\n"
    backend = fake_backend(
        [{"tests/test_a.py": "red"}, {"a.py": "partial"}], ["", output]
    )
    with pytest.raises(WrongTest):
        loop.run_group(cfg, g, log, backend)
    assert backend.briefs == ["brief tests", "brief build"]
    assert log.kept("g1-build") == output
    assert log.kept("g1-build-before").strip()
    wt = git.worktree(cfg, "g1")
    assert (wt / "a.py").read_text() == "partial"
    assert log.done() == set()
    err = capsys.readouterr().err
    for expected in ("[test_wrong] g1:", "g1-build.txt", "build", "tests", str(wt)):
        assert expected in err
    with pytest.raises(ConfigError):
        log.kept("g1-build-check")
    with pytest.raises(ConfigError):
        log.kept("g1-check")


@pytest.mark.parametrize(
    "lines",
    [
        "outside.py::test_a | wrong",
        "a.py::test_a | self",
        "later.py::test_a | later",
        "tests/a.py::test_a | wrong\n  other/b.py::test_b | wrong",
    ],
)
def test_wrong_tests_need_one_earlier_owner(green_cfg, log, fake_backend, lines):
    g = make_group(
        make_step("tests", ["tests"]),
        make_step("other", ["other"]),
        make_step("build", ["a.py"]),
        make_step("later", ["later.py"]),
    )
    backend = fake_backend(outputs=["", "", f"TEST WRONG 2:\n  {lines}\n"])
    with pytest.raises(RulingNeeded, match="exactly one earlier step"):
        loop.run_group(green_cfg, g, log, backend)
    assert len(backend.briefs) == 3


@pytest.mark.parametrize("fix", ["", "fix tests verbatim\n\nkeep this newline\n"])
def test_retest_restores_paths_then_runs_remaining_steps_and_lands(
    green_cfg, origin, log, fake_backend, fix
):
    g = make_group(
        make_step("tests", ["tests"], role="test-writer", check=["true"]),
        make_step("build", ["a.py", "new.py"]),
        make_step("later", ["b.py"]),
    )
    # The builder owns a.py too, so the snapshot must restore the earlier content.
    backend = fake_backend(
        [
            {"tests/test_a.py": "original", "a.py": "before"},
            {"a.py": "partial", "new.py": "remove me", "stray": "preserve"},
        ],
        ["", "TEST WRONG 1:\n  tests/test_a.py::test_a | wrong\n"],
    )
    with pytest.raises(WrongTest):
        loop.run_group(green_cfg, g, log, backend)
    seen = []

    class Spy(fake_backend):
        def start(self, brief, worktree, agent):
            if brief == "brief build":
                seen.append(
                    (
                        (worktree / "a.py").read_text(),
                        (worktree / "new.py").exists(),
                        (worktree / "stray").read_text(),
                        (worktree / "tests/test_a.py").read_text(),
                    )
                )
            return super().start(brief, worktree, agent)

    files = ([{"tests/test_a.py": "fixed"}] if fix else []) + [
        {"a.py": "finished"},
        {"b.py": "later"},
    ]
    resumed = Spy(files)
    commit = loop.run_retest(green_cfg, g, fix, log, resumed)
    assert seen == [("before", False, "preserve", "fixed" if fix else "original")]
    assert resumed.briefs == ([fix] if fix else []) + ["brief build", "brief later"]
    assert resumed.agents == (["test-writer"] if fix else []) + ["builder", "builder"]
    if fix:
        assert log.kept("g1-tests-retest-check") == ""
    assert run_ok(["git", "show", f"{commit}:a.py"], origin).out == "finished"
    assert log.done() == {"g1"}
    assert not (green_cfg.worktree_root / "g1").exists()


def test_retest_without_wrong_test_report_is_config_error(green_cfg, log, fake_backend):
    git.make_worktree(green_cfg, "g1")
    log.keep("g1-s1", "done")
    backend = fake_backend()
    with pytest.raises(ConfigError, match="no kept wrong-test step"):
        loop.run_retest(green_cfg, make_group(make_step()), "fix", log, backend)
    assert backend.briefs == []


@pytest.mark.parametrize("output", ["REJECTED: STEERING", "OVERLOADED: 9", "done"])
def test_retest_fix_must_pass_report_and_check(green_cfg, log, fake_backend, output):
    g = make_group(
        make_step("tests", ["tests"], check=["false"]),
        make_step("build", ["a.py"]),
    )
    wt = git.make_worktree(green_cfg, g.id)
    log.keep("g1-build-before", git.snapshot(wt))
    log.keep("g1-build", "TEST WRONG 1:\n  tests/a.py::test_a | wrong\n")
    (wt / "a.py").write_text("partial")
    backend = fake_backend(outputs=[output])
    with pytest.raises(RulingNeeded):
        loop.run_retest(green_cfg, g, "fix", log, backend)
    assert backend.briefs == ["fix"]
    assert (wt / "a.py").read_text() == "partial"


def test_retest_can_stop_again_for_wrong_test(green_cfg, log, fake_backend):
    g = make_group(make_step("tests", ["tests"]), make_step("build", ["a.py"]))
    output = "TEST WRONG 1:\n  tests/a.py::test_a | wrong\n"
    with pytest.raises(WrongTest):
        loop.run_group(green_cfg, g, log, fake_backend(outputs=["", output]))
    backend = fake_backend(outputs=[output])
    with pytest.raises(WrongTest):
        loop.run_retest(green_cfg, g, "", log, backend)
    assert backend.briefs == ["brief build"]


@pytest.mark.parametrize("view_code", [0, 1])
def test_retest_makes_test_step_views_before_fix(
    green_cfg, log, fake_backend, view_code
):
    cfg = replace(
        _viewing(green_cfg), view_cmd=VIEW_CMD if view_code == 0 else ["false"]
    )
    g = make_group(
        make_step("stub", ["a.py"]),
        make_step("tests", ["tests"], views=["a.py"]),
        make_step("build", ["b.py"]),
    )
    wt = git.make_worktree(cfg, g.id)
    (wt / "a.py").write_text("stub")
    log.keep("g1-build-before", git.snapshot(wt))
    output = "TEST WRONG 1:\n  tests/a.py::test_a | wrong\n"
    log.keep("g1-build", output)
    backend = fake_backend(outputs=["", output])
    with pytest.raises(RulingNeeded if view_code else WrongTest):
        loop.run_retest(cfg, g, "fix", log, backend)
    assert backend.briefs == ([] if view_code else ["fix", "brief build"])
    assert log.kept("g1-tests-retest-views") == ""
    if not view_code:
        assert (cfg.view_root / "g1/a.pyi").exists()
