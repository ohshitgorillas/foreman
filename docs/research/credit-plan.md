# Spreading credits across the month (draft, 2026-10-23)

## The pools
1. **Main plan: Max $200 (weekly reset)**: the backbone. Day-to-day Void work, orchestration, and local Gauntlet leads and builders.
2. **Cloud credits ($250/month, reset in November)**: long unattended builds.
3. **API credits ($200/month)**: overflow and cheap bulk work.
4. **Codex ($100 plan; resets come at random times, OpenAI)**: parallel independent steps only. Not the main road.

## Weekly budget (about 4.3 weeks a month)
| Pool | Per week | Per day |
|---|---|---|
| Cloud | ~$55 | ~$8 |
| API | ~$45 | ~$6.50 |
| Main plan | 1/7 of the week's allowance | |
| Codex | spend freely after a reset, stop at about 20% left | |

Keep about 15% of the cloud and API pools back until the last week, as a reserve.

## Who does what
- **Main plan first.** Gauntlet steps run as local leads on the main plan by default, with Sonnet builders and Opus only for design and review (already set up in f79e8a7).
- **Cloud for overnight and long work.** One cloud lead at a time, launched at night or when local is busy: a whole section like steps 44-47. That costs about $8-15 a section, so roughly 4-5 sections a week fit in budget.
- **API for bulk and cheap work.** Music filing manifests, trivia and doc passes, Haiku chores, and evals, with prompt caching on. Use the Batch API (half price) when nothing is urgent. Also used as a fallback for Claude Code only when the main plan is capped late in the week.
- **Codex for parallel steps.** Use it when two independent steps can run side by side with the main plan's work, not as a stand-in for it.

## Pacing rules
- **Monday check (each reset):** look at last week's spend in each pool and set this week's split. Note it in the handoff.
- **Main plan past 70% by Thursday:** move builders to cloud for the rest of the week, not to Codex.
- **Cloud or API past its weekly share:** pause new launches from that pool until next Monday, unless the owner OKs it.
- **Cost savers** (from token-review-2026-10-23): one step per dispatch, fresh agents over long resumes, `ruff format` plus one `make check` before reviewers, and no new process or doc work.

## Open
- Main plan is Max $200. Track its weekly usage meter (not a dollar figure). Codex is the $100 plan with random resets: check its meter at each Monday check and after any surprise reset.
