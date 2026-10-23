# Survey: agenthub (HuyAK47/agenthub), 2026-10-23
Read only from a shallow clone in ~/scratch/survey-agenthub. Nothing was run. HuyAK47's repo is a fork. The upstream is ducanh19020217/agenthub.

## 1. What it does vs our loop
- **Plan:** It has a task queue with dependencies, filled by Claude through MCP, chat or Telegram. It does not walk a plan file.
- **Brief:** It writes `.agenthub/task.md` plus context files into the worktree. There is no template we control.
- **Worktree per step:** Yes (`src/worktrees/index.ts`).
- **Harness runs checks:** Yes, after the agent writes a DONE file. A project's checks can be set to `make check`.
- **Resume with failures:** Yes. It appends the last 60 lines of each failed check and resumes, up to **2** times (`src/scheduler/index.ts` ~719). After that the task goes to review, not escalation.
- **Commit only the step's paths:** No. The worker commits for itself. Nothing limits which paths it touches.

## 2. Backends
- **Codex CLI:** Supported as a worker, with quota tracking.
- **Claude Code Max:** Used only as planner and reviewer, never as a worker. To add it: write a `ClaudeEngine` for the `Engine` interface in `src/engines/index.ts`. But the engine id `'agy'|'codex'` is hard-coded in about 21 files. Medium work.
- **Anthropic API:** Only a fallback for Claude chat, with a daily dollar cap (`src/chat/failover.ts`). There is no monthly budget. Medium work.
- **`claude --cloud`:** Missing. Its work runs off the machine, which clashes with local worktrees. Hard. The PTY code in `src/quota/claude-usage.ts` is a start.
- **Budget routing:** It picks the account with the most of its 5-hour quota left (`scheduler` ~2025). It does not compare across backends, and it does not count dollars.

## 3. Verify and commit
The worker commits. The hub then runs the checks plus three built-ins: uncommitted files, zero-byte files and conflict markers (`src/checks.ts`). Next is an optional Claude or Codex review. It can then auto-merge to an integration branch, re-run checks there, and roll back on failure (`src/merge.ts`).

## 4. Health
- **License:** AGPL-3.0, and contributions need a CLA. Fine for private use.
- **Activity:** 0 stars and 0 issues. About 755 commits in one week (Oct 16-22, 2026), mostly from one author. Version 1.0.0-rc.8.
- **Size:** About 36k lines of TypeScript, plus a React dashboard and about 1,100 tests. Files of 2k-3.7k lines (scheduler, services, telegram).
- **Quality:** Tidy and well tested, but monolithic. Much of it is in Vietnamese, including the worker protocol `src/protocol.md`.

## 5. Risks
- Workers run with permissions and sandbox bypassed by default.
- It reads Google, Codex and Claude OAuth tokens, and it talks to Telegram. That is a large trust surface for week-old code.
- It is a standalone daemon with its own database and port.
- The upstream changes very fast, so a fork will drift.
- Its design runs against our core rules: commit only the step's paths, resume once, then escalate.

## 6. Verdict: borrow ideas only
A usable fork needs about 15-18 steps of five files or fewer:
- ClaudeEngine (1 step).
- Widen the engine id (about 5 steps).
- API backend with monthly budget (1-2 steps).
- `--cloud` PTY backend (2 steps).
- Harness-side commit limited to the step's paths (2 steps).
- Resume once, then escalate (1 step).
- Brief template and plan walker (2 steps).
- Translation (1-2 steps).
- Routing across backends (1-2 steps).

That is too much to own in 36k lines that someone else is changing fast. Ideas worth taking:
- The format for feeding check failures back to the agent.
- The three built-in checks.
- Parsing quota reset times from logs.
- Scraping Claude `/usage` through a PTY.
- The shape of the fallback chain.
- The DONE/BLOCKED marker files.
