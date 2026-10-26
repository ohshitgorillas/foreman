import os
import sys
import time
from pathlib import Path

import pytest

from foreman.errors import ForemanError
from foreman.run import TIMEOUT_CODE, run, run_ok, spawn_detached


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


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_run_child_in_own_session(tmp_path: Path) -> None:
    code = "import os; print(os.getsid(0) == os.getpid(), os.getpgrp() == os.getpid())"
    r = run([sys.executable, "-c", code], tmp_path)
    assert r.out.split() == ["True", "True"]


def test_run_timeout_kills_grandchildren(tmp_path: Path) -> None:
    code = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "print(p.pid, flush=True)\n"
        "time.sleep(30)\n"
    )
    r = run([sys.executable, "-c", code], tmp_path, timeout=1)
    assert r.code == TIMEOUT_CODE
    pid = int(r.out.split()[0])
    for _ in range(50):
        if not _alive(pid):
            break
        time.sleep(0.1)
    assert not _alive(pid)


def test_run_shell_and_stdin(tmp_path: Path) -> None:
    assert run(["echo hi"], tmp_path, shell=True).out.strip() == "hi"
    assert run(["cat"], tmp_path, stdin="abc").out == "abc"


def test_spawn_detached_returns_pid_in_own_session(tmp_path: Path) -> None:
    marker = tmp_path / "sid"
    code = f"import os; open({str(marker)!r}, 'w').write(str(os.getsid(0)))"
    pid = spawn_detached([sys.executable, "-c", code])
    for _ in range(100):
        if marker.exists() and marker.read_text():
            break
        time.sleep(0.1)
    assert int(marker.read_text()) == pid != os.getsid(0)
    os.waitpid(pid, 0)
