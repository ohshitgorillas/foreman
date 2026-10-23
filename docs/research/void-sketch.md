# Repo builder harness: sketch (2026-10-23)

Goal: automate the loop Void runs by hand for Gauntlet M1. Today that loop is: cut steps, sed a brief from a template, launch Codex or cloud, wait for pings, run `make check`, resume with findings, commit, push, watch budgets. Deterministic code runs the loop; models only build and review.

## 1. Step queue (data, not prose)
One file per milestone, one record per step:
- id
- files in scope (5 or fewer)
- the one done-check command
- design reference
- depends-on
- expected size

The overload test (7.1f) runs as a lint on this file, before anything is dispatched, so a bad brief never reaches an agent.

## 2. Dispatcher
- Picks the next ready step.
- Renders the brief from one template: HEAD, the step, its files, any earlier outputs it needs, and the standing lines (ruff format, focused suites, copyright line).
- Picks a backend from the budget ledger (section 6): main plan, cloud, Codex, or API.

## 3. Backend adapters, one interface
`start(brief, worktree) -> job`, then `status(job)`, then `result(job) -> branch or commit`. There are four adapters:
- local Claude agent
- `claude --cloud` (needs a TTY wrapper)
- `codex exec`
- API batch

Each adapter writes start, stop and exit events to one event log. Pings become a by-product of that log, not something each brief has to remember to send.

## 4. Verifier (harness-owned, no model)
When a job ends, the harness itself runs format, then `make check` in the worktree.
- **Red:** it writes a short resume brief quoting the exact failures and resumes once. If the session is over ~250k tokens, it starts a fresh agent instead.
- **Red twice:** it escalates to the human.
- **Green:** it runs the reviewer pass, commits only the step's paths (fixing the step-commit whole-index bug), then pushes.

## 5. Human surface
Only three things reach the owner: a ruling needed, a step red twice, or a milestone done. Everything else stays in the log, with a one-line status on demand. That replaces the inbox watcher, polling loops and missed-ping idle time.

## 6. Budget ledger
- Per pool: Max weekly meter, cloud $, API $, and Codex % (resets come at random times).
- Each step is tagged with its actual cost, which also gives the per-group cost table for free.
- Routing rules come from credit-plan-2026-11.md.

## Where it fits
Much of this is Gauntlet's own job, run and resume seams (M1 steps 34-43 build them). The thin version is a script around the step file plus these adapters, which could run M2. The full version is Gauntlet itself running its own build.

## Biggest wins, in order
1. The verifier loop: no idle waiting for Void, and no costly resumes for mechanical failures.
2. The step-file lint: no refused briefs.
3. Automatic backend routing by budget.

## Existing tools (quick search 2026-10-23, from READMEs only, untested)
- **agenthub** (closest): Claude Code plans and reviews, Codex and Gemini workers do the work. It has a task queue with dependencies, worktrees, quota-aware scheduling, auto checks (lint, type, test, build) and review plus auto-merge. https://github.com/HuyAK47/agenthub
- **Orchestrator.inc agent-orchestrator** (Apache-2.0): one task, one agent and one workspace per worker, running from task to merged PR. It supports Claude Code, Codex and 30+ other agents, and has desktop and phone apps. https://github.com/OrchestratorInc/agent-orchestrator
- **crew**: a terminal orchestrator with tickets, dependencies and a worktree per agent. https://github.com/pikehouse/crew
- **codex-orchestrator**: Claude Code hands work to Codex in tmux. https://github.com/kingbootoshi/codex-orchestrator
- Lists: https://github.com/andyrewlee/awesome-agent-orchestrators and https://www.augmentcode.com/tools/open-source-agent-orchestrators

Gaps to check: none of them is known to have our overload lint, harness-run `make check` with a single exact-findings resume, path-limited commits, or Max/cloud/API/Codex budget routing. agenthub's quota scheduling comes nearest on budgets.
