"""Process running: the only caller of subprocess."""

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
) -> Result:
    """Run cmd in cwd. A timeout kills it and yields code TIMEOUT_CODE."""
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        err = _text(e.stderr) + f"timed out after {timeout}s\n"
        return Result(cmd, TIMEOUT_CODE, _text(e.stdout), err, time.monotonic() - start)
    except OSError as e:
        raise ForemanError(f"cannot run {cmd}: {e}") from e
    return Result(
        cmd, proc.returncode, proc.stdout, proc.stderr, time.monotonic() - start
    )


def run_ok(cmd: list[str], cwd: Path, timeout: float | None = None) -> Result:
    """Like run, but raise ForemanError on a non-zero exit."""
    result = run(cmd, cwd, timeout)
    if result.code != 0:
        raise ForemanError(f"{cmd} exited {result.code}: {result.err.strip()}")
    return result
