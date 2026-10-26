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
batch_budget = 300
dispatch_seconds = 600
"""

LIMITS = [
    "batch_budget",
    "dispatch_seconds",
    "juror_rounds",
    "page_chars",
    "red_behaviors",
]


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
        batch_budget=300,
        dispatch_seconds=600,
        juror_rounds=2,
        page_chars=3000,
        red_behaviors=6,
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

[[reviewers]]
role = "test-reviewer"
paths = ["tests/"]
"""


def test_reviewers_load_in_order(tmp_path: Path) -> None:
    c = load(_write(tmp_path, GOOD + ROSTER))
    assert c.reviewers == [
        Reviewer("diff-reviewer", ["foreman/", "tests/"]),
        Reviewer("test-reviewer", ["tests/"]),
    ]


def test_example_file_loads() -> None:
    c = load(Path(__file__).parent.parent / "foreman.example.toml")
    assert c.batch_budget == 300
    assert c.dispatch_seconds == 600
    assert c.juror_rounds == 2
    assert c.page_chars == 3000
    assert c.red_behaviors == 6
    assert c.reviewers == [
        Reviewer("diff-reviewer", ["foreman/", "tests/"]),
        Reviewer("test-reviewer", ["tests/"]),
    ]


@pytest.mark.parametrize(
    "table,match",
    [
        ('paths = ["a"]', "missing keys: role"),
        ('role = "diff-reviewer"', "missing keys: paths"),
        ('role = "diff-reviewer"\npaths = "a"', "paths must be a list"),
        ('role = "diff-reviewer"\npaths = [1]', "paths must be a list"),
        ('role = 3\npaths = ["a"]', "role must be"),
        ('role = ""\npaths = ["a"]', "role must be"),
        ('role = "diff-reviewer"\npaths = ["a"]\nx = 1', "unknown keys: x"),
        ('role = "diff-reviewer"\npaths = ["a"]\nspecs = []', "unknown keys: specs"),
    ],
)
def test_bad_reviewer(tmp_path: Path, table: str, match: str) -> None:
    path = _write(tmp_path, GOOD + "\n[[reviewers]]\n" + table + "\n")
    with pytest.raises(ConfigError, match=match) as exc:
        load(path)
    assert f"{path}: reviewers.0" in str(exc.value)


@pytest.mark.parametrize("value", ["false", "true", "1", '""', "[]"])
def test_reviewer_spec_is_removed_even_when_false(tmp_path: Path, value: str) -> None:
    path = _write(tmp_path, GOOD + ROSTER + f"spec = {value}\n")
    with pytest.raises(ConfigError) as exc:
        load(path)
    assert str(exc.value) == (
        f"{path}: reviewers.1.spec: removed; put explicit specs in the group's "
        "test-reviewer review form"
    )


@pytest.mark.parametrize(
    "role", ["spec-reviewer", "gatesmith", "unknown", " test-reviewer"]
)
def test_unsupported_reviewer_role(tmp_path: Path, role: str) -> None:
    path = _write(
        tmp_path, GOOD + f'\n[[reviewers]]\nrole = "{role}"\npaths = ["tests/"]\n'
    )
    with pytest.raises(ConfigError) as exc:
        load(path)
    assert str(exc.value) == (
        f"{path}: reviewers.0.role: unsupported reviewer role {role!r}; "
        "expected one of: diff-reviewer, test-reviewer"
    )


def test_reviewer_is_frozen(tmp_path: Path) -> None:
    reviewer = load(_write(tmp_path, GOOD + ROSTER)).reviewers[0]
    with pytest.raises(AttributeError):
        reviewer.role = "test-reviewer"  # type: ignore[misc]


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


@pytest.mark.parametrize("key", ["batch_budget", "dispatch_seconds"])
def test_limits_are_required(tmp_path: Path, key: str) -> None:
    text = "".join(ln + "\n" for ln in GOOD.splitlines() if not ln.startswith(key))
    path = _write(tmp_path, text)
    with pytest.raises(ConfigError) as exc:
        load(path)
    assert str(exc.value) == f"{path}: missing keys: {key}"


@pytest.mark.parametrize("key", LIMITS)
@pytest.mark.parametrize(
    "value", ["true", "false", "1.0", '"2"', "[]", "{}", "0", "-1"]
)
def test_limits_require_positive_integers(tmp_path: Path, key: str, value: str) -> None:
    text = "".join(ln + "\n" for ln in GOOD.splitlines() if not ln.startswith(key))
    path = _write(tmp_path, text + f"{key} = {value}\n")
    with pytest.raises(ConfigError) as exc:
        load(path)
    assert str(exc.value) == f"{path}: {key} must be an integer >= 1"


@pytest.mark.parametrize("key", LIMITS)
@pytest.mark.parametrize("value", [1, 37])
def test_limits_accept_positive_integers(tmp_path: Path, key: str, value: int) -> None:
    text = "".join(ln + "\n" for ln in GOOD.splitlines() if not ln.startswith(key))
    c = load(_write(tmp_path, text + f"{key} = {value}\n"))
    assert getattr(c, key) == value
    assert type(getattr(c, key)) is int


def test_conf_file_round_trip(conf_file: Path, green_cfg: Config) -> None:
    c = load(conf_file)
    assert c == green_cfg
    for key in LIMITS:
        assert type(getattr(c, key)) is int


HQPTUNER_BUILDERS = """\
spec_dir = "docs/specs/"

[builders]
backend = ["hqptuner/backend/", "hqptuner/__init__.py"]
frontend = ["hqptuner/frontend/"]
"""


def test_builders_table_and_spec_dir_load(tmp_path: Path) -> None:
    c = load(_write(tmp_path, GOOD + HQPTUNER_BUILDERS))
    assert c.target_repo == Path("/work/target")


def test_spec_dir_alone_loads(tmp_path: Path) -> None:
    c = load(_write(tmp_path, GOOD + 'spec_dir = "docs/specs/"\n'))
    assert c.backend == "claude-local"


def test_builders_alone_loads(tmp_path: Path) -> None:
    text = GOOD + '[builders]\nbackend = ["hqptuner/backend/"]\n'
    c = load(_write(tmp_path, text))
    assert c.backend == "claude-local"
