from pathlib import Path

import pytest

from foreman.config import Config, load
from foreman.errors import ConfigError

GOOD = """\
target_repo = "/work/target"
worktree_root = "/work/wt"
check_cmd = ["make", "check"]
format_cmd = ["ruff", "format"]
standing_lines = ["Run ruff format.", "Do not commit."]
backend = "claude-local"
state_dir = "/work/state"
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
        standing_lines=["Run ruff format.", "Do not commit."],
        backend="claude-local",
        state_dir=Path("/work/state"),
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
