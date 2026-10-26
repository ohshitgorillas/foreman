"""Foreman's 7.1f check of a brief and the cut of a red brief."""

from foreman import brief, forms, overload

VIEWS = {"gauntlet/a.py": "/v/a.txt"}
ORIGIN = forms.Origin("design.md", ("1",))


def red(
    n: int, origin: forms.Origin | None = None, text: str = "Behavior"
) -> forms.Red:
    return forms.Red(
        tuple(forms.Behavior(f"{text} {i}.", "gauntlet/a.py") for i in range(n)), origin
    )


def render(form: forms.Red, paths: list[str] | None = None) -> str:
    return brief.render(
        form, paths=paths or ["tests/test_a.py"], view_paths=VIEWS, red_ids=[]
    )


def cut(
    form: forms.Red, paths: list[str] | None = None, page: int = 10_000, cap: int = 100
):
    return overload.cut_red(form, paths or ["tests/test_a.py"], VIEWS, page, cap)


def test_max_files_is_five() -> None:
    assert overload.MAX_FILES == 5


def test_line_names_test_found_and_limit() -> None:
    assert (
        overload.Overload("page", 3100, 3000).line()
        == "OVERLOADED: page 3100, more than 3000"
    )


def test_files_passes_five_and_fails_six() -> None:
    assert overload.files([f"f{i}.py" for i in range(5)]) is None
    assert overload.files([f"f{i}.py" for i in range(6)]) == overload.Overload(
        "files", 6, 5
    )


def test_page_counts_characters() -> None:
    assert overload.page("a" * 10, 10) is None
    assert overload.page("a" * 11, 10) == overload.Overload("page", 11, 10)


def test_page_counts_characters_not_bytes() -> None:
    assert overload.page("é" * 10, 10) is None


def test_behaviors_passes_the_limit_and_fails_above_it() -> None:
    assert overload.behaviors(red(3), 3) is None
    assert overload.behaviors(red(4), 3) == overload.Overload("behaviors", 4, 3)


def test_check_passes_a_small_brief() -> None:
    assert overload.check("x", ["a.py"], 10) == []


def test_check_names_every_failing_test_in_order() -> None:
    found = overload.check("x" * 20, [f"f{i}.py" for i in range(6)], 10)
    assert found == [
        overload.Overload("files", 6, 5),
        overload.Overload("page", 20, 10),
    ]


def test_check_reports_only_the_failing_test() -> None:
    assert overload.check("x" * 20, ["a.py"], 10) == [overload.Overload("page", 20, 10)]


def test_cut_passing_red_is_one_part_the_form_itself() -> None:
    form = red(3, ORIGIN)
    result = cut(form)
    assert result == overload.Cut((form,), ())


def test_cut_by_behaviors_makes_runs_of_whole_behaviors_in_order() -> None:
    form = red(7)
    result = cut(form, cap=3)
    assert [len(p.behaviors) for p in result.parts] == [3, 3, 1]
    assert tuple(b for p in result.parts for b in p.behaviors) == form.behaviors
    assert result.refused == ()


def test_cut_by_page_grows_each_part_while_its_render_fits() -> None:
    form = red(6)
    one = len(render(red(1)))
    page = one * 2 + 20  # two behaviors fit, three do not
    result = cut(form, page=page)
    assert [len(p.behaviors) for p in result.parts] == [2, 2, 2]
    assert all(len(render(p)) <= page for p in result.parts)
    assert tuple(b for p in result.parts for b in p.behaviors) == form.behaviors


def test_cut_by_page_and_behaviors_obeys_both() -> None:
    form = red(9)
    page = len(render(red(4))) + 5
    result = cut(form, page=page, cap=3)
    assert all(len(p.behaviors) <= 3 for p in result.parts)
    assert all(len(render(p)) <= page for p in result.parts)
    assert tuple(b for p in result.parts for b in p.behaviors) == form.behaviors


def test_every_part_keeps_the_origin() -> None:
    result = cut(red(5, ORIGIN), cap=2)
    assert len(result.parts) == 3
    assert all(p.origin == ORIGIN for p in result.parts)


def test_cut_cost_of_origin_counts_toward_each_part_page() -> None:
    form = red(4, ORIGIN)
    page = len(render(red(2, ORIGIN)))
    result = cut(form, page=page)
    assert all(len(render(p)) <= page for p in result.parts)
    assert result.refused == ()


def test_cut_refuses_six_files_and_names_the_overload() -> None:
    paths = [f"tests/test_{i}.py" for i in range(6)]
    result = cut(red(2), paths)
    assert result.parts == ()
    assert result.refused == (overload.Overload("files", 6, 5),)


def test_cut_refuses_when_one_behavior_alone_fails_the_page() -> None:
    form = forms.Red(
        (
            forms.Behavior("Short.", "gauntlet/a.py"),
            forms.Behavior("Long " * 200, "gauntlet/a.py"),
        )
    )
    page = 300
    result = cut(form, page=page)
    assert result.parts == ()
    assert [o.test for o in result.refused] == ["page"]
    assert result.refused[0].limit == page


def test_cut_refusal_names_every_failing_overload() -> None:
    form = forms.Red((forms.Behavior("Long " * 200, "gauntlet/a.py"),))
    paths = [f"tests/test_{i}.py" for i in range(6)]
    result = cut(form, paths, page=300)
    assert result.parts == ()
    assert {o.test for o in result.refused} == {"files", "page"}


def test_cut_passing_at_the_limits_is_not_cut() -> None:
    form = red(3)
    result = cut(form, page=len(render(form)), cap=3)
    assert result.parts == (form,)
