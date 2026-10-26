"""Process running: the only caller of subprocess."""

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from foreman.errors import ForemanError

TIMEOUT_CODE = 124


@dataclass(frozen=True)
class Result:
    cmd: list[str]
    code: int
    out: str
    err: str
    seconds: float


def _text(data: str | bytes | None) -> str:
    if data is None:
        return ""
    return data if isinstance(data, str) else data.decode(errors="replace")


def run(
    cmd: list[str],
    cwd: Path,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
    shell: bool = False,
) -> Result:
    """Run cmd in cwd in its own session. A timeout kills the whole process
    group and yields code TIMEOUT_CODE. With shell, cmd is passed to the shell
    as Popen takes it (one command string in a one-item list)."""
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            shell=shell,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as e:
        raise ForemanError(f"cannot run {cmd}: {e}") from e
    try:
        out, err = proc.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = proc.communicate()
        err = _text(err) + f"timed out after {timeout}s\n"
        return Result(cmd, TIMEOUT_CODE, _text(out), err, time.monotonic() - start)
    return Result(cmd, proc.returncode, out, err, time.monotonic() - start)


def spawn_detached(cmd: list[str]) -> int:
    """Start cmd in its own session with stdio to /dev/null; return its pid."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        raise ForemanError(f"cannot run {cmd}: {e}") from e
    return proc.pid


def run_ok(cmd: list[str], cwd: Path, timeout: float | None = None) -> Result:
    """Like run, but raise ForemanError on a non-zero exit."""
    result = run(cmd, cwd, timeout)
    if result.code != 0:
        raise ForemanError(f"{cmd} exited {result.code}: {result.err.strip()}")
    return result
