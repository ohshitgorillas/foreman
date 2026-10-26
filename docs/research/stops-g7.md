# Stops in the g7 build, 25–26 Oct 2026

Every stop in the evidence of the g7 build and the Foreman fixes around it, once each, with its trigger and whether it caught a real defect. Sources: `state-g7/events.jsonl`, `state-g7-old/events.jsonl`, `~/codex-jobs/gauntlet-foreman/g7-resume*.out` and `lead2*.out`, `~/codex-jobs/foreman-generic/`. A stop seen in two sources is listed once under its first.

**REAL** caught broken code, a test that was really wrong, or a spec wrong in substance. **MISMATCH** stopped on a report's format or wording, a rule or brief-form disagreement, a refusal over paperwork, or a harness limit, while the code and tests were fine. **UNKNOWN** cannot be judged because the kept report was overwritten or nothing was written.

## Tally

28 stops: 4 REAL, 18 MISMATCH, 6 UNKNOWN. Of the 22 that can be judged, 18 stopped the build without a defect behind them.

## Foreman runs

| # | Where | Stop | Trigger | Verdict | Evidence |
|---|---|---|---|---|---|
| 1 | `state-g7-old/events.jsonl:16` | red report requires exactly one STATUS record | `foreman/reports.py:162` before 5c48afe | MISMATCH | `state-g7-old/g7-5.txt:56-58` holds three valid `STATUS ??` lines; the check shows the 8 tests red as intended |
| 2 | `state-g7/events.jsonl:48` | retry target is not a build | `foreman/loop.py:466` before a93fbee | REAL | `state-g7/aside-lead6-step7/g7-7-juror-2.txt:1` "VERDICT build wrong": the port made no edit. The stop text was a harness limit removed in a93fbee |
| 3 | `state-g7/events.jsonl:62-63` | test-writer rejected (steering) | `foreman/loop.py:94`, refusal read at `foreman/reports.py:266` | MISMATCH | `state-g7/g7-8-refused-steering.txt:1-2`: a behavior line cited `gates/core/checker.py:347` |
| 4 | `state-g7/events.jsonl:70-71` | test-writer rejected (steering) | `foreman/loop.py:94` | MISMATCH | `state-g7/aside-lead7-step8/g7-8.txt:1`: `REJECTED: STEERING` on three plain spec behaviors |
| 5 | `state-g7/events.jsonl:80` | red report: blocked or incomplete | `foreman/reports.py:180` | UNKNOWN | `g7-8.txt` overwritten by the 20:38 run; no copy kept |
| 6 | `state-g7/events.jsonl:89` | red report: blocked or incomplete | `foreman/reports.py:180` | MISMATCH | `state-g7/g7-8.txt`: `BLOCKED none`, 13 `LEFT` lines for behaviors already covered by red tests; check "26 failed" as expected |
| 7 | `state-g7/events.jsonl:103` | run halted with no stop logged after `g7-5-brief` | none written | UNKNOWN | no dispatch, outcome or ruling logged; killed from outside |
| 8 | `state-g7/events.jsonl:122`, `g7-resume.out:1` | red report: blocked or incomplete | `foreman/reports.py:180` | UNKNOWN | `g7-5.txt` overwritten; ran before the step 5 design fix 7f20db9 |
| 9 | `state-g7/events.jsonl:137`, `g7-resume5.out:1`, `foreman-generic/lead3.out:3` | red report: blocked or incomplete | `foreman/reports.py:180`, on an `UNTESTABLE` line | MISMATCH | queue behavior 3 asked for a red test of the required keyword, which the design says no red test pins (`m2-launcher-kinds-design.md:682`); the test writer was right; queue text fixed in e7ce669 |
| 10 | `state-g7/events.jsonl:152`, `g7-resume5b.out:1` | red report: blocked or incomplete | `foreman/reports.py:175-180`, on an `UNTESTABLE` line | MISMATCH | `state-g7/g7-5.txt:101` `UNTESTABLE` on a clause the design rules covered by the type gate, `:102` `BLOCKED none`; 28 tests red as intended |
| 11 | `state-g7/events.jsonl:162` | last event `g7-5-brief` kept, then nothing | none written | UNKNOWN | no output written; run ended or was killed without logging a stop |

## Leads and workers

| # | Where | Stop | Trigger | Verdict | Evidence |
|---|---|---|---|---|---|
| 12 | `gauntlet-foreman/lead2.out:5` | builder fix-up timed out at ten minutes | `foreman/loop.py:92` | MISMATCH | `lead2.out:3`: the edit took 3 seconds; the brief's test routine took the rest |
| 13 | `gauntlet-foreman/lead2.out:11` | test-writer BLOCKED report did not stop the group | Foreman bug, `docs/roadmap.md:5` | REAL | a harness defect: a BLOCKED report went unread; the check now at `foreman/reports.py:175` |
| 14 | `gauntlet-foreman/lead20.out:14` | test writer did 2 of 14 behaviors and stopped | oversized brief | MISMATCH | `lead20.out:21`: 14 behaviors, 2,838 characters |
| 15 | `gauntlet-foreman/lead20.out:15` | lead hands back 8 build steps over its cap | `lead.md` fifty-minute cap | MISMATCH | design still proposed; both repositories green (`lead20.out:17`) |
| 16 | `gauntlet-foreman/lead20.out:21` | question: cap behaviors per red request? | `red-overload-design.md` decision 3 | REAL | the design's 3,000-character check would have passed the 2,838-character brief that failed; now `red_behaviors = 6` |
| 17 | `gauntlet-foreman/lead21.out:1` | preflight finds 8 problems; g7 not resumed | `foreman/cli.py:97` | MISMATCH | `lead21.out:8-9`: two unclear design sentences and six role-rule gaps; code committed |
| 18 | `gauntlet-foreman/lead22.out:13` | step 4 needed a fix before commit | not shown | UNKNOWN | no failing test quoted |
| 19 | `gauntlet-foreman/lead22.out:23` | step 6 sent back for formatting | the lead's diff read | MISMATCH | "The step 6 logic is correct": an awkward line wrap |
| 20 | `gauntlet-foreman/lead22.out:27` | step 7 needed a fix | not shown | UNKNOWN | `lead22.out:31` suggests a real logic bug (unrun parts not skipped), but no red check is shown |
| 21 | `gauntlet-foreman/lead23.out:13` | second preflight has findings | `foreman/cli.py:97` | MISMATCH | `lead23.out:15-17`: the test-writer role disagreed with what Foreman sends as a view |
| 22 | `gauntlet-foreman/lead23.out:23` | 1 of 12 preflight problems left | `foreman/cli.py:97` | MISMATCH | `lead23.out:26`: a stub rule gap; Gauntlet's checks pass |
| 23 | `gauntlet-foreman/lead25.out:7` | architect refuses to edit a companion design file | `architect.md:25` | MISMATCH | rulings committed as 7f20db9 anyway; `make check` passed |
| 24 | `gauntlet-foreman/lead25.out:8`, `foreman-generic/next-section-pending.md:1` | preflight: 3 step-5 behaviors no longer match the reworded design word for word | `foreman/preflight.py:79` via `foreman/cli.py:97` | MISMATCH | d9d1d0c: same meaning, new wording ("attempted-call count" to "call count") |
| 25 | `gauntlet-foreman/lead25.out:11` | question: may the queue file outside Gauntlet be edited? | `lead.md` slice scope | MISMATCH | a permission question over a one-file text sync |
| 26 | `gauntlet-foreman/lead27.out:1` | OVERLOADED: harness fix 110–150 min against a 50-min cap | `CLAUDE.md` 7.1f | MISMATCH | a brief-size refusal; no code involved |
| 27 | `foreman-generic/lead1.out:18-21` | fix left uncommitted: new test writes a setting twice | `lead.md` "a test is never edited to pass" | REAL | the test was broken (invalid TOML); fixed in 499291d, `tests/test_preflight.py:797-799` |
| 28 | `foreman-generic/lead2.out:8` | test-writer rejected the repair brief as steering | `test-writer.md:32` | MISMATCH | the brief named the fix; the same fix then landed through another agent |

## Classes of mismatch

- Red report records Foreman reads as a stop although the tests are red as intended: `UNTESTABLE` on a clause the design says no test pins (9, 10), `LEFT` for behaviors already covered (6), several `STATUS` lines (1).
- Worker refusals over brief form: steering (3, 4, 28), size (14, 15, 26).
- Preflight wording and role-rule checks that stop a resume with no code at fault (17, 21, 22, 24).
- Agent stops over scope, formatting, or the file a rule allows (19, 23, 25).
- A timeout spent on a brief's test routine, not on the work (12).
