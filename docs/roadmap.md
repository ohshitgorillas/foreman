# Roadmap

1. Design the simplest harness that meets docs/charter.md: the shared core first, then the smallest first version that can run one real Gauntlet build step end to end.
2. Run a real Gauntlet milestone section end to end. The first real runs (docs/research/first-run-2026-10-23.md) showed Foreman's step model does not fit Gauntlet's: Gauntlet's roles accept only fixed brief shapes, and its stub, red and build steps land together as one group, with the full check only at the end.
   - Open bug: a test-writer's BLOCKED report does not stop the group. The group carries on to the next dispatch as if the red tests had been written; it should stop and ask for a ruling, the way an overrun or a refusal does.
   - Known gap (red-overload, docs/superpowers/specs/red-overload-design.md): the size check measures each part of a cut red brief without the retry notes added when a step is re-run, so a re-run part can go over the page limit. The fix belongs in the design.
   - Drop the agent run timeout (owner ruling, 26 Oct: no wall-clock caps on agents). Remove `dispatch_seconds` from Foreman's code: `foreman/config.py:29`, `:50` (the required "backend run timeout"); `foreman/backends.py:122-128`, `:145`, `:151`, `:191-195`, `:232`, `:257`, `:259` (ClaudeLocal and CodexLocal run each agent under it); `foreman/judge.py:28`, `:38`, `:42-43` (the juror timeout as a finding); `foreman/loop.py:107`, `:121` (TIMEOUT_CODE becomes "{role} timed out"); and the `dispatch_seconds` lines of `foreman.example.toml`, `foreman.toml`, `foreman-gauntlet.toml` and `foreman-g7.toml`, set to 86400 as a hang guard in the meantime. Keep `foreman/run.py:33-65` if command timeouts stay, and keep `check_seconds`, which times commands, not agents.

## Parked

- A `foreman stats` sizing command is wanted so the Divvyer can size dispatches from measured data. Until it exists, default sizes stand.
