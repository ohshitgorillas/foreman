"""Finding weights, role routing and indivisible batch packing."""

from pathlib import Path

import pytest
from conftest import make_group, make_step

from foreman import batches, forms, overload, steps
from foreman.batches import Batch, Unit


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (1, Unit("a.py", 1, 14)),
        (3, Unit("a.py", 3, 10)),
        (4, Unit("a.py", 4, 7)),
        (6, Unit("a.py", 6, 2)),
        (9, Unit("a.py", 8, 3)),
        (12, Unit("a.py", 12, 1)),
        (14, Unit("a.py", 1, 14)),
    ],
)
def test_unit_uses_innermost_function_async_function_or_class(
    tmp_path: Path, line: int, expected: Unit
) -> None:
    (tmp_path / "a.py").write_text(
        "CONSTANT = 1\n"
        "\n"
        "class Outer:\n"
        "    def method(self):\n"
        "        # an enclosing body line\n"
        "        async def inner():\n"
        "            return 1\n"
        "        class Nested:\n"
        "            value = 2\n"
        "            other = 3\n"
        "\n"
        "    def other(self): return 4\n"
        "\n"
        "TAIL = 5\n"
    )
    assert batches.unit(tmp_path, "a.py", line) == expected


@pytest.mark.parametrize(
    ("path", "text", "line", "weight"),
    [
        ("broken.py", b"def broken(:\n    pass\n# tail", 2, 3),
        ("a.txt", b"def looks_python():\n    pass\n# tail\n", 2, 3),
        ("empty.py", b"", 1, 0),
        ("bad_encoding.py", b"# coding: ascii\nvalue = '\xff'\n", 2, 2),
    ],
)
def test_unit_whole_file_fallback(
    tmp_path: Path, path: str, text: bytes, line: int, weight: int
) -> None:
    (tmp_path / path).write_bytes(text)
    assert batches.unit(tmp_path, path, line) == Unit(path, 1, weight)


def test_unit_accepts_python_source_encoding(tmp_path: Path) -> None:
    (tmp_path / "latin.py").write_bytes(
        b"# coding: latin-1\n\ndef value():\n    return '\xe9'\n"
    )
    assert batches.unit(tmp_path, "latin.py", 4) == Unit("latin.py", 3, 2)


def test_shared_units_stay_together_and_findings_keep_source_order(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.py").write_text(
        "def first():\n    return 1\n\ndef second():\n    return 2\n"
    )
    group = make_group(make_step(paths=["a.py"]))
    source = forms.ReviewerSource("diff-reviewer", "g-review-diff-reviewer")
    lines = [
        "drift | a.py:1 | first | fails when: x",
        "drift | a.py:4 | second | fails when: y",
        "drift | a.py:2 | first again | fails when: z",
    ]
    packed, unowned = batches.cut(lines, source, group, tmp_path, 4)
    assert packed == [Batch("core-builder", source, tuple(lines), 4)]
    assert unowned == []
    packed, unowned = batches.cut(lines, source, group, tmp_path, 3)
    assert packed == [
        Batch("core-builder", source, (lines[0], lines[2]), 2),
        Batch("core-builder", source, (lines[1],), 2),
    ]
    assert unowned == []


def test_packs_by_receiving_role_across_steps_and_uses_last_owner(
    tmp_path: Path,
) -> None:
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text("def f():\n    pass\n")
    group = make_group(
        make_step("early", ["a.py"], role="test-writer"),
        make_step("last", ["a.py"]),
        make_step("seam", ["b.py"], role="seam-builder"),
        make_step("core", ["c.py"]),
    )
    source = forms.GateSource("gate-round")
    lines = ["a.py:1: a", "b.py:1: b", "c.py:1: c"]
    packed, unowned = batches.cut(lines, source, group, tmp_path, 4)
    assert packed == [
        Batch("core-builder", source, (lines[0], lines[2]), 4),
        Batch("seam-builder", source, (lines[1],), 2),
    ]
    assert unowned == []


def test_five_distinct_files_limit_applies_even_with_large_budget(
    tmp_path: Path,
) -> None:
    paths = [f"a{i}.py" for i in range(6)]
    for path in paths:
        (tmp_path / path).write_text("def f():\n    pass\n\ndef g():\n    pass\n")
    group = make_group(*(make_step(str(i), [path]) for i, path in enumerate(paths)))
    source = forms.GateSource("check")
    lines = [f"{path}:1: f" for path in paths[:5]]
    lines += ["a0.py:4: g", "a5.py:1: sixth file"]
    packed, unowned = batches.cut(lines, source, group, tmp_path, 1000)
    assert packed == [
        Batch("core-builder", source, tuple(lines[:6]), 12),
        Batch("core-builder", source, (lines[6],), 2),
    ]
    assert unowned == []


def test_over_budget_unit_goes_alone_between_smaller_batches(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "def small():\n    pass\n\ndef big():\n    a = 1\n    b = 2\n    return a + b\n\ndef end():\n    pass\n"
    )
    group = make_group(make_step(paths=["a.py"]))
    source = forms.GateSource("check")
    lines = ["a.py:2: small", "a.py:5: big", "a.py:10: end", "a.py:7: big again"]
    packed, unowned = batches.cut(lines, source, group, tmp_path, 3)
    assert packed == [
        Batch("core-builder", source, (lines[0],), 2),
        Batch("core-builder", source, (lines[1], lines[3]), 4),
        Batch("core-builder", source, (lines[2],), 2),
    ]
    assert unowned == []


def test_unowned_and_unanchored_lines_are_returned_verbatim(tmp_path: Path) -> None:
    group = make_group(make_step(paths=["a.py"]))
    source = forms.GateSource("check")
    lines = [
        "missing.py:2: unowned",
        "no primary anchor; see a.py:1",
        f"{tmp_path.parent / 'outside.py'}:1: outside",
    ]
    assert batches.cut(lines, source, group, tmp_path, 10) == ([], lines)


def test_primary_absolute_anchor_and_separate_sources(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def f():\n    pass\n")
    group = make_group(make_step(paths=["a.py"]))
    line = f"drift | {tmp_path / 'a.py'}:2 | reuse other.py:99 | fails when: x"
    for source in (
        forms.GateSource("check"),
        forms.ReviewerSource("diff-reviewer", "round-one"),
        forms.ReviewerSource("diff-reviewer", "round-two"),
    ):
        assert batches.cut([line], source, group, tmp_path, 10) == (
            [Batch("core-builder", source, (line,), 2)],
            [],
        )


def test_test_reviewer_category_and_anchor_share_the_first_field(
    tmp_path: Path,
) -> None:
    (tmp_path / "t.py").write_text("def test_f():\n    pass\n")
    group = make_group(make_step(role="test-writer", paths=["t.py"]))
    source = forms.ReviewerSource("test-reviewer", "round-one")
    line = "loosened t.py:2 | weak | passes on wrong code: x | docs: none | fix: y"
    assert batches.cut([line], source, group, tmp_path, 10) == (
        [Batch("test-writer", source, (line,), 2)],
        [],
    )


def test_empty_findings_produce_no_batches(tmp_path: Path) -> None:
    assert batches.cut([], forms.GateSource("check"), make_group(), tmp_path, 1) == (
        [],
        [],
    )


def test_batch_file_limit_is_the_overload_home(tmp_path: Path) -> None:
    assert not hasattr(steps, "MAX_PATHS")
    n = overload.MAX_FILES + 1
    paths = [f"a{i}.py" for i in range(n)]
    for path in paths:
        (tmp_path / path).write_text("def f():\n    pass\n")
    group = make_group(*(make_step(str(i), [path]) for i, path in enumerate(paths)))
    lines = [f"{path}:1: f" for path in paths]
    packed, _ = batches.cut(lines, forms.GateSource("check"), group, tmp_path, 1000)
    assert [len(b.lines) for b in packed] == [overload.MAX_FILES, 1]
