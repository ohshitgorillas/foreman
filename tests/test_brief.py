"""Tests for foreman.brief."""

from conftest import make_group

from foreman.brief import finding, review
from foreman.config import Reviewer

GROUP = make_group(gid="g1", design="docs/d.md")


def test_finding_is_the_lines_verbatim() -> None:
    lines = ["foreman/a.py:3: bad $x", "  tests/b.py:9: worse  "]
    assert finding(lines) == "foreman/a.py:3: bad $x\n  tests/b.py:9: worse  "


def test_review_names_tree_and_spec_when_asked() -> None:
    reviewer = Reviewer(role="spec-reviewer", paths=["foreman"], spec=True)
    assert review(reviewer, GROUP) == "working tree against HEAD\nspec: docs/d.md"


def test_review_omits_spec_when_not_asked() -> None:
    reviewer = Reviewer(role="diff-reviewer", paths=["foreman"], spec=False)
    assert review(reviewer, GROUP) == "working tree against HEAD"
