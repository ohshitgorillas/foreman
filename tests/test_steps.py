from pathlib import Path

import pytest

from foreman.errors import ConfigError
from foreman.steps import Group, Step, lint, load, next_ready, owner


def mk(id: str, **kw: object) -> Step:
    base: dict[str, object] = {
        "id": id,
        "role": "builder",
        "brief": "do it",
        "paths": ["a.py"],
        "check": ["make", "check"],
        "expect_code": 0,
    }
    base.update(kw)
    return Step(**base)  # type: ignore[arg-type]


def grp(id: str, steps: list[Step] | None = None, **kw: object) -> Group:
    base: dict[str, object] = {
        "id": id,
        "steps": steps if steps is not None else [mk(id + "-s")],
        "design": "d.md",
        "after": [],
    }
    base.update(kw)
    return Group(**base)  # type: ignore[arg-type]


GOOD = """
[groups.g1]
steps = ["one", "two"]
design = "docs/d.md"

[groups.g2]
steps = ["three"]
after = ["g1"]

[steps.one]
role = "builder"
brief = "Build one."
paths = ["a.py", "b.py"]
check = ["make", "check"]

[steps.two]
role = "builder"
brief = "Build two."
paths = ["c.py"]
expect_code = 1

[steps.three]
role = "builder"
brief = "Build three."
paths = ["d.py"]
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "steps.toml"
    p.write_text(text)
    return p


def test_load_groups_and_steps(tmp_path: Path) -> None:
    groups = load(write(tmp_path, GOOD))
    assert [g.id for g in groups] == ["g1", "g2"]
    g1, g2 = groups
    assert [s.id for s in g1.steps] == ["one", "two"]
    assert g1.design == "docs/d.md" and g1.after == []
    assert g2.after == ["g1"] and g2.design == ""
    one, two = g1.steps
    assert one.check == ["make", "check"] and one.expect_code == 0
    assert two.check == [] and two.expect_code == 1
    assert one.brief == "Build one."


@pytest.mark.parametrize(
    "old,new,msg",
    [
        ('brief = "Build one."\n', "", "missing keys: brief"),
        ('role = "builder"\nbrief = "Build three."', 'role = 3\nbrief = "x"', "role"),
        ("expect_code = 1", 'expect_code = "1"', "expect_code"),
        ('steps = ["three"]', 'steps = ["three", "ghost"]', "unknown steps: ghost"),
        ('steps = ["three"]', "steps = []\nbogus = 1", "unknown keys: bogus"),
    ],
)
def test_load_rejects(tmp_path: Path, old: str, new: str, msg: str) -> None:
    with pytest.raises(ConfigError, match=msg):
        load(write(tmp_path, GOOD.replace(old, new)))


def test_load_rejects_step_in_no_group(tmp_path: Path) -> None:
    text = GOOD + '\n[steps.lost]\nrole = "b"\nbrief = "x"\npaths = ["z"]\n'
    with pytest.raises(ConfigError, match="steps in no group: lost"):
        load(write(tmp_path, text))


def test_load_rejects_missing_file_bad_toml_and_no_tables(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load(tmp_path / "none.toml")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load(write(tmp_path, "[groups"))
    with pytest.raises(ConfigError, match="no \\[groups"):
        load(write(tmp_path, ""))
    with pytest.raises(ConfigError, match="unknown keys: x"):
        load(write(tmp_path, GOOD + "\nx = 1\n"))


def test_lint_clean() -> None:
    assert lint([grp("a"), grp("b", after=["a"])]) == []


def test_lint_paths_and_brief() -> None:
    bad = [
        mk("none", paths=[]),
        mk("many", paths=list("abcdef")),
        mk("blank", brief="  "),
    ]
    out = lint([grp("g", bad)])
    assert "none: no paths" in out
    assert "many: 6 paths, more than 5" in out
    assert "blank: no brief" in out


def test_lint_five_paths_is_fine() -> None:
    assert lint([grp("g", [mk("s", paths=list("abcde"))])]) == []


def test_lint_duplicates() -> None:
    s = mk("s")
    out = lint([grp("g", [s]), grp("g", [mk("t")]), grp("h", [s])])
    assert "g: duplicate group id" in out
    assert "s: step in more than one group: g, h" in out
    assert "s: duplicate step id" not in out
    assert "t: duplicate step id" not in out
    assert any("s: duplicate step id" in p for p in lint([grp("g", [s, s])]))


def test_lint_after_unknown_self_and_cycle() -> None:
    out = lint([grp("a", after=["ghost", "a"])])
    assert "a: after unknown group ghost" in out
    assert "a: after itself" in out
    cyc = lint([grp("a", after=["b"]), grp("b", after=["a"])])
    assert any("after cycle" in p for p in cyc)


def test_next_ready() -> None:
    a, b = grp("a"), grp("b", after=["a"])
    assert next_ready([b, a], set()) is a
    assert next_ready([a, b], {"a"}) is b
    assert next_ready([a, b], {"a", "b"}) is None
    assert next_ready([b], set()) is None


def test_owner_is_last_step_holding_path() -> None:
    first = mk("first", paths=["foreman/", "x.py"])
    second = mk("second", paths=["foreman/steps.py"])
    g = grp("g", [first, second])
    assert owner(g, "foreman/steps.py") is second
    assert owner(g, "foreman/other.py") is first
    assert owner(g, "x.py") is first
    assert owner(g, "x.pyc") is None
    assert owner(g, "foremanx/a.py") is None
    assert owner(g, "nowhere.py") is None


def test_load_views_default_empty_and_read(tmp_path: Path) -> None:
    groups = load(
        write(
            tmp_path,
            GOOD.replace('paths = ["d.py"]', 'paths = ["d.py"]\nviews = ["a.py"]'),
        )
    )
    three = groups[1].steps[0]
    assert three.views == ["a.py"]
    assert groups[0].steps[0].views == []


def test_load_rejects_bad_views(tmp_path: Path) -> None:
    text = GOOD.replace('paths = ["d.py"]', 'paths = ["d.py"]\nviews = "a.py"')
    with pytest.raises(ConfigError, match="views must be a list of strings"):
        load(write(tmp_path, text))


def test_lint_views() -> None:
    first = mk("a", paths=["pkg"])
    ok = grp("g", [first, mk("b", views=["pkg/m.py"])])
    assert lint([ok]) == []
    assert lint([grp("g", [mk("b", views=["pkg/m.py"]), first])]) == []
    assert lint([grp("g", [mk("b", views=["x.py"])])]) == []
    bad = grp("g", [first, mk("b", views=["pkg/README.md"])])
    assert any("not a .py" in p for p in lint([bad]))


def test_lint_red_names_an_earlier_checked_step() -> None:
    first = mk("a")
    assert lint([grp("g", [first, mk("b", red="a")])]) == []
    assert lint([grp("g", [mk("b", red="a"), first])]) != []
    assert lint([grp("g", [mk("a", check=[]), mk("b", red="a")])]) != []


def test_load_red_default_empty_and_read(tmp_path: Path) -> None:
    text = GOOD.replace('paths = ["d.py"]', 'paths = ["d.py"]\nred = "x"')
    groups = load(write(tmp_path, text))
    assert groups[1].steps[0].red == "x"
    assert groups[0].steps[0].red == ""
