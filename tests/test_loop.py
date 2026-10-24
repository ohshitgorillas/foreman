import json
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
from foreman.run import TIMEOUT_CODE, run_ok

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


@pytest.mark.parametrize("load,tokens", [(None, None), (317, 842), (1, 0)])
@pytest.mark.parametrize(
    "output,outcome",
    [
        ("FINDINGS 0\n", "done"),
        ("REJECTED: STEERING extra fields", "refused"),
        ("OVERLOADED: 6 files", "refused"),
    ],
)
def test_dispatch_records_returned_usage_and_outcome(
    green_cfg, log, fake_backend, load, tokens, output, outcome
) -> None:
    g = make_group(make_step())
    backend = fake_backend(outputs=[output], token_counts=[tokens], seconds=[2.75])
    if outcome == "done":
        report = loop._dispatch(
            g,
            green_cfg.target_repo,
            log,
            backend,
            "g1-s1",
            "stub",
            "core-builder",
            load=load,
        )
        assert report.kind == "done"
    else:
        with pytest.raises(RulingNeeded):
            loop._dispatch(
                g,
                green_cfg.target_repo,
                log,
                backend,
                "g1-s1",
                "stub",
                "core-builder",
                load=load,
            )
    rows = [
        json.loads(line)
        for line in (green_cfg.state_dir / "events.jsonl").read_text().splitlines()
    ]
    records = [row for row in rows if row["kind"] == "dispatch"]
    assert len(records) == 1
    record = records[0]
    assert {key: value for key, value in record.items() if key != "ts"} == {
        "kind": "dispatch",
        "step_id": "g1",
        "name": "g1-s1",
        "role": "core-builder",
        "load": load,
        "seconds": 2.75,
        "tokens": tokens,
        "outcome": outcome,
    }
    assert log.kept("g1-s1") == output.rstrip("\n") + "\n"
    assert log.kept("g1-s1-brief") == "stub"
    assert rows[1]["kind"] == "kept"
    assert rows[2]["kind"] == "dispatch"
    if outcome == "refused":
        assert rows[3]["kind"] == "ruling_needed"


@pytest.mark.parametrize(
    "output",
    ["FINDINGS 0\n", "REJECTED: STEERING extra fields", "OVERLOADED: 6 files"],
)
def test_timeout_records_overrun_and_stops_before_checks_or_next_dispatch(
    green_cfg, log, fake_backend, monkeypatch, output
) -> None:
    g = make_group(make_step("s1"), make_step("s2"))
    backend = fake_backend(
        outputs=[output], codes=[TIMEOUT_CODE], seconds=[600.5], token_counts=[27]
    )

    def unexpected(*args, **kwargs):
        pytest.fail("timeout must stop before report parsing or checks")

    monkeypatch.setattr(loop.reports, "read", unexpected)
    monkeypatch.setattr(loop.verify, "check_step", unexpected)
    monkeypatch.setattr(loop.verify, "verify", unexpected)
    with pytest.raises(RulingNeeded, match="timed out") as stopped:
        loop.run_group(green_cfg, g, log, backend)
    wt = git.worktree(green_cfg, "g1")
    assert str(wt) in str(stopped.value)
    assert str(green_cfg.state_dir / "g1-s1.txt") in str(stopped.value)
    assert backend.agents == ["core-builder"]
    assert log.kept("g1-s1") == output.rstrip("\n") + "\n"
    rows = [
        json.loads(line)
        for line in (green_cfg.state_dir / "events.jsonl").read_text().splitlines()
    ]
    records = [row for row in rows if row["kind"] == "dispatch"]
    assert len(records) == 1
    assert records[0]["outcome"] == "overran"
    assert records[0]["load"] is None
    assert records[0]["seconds"] == 600.5
    assert records[0]["tokens"] == 27
    assert rows[-1]["kind"] == "ruling_needed"
    assert not log.done()


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


def test_red_gate_keeps_evidence_and_stops_before_fix_dispatch(
    green_cfg, log, fake_backend
) -> None:
    cfg = replace(green_cfg, check_cmd=RED_UNTIL_FIXED)
    g = make_group(make_step("s1", ["a.py"]), make_step("s2", ["b.py"]))
    backend = fake_backend([{"a.py": "1"}, {"b.py": "2"}, {"a.py": "fixed"}])
    with pytest.raises(RulingNeeded, match="combined-tree check needs judgment"):
        loop.run_group(cfg, g, log, backend)
    assert len(backend.briefs) == 2
    assert log.kept("g1-check") == "a.py:1 bad\n"
    assert git.worktree(cfg, g.id).exists()
    assert not log.done()


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
    cfg = replace(green_cfg, reviewers=[Reviewer("diff-reviewer", ["gauntlet/core"])])
    g = make_group(make_step("s1", ["gauntlet/core/a.py"]))
    backend = fake_backend(
        [{"gauntlet/core/a.py": "1"}],
        [
            "",
            "FINDINGS 1\n  misplaced | gauntlet/core/a.py:1 | wrong | fails when: called\n",
        ],
    )
    with pytest.raises(FindingsToRule):
        loop.run_group(cfg, g, log, backend)
    return cfg


def test_upheld_sends_lines_then_lands(green_cfg, origin, log, fake_backend) -> None:
    cfg = _stopped_for_findings(green_cfg, log, fake_backend)
    g = make_group(make_step("s1", ["gauntlet/core/a.py"]))
    backend = fake_backend([{"gauntlet/core/a.py": "2"}], ["", "FINDINGS 0\n"])
    commit = loop.run_upheld(
        cfg,
        g,
        "misplaced | gauntlet/core/a.py:1 | wrong | fails when: called\n",
        log,
        backend,
    )
    assert (
        backend.briefs[0]
        == "misplaced | gauntlet/core/a.py:1 | wrong | fails when: called"
    )
    assert run_ok(["git", "rev-parse", "foreman/g1"], origin).out.strip() == commit
    assert log.done() == {"g1"}
    assert not (cfg.worktree_root / "g1").exists()


def test_upheld_nothing_lands_directly(green_cfg, origin, log, fake_backend) -> None:
    cfg = _stopped_for_findings(green_cfg, log, fake_backend)
    g = make_group(make_step("s1", ["gauntlet/core/a.py"]))
    backend = fake_backend()
    loop.run_upheld(cfg, g, "", log, backend)
    assert backend.briefs == []
    assert log.done() == {"g1"}


def test_upheld_line_in_no_review_raises(green_cfg, log, fake_backend) -> None:
    cfg = _stopped_for_findings(green_cfg, log, fake_backend)
    g = make_group(make_step("s1", ["gauntlet/core/a.py"]))
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


def test_upheld_batches_share_role_across_steps_and_keep_sources_separate(
    green_cfg, log, fake_backend
):
    cfg = replace(
        green_cfg,
        batch_budget=3,
        check_cmd=["false"],
        reviewers=[
            Reviewer("diff-reviewer", ["gauntlet"]),
            Reviewer("test-reviewer", ["tests"]),
        ],
    )
    g = make_group(
        make_step("a", ["gauntlet/core/a.py"]),
        make_step("b", ["gauntlet/core/b.py"]),
        make_step("c", ["gauntlet/core/c.py"]),
        make_step("s", ["gauntlet/seams/s.py"], role="seam-builder"),
        make_step("t", ["tests/test_a.py"], role="test-writer"),
    )
    wt = git.make_worktree(cfg, g.id)
    for step in g.steps:
        path = wt / step.paths[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("value = 1\n")
    (wt / "gauntlet/core/c.py").write_text(
        "def c():\n    value = 1\n    return value\n"
    )
    core_a = "misplaced | gauntlet/core/a.py:1 | wrong | fails when: called"
    core_b = "drift | gauntlet/core/b.py:1 | wrong | fails when: called"
    core_c = "shortcut | gauntlet/core/c.py:2 | wrong | fails when: called"
    seam = "leftover | gauntlet/seams/s.py:1 | wrong | fails when: called"
    test = "tests/test_a.py:1 wrong"
    diff_key = "g1-review-diff-reviewer"
    test_key = "g1-review-test-reviewer"
    diff_report = (
        "FINDINGS 4\n  " + f"{core_a}\n  {core_c}\n  {core_b}\n  {seam}" + "\n"
    )
    test_report = f"FINDINGS 1\n  {test}\n"
    log.keep(diff_key, diff_report)
    log.keep(test_key, test_report)
    upheld = f"{core_a}\n{core_b}\n{core_c}\n{seam}\n{test}"
    backend = fake_backend(token_counts=[11, 12, 13, 14])
    with pytest.raises(RulingNeeded, match="needs judgment"):
        loop.run_upheld(cfg, g, upheld, log, backend)
    assert backend.briefs == [f"{core_a}\n{core_b}", core_c, seam, test]
    assert backend.agents == [
        "core-builder",
        "core-builder",
        "seam-builder",
        "test-writer",
    ]
    assert log.kept(f"{diff_key}-handled") == diff_report
    assert log.kept(f"{test_key}-handled") == test_report
    for number, text in enumerate(backend.briefs[:3], 1):
        assert log.kept(f"{diff_key}-batch-{number}-brief") == text
    assert log.kept(f"{test_key}-batch-1-brief") == test
    rows = [
        json.loads(line)
        for line in (cfg.state_dir / "events.jsonl").read_text().splitlines()
    ]
    records = [row for row in rows if row["kind"] == "dispatch"]
    assert [row["load"] for row in records] == [2, 3, 1, 1]
    assert [row["tokens"] for row in records] == [11, 12, 13, 14]
    # A continuation after the gate stop must not replay the handled rounds.
    resumed = fake_backend()
    with pytest.raises(RulingNeeded, match="needs judgment"):
        loop.run_upheld(cfg, g, upheld, EventLog(cfg), resumed)
    assert resumed.briefs == []
    assert not log.done()


@pytest.mark.parametrize("output", ["OVERLOADED: 6", "REJECTED: STEERING", "timeout"])
def test_upheld_round_is_not_handled_until_every_batch_returns(
    green_cfg, log, fake_backend, output
):
    cfg = replace(
        green_cfg,
        batch_budget=1,
        reviewers=[Reviewer("diff-reviewer", ["gauntlet/core"])],
    )
    g = make_group(make_step("s1", ["gauntlet/core/a.py", "gauntlet/core/b.py"]))
    wt = git.make_worktree(cfg, g.id)
    (wt / "gauntlet/core").mkdir(parents=True)
    for path in g.steps[0].paths:
        (wt / path).write_text("value = 1\n")
    lines = [
        f"misplaced | {path}:1 | wrong | fails when: called"
        for path in g.steps[0].paths
    ]
    key = "g1-review-diff-reviewer"
    log.keep(key, "FINDINGS 2\n  " + "\n  ".join(lines) + "\n")
    backend = fake_backend(
        outputs=["", output], codes=[0, TIMEOUT_CODE if output == "timeout" else 0]
    )
    with pytest.raises(RulingNeeded):
        loop.run_upheld(cfg, g, "\n".join(lines), log, backend)
    assert backend.briefs == lines
    with pytest.raises(ConfigError):
        log.kept(f"{key}-handled")
    assert not log.done()
    assert wt.exists()


def test_upheld_invalid_typed_finding_stops_before_any_batch(
    green_cfg, log, fake_backend
):
    cfg = replace(
        green_cfg,
        batch_budget=1,
        reviewers=[Reviewer("diff-reviewer", ["gauntlet/core"])],
    )
    g = make_group(make_step("s1", ["gauntlet/core/a.py", "gauntlet/core/b.py"]))
    wt = git.make_worktree(cfg, g.id)
    (wt / "gauntlet/core").mkdir(parents=True)
    for path in g.steps[0].paths:
        (wt / path).write_text("value = 1\n")
    valid = "misplaced | gauntlet/core/a.py:1 | wrong | fails when: called"
    invalid = "unsupported | gauntlet/core/b.py:1 | wrong | fails when: called"
    log.keep("g1-review-diff-reviewer", f"FINDINGS 2\n  {valid}\n  {invalid}\n")
    backend = fake_backend()
    with pytest.raises(RulingNeeded, match="unsupported builder finding category"):
        loop.run_upheld(cfg, g, f"{valid}\n{invalid}", log, backend)
    assert backend.briefs == []


@pytest.mark.parametrize(
    "kind,action,target,expected",
    [
        ("spec wrong", "owner", None, WrongTest),
        ("undetermined", "owner", None, WrongTest),
        ("build wrong", "retry", "build", None),
        ("build wrong", "retry", "red", RulingNeeded),
        ("build wrong", "retry", "stub", RulingNeeded),
        ("build wrong", "retry", "absent", RulingNeeded),
    ],
)
def test_juror_follows_owner_and_build_verdicts(
    green_cfg,
    log,
    fake_backend,
    origin,
    capsys,
    monkeypatch,
    kind,
    action,
    target,
    expected,
):
    cfg = _viewing(green_cfg)
    test_id = "tests/test_a.py::test_new"
    red = make_step(
        "red",
        ["tests/test_a.py"],
        role="test-writer",
        form=forms.Red((forms.Behavior("Approved behavior", "a.py"),)),
        views=["a.py"],
        check=["false"],
        expect_code=1,
    )
    build = make_step(
        "build",
        form=forms.Build(
            "core-builder", "Approved build", (forms.RedSelection("red"),)
        ),
        check=["true"],
    )
    group = make_group(
        make_step("stub", ["a.py", "gauntlet/core/a.py"]),
        red,
        build,
        make_step("later", ["later.py"]),
        design="docs/superpowers/specs/approved.md",
    )
    verdict = f"VERDICT {kind}\nACTION {action}\n"
    if target is not None:
        verdict += f"STEP {target}\n"
    verdict += "REASON Preserve the approved behavior\nEVIDENCE a.py:1 | implementation contradicts behavior\nEND JUDGMENT"
    backend = fake_backend(
        [
            {"a.py": "stub", "gauntlet/core/a.py": "original"},
            {"tests/test_a.py": "red"},
            {"gauntlet/core/a.py": "partial"},
            {},
            {"gauntlet/core/a.py": "fixed"},
            {"later.py": "later"},
        ],
        ["", _red_report(test_id), f"TEST WRONG 1:\n  {test_id} | mismatch\n", verdict],
    )
    restored = []
    restore = git.restore

    def observe_restore(wt, tree, paths):
        restore(wt, tree, paths)
        restored.append((tree, (wt / "gauntlet/core/a.py").read_text()))

    monkeypatch.setattr(git, "restore", observe_restore)
    if expected is not None:
        with pytest.raises(expected):
            loop.run_group(cfg, group, log, backend)
        assert not log.done()
        assert git.worktree(cfg, "g1").is_dir()
        assert backend.agents == [
            "core-builder",
            "test-writer",
            "core-builder",
            "juror",
        ]
        assert restored == []
        if expected is WrongTest:
            assert "[test_wrong] g1:" in capsys.readouterr().err
    else:
        loop.run_group(cfg, group, log, backend)
        assert log.done() == {"g1"}
        assert restored == [(log.kept("g1-build-before"), "original")]
        assert backend.agents == [
            "core-builder",
            "test-writer",
            "core-builder",
            "juror",
            "core-builder",
            "core-builder",
        ]
        assert (
            backend.briefs[4]
            == f"Approved build\ngauntlet/core/a.py\n{test_id}\nPreserve the approved behavior"
        )
        assert "later.py" in backend.briefs[5]
    judgment = json.loads(log.kept("g1-build-judgment"))
    assert judgment["steps"] == ["red", "build"]
    assert judgment["red_ids"] == [test_id]
    assert judgment["step"].endswith("Approved build")
    assert all(
        path.endswith(("g1-red.txt", "g1-build.txt")) for path in judgment["reports"]
    )
    assert backend.briefs[3].startswith(
        "judgment\nspec: docs/superpowers/specs/approved.md\nstep: red\nstep: build\n"
    )
    assert log.kept("g1-build-juror-1").strip() == verdict


@pytest.mark.parametrize(
    "output,code",
    [
        ("malformed verdict", 0),
        ("REJECTED: STEERING missing evidence", 0),
        ("OVERLOADED: 6 paths", 0),
        ("partial", TIMEOUT_CODE),
    ],
)
def test_juror_attempts_survive_restart_and_stop_at_configured_cap(
    green_cfg,
    log,
    fake_backend,
    output,
    code,
    capsys,
):
    cfg = replace(green_cfg, juror_rounds=2)
    group = make_group(make_step("build"))
    wt = git.make_worktree(cfg, group.id)
    path = log.keep("g1-build", "failure")
    judgment = forms.Judgment(
        "docs/superpowers/specs/approved.md",
        ("build",),
        "Approved build",
        (),
        (str(path),),
    )
    # Include a prior reservation without a completed dispatch.
    log.keep("g1-build-juror-1", "")
    backend = fake_backend(outputs=[output], codes=[code])
    with pytest.raises(RulingNeeded):
        loop._judge(cfg, group, "build", judgment, wt, EventLog(cfg), backend)
    assert backend.agents == ["juror"]
    assert log.kept("g1-build-juror-2").strip() == output
    fresh = fake_backend()
    with pytest.raises(WrongTest, match="juror rounds exhausted"):
        loop._judge(cfg, group, "build", judgment, wt, EventLog(cfg), fresh)
    assert fresh.agents == []
    assert str(wt) in capsys.readouterr().err
    assert not log.done()
