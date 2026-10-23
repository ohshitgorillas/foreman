# Survey: Orchestrator.inc agent-orchestrator ("AO")

Repo: github.com/OrchestratorInc/agent-orchestrator, commit fd7be1b (2026-10-23).
Read-only survey of a shallow clone at ~/scratch/survey-orchestrator-inc/repo. Nothing was run or installed.

## 1. What it does vs our loop
AO is a desktop app (Electron) with a local Go daemon. It supervises interactive agent sessions and shows them on a Kanban. It is built around PRs and GitHub CI.
- **Planned build, step by step:** No. The "orchestrator" is an LLM chat agent that plans and spawns workers. Nothing reads a plan file and runs the steps in order.
- **Render a brief per step:** Partly. It builds a worker prompt from the task text plus project rules (`AgentRules`, `AgentRulesFile` in `backend/internal/domain/projectconfig.go`). There are no step templates.
- **One agent per step, own worktree:** Yes. This is its core strength (`backend/internal/adapters/workspace/gitworktree/workspace.go`).
- **Harness runs `make check` itself:** No. The only hooks are `PostCreate`/`PreRemove` setup scripts. Checking relies on the agent, or on remote CI after a PR.
- **Resume once with exact failures, else escalate:** Partly. It sends "fix CI" and review nudges to the same agent when a PR's CI fails (`backend/internal/lifecycle/reactions.go`). The input is GitHub check state, not local check output. Escalation means a "Needs you" card.
- **Commit only the step's paths:** No. The worker agent commits, pushes and opens the PR itself. AO does not scope paths.
- **Routing by remaining budget:** No routing across providers. It reads Claude and Codex usage limits, switches to another signed-in account of the *same* provider, and can auto-resume a turn when a limit resets.

## 2. Our backends
- **Claude Code, Max, local:** Supported. It runs the interactive TUI in a PTY, or Chat over ACP. Not `claude -p`.
- **Codex CLI:** Supported, through the TUI or `codex app-server`. Not `codex exec`. It reads Codex limits and reset times, which suits the random quota resets.
- **`claude --cloud`:** Missing, and hard to add. The `ports.Agent` interface (`backend/internal/ports/agent.go`) assumes a local process in a local worktree. A cloud session has no local worktree, so every later stage would need a new path (diff fetch, local check, commit). AO's own "AO Cloud" is a separate hosted service with WorkOS, Postgres and Coder VMs. It is not Anthropic's cloud.
- **Anthropic API ($200 credits):** Small to medium. You could run Claude Code with `ANTHROPIC_API_KEY`. But the account layer strips that variable on purpose (`backend/internal/adapters/agentaccount/claude.go`, `claudeCredentialEnv`). You would add an API-key account kind there and in `service/agentaccounts/service.go`, plus a dollar-spend reading.

## 3. How it verifies and commits
The agent commits. AO watches the PR, CI checks and reviews through the GitHub/GitLab adapters (`adapters/scm`). It nudges the agent, with dedup and a retry cap of about 3. It can also start an AI reviewer agent (`backend/internal/autoreview`). Uncommitted work is saved to `refs/ao/preserved/<id>` before teardown. AO never runs a gate itself, never stages chosen paths, and never commits for the agent.

## 4. Health
- License: Apache-2.0. No CLA was seen in CONTRIBUTING.
- Activity: very high. 13.5k stars, 1.9k forks. More than 100 commits in the last week. Releases ship daily (v0.14.5 on 2026-10-22). 509 open issues and 487 open PRs.
- Size: huge. About 380k lines of non-test Go (287k of it in `backend`), about 397k lines of Go tests and about 460k lines of TS/TSX. Plus an Electron app, a mobile app, a cloud service, and a private submodule (`private/ao-cloud`).
- Quality: careful and well commented. Clear ports/adapters split, and the tests are thorough. But the code is very complex: PTY hosts, TUI/Chat handoff, hibernation, CDC and SSE. It mostly serves a GUI product.

## 5. Risks
- A fork goes stale within days. Upstream changes far too fast to keep rebasing.
- About 95% of the code is UI, mobile, cloud and 30+ agent adapters that we would carry but never use.
- The interactive TUI control model fights a headless batch loop.
- Telemetry: packaged builds send PostHog events, including the GitHub username and repo owner. Daemons started directly do not send it by default.
- The PR-and-remote-CI workflow is assumed throughout. Our loop is local check, then local commit.
- No `claude --cloud` concept. The TTY requirement would need its PTY layer, which exists, but the worktree model does not fit.

## 6. Verdict: borrow ideas only
Do not fork. It cannot run our loop today, and a fork would cost more than writing our own.
- Rough fork size: 12-15 steps of 5 files or fewer. That covers a plan runner service, a local gate runner, scoped commits, resume-once and escalate, an API-key account kind, a cross-provider budget router, a cloud-session backend (about 4 steps alone), CLI wiring, and stripping telemetry. Then add ongoing rebase cost on a 380k-line Go tree.
- Our own harness is likely 6-8 steps.

Worth copying:
- How it reads Claude usage: the `get_usage` control request in `agentaccount/claude.go` `Limits`.
- How it reads Codex limits and reset times: `agentaccount/codex.go`.
- Auto-resume at quota reset: `service/chat/usage_limit.go`.
- Its git worktree lifecycle and preserve-ref trick: `gitworktree/workspace.go` `StashUncommitted`.
- Dedup of nudges with an attempt cap: `lifecycle/reactions.go`.
