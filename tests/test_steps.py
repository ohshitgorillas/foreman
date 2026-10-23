from pathlib import Path

import pytest

from foreman.errors import ConfigError
from foreman.steps import Step, lint, load, next_ready


def mk(id: str, **kw: object) -> Step:
    base: dict[str, object] = {
        "id": id,
        "paths": ["a.py"],
        "done_check": ["make check"],
        "design_ref": "design:1",
        "depends_on": [],
        "size": "s",
    }
    base.update(kw)
    return Step(**base)  # type: ignore[arg-type]


GOOD = """
[steps.one]
paths = ["a.py", "b.py"]
done_check = ["make check"]
design_ref = "design:1"
depends_on = []
size = "s"
agent = "builder"

[steps.two]
paths = ["c.py"]
done_check = ["make check"]
design_ref = "design:2"
depends_on = ["one"]
size = "m"
agent = "builder"
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "m.toml"
    p.write_text(text)
    return p


def test_load_reads_steps_in_order(tmp_path: Path) -> None:
    steps = load(write(tmp_path, GOOD))
    assert [s.id for s in steps] == ["one", "two"]
    assert steps[0] == mk("one", paths=["a.py", "b.py"])
    assert steps[1].depends_on == ["one"] and steps[1].size == "m"


@pytest.mark.parametrize(
    "text",
    [
        "not toml [",
        "x = 1",
        GOOD + "\n[other]\na = 1\n",
        GOOD.replace('size = "s"\n', ""),
        GOOD.replace('size = "s"', 'size = "s"\nextra = 1'),
        GOOD.replace('paths = ["c.py"]', 'paths = "c.py"'),
        GOOD.replace('size = "m"', "size = 3"),
        GOOD.replace('agent = "builder"\n', "", 1),
    ],
)
def test_load_rejects_bad_files(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError):
        load(write(tmp_path, text))


def test_load_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load(tmp_path / "nope.toml")


def test_lint_clean() -> None:
    assert lint([mk("a"), mk("b", depends_on=["a"])]) == []


def test_lint_five_paths_ok_six_not() -> None:
    assert lint([mk("a", paths=list("12345"))]) == []
    (problem,) = lint([mk("a", paths=list("123456"))])
    assert "a" in problem and "6 paths" in problem


def test_lint_no_paths() -> None:
    assert lint([mk("a", paths=[])]) == ["a: no paths"]


@pytest.mark.parametrize("checks", [[], ["x", "y"]])
def test_lint_done_check_count(checks: list[str]) -> None:
    (problem,) = lint([mk("a", done_check=checks)])
    assert f"{len(checks)} done-checks" in problem


def test_lint_graph_problems() -> None:
    assert lint([mk("a"), mk("a")]) == ["a: duplicate step id"]
    assert lint([mk("a", depends_on=["zz"])]) == ["a: depends on unknown step zz"]
    assert lint([mk("a", depends_on=["a"])]) == ["a: depends on itself"]
    cyc = lint([mk("a", depends_on=["b"]), mk("b", depends_on=["a"])])
    assert len(cyc) == 1 and "cycle" in cyc[0]


def test_lint_reports_every_problem() -> None:
    problems = lint([mk("a", paths=list("123456"), done_check=[])])
    assert len(problems) == 2


def test_next_ready() -> None:
    steps = [mk("a"), mk("b", depends_on=["a"]), mk("c")]
    assert next_ready(steps, set()) == steps[0]
    assert next_ready(steps, {"a"}) == steps[1]
    assert next_ready(steps, {"a", "b"}) == steps[2]
    assert next_ready(steps, {"a", "b", "c"}) is None


def test_next_ready_skips_blocked() -> None:
    steps = [mk("b", depends_on=["a"]), mk("a")]
    assert next_ready(steps, set()) == steps[1]
    assert next_ready([mk("b", depends_on=["zz"])], set()) is None
