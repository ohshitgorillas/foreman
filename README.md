# Foreman

Foreman is a small harness that builds a repository step by step with agents, doing what was once done by hand: brief an agent, check its work, send the diff to reviewers, commit. It routes the work across the agent pools on hand (local Claude Code, Codex) so no one pool carries everything. Its first target is Gauntlet's own build. `docs/charter.md` states what the owner wants.

Python 3.12, standard library only. pytest and ruff are development dependencies.

## Setup

```sh
uv sync
cp foreman.example.toml foreman.toml   # then edit
```

`foreman.toml` names the target repository, the worktree and state folders, the check and format commands, the backend (`claude-local` or `codex-local`), the reviewer roster and the target's builder roles. `foreman.example.toml` documents every key.

## Use

```sh
foreman run queues/m2.toml              # run every ready group in order
foreman run queues/m2.toml --group g1   # run one group
foreman resume queues/m2.toml g1 --from 3
foreman upheld queues/m2.toml g1 findings.txt
foreman retest queues/m2.toml g1 verdict.txt
foreman status                          # one-line status
foreman stats                           # per-role dispatch means and counts
```

`--config PATH` picks a config other than `./foreman.toml`.

A step file (under `queues/`) holds groups and steps in TOML. Each group lists its steps, its design under `docs/superpowers/specs/` and the groups it runs `after`. Each step names the target repository's role, its brief shape, the paths it may write (one to five), its check command and the check's expected exit code, and a `form` with the role's brief fields.

A group runs in its own git worktree: Foreman lints the steps, dispatches each step's brief, runs its check, runs the group check and the configured reviewers, and commits the group. A step that goes red is re-run or sent to the `juror`; anything Foreman cannot settle stops the run with the worktree kept, to be continued with `resume`, `upheld` or `retest`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | done |
| 2 | bad or missing config or step file |
| 4 | an agent could not start or vanished |
| 6 | a ruling is needed: refusal, overload, missed check, no change |
| 7 | reviewers filed findings to uphold or decline (`upheld`) |
| 8 | a test or spec is wrong; the owner acts (`retest`) |

Events are logged to `events.jsonl` under `state_dir`.

## Development

```sh
make check    # ruff format --check, ruff check, pytest
make format   # ruff format
make smoke    # opt-in end-to-end run with real `claude -p` agents
```

## Layout

- `foreman/`: the package; `cli.py` is the entry point, `loop.py` runs groups.
- `tests/`: the pytest suite; `tests/smoke/` is the opt-in end-to-end run.
- `queues/`: step files.
- `docs/charter.md`: what the owner wants.
- `docs/roadmap.md`: the plan.
- `docs/superpowers/specs/`: designs.
- `docs/research/`: background.
