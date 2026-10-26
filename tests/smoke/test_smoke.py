"""End-to-end smoke: one tiny group through every stage on the real claude-local backend.

Opt-in only (`make smoke`): it dispatches real `claude -p` agents and costs tokens.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

from foreman.run import run, run_ok

pytestmark = pytest.mark.smoke

FOREMAN = Path(__file__).resolve().parents[2]
ROLES = Path.home() / "dev" / "gauntlet" / ".claude" / "agents"
ROLE_FILES = ("test-writer", "core-builder", "diff-reviewer", "test-reviewer", "juror")
DESIGN = "docs/superpowers/specs/toy-design.md"

TOY = {
    ".gitignore": ".venv/\n__pycache__/\n.ruff_cache/\n.pytest_cache/\n.mypy_cache/\n",
    "pyproject.toml": """[project]
name = "toy"
version = "0.0.0"
requires-python = ">=3.12"
dependencies = []

[dependency-groups]
dev = ["mypy", "pytest", "ruff"]

[tool.pytest.ini_options]
pythonpath = ["."]
""",
    "Makefile": """.PHONY: check check-tests

check:
\tuv run ruff format --check .
\tuv run ruff check .
\tuv run pytest -q

check-tests:
\tuv run ruff check $(FILES)
""",
    "gates/__init__.py": "",
    "gates/heavy.py": '''"""Run the command after `--`; the toy stand-in for Gauntlet's heavy-gate wrapper."""

import subprocess
import sys

args = sys.argv[1:]
sys.exit(subprocess.run(args[args.index("--") + 1 :], check=False).returncode)
''',
    "gauntlet/__init__.py": "",
    "gauntlet/core/__init__.py": "",
    "tests/__init__.py": "",
    "docs/charter.md": "# Toy charter\n\nA toy repository for Foreman's end-to-end smoke test.\n",
    "docs/design.md": f"# Toy design\n\n## Add\n\n`gauntlet.core.toy.add(a: int, b: int) -> int` returns the sum of its two integer arguments: `add(2, 3)` is `5`, `add(-1, 1)` is `0`.\n\nFull design: `{DESIGN}`.\n",
    "docs/config.md": "# Toy config\n\nThere is no configuration.\n",
    "docs/roadmap.md": f"# Toy roadmap\n\n1. Add, by `{DESIGN}`.\n",
    DESIGN: """# Toy add design

## Add

`gauntlet/core/toy.py` defines `add(a: int, b: int) -> int`, which returns the sum of its two integer arguments: `add(2, 3)` is `5`, `add(-1, 1)` is `0`, `add(0, 0)` is `0`.

## Order

| Step | Agent | Paths | Contents |
|---|---|---|---|
| 1 | core-builder (stub) | gauntlet/core/toy.py | `def add(a: int, b: int) -> int: ...` |
| 2 | test-writer (red) | tests/test_toy.py | Red tests for §Add. |
| 3 | core-builder | gauntlet/core/toy.py | Implement §Add. |
""",
}

STEPS = f"""[groups.g1]
steps = ["1", "2", "3"]
design = "{DESIGN}"

[steps.1]
role = "core-builder"
shape = "stub"
paths = ["gauntlet/core/toy.py"]
[steps.1.form]
step = "1 | core-builder (stub) | gauntlet/core/toy.py | def add(a: int, b: int) -> int: ..."

[steps.2]
role = "test-writer"
shape = "red"
paths = ["tests/test_toy.py"]
views = ["gauntlet/core/toy.py"]
check = ["uv", "run", "pytest", "-q", "tests/test_toy.py"]
expect_code = 1
[steps.2.form]
origin = {{ design = "{DESIGN}", steps = ["2"] }}
behaviors = [
  {{ text = "returns the sum of its two integer arguments: `add(2, 3)` is `5`, `add(-1, 1)` is `0`, `add(0, 0)` is `0`.", source = "gauntlet/core/toy.py" }},
]

[steps.3]
role = "core-builder"
shape = "build"
paths = ["gauntlet/core/toy.py"]
check = ["uv", "run", "pytest", "-q", "tests/test_toy.py"]
[steps.3.form]
step = "3 | core-builder | gauntlet/core/toy.py | Implement {DESIGN} §Add."
red = [{{ step = "2" }}]
"""


def _git(repo: Path, *args: str) -> str:
    return run_ok(["git", *args], repo).out


def _toy(root: Path) -> Path:
    repo = root / "toy"
    for rel, text in TOY.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    agents = repo / ".claude" / "agents"
    agents.mkdir(parents=True)
    for role in ROLE_FILES:
        shutil.copy(ROLES / f"{role}.md", agents / f"{role}.md")
    run_ok(["uv", "lock", "-q"], repo, timeout=300)
    _git(repo, "init", "-q", "-b", "main")
    for key, value in (
        ("user.name", "Smoke"),
        ("user.email", "smoke@example.com"),
        ("commit.gpgsign", "false"),
    ):
        _git(repo, "config", key, value)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "toy")
    run_ok(["git", "init", "-q", "--bare", str(root / "origin.git")], root)
    _git(repo, "remote", "add", "origin", str(root / "origin.git"))
    _git(repo, "push", "-q", "origin", "main")
    return repo


def _config(root: Path, repo: Path) -> Path:
    def toml(value: object) -> str:
        return json.dumps(str(value) if isinstance(value, Path) else value)

    values = {
        "target_repo": repo,
        "worktree_root": root / "worktrees",
        "state_dir": root / "state",
        "check_cmd": ["make", "check"],
        "format_cmd": ["uv", "run", "ruff", "format"],
        "backend": "claude-local",
        "view_cmd": ["uv", "run", "stubgen"],
        "view_root": root / "views",
        "batch_budget": 300,
        "dispatch_seconds": 600,
        "juror_rounds": 1,
    }
    text = "".join(f"{k} = {toml(v)}\n" for k, v in values.items())
    text += '\n[[reviewers]]\nrole = "diff-reviewer"\npaths = ["gauntlet/"]\n'
    text += '\n[[reviewers]]\nrole = "test-reviewer"\npaths = ["tests/"]\n'
    path = root / "foreman.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _foreman(config: Path, *args: str) -> int:
    result = run(
        [sys.executable, "-m", "foreman", "--config", str(config), *args], FOREMAN
    )
    sys.stdout.write(result.out)
    sys.stderr.write(result.err)
    return result.code


def test_one_group_lands_through_every_stage() -> None:
    root = Path(
        run_ok(["mktemp", "-d", "/tmp/foreman-smoke.XXXXXX"], FOREMAN).out.strip()
    )
    sys.stderr.write(f"smoke root: {root}\n")
    repo = _toy(root)
    config = _config(root, repo)
    step_file = root / "steps.toml"
    step_file.write_text(STEPS, encoding="utf-8")

    code = _foreman(config, "run", str(step_file))
    if code == 7:  # FindingsToRule: overrule every finding to exercise land.
        empty = root / "upheld.txt"
        empty.write_text("", encoding="utf-8")
        code = _foreman(config, "upheld", str(step_file), "g1", str(empty))
    assert code == 0, f"foreman exited {code}; state in {root / 'state'}"

    origin = f"--git-dir={root / 'origin.git'}"
    landed = _git(repo, origin, "ls-tree", "-r", "--name-only", "foreman/g1").split()
    assert "gauntlet/core/toy.py" in landed
    assert "tests/test_toy.py" in landed
    events = (root / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"group_done"' in events
    shutil.rmtree(root)
