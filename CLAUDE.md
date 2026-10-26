# Foreman

Foreman is a small Python 3.12 harness (package `foreman/`, standard
library only; pytest is a development dependency in `tests/`). It routes
agent work across the Claude and Codex pools to build a repository step
by step, shared structure first. `docs/charter.md` states what the owner
wants.

## Commands

- `make check` runs `uv run ruff format --check`, `uv run ruff check` and
  `uv run pytest -q` over `foreman` and `tests`.
- `make format` runs `uv run ruff format` over the same directories.

## Designs

Designs live in `docs/superpowers/specs/<topic>-design.md`. The plan is
`docs/roadmap.md`; background is in `docs/research/`.

## Roster

Agents live in `.claude/agents/`, one per craft, each confined to the paths in *Writes*.

| Agent | Writes | Red test |
|---|---|---|
| `builder` | `foreman/`, `tests/`, `pyproject.toml`, `Makefile`, `foreman.example.toml` | **own** |
| `architect` | `docs/superpowers/specs/<topic>-design.md` | **none** |
| `juror` | nothing (user-scope agent, read-only) | — |

A step no row owns goes to `general-purpose`, which writes its own red test first. A change confined to `docs/` or `CLAUDE.md` takes no red test.
