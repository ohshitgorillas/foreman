import json
from dataclasses import asdict, replace

import pytest
from conftest import make_group, make_red, make_step

from foreman import forms, git, loop
from foreman.config import Config, Reviewer
from foreman.errors import (
    BackendError,
    ConfigError,
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


def _undetermined(anchor="a.py:1"):
    return (
        "VERDICT undetermined\nACTION owner\nREASON Need owner evidence\n"
        f"EVIDENCE {anchor} | observed failure needs clarification\nEND JUDGMENT"
    )


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
    green_cfg, log, fake_backend, monkeypatch, load, tokens, output, outcome
) -> None:
    g = make_group(make_step())
    backend = fake_backend(outputs=[output], token_counts=[tokens], seconds=[2.75])
    wait = backend.wait
    diagnostic = "Reading additional input from stdin...\n"
    monkeypatch.setattr(backend, "wait", lambda job: replace(wait(job), err=diagnostic))
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
    assert log.kept("g1-s1-stderr") == diagnostic
    assert log.kept("g1-s1-brief") == "stub"
    assert all(row["kind"] == "kept" for row in rows[:3])
    assert rows[3]["kind"] == "dispatch"
    if outcome == "refused":
        assert rows[4]["kind"] == "ruling_needed"


def test_dispatch_refreshes_diagnostics_when_reusing_report_name(
    green_cfg, log, fake_backend, monkeypatch
):
    group = make_group(make_step())
    backend = fake_backend(outputs=["FINDINGS 0", "FINDINGS 0"])
    wait = backend.wait
    diagnostics = iter(["old diagnostic\n", ""])
    monkeypatch.setattr(
        backend, "wait", lambda job: replace(wait(job), err=next(diagnostics))
    )
    for expected in ("old diagnostic\n", ""):
        loop._dispatch(
            group, green_cfg.target_repo, log, backend, "g1-s1", "stub", "core-builder"
        )
        assert log.kept("g1-s1-stderr") == expected


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
    wait = backend.wait
    monkeypatch.setattr(
        backend, "wait", lambda job: replace(wait(job), err="timeout diagnostic\n")
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
    assert log.kept("g1-s1-stderr") == "timeout diagnostic\n"
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


def test_missed_step_check_reaches_juror(green_cfg, log, fake_backend) -> None:
    g = make_group(
        make_step("s1", ["a.py"], check=["false"]),
        design="docs/superpowers/specs/approved.md",
    )
    backend = fake_backend([{"a.py": "1"}], ["", _undetermined()])
    with pytest.raises(WrongTest, match="Need owner evidence"):
        loop.run_group(green_cfg, g, log, backend)
    assert backend.agents == ["core-builder", "juror"]
    assert not log.done()


def test_step_check_matching_expect_code_passes(
    green_cfg, origin, log, fake_backend
) -> None:
    g = make_group(make_step("s1", ["a.py"], check=["true"]))
    loop.run_group(green_cfg, g, log, fake_backend([{"a.py": "1"}]))
    assert log.done() == {"g1"}


@pytest.mark.parametrize(
    "check_cmd,output",
    [
        (RED_UNTIL_FIXED, "a.py:1 bad\n"),
        (["sh", "-c", "echo other.py:3 bad; exit 1"], "other.py:3 bad\n"),
        (["sh", "-c", "echo broken; exit 1"], "broken\n"),
    ],
)
def test_red_gate_keeps_evidence_and_reaches_juror_before_fix_dispatch(
    green_cfg, log, fake_backend, check_cmd, output
) -> None:
    cfg = replace(green_cfg, check_cmd=check_cmd)
    g = make_group(
        make_step("s1", ["a.py"]),
        make_step("s2", ["b.py"]),
        design="docs/superpowers/specs/approved.md",
    )
    backend = fake_backend([{"a.py": "1"}, {"b.py": "2"}], ["", "", _undetermined()])
    with pytest.raises(WrongTest, match="Need owner evidence"):
        loop.run_group(cfg, g, log, backend)
    assert backend.agents == ["core-builder", "core-builder", "juror"]
    assert log.kept("g1-check") == output
    judgment = json.loads(log.kept("g1-group-check-judgment"))
    assert judgment["steps"] == ["s1", "s2"]
    assert judgment["red_ids"] == []
    assert set(judgment["reports"]) == {
        str(cfg.state_dir / f"g1-{key}.txt") for key in ("s1", "s2", "check")
    }
    wt = git.worktree(cfg, g.id)
    assert wt.exists()
    assert (wt / "a.py").read_text() == "1"
    assert not log.done()


def test_red_gate_juror_cap_notifies_owner_and_keeps_worktree(
    green_cfg, log, fake_backend, capsys
) -> None:
    cfg = replace(green_cfg, check_cmd=RED_UNTIL_FIXED, juror_rounds=1)
    g = make_group(
        make_step("s1", ["a.py"]), design="docs/superpowers/specs/approved.md"
    )
    backend = fake_backend([{"a.py": "1"}], ["", "malformed verdict"])
    with pytest.raises(RulingNeeded, match="verdict"):
        loop.run_group(cfg, g, log, backend)
    assert backend.agents == ["core-builder", "juror"]
    assert log.kept("g1-group-check-juror-1").strip() == "malformed verdict"
    wt = git.worktree(cfg, g.id)
    resumed = fake_backend()
    with pytest.raises(WrongTest, match="juror rounds exhausted"):
        loop._gate(cfg, g, wt, EventLog(cfg), resumed)
    assert resumed.agents == []
    assert "[test_wrong] g1:" in capsys.readouterr().err
    assert wt.exists()
    assert log.kept("g1-check") == "a.py:1 bad\n"
    assert not log.done()


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
    assert backend.briefs[1] == "working tree"
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


def test_resume_reruns_from_named_step_on_kept_worktree_and_lands(
    green_cfg, origin, log, fake_backend
) -> None:
    g = make_group(
        make_step("s1", ["a.py"]), make_step("s2", ["b.py"]), make_step("s3", ["c.py"])
    )
    with pytest.raises(RulingNeeded):
        loop.run_group(
            green_cfg,
            g,
            log,
            fake_backend([{"a.py": "1"}], ["", "REJECTED: STEERING too much"]),
        )
    assert log.kept("g1-s1") == ""
    backend = fake_backend([{"b.py": "2"}, {"c.py": "3"}])
    commit = loop.run_resume(green_cfg, g, "s2", EventLog(green_cfg), backend)
    assert backend.briefs == [
        "stub\nApproved step s2\nb.py",
        "stub\nApproved step s3\nc.py",
    ]
    assert run_ok(["git", "rev-parse", "foreman/g1"], origin).out.strip() == commit
    files = run_ok(["git", "show", "--name-only", "--format=", commit], origin)
    assert sorted(files.out.split()) == ["a.py", "b.py", "c.py"]
    assert not (green_cfg.worktree_root / "g1").exists()
    assert EventLog(green_cfg).done() == {"g1"}


def test_resume_goes_through_review(green_cfg, log, fake_backend) -> None:
    cfg = _reviewed(green_cfg)
    g = make_group(make_step("s1", ["a.py"]), make_step("s2", ["b.py"]))
    with pytest.raises(RulingNeeded):
        loop.run_group(
            cfg,
            g,
            log,
            fake_backend([{"a.py": "1"}], ["", "REJECTED: STEERING too much"]),
        )
    backend = fake_backend([{"b.py": "2"}], ["", "FINDINGS 1\n  a.py:1 wrong\n"])
    with pytest.raises(FindingsToRule):
        loop.run_resume(cfg, g, "s2", log, backend)
    assert (green_cfg.worktree_root / "g1").exists()


def test_resume_unknown_step_raises_before_dispatch(
    green_cfg, log, fake_backend
) -> None:
    g = make_group(make_step("s1", ["a.py"]))
    git.make_worktree(green_cfg, "g1")
    backend = fake_backend()
    with pytest.raises(ConfigError, match=r"group g1 has no step 'zz' \(steps: s1\)"):
        loop.run_resume(green_cfg, g, "zz", log, backend)
    assert backend.briefs == []


def test_resume_without_kept_worktree_raises_before_dispatch(
    green_cfg, log, fake_backend
) -> None:
    g = make_group(make_step("s1", ["a.py"]))
    backend = fake_backend()
    with pytest.raises(ConfigError, match="group g1 has no kept worktree to resume"):
        loop.run_resume(green_cfg, g, "s1", log, backend)
    assert backend.briefs == []
    assert not (green_cfg.worktree_root / "g1").exists()


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
        design="docs/superpowers/specs/approved.md",
    )
    output = "TEST WRONG 1:\n  tests/test_a.py::test_a | wrong | spec:1\n"
    backend = fake_backend(
        [{"tests/test_a.py": "red"}, {"a.py": "partial"}],
        ["", output, _undetermined()],
    )
    with pytest.raises(WrongTest):
        loop.run_group(cfg, g, log, backend)
    assert backend.briefs[:2] == [
        "stub\nApproved step tests\ntests",
        "stub\nApproved step build\na.py",
    ]
    assert backend.agents == ["test-writer", "core-builder", "juror"]
    assert backend.briefs[2].startswith("judgment\n")
    assert log.kept("g1-build") == output
    assert log.kept("g1-build-juror-1").strip() == _undetermined()
    assert log.kept("g1-build-before").strip()
    wt = git.worktree(cfg, "g1")
    assert (wt / "a.py").read_text() == "partial"
    assert log.done() == set()
    err = capsys.readouterr().err
    for expected in (
        "[test_wrong] g1:",
        "g1-build.txt",
        "Need owner evidence",
        str(wt),
    ):
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
def test_wrong_tests_reach_juror_without_one_earlier_owner(
    green_cfg, log, fake_backend, lines
):
    g = make_group(
        make_step("tests", ["tests"]),
        make_step("other", ["other"]),
        make_step("build", ["a.py"]),
        make_step("later", ["later.py"]),
        design="docs/superpowers/specs/approved.md",
    )
    output = f"TEST WRONG 2:\n  {lines}\n"
    backend = fake_backend(outputs=["", "", output, _undetermined("README.md:1")])
    with pytest.raises(WrongTest, match="Need owner evidence"):
        loop.run_group(green_cfg, g, log, backend)
    assert backend.agents == ["core-builder", "core-builder", "core-builder", "juror"]
    assert backend.briefs[3].startswith("judgment\n")
    judgment = json.loads(log.kept("g1-build-judgment"))
    assert judgment["steps"] == ["build"]
    assert judgment["red_ids"] == []
    assert judgment["reports"] == [str(green_cfg.state_dir / "g1-build.txt")]
    assert log.kept("g1-build") == output
    assert not log.done()


@pytest.mark.parametrize("trigger", ["step", "group"])
@pytest.mark.parametrize("fixed", [True, False])
def test_retest_owner_retry_after_cap_resumes_correct_context(
    green_cfg, origin, log, fake_backend, trigger, fixed
):
    cfg = replace(
        green_cfg,
        juror_rounds=1,
        check_cmd=RED_UNTIL_FIXED if trigger == "group" else ["true"],
    )
    group = make_group(
        make_step(
            "build", ["a.py"], form=forms.Build("core-builder", "Approved build", ())
        ),
        make_step("later", ["later.py"]),
        design="docs/superpowers/specs/approved.md",
    )
    verdict = (
        "VERDICT build wrong\nACTION retry\nSTEP build\nREASON Fix implementation\n"
        "EVIDENCE a.py:1 | build contradicts approved behavior\nEND JUDGMENT"
    )
    wrong = "TEST WRONG 1:\n  tests/test_a.py::test_a | wrong\n"
    if trigger == "step":
        backend = fake_backend(
            [{"a.py": "partial"}, {}, {"a.py": "still partial"}],
            [wrong, verdict, wrong],
        )
    else:
        backend = fake_backend(
            [{"a.py": "partial"}, {"later.py": "later"}, {}, {"a.py": "still partial"}],
            ["", "", verdict, ""],
        )
    with pytest.raises(WrongTest, match="juror rounds exhausted"):
        loop.run_group(cfg, group, log, backend)
    owner_verdict = verdict.replace("Fix implementation", "Apply owner correction")
    resumed = fake_backend(
        [{"a.py": "fixed" if fixed else "still partial"}, {"later.py": "later"}],
        [wrong] if not fixed and trigger == "step" else [],
    )
    if fixed:
        commit = loop.run_retest(cfg, group, owner_verdict, EventLog(cfg), resumed)
        assert resumed.agents == ["core-builder"] * (2 if trigger == "step" else 1)
        assert run_ok(["git", "show", f"{commit}:a.py"], origin).out == "fixed"
        assert run_ok(["git", "show", f"{commit}:later.py"], origin).out == "later"
        assert log.done() == {"g1"}
    else:
        with pytest.raises(WrongTest, match="juror rounds exhausted"):
            loop.run_retest(cfg, group, owner_verdict, EventLog(cfg), resumed)
        assert resumed.agents == ["core-builder"]
        assert not log.done()
        assert git.worktree(cfg, group.id).is_dir()
    assert resumed.briefs[0] == "Approved build\na.py\nApply owner correction"
    context = "build" if trigger == "step" else "group-check"
    assert log.kept(f"g1-{context}-juror-1") == verdict + "\n"
    assert log.kept("g1-retest-verdict") == owner_verdict


@pytest.mark.parametrize(
    "text", ["", "  ", "fix tests", _undetermined() + "\n" + _undetermined()]
)
def test_retest_rejects_invalid_owner_content_before_dispatch(
    green_cfg, log, fake_backend, text
):
    group = make_group(make_step("build"), design="docs/superpowers/specs/approved.md")
    wrong = "TEST WRONG 1:\n  tests/test_a.py::test_a | wrong\n"
    with pytest.raises(WrongTest):
        loop.run_group(
            green_cfg,
            group,
            log,
            fake_backend(outputs=[wrong, _undetermined("README.md:1")]),
        )
    backend = fake_backend()
    with pytest.raises(ConfigError):
        loop.run_retest(green_cfg, group, text, EventLog(green_cfg), backend)
    assert backend.briefs == []


def test_retest_owner_verdict_preserves_stop(green_cfg, log, fake_backend):
    group = make_group(make_step("build"), design="docs/superpowers/specs/approved.md")
    wrong = "TEST WRONG 1:\n  tests/test_a.py::test_a | wrong\n"
    with pytest.raises(WrongTest):
        loop.run_group(
            green_cfg,
            group,
            log,
            fake_backend(outputs=[wrong, _undetermined("README.md:1")]),
        )
    backend = fake_backend()
    with pytest.raises(WrongTest):
        loop.run_retest(
            green_cfg, group, _undetermined("README.md:1"), EventLog(green_cfg), backend
        )
    assert backend.briefs == []
    assert git.worktree(green_cfg, group.id).exists()
    assert not log.done()


def test_retest_requires_kept_owner_stop(green_cfg, log, fake_backend):
    group = make_group(make_step())
    git.make_worktree(green_cfg, group.id)
    backend = fake_backend()
    with pytest.raises(ConfigError):
        loop.run_retest(green_cfg, group, _undetermined("README.md:1"), log, backend)
    assert backend.briefs == []


def test_red_renders_generated_view_and_keeps_exact_dispatch(
    green_cfg, log, fake_backend, monkeypatch
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
    wait = backend.wait
    diagnostic = "Reading additional input from stdin...\n"
    monkeypatch.setattr(backend, "wait", lambda job: replace(wait(job), err=diagnostic))
    loop._steps(cfg, g, wt, log, backend, 0)
    expected = f"red\nbehavior: Preserve safe data.\nview: {cfg.view_root}/g1/a.pyi"
    assert backend.briefs[1] == expected
    assert log.kept("g1-red-brief") == expected
    assert log.kept("g1-red") == report + "\n"
    assert log.kept("g1-red-stderr") == diagnostic


@pytest.mark.parametrize("role", ["diff-reviewer", "test-reviewer"])
def test_default_review_brief_is_kept_even_when_empty(
    green_cfg, origin, log, fake_backend, role
):
    cfg = replace(green_cfg, reviewers=[Reviewer(role, ["a.py"])])
    g = make_group(make_step("s1", ["a.py"]))
    backend = fake_backend([{"a.py": "1"}])
    loop.run_group(cfg, g, log, backend)
    expected = "working tree"
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


@pytest.mark.parametrize("outside", ["a.py", "tests/test_other.py", "stray.txt"])
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
    backend = fake_backend(
        [{outside: "unexpected", "tests/test_a.py": "red", "ignored.txt": "cache"}]
    )
    with pytest.raises(
        RulingNeeded, match="red output outside declared paths"
    ) as error:
        loop._steps(green_cfg, g, wt, log, backend, 0)
    assert outside in str(error.value)
    assert "ignored.txt" not in str(error.value)
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
    (wt / ".gitignore").write_text(".ruff_cache/\n")
    (wt / "a.py").write_text("earlier stub")
    (wt / "tests").mkdir()
    (wt / "tests/test_a.py").write_text("old tests")
    loop._steps(
        green_cfg,
        g,
        wt,
        log,
        fake_backend(
            [
                {
                    "tests/test_a.py": "old and new tests",
                    ".ruff_cache/entry": "cache",
                }
            ],
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
                f'RUN red | {test_id} | assertion | "FAILED {test_id} - AssertionError"',
                f'RUN rerun | {test_id} | assertion | "FAILED {test_id} - AssertionError"',
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
        design="docs/superpowers/specs/approved.md",
    )
    wt = git.make_worktree(cfg, g.id)
    for step in g.steps:
        path = wt / step.paths[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("value = 1\n")
    loop._steps(cfg, g, wt, log, fake_backend(), 0)
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
    log.keep("g1-review-queue", json.dumps(asdict(g), sort_keys=True))
    log.keep(diff_key, diff_report)
    log.keep(test_key, test_report)
    upheld = f"{core_a}\n{core_b}\n{core_c}\n{seam}\n{test}"
    owner_verdict = _undetermined("gauntlet/core/a.py:1")
    backend = fake_backend(
        outputs=["", "", "", "", owner_verdict],
        token_counts=[11, 12, 13, 14, 15],
    )
    with pytest.raises(WrongTest, match="Need owner evidence"):
        loop.run_upheld(cfg, g, upheld, log, backend)
    assert backend.briefs[:4] == [f"{core_a}\n{core_b}", core_c, seam, test]
    assert backend.briefs[4].startswith("judgment\n")
    assert backend.agents == [
        "core-builder",
        "core-builder",
        "seam-builder",
        "test-writer",
        "juror",
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
    records = [
        row for row in rows if row["kind"] == "dispatch" and row["tokens"] is not None
    ]
    assert [row["load"] for row in records] == [2, 3, 1, 1, None]
    assert [row["tokens"] for row in records] == [11, 12, 13, 14, 15]
    # A continuation after the gate stop must not replay the handled rounds.
    resumed = fake_backend(outputs=[owner_verdict])
    with pytest.raises(WrongTest, match="Need owner evidence"):
        loop.run_upheld(cfg, g, upheld, EventLog(cfg), resumed)
    assert resumed.agents == ["juror"]
    assert log.kept("g1-group-check-juror-2").strip() == owner_verdict
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
    log.keep("g1-review-queue", json.dumps(asdict(g), sort_keys=True))
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
    log.keep("g1-review-queue", json.dumps(asdict(g), sort_keys=True))
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


def test_juror_retry_sends_a_test_writer_step_back_to_its_own_role(
    green_cfg, log, fake_backend, origin, monkeypatch
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
    verdict = (
        "VERDICT build wrong\nACTION retry\nSTEP red\n"
        "REASON Preserve the approved behavior\n"
        "EVIDENCE a.py:1 | test contradicts behavior\nEND JUDGMENT"
    )
    backend = fake_backend(
        [
            {"a.py": "stub", "gauntlet/core/a.py": "original"},
            {"tests/test_a.py": "wrong"},
            {"gauntlet/core/a.py": "partial"},
            {},
            {"tests/test_a.py": "red"},
            {"gauntlet/core/a.py": "fixed"},
            {"later.py": "later"},
        ],
        [
            "",
            _red_report(test_id),
            f"TEST WRONG 1:\n  {test_id} | mismatch\n",
            verdict,
            _red_report(test_id),
        ],
    )
    restored = []
    restore = git.restore

    def observe_restore(wt, tree, paths):
        restore(wt, tree, paths)
        restored.append(tuple(paths))

    monkeypatch.setattr(git, "restore", observe_restore)
    loop.run_group(cfg, group, log, backend)
    assert log.done() == {"g1"}
    assert backend.agents == [
        "core-builder",
        "test-writer",
        "core-builder",
        "juror",
        "test-writer",
        "core-builder",
        "core-builder",
    ]
    assert restored == [tuple(build.paths), tuple(red.paths)]
    assert backend.briefs[4] == backend.briefs[1]
    assert "Preserve the approved behavior" not in backend.briefs[4]
    assert backend.briefs[5] == backend.briefs[2]
    assert "later.py" in backend.briefs[6]


class _StartFails:
    """Wraps a backend so its first start raises before any agent runs."""

    def __init__(self, inner):
        self.inner = inner
        self.failed = False

    def start(self, brief, worktree, role):
        if not self.failed:
            self.failed = True
            raise BackendError("cannot start juror")
        return self.inner.start(brief, worktree, role)

    def __getattr__(self, attr):
        return getattr(self.inner, attr)


def test_juror_start_failure_does_not_use_a_round(green_cfg, log, fake_backend):
    cfg = replace(green_cfg, juror_rounds=1)
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
    failing = _StartFails(fake_backend())
    with pytest.raises(BackendError):
        loop._judge(cfg, group, "build", judgment, wt, EventLog(cfg), failing)
    assert failing.inner.agents == []
    started = fake_backend(outputs=["malformed verdict"])
    with pytest.raises(RulingNeeded):
        loop._judge(cfg, group, "build", judgment, wt, EventLog(cfg), started)
    assert started.agents == ["juror"]
    fresh = fake_backend()
    with pytest.raises(WrongTest, match="juror rounds exhausted"):
        loop._judge(cfg, group, "build", judgment, wt, EventLog(cfg), fresh)
    assert fresh.agents == []


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


@pytest.mark.parametrize(
    "change",
    [
        "machinery",
        "assertions",
        "rename",
        "missing",
        "outside",
        "filtered",
        "membership",
    ],
)
def test_juror_repair_preserves_producer_and_restores_builder(
    green_cfg, log, fake_backend, change
):
    cfg = _viewing(green_cfg)
    test_id = "tests/test_a.py::test_new"
    original = "def test_new():\n    value = 1\n    assert value == 1\n"
    repaired = original.replace("value = 1", "value = 2")
    red = make_step(
        "red",
        ["tests/test_a.py", "tests/test_b.py"],
        role="test-writer",
        views=["a.py"],
        form=forms.Red((forms.Behavior("Approved behavior", "a.py"),)),
        check=["false"],
        expect_code=1,
    )
    build = make_step(
        "build",
        ["a.py", "new.py"],
        form=forms.Build(
            "core-builder",
            "Approved build",
            (forms.RedSelection("red", ("tests/test_a.py",)),),
        ),
        check=["true"],
    )
    group = make_group(red, build, design="docs/superpowers/specs/approved.md")
    wt = git.make_worktree(cfg, group.id)
    (wt / "a.py").write_text("original")
    (wt / ".gitignore").write_text(".ruff_cache/\n")
    loop._steps(
        cfg,
        make_group(red),
        wt,
        log,
        fake_backend(
            [{"tests/test_a.py": original, "tests/test_b.py": original}],
            [_red_report(test_id, "tests/test_b.py::test_new")],
        ),
        0,
    )
    producer_report = log.kept("g1-red")
    producer_evidence = log.kept("g1-red-evidence")
    if change == "assertions":
        repaired = repaired.replace("== 1", "== 2")
    elif change == "rename":
        repaired = repaired.replace("test_new", "test_renamed")
    elif change == "missing":
        repaired = "value = 1\n"
    files = {"tests/test_a.py": repaired, ".ruff_cache/entry": "cache"}
    if change == "outside":
        files["other.py"] = "unexpected"
    elif change == "filtered":
        files["tests/test_b.py"] = original.replace("test_new", "test_renamed")
    elif change == "membership":
        files["tests/test_a.py"] += "\ndef test_extra():\n    pass\n"
    verdict = (
        "VERDICT tests wrong\nACTION repair\nSTEP red\n"
        "REASON Machinery misrepresents approved behavior\n"
        "EVIDENCE tests/test_a.py:2 | wrong fixture value\n"
        "FAILURE fixture provided wrong value\nEND JUDGMENT"
    )
    seen = []

    class Spy(fake_backend):
        def start(self, brief, worktree, agent):
            if len(self.briefs) == 3:
                seen.append(
                    (
                        (worktree / "a.py").read_text(),
                        (worktree / "new.py").exists(),
                        (worktree / "stray").read_text(),
                    )
                )
            return super().start(brief, worktree, agent)

    backend = Spy(
        [
            {"a.py": "partial", "new.py": "remove", "stray": "keep"},
            {},
            files,
            {"a.py": "finished"},
        ],
        [
            f"TEST WRONG 1:\n  {test_id} | wrong machinery\n",
            verdict,
            "RUN after all green",
        ],
    )
    if change == "machinery":
        loop._step(cfg, group, 1, wt, log, backend)
        assert seen == [("original", False, "keep")]
        assert backend.briefs[3] == f"Approved build\na.py\nnew.py\n{test_id}"
        assert json.loads(log.kept("g1-red-repair-evidence"))["red_ids"] == [
            test_id,
            "tests/test_b.py::test_new",
        ]
    else:
        with pytest.raises(RulingNeeded) as error:
            loop._step(cfg, group, 1, wt, log, backend)
        assert ".ruff_cache" not in str(error.value)
        if change == "outside":
            assert "other.py" in str(error.value)
        assert len(backend.briefs) == 3
        assert (wt / "a.py").read_text() == "partial"
    assert backend.briefs[2] == (
        f"repair\nfiles: tests/test_a.py\nfiles: tests/test_b.py\nview: {cfg.view_root}/g1/a.pyi\n"
        "failure: fixture provided wrong value"
    )
    assert backend.agents[:3] == ["core-builder", "juror", "test-writer"]
    assert log.kept("g1-red") == producer_report
    assert log.kept("g1-red-evidence") == producer_evidence
    if change == "machinery":
        # A successful producer rerun starts a new evidence generation, including
        # new assertions, and must not inherit the old repair fingerprint.
        fingerprint = log.kept("g1-red-repair-evidence")
        rerun = original.replace("== 1", "== 2")
        loop._steps(
            cfg,
            make_group(red),
            wt,
            log,
            fake_backend(
                [{"tests/test_a.py": rerun, "tests/test_b.py": rerun}],
                [_red_report(test_id, "tests/test_b.py::test_new")],
            ),
            0,
        )
        resumed = fake_backend()
        continued_log = EventLog(cfg)
        loop._follow_verdict(
            cfg,
            group,
            loop._judgment(group, 1, wt, continued_log),
            wt,
            continued_log,
            resumed,
            loop.reports.verdict(verdict),
        )
        assert resumed.agents == ["test-writer", "core-builder"]
        assert continued_log.kept("g1-red-repair-evidence") != fingerprint
    elif change != "outside":
        # A restart cannot bless a failed repair's edited assertions or ids by
        # capturing them as a fresh baseline.
        resumed = fake_backend()
        continued_log = EventLog(cfg)
        with pytest.raises(RulingNeeded):
            loop._follow_verdict(
                cfg,
                group,
                loop._judgment(group, 1, wt, continued_log),
                wt,
                continued_log,
                resumed,
                loop.reports.verdict(verdict),
            )
        assert resumed.briefs == []


@pytest.mark.parametrize("outcome", ["success", "bad-report", "bad-check"])
def test_redo_red_restores_original_snapshots_and_replaces_evidence(
    green_cfg, log, fake_backend, outcome
):
    cfg = _viewing(green_cfg)
    old_id = "tests/test_a.py::test_wrong"
    new_id = "tests/test_a.py::test_approved"
    red = make_step(
        "red",
        ["tests/test_a.py"],
        role="test-writer",
        views=["a.py"],
        form=forms.Red((forms.Behavior("Keep the approved claim", "a.py"),)),
        check=["sh", "-c", "test ! -f bad-check"],
        expect_code=0,
    )
    builds = [
        make_step(
            name,
            ["a.py", f"{name}.py"],
            form=forms.Build(
                "core-builder", f"Approved {name}", (forms.RedSelection("red"),)
            ),
        )
        for name in ("first", "second", "future")
    ]
    unrelated = make_step("unrelated", ["keep.py"])
    group = make_group(
        red,
        builds[0],
        unrelated,
        builds[1],
        builds[2],
        design="docs/superpowers/specs/approved.md",
    )
    wt = git.make_worktree(cfg, group.id)
    (wt / "a.py").write_text("original")
    initial = fake_backend(
        [
            {"tests/test_a.py": "wrong"},
            {"a.py": "first", "first.py": "old"},
            {"keep.py": "preserve"},
            {"a.py": "second", "second.py": "old"},
        ],
        [_red_report(old_id)],
    )
    for index in range(4):
        loop._step(cfg, group, index, wt, log, initial)
    original_brief = log.kept("g1-red-brief")
    snapshots = {
        name: log.kept(f"g1-{name}-before") for name in ("red", "first", "second")
    }
    judgment = loop._judgment(group, 3, wt, log)
    verdict = loop.reports.verdict(
        "VERDICT tests wrong\nACTION redo red\nSTEP red\n"
        "REASON Assertions contradict approved behavior\n"
        "EVIDENCE tests/test_a.py:1 | assertion is wrong\nEND JUDGMENT"
    )

    class Spy(fake_backend):
        def start(self, text, worktree, agent):
            if not self.briefs:
                assert (worktree / "a.py").read_text() == "original"
                assert not (worktree / "tests/test_a.py").exists()
                assert not (worktree / "first.py").exists()
                assert not (worktree / "second.py").exists()
                assert (worktree / "keep.py").read_text() == "preserve"
                assert log.kept("g1-red-evidence") == ""
                for index in (1, 3, 4):
                    with pytest.raises(
                        RulingNeeded, match="no current successful evidence"
                    ):
                        loop._red_ids(group, index, worktree, EventLog(cfg))
            return super().start(text, worktree, agent)

    if outcome == "bad-check":
        (wt / "bad-check").touch()
    backend = Spy(
        [{"tests/test_a.py": "approved"}]
        + (
            [{"a.py": "first fresh"}, {"a.py": "second fresh"}]
            if outcome == "success"
            else []
        ),
        ["invalid evidence" if outcome == "bad-report" else _red_report(new_id)]
        + ([_undetermined()] if outcome == "bad-check" else []),
    )
    if outcome == "success":
        loop._follow_verdict(cfg, group, judgment, wt, log, backend, verdict)
        assert backend.briefs == [
            original_brief,
            f"Approved first\na.py\nfirst.py\n{new_id}",
            f"Approved second\na.py\nsecond.py\n{new_id}",
        ]
        assert (wt / "a.py").read_text() == "second fresh"
        assert loop._red_ids(group, 4, wt, EventLog(cfg)) == [new_id]
    else:
        expected = WrongTest if outcome == "bad-check" else RulingNeeded
        with pytest.raises(expected):
            loop._follow_verdict(cfg, group, judgment, wt, log, backend, verdict)
        assert backend.briefs[0] == original_brief
        if outcome == "bad-check":
            assert backend.agents == ["test-writer", "juror"]
            assert backend.briefs[1].startswith("judgment\n")
            failed = json.loads(log.kept("g1-red-judgment"))
            assert failed["steps"] == ["red"]
            assert str(cfg.state_dir / "g1-red-check.txt") in failed["reports"]
        else:
            assert backend.briefs == [original_brief]
        assert log.kept("g1-red-evidence") == ""
        assert (wt / "a.py").read_text() == "original"
        for index in (1, 3, 4):
            with pytest.raises(RulingNeeded, match="no current successful evidence"):
                loop._red_ids(group, index, wt, EventLog(cfg))
    assert {name: log.kept(f"g1-{name}-before") for name in snapshots} == snapshots
    assert (wt / "keep.py").read_text() == "preserve"


@pytest.mark.parametrize("trigger", ["step", "formatter", "group"])
@pytest.mark.parametrize("with_producers", [False, True])
def test_judgment_check_failures_keep_context_without_finding_anchors(
    green_cfg, log, fake_backend, trigger, with_producers
):
    cfg = _viewing(green_cfg)
    failure = ["sh", "-c", "echo opaque failure >&2; exit 3"]
    stub = make_step("stub", ["a.py"], check=failure if trigger == "step" else [])
    group = make_group(stub, design="docs/superpowers/specs/approved.md")
    files = [{"a.py": "original"}]
    outputs = [""]
    ids = ["tests/test_a.py::test_a", "tests/test_b.py::test_b"]
    if with_producers:
        producers = [
            make_step(
                name,
                [test_id.partition("::")[0]],
                role="test-writer",
                form=forms.Red((forms.Behavior("Approved behavior", "a.py"),)),
                views=["a.py"],
                check=["false"],
                expect_code=1,
            )
            for name, test_id in zip(["red_a", "red_b"], ids)
        ]
        build = make_step(
            "build",
            ["a.py"],
            form=forms.Build(
                "core-builder",
                "Approved build",
                (forms.RedSelection("red_a"), forms.RedSelection("red_b")),
            ),
            check=failure if trigger == "step" else ["true"],
        )
        group = make_group(
            replace(stub, check=[]),
            *producers,
            build,
            design="docs/superpowers/specs/approved.md",
        )
        files += [{test_id.partition("::")[0]: "red"} for test_id in ids]
        files += [{"a.py": "built"}]
        outputs += [_red_report(test_id) for test_id in ids] + [""]
    if trigger == "formatter":
        cfg = replace(cfg, format_cmd=failure)
    elif trigger == "group":
        cfg = replace(cfg, check_cmd=failure)
    outputs.append(
        "VERDICT undetermined\nACTION owner\nREASON Need owner evidence\n"
        "EVIDENCE a.py:1 | observed implementation\nEND JUDGMENT"
    )
    backend = fake_backend(files, outputs)
    with pytest.raises(WrongTest, match="Need owner evidence"):
        loop.run_group(cfg, group, log, backend)
    context = group.steps[-1].id if trigger == "step" else "group-check"
    judgment = json.loads(log.kept(f"g1-{context}-judgment"))
    expected_steps = [s.id for s in group.steps]
    if trigger == "step" and with_producers:
        expected_steps.remove("stub")
    assert judgment["steps"] == expected_steps
    assert judgment["red_ids"] == (ids if with_producers else [])
    assert judgment["spec"] == group.design
    assert (
        "Approved build" in judgment["step"]
        if with_producers
        else "Approved step stub" in judgment["step"]
    )
    check_key = f"g1-{context}-check" if trigger == "step" else "g1-check"
    report_paths = {str(log.keep(check_key, log.kept(check_key)))}
    report_paths.update(
        str(log.keep(f"g1-{sid}", log.kept(f"g1-{sid}"))) for sid in expected_steps
    )
    assert set(judgment["reports"]) == report_paths
    assert "opaque failure" in log.kept(check_key)
    assert backend.agents[-1] == "juror"
    assert len(backend.agents) == len(group.steps) + 1
    assert log.kept(f"g1-{context}-juror-1").startswith("VERDICT undetermined")
    assert not log.done()


def test_judgment_expected_producer_red_remains_successful_evidence(
    green_cfg, log, fake_backend, origin
):
    cfg = _viewing(green_cfg)
    test_id = "tests/test_a.py::test_a"
    producer = make_step(
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
        ["a.py"],
        form=forms.Build(
            "core-builder", "Approved build", (forms.RedSelection("red"),)
        ),
        check=["true"],
    )
    group = make_group(make_step("stub", ["a.py"]), producer, build)
    backend = fake_backend(
        [{"a.py": "stub"}, {"tests/test_a.py": "red"}, {"a.py": "built"}],
        ["", _red_report(test_id), ""],
    )
    loop.run_group(cfg, group, log, backend)
    assert backend.agents == ["core-builder", "test-writer", "core-builder"]
    assert test_id in backend.briefs[-1]
    assert log.kept("g1-red-evidence")
    assert log.done() == {"g1"}


@pytest.mark.parametrize("trigger", ["step", "formatter", "group"])
def test_judgment_check_timeouts_remain_rulings(green_cfg, log, fake_backend, trigger):
    timeout = ["sh", "-c", "echo infrastructure timeout >&2; exit 124"]
    cfg = green_cfg
    if trigger == "formatter":
        cfg = replace(cfg, format_cmd=timeout)
    elif trigger == "group":
        cfg = replace(cfg, check_cmd=timeout)
    group = make_group(
        make_step("stub", ["a.py"], check=timeout if trigger == "step" else [])
    )
    backend = fake_backend([{"a.py": "stub"}])
    with pytest.raises(RulingNeeded, match="timed out"):
        loop.run_group(cfg, group, log, backend)
    assert backend.agents == ["core-builder"]
    assert not log.done()


@pytest.mark.parametrize("invalid", ["missing-spec", "stale-producer"])
def test_judgment_group_rejects_missing_spec_or_stale_evidence(
    green_cfg, log, fake_backend, invalid
):
    cfg = replace(_viewing(green_cfg), check_cmd=["false"])
    producer = make_step(
        "red",
        ["tests/test_a.py"],
        role="test-writer",
        form=forms.Red((forms.Behavior("Approved behavior", "a.py"),)),
        views=["a.py"],
        check=["false"],
        expect_code=1,
    )
    group = make_group(
        make_step("stub", ["a.py"]),
        producer,
        design="docs/superpowers/specs/approved.md",
    )
    backend = fake_backend(
        [{"a.py": "stub"}, {"tests/test_a.py": "red"}],
        ["", _red_report("tests/test_a.py::test_a")],
    )
    wt = git.make_worktree(cfg, group.id)
    loop._steps(cfg, group, wt, log, backend, 0)
    if invalid == "missing-spec":
        group = replace(group, design="")
        diagnostic = "judgment.spec"
    else:
        log.keep("g1-red-evidence", "stale")
        diagnostic = "no current successful evidence"
    with pytest.raises(RulingNeeded, match=diagnostic):
        loop._gate(cfg, group, wt, log, backend)
    assert backend.agents == ["core-builder", "test-writer"]
    assert not log.done()


def test_judgment_group_verdict_return_keeps_landing_blocked(
    green_cfg, log, fake_backend, capsys
):
    cfg = replace(green_cfg, check_cmd=["false"], juror_rounds=2)
    group = make_group(
        make_step("build", ["a.py"], form=forms.Build("core-builder", "Build it", ())),
        design="docs/superpowers/specs/approved.md",
    )
    verdict = (
        "VERDICT build wrong\nACTION retry\nSTEP build\nREASON Fix implementation\n"
        "EVIDENCE a.py:1 | still broken\nEND JUDGMENT"
    )
    backend = fake_backend(
        [
            {"a.py": "broken"},
            {},
            {"a.py": "still broken"},
            {},
            {"a.py": "broken again"},
        ],
        ["", verdict, "", verdict],
    )
    with pytest.raises(WrongTest, match="juror rounds exhausted"):
        loop.run_group(cfg, group, log, backend)
    assert backend.agents == [
        "core-builder",
        "juror",
        "core-builder",
        "juror",
        "core-builder",
    ]
    assert log.kept("g1-group-check-juror-2").rstrip() == verdict
    resumed = fake_backend()
    with pytest.raises(WrongTest, match="juror rounds exhausted"):
        loop._gate(cfg, group, git.worktree(cfg, group.id), EventLog(cfg), resumed)
    assert resumed.agents == []
    assert "[test_wrong] g1:" in capsys.readouterr().err
    assert git.worktree(cfg, group.id).exists()
    assert not log.done()


def test_group_retry_replays_dependent_writes_and_preserves_unrelated(
    green_cfg, origin, log, fake_backend
):
    cfg = replace(green_cfg, check_cmd=["sh", "-c", 'test "$(cat a.py)" = final'])
    group = make_group(
        make_step(
            "build",
            ["a.py"],
            form=forms.Build("core-builder", "Build first", ()),
            check=["true"],
        ),
        make_step("unrelated", ["keep.py"]),
        make_step(
            "dependent",
            ["a.py", "extra.py"],
            form=forms.Build("core-builder", "Build later", ()),
            check=["true"],
        ),
        design="docs/superpowers/specs/approved.md",
    )
    verdict = (
        "VERDICT build wrong\nACTION retry\nSTEP build\nREASON Fix implementation\n"
        "EVIDENCE a.py:1 | initial build was wrong\nEND JUDGMENT"
    )

    class Spy(fake_backend):
        def start(self, text, worktree, agent):
            if len(self.briefs) == 4:
                assert not (worktree / "a.py").exists()
                assert not (worktree / "extra.py").exists()
                assert (worktree / "keep.py").read_text() == "keep"
            if len(self.briefs) == 5:
                assert (worktree / "a.py").read_text() == "fixed first"
            return super().start(text, worktree, agent)

    backend = Spy(
        [
            {"a.py": "first"},
            {"keep.py": "keep"},
            {"a.py": "broken", "extra.py": "stale"},
            {},
            {"a.py": "fixed first"},
            {"a.py": "final", "extra.py": "fresh"},
        ],
        ["", "", "", verdict],
    )
    commit = loop.run_group(cfg, group, log, backend)
    assert backend.agents == [
        "core-builder",
        "core-builder",
        "core-builder",
        "juror",
        "core-builder",
        "core-builder",
    ]
    assert backend.briefs[-1] == backend.briefs[2]
    assert "Fix implementation" in backend.briefs[-2]
    assert run_ok(["git", "show", f"{commit}:keep.py"], origin).out == "keep"
    assert run_ok(["git", "show", f"{commit}:extra.py"], origin).out == "fresh"
    assert log.done() == {"g1"}


@pytest.mark.parametrize("action", ["repair", "redo red"])
def test_group_test_fix_resumes_affected_checks_only(
    green_cfg, origin, log, fake_backend, monkeypatch, action
):
    cfg = replace(
        _viewing(green_cfg),
        check_cmd=["sh", "-c", "grep -q 'value = 2' tests/test_a.py"],
    )
    old_id = "tests/test_a.py::test_value"
    new_id = old_id if action == "repair" else "tests/test_a.py::test_new_value"
    original = "def test_value():\n    value = 1\n    assert value == 2\n"
    repaired = original.replace("value = 1", "value = 2")
    if action == "redo red":
        repaired = repaired.replace("test_value", "test_new_value")
    producer = make_step(
        "red",
        ["tests/test_a.py"],
        views=["a.py"],
        role="test-writer",
        form=forms.Red((forms.Behavior("Approved behavior", "a.py"),)),
        check=["false"],
        expect_code=1,
    )

    def build(name, source):
        return make_step(
            name,
            [source],
            form=forms.Build(
                "core-builder", f"Build {name}", (forms.RedSelection("red"),)
            ),
            check=["true"],
        )

    group = make_group(
        make_step("stub", ["a.py"]),
        producer,
        build("first", "a.py"),
        build("second", "b.py"),
        make_step(
            "unrelated",
            ["keep.py"],
            form=forms.Build("core-builder", "Unrelated", ()),
            check=["true"],
        ),
        design="docs/superpowers/specs/approved.md",
    )
    verdict = (
        f"VERDICT tests wrong\nACTION {action}\nSTEP red\nREASON Fix approved tests\n"
        "EVIDENCE tests/test_a.py:2 | wrong test input\n"
        + ("FAILURE wrong fixture value\n" if action == "repair" else "")
        + "END JUDGMENT"
    )
    files = [
        {"a.py": "stub"},
        {"tests/test_a.py": original},
        {"a.py": "first"},
        {"b.py": "second"},
        {"keep.py": "preserve"},
        {},
        {"tests/test_a.py": repaired},
    ]
    outputs = [
        "",
        _red_report(old_id),
        "",
        "",
        "",
        verdict,
        "RUN after all green" if action == "repair" else _red_report(new_id),
    ]
    if action == "redo red":
        files += [{"a.py": "fresh first"}, {"b.py": "fresh second"}]
    backend = fake_backend(files, outputs)
    checked = []
    original_check = loop.verify.check_step

    def check(step, worktree):
        checked.append(step.id)
        return original_check(step, worktree)

    monkeypatch.setattr(loop.verify, "check_step", check)
    commit = loop.run_group(cfg, group, log, backend)
    assert checked == ["stub", "red", "first", "second", "unrelated"] + (
        ["red"] if action == "redo red" else []
    ) + ["first", "second"]
    assert backend.agents == [
        "core-builder",
        "test-writer",
        "core-builder",
        "core-builder",
        "core-builder",
        "juror",
        "test-writer",
    ] + (["core-builder", "core-builder"] if action == "redo red" else [])
    if action == "redo red":
        assert all(
            new_id in text and old_id not in text for text in backend.briefs[-2:]
        )
    else:
        assert log.kept("g1-red").rstrip() == _red_report(old_id)
    assert run_ok(["git", "show", f"{commit}:keep.py"], origin).out == "preserve"
    assert log.done() == {"g1"}


@pytest.mark.parametrize("action", ["repair", "redo red"])
def test_group_test_fix_keeps_transitive_completed_dependents(
    green_cfg, origin, log, fake_backend, action
):
    cfg = replace(
        green_cfg,
        view_cmd=["sh", "-c", 'mkdir -p "$2" && touch "$2/a.pyi" "$2/b.pyi"', "sh"],
        check_cmd=["sh", "-c", "grep -q 'value = 2' tests/test_a.py"],
    )
    test_id = "tests/test_a.py::test_value"
    original = "def test_value():\n    value = 1\n    assert value == 2\n"
    producer = make_step(
        "red",
        ["tests/test_a.py"],
        views=["a.py"],
        role="test-writer",
        form=forms.Red((forms.Behavior("Approved behavior", "a.py"),)),
        check=["false"],
        expect_code=1,
    )
    build = make_step(
        "build",
        ["a.py"],
        form=forms.Build(
            "core-builder", "Build implementation", (forms.RedSelection("red"),)
        ),
        check=["sh", "-c", "grep -q 'value = 1' tests/test_a.py || grep -q fixed a.py"],
    )
    dependent = make_step(
        "dependent",
        ["b.py"],
        views=["a.py"],
        form=forms.Build("core-builder", "Build dependent", ()),
        check=["true"],
    )
    transitive = make_step(
        "transitive",
        ["c.py"],
        views=["b.py"],
        form=forms.Build("core-builder", "Build transitive", ()),
        check=["true"],
    )
    group = make_group(
        make_step("stub", ["a.py"]),
        producer,
        build,
        dependent,
        transitive,
        make_step("unrelated", ["keep.py"]),
        design="docs/superpowers/specs/approved.md",
    )
    verdict = (
        f"VERDICT tests wrong\nACTION {action}\nSTEP red\nREASON Fix tests\nEVIDENCE tests/test_a.py:2 | fixture wrong\n"
        + ("FAILURE wrong fixture\n" if action == "repair" else "")
        + "END JUDGMENT"
    )
    retry = "VERDICT build wrong\nACTION retry\nSTEP build\nREASON Fix implementation\nEVIDENCE a.py:1 | implementation wrong\nEND JUDGMENT"
    files = [
        {"a.py": "stub"},
        {"tests/test_a.py": original},
        {"a.py": "initial"},
        {"b.py": "old"},
        {"c.py": "old"},
        {"keep.py": "preserve"},
        {},
        {"tests/test_a.py": original.replace("value = 1", "value = 2")},
    ]
    outputs = [
        "",
        _red_report(test_id),
        "",
        "",
        "",
        "",
        verdict,
        "RUN after all green" if action == "repair" else _red_report(test_id),
    ]
    if action == "repair":
        files.append({})
        outputs.append(retry)
    files += [{"a.py": "fixed"}, {"b.py": "fresh"}, {"c.py": "fresh"}]
    backend = fake_backend(files, outputs)
    commit = loop.run_group(cfg, group, log, backend)
    assert backend.agents == [
        "core-builder",
        "test-writer",
        "core-builder",
        "core-builder",
        "core-builder",
        "core-builder",
        "juror",
        "test-writer",
    ] + (["juror"] if action == "repair" else []) + [
        "core-builder",
        "core-builder",
        "core-builder",
    ]
    assert run_ok(["git", "show", f"{commit}:b.py"], origin).out == "fresh"
    assert run_ok(["git", "show", f"{commit}:c.py"], origin).out == "fresh"
    assert run_ok(["git", "show", f"{commit}:keep.py"], origin).out == "preserve"


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "missing",
        "assertions",
        "membership",
        "report",
        "invalidated",
        "partial",
        "form",
        "paths",
        "views",
        "design",
        "build",
    ],
)
@pytest.mark.parametrize("continuation", ["step", "group", "upheld"])
def test_retest_refreshes_preserved_red_evidence_before_restoring_build(
    green_cfg, origin, log, fake_backend, change, continuation
):
    cfg = _viewing(green_cfg)
    test_id = "tests/test_a.py::test_new"
    original = "def test_new():\n    value = 1\n    assert value == 1\n"
    red = make_step(
        "red",
        ["tests/test_a.py"],
        role="test-writer",
        views=["a.py"],
        form=forms.Red((forms.Behavior("Approved behavior", "a.py"),)),
        check=["false"],
        expect_code=1,
    )
    build = make_step(
        "build",
        ["a.py"],
        form=forms.Build(
            "core-builder", "Approved build", (forms.RedSelection("red"),)
        ),
    )
    group = make_group(red, build, design="docs/superpowers/specs/approved.md")
    wt = git.make_worktree(cfg, group.id)
    (wt / "a.py").write_text("original")
    wrong = f"TEST WRONG 1:\n  {test_id} | wrong machinery\n"
    repair = (
        "VERDICT tests wrong\nACTION repair\nSTEP red\nREASON Repair machinery\n"
        "EVIDENCE tests/test_a.py:2 | wrong fixture\nFAILURE wrong fixture\nEND JUDGMENT"
    )
    with pytest.raises(WrongTest):
        loop._steps(
            cfg,
            group,
            wt,
            log,
            fake_backend(
                [
                    {"tests/test_a.py": original},
                    {"a.py": "partial"},
                    {},
                    {},
                    {"a.py": "stopped"},
                    {},
                ],
                [
                    _red_report(test_id),
                    wrong,
                    repair,
                    "RUN after all green",
                    wrong,
                    _undetermined("tests/test_a.py:1"),
                ],
            ),
            0,
        )
    retry = (
        "VERDICT build wrong\nACTION retry\nSTEP build\nREASON Fix implementation\n"
        "EVIDENCE a.py:1 | implementation defect\nEND JUDGMENT"
    )
    if continuation == "group":
        cfg = replace(cfg, check_cmd=RED_UNTIL_FIXED)
        with pytest.raises(WrongTest):
            loop.run_retest(
                cfg,
                group,
                retry,
                EventLog(cfg),
                fake_backend([{"a.py": "stopped"}, {}], ["", _undetermined("a.py:1")]),
            )
    elif continuation == "upheld":
        cfg = _reviewed(cfg)
        with pytest.raises(FindingsToRule):
            loop.run_retest(
                cfg,
                group,
                retry,
                EventLog(cfg),
                fake_backend(
                    [{"a.py": "stopped"}, {}], ["", "FINDINGS 1\n  a.py:1 wrong\n"]
                ),
            )
    if change == "missing":
        (wt / "tests/test_a.py").unlink()
    elif change == "assertions":
        (wt / "tests/test_a.py").write_text(original.replace("== 1", "== 2"))
    elif change == "membership":
        (wt / "tests/test_a.py").write_text(
            original + "\ndef test_extra():\n    pass\n"
        )
    elif change == "report":
        log.keep("g1-red", _red_report("tests/test_a.py::test_replaced"))
    elif change == "invalidated":
        log.keep("g1-red-evidence", "")
    elif change == "partial":
        log.keep("g1-red", "SHAPE red")
    elif change == "form":
        red = replace(
            red, form=forms.Red((forms.Behavior("Changed behavior", "a.py"),))
        )
    elif change == "paths":
        red = replace(red, paths=["tests/test_a.py", "tests/test_b.py"])
    elif change == "views":
        red = replace(red, views=["a.py", "b.py"])
    elif change == "design":
        group = replace(group, design="docs/superpowers/specs/changed.md")
    elif change == "build":
        build = replace(build, form=replace(build.form, step="Changed build"))
    group = replace(group, steps=[red, build])
    resumed = fake_backend([{"a.py": "fixed"}])
    resume = loop.run_upheld if continuation == "upheld" else loop.run_retest
    text = "" if continuation == "upheld" else retry
    if change == "none":
        resume(cfg, group, text, EventLog(cfg), resumed)
        assert resumed.briefs == (
            []
            if continuation == "upheld"
            else [f"Approved build\na.py\n{test_id}\nFix implementation"]
        )
    else:
        with pytest.raises(RulingNeeded):
            resume(cfg, group, text, EventLog(cfg), resumed)
        assert resumed.briefs == []
        assert (wt / "a.py").read_text() == "stopped"
        assert not log.done()


@pytest.mark.parametrize("valid_report", [True, False])
def test_named_red_producer_uses_report_ids_with_existing_source_view(
    green_cfg, log, fake_backend, valid_report
):
    cfg = _viewing(green_cfg)
    first = "tests/test_a.py::test_new[1-2]"
    second = "tests/test_a.py::test_other"
    summary = "FAILED tests/test_a.py::test_existing - boom\nERROR tests/test_b.py\n"
    red = make_step(
        "red",
        ["tests/test_a.py"],
        role="test-writer",
        form=forms.Red((forms.Behavior("A behavior", "a.py"),)),
        views=["a.py"],
        check=["sh", "-c", f"printf '{summary}'; exit 1"],
        expect_code=1,
    )
    build = make_step(
        "build",
        form=forms.Build(
            "core-builder", "Approved build", (forms.RedSelection("red"),)
        ),
    )
    group = make_group(red, build)
    wt = git.make_worktree(cfg, group.id)
    (wt / "a.py").write_text("existing source")
    backend = fake_backend(
        outputs=[_red_report(first, second) if valid_report else summary]
    )
    if valid_report:
        loop._steps(cfg, group, wt, log, backend, 0)
        assert (
            backend.briefs[1]
            == f"Approved build\ngauntlet/core/a.py\n{first}\n{second}"
        )
        assert log.kept("g1-build-brief") == backend.briefs[1]
    else:
        with pytest.raises(RulingNeeded, match="g1-red.txt"):
            loop._steps(cfg, group, wt, log, backend, 0)
        assert backend.agents == ["test-writer"]
    assert log.kept("g1-red-check") == summary
    assert (cfg.view_root / "g1/a.pyi").is_file()


def _cut_red_run(cfg, log, fake_backend, texts, outputs, **limits):
    cfg = replace(_viewing(cfg), **limits)
    red = make_red("r1", texts, source="a.py")
    g = make_group(red)
    wt = git.make_worktree(cfg, g.id)
    (wt / "a.py").write_text("stub")
    backend = fake_backend([{"tests/test_a.py": "red"}] * len(outputs), outputs=outputs)
    return red, g, wt, backend, lambda: loop._steps(cfg, g, wt, log, backend, 0)


def test_red_over_the_page_limit_is_refused_before_any_dispatch(
    green_cfg, log, fake_backend
):
    _, _, _, backend, run = _cut_red_run(
        green_cfg, log, fake_backend, ("One.", "Two."), [], page_chars=1
    )
    with pytest.raises(RulingNeeded, match="OVERLOADED: page"):
        run()
    assert backend.briefs == []


def test_red_over_the_behavior_limit_is_cut_into_parts_of_whole_behaviors(
    green_cfg, log, fake_backend
):
    first, second = "tests/test_a.py::test_one", "tests/test_a.py::test_two"
    outputs = [_red_report(first), _red_report(second)]
    red, _, _, backend, run = _cut_red_run(
        green_cfg,
        log,
        fake_backend,
        ("One.", "Two.", "Three."),
        outputs,
        red_behaviors=2,
    )
    outputs[0] = _red_report(first, "tests/test_a.py::test_one_b", behavior=2)
    backend.outputs[0] = outputs[0]
    run()
    assert len(backend.briefs) == 2
    assert backend.briefs[0].count("behavior:") == 2
    assert backend.briefs[1].count("behavior:") == 1
    assert log.kept("g1-r1-parts") == "[2, 1]"
    assert log.kept("g1-r1-brief").count("behavior:") == 3
    assert log.kept("g1-r1-part-1-brief") == backend.briefs[0]
    assert log.kept("g1-r1-part-2") == outputs[1] + "\n"
    evidence = loop._red_evidence(red, "g1-r1", log)
    assert [(e.test_id, e.behavior) for e in evidence] == [
        (first, 2),
        ("tests/test_a.py::test_one_b", 2),
        (second, 3),
    ]


def test_red_in_one_part_keeps_its_name_and_a_single_count(
    green_cfg, log, fake_backend
):
    report = _red_report("tests/test_a.py::test_one")
    red, _, _, backend, run = _cut_red_run(
        green_cfg, log, fake_backend, ("One.",), [report]
    )
    run()
    assert len(backend.briefs) == 1
    assert log.kept("g1-r1-parts") == "[1]"
    assert log.kept("g1-r1") == report + "\n"
    assert loop._parts("g1-r1", log, red) == [("g1-r1", 1)]


def test_red_part_reporting_a_behavior_beyond_its_own_count_stops(
    green_cfg, log, fake_backend
):
    outputs = [
        _red_report("tests/test_a.py::test_one", behavior=3),
        _red_report("tests/test_a.py::test_two"),
    ]
    _, _, _, _, run = _cut_red_run(
        green_cfg,
        log,
        fake_backend,
        ("One.", "Two.", "Three."),
        outputs,
        red_behaviors=2,
    )
    with pytest.raises(RulingNeeded, match="outside producer form"):
        run()


def test_missing_parts_read_as_one_report_of_all_behaviors(log):
    red = make_red("r1", ("One.", "Two."))
    assert loop._parts("g1-r1", log, red) == [("g1-r1", 2)]


def test_wrong_test_in_first_part_judges_only_the_parts_that_ran(
    green_cfg, log, fake_backend
):
    cfg = replace(_viewing(green_cfg), red_behaviors=1)
    group = make_group(
        make_red("r1", ("One.", "Two."), source="a.py"),
        design="docs/superpowers/specs/approved.md",
    )
    wt = git.make_worktree(cfg, group.id)
    (wt / "a.py").write_text("stub")
    log.keep("g1-r1-part-2", "stale report of an earlier run")
    output = "TEST WRONG 1:\n  tests/test_a.py::test_a | wrong | spec:1\n"
    backend = fake_backend(
        [{"tests/test_a.py": "red"}], [output, _undetermined("README.md:1")]
    )
    with pytest.raises(WrongTest):
        loop._steps(cfg, group, wt, log, backend, 0)
    assert backend.agents == ["test-writer", "juror"]
    judgment = json.loads(log.kept("g1-r1-judgment"))
    assert judgment["reports"] == [str(cfg.state_dir / "g1-r1-part-1.txt")]
    assert log.kept("g1-r1-part-2") == ""


def _hqptuner(cfg: Config, **fields: object) -> Config:
    """cfg naming HQPTuner's own builder and spec folder instead of Gauntlet's."""
    return replace(
        cfg, builders={"backend": ("hqptuner/",)}, spec_dir="specs/", **fields
    )


def _backend_step(sid: str = "s1", **fields: object):
    return make_step(
        sid,
        ["hqptuner/a.py"],
        role="backend",
        form=forms.Stub("backend", f"Approved step {sid}"),
        **fields,
    )


def test_upheld_fix_goes_to_a_configured_builder_inside_its_own_folder(
    green_cfg, origin, log, fake_backend
):
    cfg = _hqptuner(green_cfg, reviewers=[Reviewer("diff-reviewer", ["hqptuner"])])
    g = make_group(_backend_step(), design="specs/approved.md")
    wt = git.make_worktree(cfg, g.id)
    (wt / "hqptuner").mkdir()
    (wt / "hqptuner/a.py").write_text("value = 1\n")
    line = "misplaced | hqptuner/a.py:1 | wrong | fails when: called"
    log.keep("g1-review-queue", json.dumps(asdict(g), sort_keys=True))
    log.keep("g1-review-diff-reviewer", f"FINDINGS 1\n  {line}\n")
    backend = fake_backend([{"hqptuner/a.py": "value = 2\n"}], ["", "FINDINGS 0\n"])
    commit = loop.run_upheld(cfg, g, line, log, backend)
    assert backend.agents == ["backend", "diff-reviewer"]
    assert backend.briefs[0] == line
    assert run_ok(["git", "show", f"{commit}:hqptuner/a.py"], origin).out == (
        "value = 2\n"
    )
    assert log.done() == {"g1"}


@pytest.mark.parametrize("role", ["diff-reviewer", "test-reviewer"])
def test_default_review_of_a_configured_builder_group_lands(
    green_cfg, origin, log, fake_backend, role
):
    cfg = _hqptuner(green_cfg, reviewers=[Reviewer(role, ["hqptuner"])])
    g = make_group(_backend_step(), design="specs/approved.md")
    backend = fake_backend([{"hqptuner/a.py": "1"}], ["", "FINDINGS 0\n"])
    loop.run_group(cfg, g, log, backend)
    assert backend.agents == ["backend", role]
    assert backend.briefs[1] == "working tree"
    assert log.done() == {"g1"}


def test_failing_configured_builder_step_reaches_juror_with_configured_spec(
    green_cfg, log, fake_backend
):
    cfg = _hqptuner(green_cfg)
    g = make_group(_backend_step(check=["false"]), design="specs/approved.md")
    backend = fake_backend(
        [{"hqptuner/a.py": "1"}], ["", _undetermined("hqptuner/a.py:1")]
    )
    with pytest.raises(WrongTest, match="Need owner evidence"):
        loop.run_group(cfg, g, log, backend)
    assert backend.agents == ["backend", "juror"]
    assert json.loads(log.kept("g1-s1-judgment"))["spec"] == "specs/approved.md"
    assert not log.done()


def test_retest_accepts_a_kept_judgment_of_the_configured_spec(
    green_cfg, log, fake_backend
):
    cfg = _hqptuner(green_cfg)
    g = make_group(_backend_step(), design="specs/approved.md")
    wt = git.make_worktree(cfg, g.id)
    judgment = forms.Judgment(
        "specs/approved.md",
        ("s1",),
        "Approved step s1",
        (),
        (str(log.keep("g1-check", "hqptuner/a.py:1 bad\n")),),
    )
    log.keep(
        "g1-pending-judgment",
        json.dumps(
            {
                "context": "group-check",
                "queue": json.dumps(asdict(g), sort_keys=True),
                "completed": True,
                "judgment": asdict(judgment),
            }
        ),
    )
    log.keep("g1-owner-judgment", json.dumps(asdict(judgment)))
    backend = fake_backend()
    with pytest.raises(WrongTest, match="Need owner evidence"):
        loop.run_retest(cfg, g, _undetermined("README.md:1"), log, backend)
    assert backend.briefs == []
    assert wt.exists()
    assert not log.done()
