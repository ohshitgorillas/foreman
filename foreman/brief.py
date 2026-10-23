"""The briefs Foreman composes: a finding brief and a reviewer brief."""

from foreman.config import Reviewer
from foreman.steps import Group


def finding(lines: list[str]) -> str:
    """The lines verbatim, one per line, nothing else."""
    return "\n".join(lines)


def review(reviewer: Reviewer, group: Group) -> str:
    """Point a reviewer at the working tree, and at the design when it reads the spec."""
    parts = ["working tree against HEAD"]
    if reviewer.spec:
        parts.append(f"spec: {group.design}")
    return "\n".join(parts)
