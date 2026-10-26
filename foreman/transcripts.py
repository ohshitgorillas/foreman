"""Claude Code's subagent history, read as finished dispatch rows for stats."""

import json
from datetime import datetime
from pathlib import Path

DEFAULT_ROOT = Path("~/.claude/projects")
_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _records(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def _stamp(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _row(path: Path) -> dict | None:
    """One transcript as a dispatch row, or None when it has no role or is open."""
    try:
        meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
        records = _records(path)
    except (OSError, ValueError):
        return None
    role = meta.get("agentType") if isinstance(meta, dict) else None
    if not isinstance(role, str) or not role or meta.get("finished") is False:
        return None
    usage: dict[object, int] = {}
    for i, r in enumerate(records):
        message = r.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
            continue
        counts = [message["usage"].get(key, 0) for key in _USAGE_KEYS]
        if all(type(c) is int and c >= 0 for c in counts):
            # A streamed message repeats its usage on each line; count it once.
            usage[message.get("id") or i] = sum(counts)
    stamps = [s for s in (_stamp(r.get("timestamp")) for r in records) if s is not None]
    if not stamps:
        return None
    return {
        "role": role,
        "tokens": sum(usage.values()) if usage else None,
        "seconds": max(stamps) - min(stamps),
        "outcome": "done",
    }


def dispatches(root: Path = DEFAULT_ROOT) -> list[dict]:
    """Every finished subagent transcript under root as a dispatch row."""
    root = root.expanduser()
    rows = (_row(p) for p in sorted(root.glob("*/*/subagents/agent-*.jsonl")))
    return [r for r in rows if r is not None]
