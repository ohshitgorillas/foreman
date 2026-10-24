from dataclasses import replace

import pytest
from conftest import make_group, make_step

from foreman import forms, git, loop
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
    assert backend.briefs == [
        "stub\nApproved step s1\na.py",
        "stub\nApproved step s2\nb.py",
    ]
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
    assert backend.agents[2] == "core-builder"
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
    return replace(cfg, reviewers=[Reviewer("diff-reviewer", ["a.py"])])


def test_reviewer_findings_raise_and_are_kept(
    green_cfg, log, fake_backend, capsys
) -> None:
    cfg = _reviewed(green_cfg)
    g = make_group(make_step("s1", ["a.py"]))
    backend = fake_backend([{"a.py": "1"}], ["", "FINDINGS 1\n  a.py:1 wrong\n"])
    with pytest.raises(FindingsToRule):
        loop.run_group(cfg, g, log, backend)
    assert backend.briefs[1] == ""
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
    cfg = replace(green_cfg, reviewers=[Reviewer("diff-reviewer", ["docs"])])
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
    assert backend.briefs == [
        "stub\nApproved step s1\na.py",
        "stub\nApproved step s2\nb.py",
    ]
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
    assert backend.briefs == [
        "stub\nApproved step tests\ntests",
        "stub\nApproved step build\na.py",
    ]
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
            if brief == "stub\nApproved step build\na.py\nnew.py":
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
    assert resumed.briefs == ([fix] if fix else []) + [
        "stub\nApproved step build\na.py\nnew.py",
        "stub\nApproved step later\nb.py",
    ]
    assert resumed.agents == (["test-writer"] if fix else []) + [
        "core-builder",
        "core-builder",
    ]
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
    assert backend.briefs == ["stub\nApproved step build\na.py"]


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
    assert backend.briefs == (
        [] if view_code else ["fix", "stub\nApproved step build\nb.py"]
    )
    assert log.kept("g1-tests-retest-views") == ""
    if not view_code:
        assert (cfg.view_root / "g1/a.pyi").exists()


def test_red_renders_generated_view_and_keeps_exact_dispatch(
    green_cfg, log, fake_backend
):
    cfg = _viewing(green_cfg)
    red = make_step(
        "red",
        ["tests/test_a.py"],
        role="test-writer",
        views=["a.py"],
        form=forms.Red((forms.Behavior("Preserve safe data.", "a.py"),)),
    )
    g = make_group(make_step("stub", ["a.py"]), red)
    wt = git.make_worktree(cfg, g.id)
    report = _red_report("tests/test_a.py::test_new")
    backend = fake_backend(
        [{"a.py": "stub"}, {"tests/test_a.py": "red"}], outputs=["", report]
    )
    loop._steps(cfg, g, wt, log, backend, 0)
    expected = f"red\nbehavior: Preserve safe data.\nview: {cfg.view_root}/g1/a.pyi"
    assert backend.briefs[1] == expected
    assert log.kept("g1-red-brief") == expected
    assert log.kept("g1-red") == report + "\n"


@pytest.mark.parametrize("role", ["diff-reviewer", "test-reviewer"])
def test_default_review_brief_is_kept_even_when_empty(
    green_cfg, origin, log, fake_backend, role
):
    cfg = replace(green_cfg, reviewers=[Reviewer(role, ["a.py"])])
    g = make_group(make_step("s1", ["a.py"]))
    backend = fake_backend([{"a.py": "1"}])
    loop.run_group(cfg, g, log, backend)
    expected = "" if role == "diff-reviewer" else "working tree"
    assert backend.briefs[1] == expected
    assert log.kept(f"g1-review-{role}-brief") == expected


def test_test_review_uses_explicit_specs_in_order(green_cfg, origin, log, fake_backend):
    cfg = replace(green_cfg, reviewers=[Reviewer("test-reviewer", ["a.py"])])
    specs = ("docs/superpowers/specs/b.md", "docs/superpowers/specs/a.md")
    g = make_group(
        make_step("s1", ["a.py"]),
        reviews={"test-reviewer": forms.TestReview("working-tree", specs=specs)},
    )
    backend = fake_backend([{"a.py": "1"}])
    loop.run_group(cfg, g, log, backend)
    assert backend.briefs[1] == (
        "working tree\nspec: docs/superpowers/specs/b.md"
        "\nspec: docs/superpowers/specs/a.md"
    )


@pytest.mark.parametrize("outside", ["a.py", "tests/test_other.py", "ignored.txt"])
def test_red_output_outside_paths_stops_before_check_and_next_dispatch(
    green_cfg, log, fake_backend, outside
):
    red = make_step(
        "red",
        ["tests/test_a.py"],
        role="test-writer",
        check=["true"],
        form=forms.Red(()),
    )
    g = make_group(red, make_step("later", ["b.py"]))
    wt = git.make_worktree(green_cfg, g.id)
    (wt / "a.py").write_text("before")
    (wt / ".gitignore").write_text("ignored.txt\n")
    backend = fake_backend([{outside: "unexpected", "tests/test_a.py": "red"}])
    with pytest.raises(
        RulingNeeded, match="red output outside declared paths"
    ) as error:
        loop._steps(green_cfg, g, wt, log, backend, 0)
    assert outside in str(error.value)
    assert "g1-red.txt" in str(error.value)
    assert len(backend.briefs) == 1
    assert log.kept("g1-red-before")
    assert log.kept("g1-red-brief") == "red"
    with pytest.raises(ConfigError):
        log.kept("g1-red-check")


def test_red_scope_uses_snapshot_and_allows_changes_to_its_existing_file(
    green_cfg, log, fake_backend
):
    green_cfg = _viewing(green_cfg)
    red = make_step(
        "red",
        ["tests/test_a.py"],
        role="test-writer",
        views=["a.py"],
        form=forms.Red((forms.Behavior("A behavior", "a.py"),)),
    )
    g = make_group(red)
    wt = git.make_worktree(green_cfg, g.id)
    (wt / "a.py").write_text("earlier stub")
    (wt / "tests").mkdir()
    (wt / "tests/test_a.py").write_text("old tests")
    loop._steps(
        green_cfg,
        g,
        wt,
        log,
        fake_backend(
            [{"tests/test_a.py": "old and new tests"}],
            outputs=[_red_report("tests/test_a.py::test_new")],
        ),
        0,
    )
    assert (wt / "a.py").read_text() == "earlier stub"


def test_generated_view_path_must_fit_single_line(green_cfg, log, fake_backend):
    cfg = replace(_viewing(green_cfg), view_root=green_cfg.view_root / "bad\nroot")
    g = make_group(make_step("s1", ["b.py"], views=["a.py"]))
    wt = git.make_worktree(cfg, g.id)
    (wt / "a.py").write_text("stub")
    backend = fake_backend()
    with pytest.raises(RulingNeeded, match="not a single line"):
        loop._steps(cfg, g, wt, log, backend, 0)
    assert backend.briefs == []


def _red_report(*ids: str, behavior: int = 1) -> str:
    records = [
        "SHAPE red",
        "BRIEF red",
        "VIEWS none",
        "BLOCKED none",
        "GATE not run",
        "CHANGED 1",
        "STATUS done",
    ]
    for test_id in ids:
        records.extend(
            [
                f"BEHAVIOR {behavior} | {test_id} | docs: spec:1 | breaks when: missing",
                f'RUN red | {test_id} | assertion | "missing"',
                f'RUN rerun | {test_id} | assertion | "missing"',
            ]
        )
    records.extend(
        [
            'RUN red | tests/test_a.py::test_existing | passed | "ok"',
            'RUN rerun | tests/test_a.py::test_existing | passed | "ok"',
            "DIFF none",
            "COMMANDS none",
            "END TESTS",
        ]
    )
    return "\n".join(records)


def test_build_resolves_filtered_multiple_producers_after_log_reload(
    green_cfg, log, fake_backend
):
    green_cfg = _viewing(green_cfg)
    red_a = make_step(
        "red-a",
        ["tests/test_a.py", "tests/test_b.py"],
        role="test-writer",
        form=forms.Red((forms.Behavior("A behavior", "a.py"),)),
        views=["a.py"],
        check=["false"],
        expect_code=1,
    )
    red_b = replace(red_a, id="red-b", paths=["tests/test_c.py"])
    first = "tests/test_a.py::TestA::test_new[with spaces]"
    second = "tests/test_a.py::test_another"
    other = "tests/test_c.py::test_new"
    build = make_step(
        "build",
        form=forms.Build(
            "core-builder",
            "Approved build",
            (
                forms.RedSelection("red-b"),
                forms.RedSelection("red-a", ("tests/test_a.py",)),
            ),
        ),
    )
    producers = make_group(red_a, red_b)
    wt = git.make_worktree(green_cfg, producers.id)
    (wt / "a.py").write_text("stub")
    reports = [
        _red_report(first, "tests/test_b.py::test_excluded", second),
        _red_report(other),
    ]
    loop._steps(green_cfg, producers, wt, log, fake_backend(outputs=reports), 0)
    backend = fake_backend()
    loop._steps(
        green_cfg, make_group(red_a, red_b, build), wt, EventLog(green_cfg), backend, 2
    )
    assert backend.briefs == [
        f"Approved build\ngauntlet/core/a.py\n{other}\n{first}\n{second}"
    ]
    assert log.kept("g1-red-a") == reports[0] + "\n"
    assert log.kept("g1-build-brief") == backend.briefs[0]


@pytest.mark.parametrize(
    "case",
    [
        "partial",
        "behavior",
        "scope",
        "duplicate",
        "excluded-invalid",
        "empty-selection",
    ],
)
def test_invalid_red_evidence_stops_before_builder(green_cfg, log, fake_backend, case):
    green_cfg = _viewing(green_cfg)
    red = make_step(
        "red",
        ["tests/test_a.py", "tests/test_b.py"],
        role="test-writer",
        form=forms.Red((forms.Behavior("A behavior", "a.py"),)),
        views=["a.py"],
    )
    test_id = "tests/test_a.py::test_new"
    report = _red_report(test_id)
    if case == "partial":
        report = report.removesuffix("END TESTS")
    elif case == "behavior":
        report = _red_report(test_id, behavior=2)
    elif case == "scope":
        report = _red_report("tests/test_other.py::test_new")
    elif case == "duplicate":
        report = _red_report(test_id, test_id)
    elif case == "excluded-invalid":
        report = _red_report(test_id, "tests/test_b.py::test_bad").replace(
            "RUN rerun | tests/test_b.py::test_bad | assertion",
            "RUN rerun | tests/test_b.py::test_bad | passed",
        )
    build = make_step(
        "build",
        form=forms.Build(
            "core-builder",
            "Approved build",
            (
                forms.RedSelection(
                    "red",
                    (
                        "tests/test_b.py"
                        if case == "empty-selection"
                        else "tests/test_a.py",
                    ),
                ),
            ),
        ),
    )
    g = make_group(red, build)
    wt = git.make_worktree(green_cfg, g.id)
    (wt / "a.py").write_text("stub")
    backend = fake_backend(outputs=[report])
    with pytest.raises(RulingNeeded, match="g1-red.txt"):
        loop._steps(green_cfg, g, wt, log, backend, 0)
    assert backend.agents == ["test-writer"]
    assert log.kept("g1-red") == report.rstrip("\n") + "\n"


@pytest.mark.parametrize("failure", ["rejected", "partial", "check", "views"])
def test_failed_red_rerun_invalidates_earlier_success(
    green_cfg, log, fake_backend, failure
):
    green_cfg = _viewing(green_cfg)
    red = make_step(
        "red",
        ["tests/test_a.py"],
        role="test-writer",
        form=forms.Red((forms.Behavior("A behavior", "a.py"),)),
        views=["a.py"],
    )
    build = make_step(
        "build",
        form=forms.Build(
            "core-builder", "Approved build", (forms.RedSelection("red"),)
        ),
    )
    wt = git.make_worktree(green_cfg, "g1")
    (wt / "a.py").write_text("stub")
    loop._steps(
        green_cfg,
        make_group(red),
        wt,
        log,
        fake_backend(outputs=[_red_report("tests/test_a.py::test_new")]),
        0,
    )
    cfg = green_cfg
    output = _red_report("tests/test_a.py::test_new")
    if failure == "rejected":
        output = "REJECTED: STEERING invalid"
    elif failure == "partial":
        output = "SHAPE red"
    elif failure == "check":
        red = replace(red, check=["false"])
    else:
        red = replace(red, views=["a.py"])
        cfg = replace(green_cfg, view_cmd=["false"])
    with pytest.raises(RulingNeeded):
        loop._steps(cfg, make_group(red), wt, log, fake_backend(outputs=[output]), 0)
    backend = fake_backend()
    with pytest.raises(RulingNeeded, match="g1-red.txt"):
        loop._steps(cfg, make_group(red, build), wt, EventLog(cfg), backend, 1)
    assert backend.briefs == []


def test_red_rerun_replaces_ids_and_overlapping_producers_stop(
    green_cfg, log, fake_backend
):
    green_cfg = _viewing(green_cfg)
    red = make_step(
        "red",
        ["tests/test_a.py"],
        role="test-writer",
        form=forms.Red((forms.Behavior("A behavior", "a.py"),)),
        views=["a.py"],
    )
    build = make_step(
        "build",
        form=forms.Build(
            "core-builder",
            "Approved build",
            (forms.RedSelection("red"),),
        ),
    )
    wt = git.make_worktree(green_cfg, "g1")
    (wt / "a.py").write_text("stub")
    for name in ("old", "new"):
        loop._steps(
            green_cfg,
            make_group(red),
            wt,
            log,
            fake_backend(outputs=[_red_report(f"tests/test_a.py::test_{name}")]),
            0,
        )
    backend = fake_backend()
    loop._steps(green_cfg, make_group(red, build), wt, log, backend, 1)
    assert backend.briefs == [
        "Approved build\ngauntlet/core/a.py\ntests/test_a.py::test_new"
    ]
    other = replace(red, id="other")
    loop._steps(
        green_cfg,
        make_group(other),
        wt,
        log,
        fake_backend(outputs=[_red_report("tests/test_a.py::test_new")]),
        0,
    )
    build = replace(
        build,
        form=replace(
            build.form, red=(forms.RedSelection("red"), forms.RedSelection("other"))
        ),
    )
    backend = fake_backend()
    with pytest.raises(RulingNeeded, match="overlapping red test ids"):
        loop._steps(green_cfg, make_group(red, other, build), wt, log, backend, 2)
    assert backend.briefs == []


def test_missing_red_report_stops_before_builder(green_cfg, log, fake_backend):
    green_cfg = _viewing(green_cfg)
    red = make_step(
        "red",
        ["tests/test_a.py"],
        role="test-writer",
        form=forms.Red((forms.Behavior("A behavior", "a.py"),)),
        views=["a.py"],
    )
    build = make_step(
        "build",
        form=forms.Build(
            "core-builder", "Approved build", (forms.RedSelection("red"),)
        ),
    )
    wt = git.make_worktree(green_cfg, "g1")
    (wt / "a.py").write_text("stub")
    backend = fake_backend()
    with pytest.raises(RulingNeeded, match="g1-red.txt"):
        loop._steps(green_cfg, make_group(red, build), wt, log, backend, 1)
    assert backend.briefs == []
