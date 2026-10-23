import sys
from pathlib import Path

import pytest

from foreman.errors import ForemanError
from foreman.run import TIMEOUT_CODE, run, run_ok


def test_run_captures_output_and_code(tmp_path: Path) -> None:
    code = "import sys; print('o'); sys.stderr.write('e'); sys.exit(3)"
    r = run([sys.executable, "-c", code], tmp_path)
    assert (r.code, r.out.strip(), r.err.strip()) == (3, "o", "e")
    assert r.seconds >= 0


def test_run_uses_cwd_and_env(tmp_path: Path) -> None:
    code = "import os; print(os.getcwd(), os.environ['X'])"
    r = run([sys.executable, "-c", code], tmp_path, env={"X": "1"})
    assert r.out.split() == [str(tmp_path.resolve()), "1"]


def test_run_timeout(tmp_path: Path) -> None:
    r = run([sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, timeout=0.2)
    assert r.code == TIMEOUT_CODE
    assert "timed out" in r.err


def test_run_missing_binary(tmp_path: Path) -> None:
    with pytest.raises(ForemanError):
        run(["no-such-binary-xyz"], tmp_path)


def test_run_ok_raises_on_failure(tmp_path: Path) -> None:
    with pytest.raises(ForemanError, match="exited 1"):
        run_ok([sys.executable, "-c", "raise SystemExit(1)"], tmp_path)
    assert run_ok([sys.executable, "-c", "pass"], tmp_path).code == 0
