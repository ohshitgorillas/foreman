# Roadmap

1. Design the simplest harness that meets docs/charter.md: the shared core first, then the smallest first version that can run one real Gauntlet build step end to end.
2. Run a real Gauntlet milestone section end to end. The first real runs (docs/research/first-run-2026-10-23.md) showed Foreman's step model does not fit Gauntlet's: Gauntlet's roles accept only fixed brief shapes, and its stub, red and build steps land together as one group, with the full check only at the end.
   - Open bug: a test-writer's BLOCKED report does not stop the group. The group carries on to the next dispatch as if the red tests had been written; it should stop and ask for a ruling, the way an overrun or a refusal does.
   - Known gap (red-overload, docs/superpowers/specs/red-overload-design.md): the size check measures each part of a cut red brief without the retry notes added when a step is re-run, so a re-run part can go over the page limit. The fix belongs in the design.

## Parked

- A `foreman stats` sizing command is wanted so the Divvyer can size dispatches from measured data. Until it exists, default sizes stand.
