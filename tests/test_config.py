from pathlib import Path

import pytest

from foreman.config import Config, Reviewer, load
from foreman.errors import ConfigError

GOOD = """\
target_repo = "/work/target"
worktree_root = "/work/wt"
check_cmd = ["make", "check"]
format_cmd = ["ruff", "format"]
backend = "claude-local"
state_dir = "/work/state"
view_cmd = ["uv", "run", "stubgen"]
view_root = "/work/views"
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "foreman.toml"
    p.write_text(text)
    return p


def test_load_good(tmp_path: Path) -> None:
    c = load(_write(tmp_path, GOOD))
    assert c == Config(
        target_repo=Path("/work/target"),
        worktree_root=Path("/work/wt"),
        check_cmd=["make", "check"],
        format_cmd=["ruff", "format"],
        backend="claude-local",
        state_dir=Path("/work/state"),
        reviewers=[],
        view_cmd=["uv", "run", "stubgen"],
        view_root=Path("/work/views"),
    )


def test_load_expands_home(tmp_path: Path) -> None:
    c = load(_write(tmp_path, GOOD.replace("/work/state", "~/st")))
    assert c.state_dir == Path.home() / "st"


def test_config_is_frozen(tmp_path: Path) -> None:
    c = load(_write(tmp_path, GOOD))
    with pytest.raises(AttributeError):
        c.backend = "x"  # type: ignore[misc]


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load(tmp_path / "absent.toml")


def test_invalid_toml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invalid TOML"):
        load(_write(tmp_path, "target_repo = ["))


def test_missing_key(tmp_path: Path) -> None:
    text = GOOD.replace('backend = "claude-local"\n', "")
    with pytest.raises(ConfigError, match="missing keys: backend"):
        load(_write(tmp_path, text))


def test_unknown_key(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown keys: extra"):
        load(_write(tmp_path, GOOD + "extra = 1\n"))


@pytest.mark.parametrize(
    "old,new",
    [
        ('target_repo = "/work/target"', "target_repo = 3"),
        ('check_cmd = ["make", "check"]', 'check_cmd = "make check"'),
        ('format_cmd = ["ruff", "format"]', "format_cmd = [1]"),
        ('backend = "claude-local"', "backend = 1"),
    ],
)
def test_wrong_types(tmp_path: Path, old: str, new: str) -> None:
    with pytest.raises(ConfigError):
        load(_write(tmp_path, GOOD.replace(old, new)))


ROSTER = """
[[reviewers]]
role = "diff-reviewer"
paths = ["foreman/", "tests/"]
spec = false

[[reviewers]]
role = "spec-reviewer"
paths = ["foreman/"]
spec = true
"""


def test_reviewers_load_in_order(tmp_path: Path) -> None:
    c = load(_write(tmp_path, GOOD + ROSTER))
    assert c.reviewers == [
        Reviewer("diff-reviewer", ["foreman/", "tests/"], False),
        Reviewer("spec-reviewer", ["foreman/"], True),
    ]


def test_example_file_loads() -> None:
    c = load(Path(__file__).parent.parent / "foreman.example.toml")
    assert [r.role for r in c.reviewers] == ["diff-reviewer", "spec-reviewer"]


@pytest.mark.parametrize(
    "table,match",
    [
        ('role = "r"\npaths = ["a"]', "missing keys: spec"),
        ('role = "r"\npaths = ["a"]\nspec = 1', "spec must be a boolean"),
        ('role = "r"\npaths = "a"\nspec = true', "paths must be a list"),
        ('role = 3\npaths = ["a"]\nspec = true', "role must be"),
        ('role = "r"\npaths = ["a"]\nspec = true\nx = 1', "unknown keys: x"),
    ],
)
def test_bad_reviewer(tmp_path: Path, table: str, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        load(_write(tmp_path, GOOD + "\n[[reviewers]]\n" + table + "\n"))


def test_reviewers_must_be_tables(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="reviewers must be"):
        load(_write(tmp_path, 'reviewers = ["x"]\n' + GOOD))


def test_standing_lines_now_unknown(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown keys: standing_lines"):
        load(_write(tmp_path, GOOD + "standing_lines = []\n"))


@pytest.mark.parametrize("key", ["view_cmd", "view_root"])
def test_view_keys_are_required(tmp_path: Path, key: str) -> None:
    text = "".join(ln + "\n" for ln in GOOD.splitlines() if not ln.startswith(key))
    with pytest.raises(ConfigError, match=f"missing keys: {key}"):
        load(_write(tmp_path, text))
