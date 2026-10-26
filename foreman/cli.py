"""Command line: parses argv, lints, calls loop, maps each ForemanError to its exit code."""

import argparse
import contextlib
import io
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

from foreman import backends, config, events, loop, preflight, steps
from foreman.errors import EXIT_OK, ConfigError, ForemanError
from foreman.events import EventLog, fail, say
from foreman.steps import Group


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="foreman")
    p.add_argument("--config", type=Path, default=Path("foreman.toml"))
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run a milestone's groups")
    r.add_argument("step_file", type=Path)
    r.add_argument("--group", dest="group_id")
    u = sub.add_parser("upheld", help="continue a kept group from upheld findings")
    u.add_argument("step_file", type=Path)
    u.add_argument("group_id")
    u.add_argument("findings", type=Path)
    t = sub.add_parser(
        "retest", help="continue an owner stop with a juror-format verdict"
    )
    t.add_argument("step_file", type=Path)
    t.add_argument("group_id")
    t.add_argument("verdict", type=Path)
    s = sub.add_parser(
        "resume", help="rerun a kept group from a named step on its worktree"
    )
    s.add_argument("step_file", type=Path)
    s.add_argument("group_id")
    s.add_argument(
        "--from",
        dest="step_id",
        required=True,
        metavar="STEP_ID",
        help="the step to rerun from; earlier steps keep their state",
    )
    sub.add_parser("status", help="print the one-line status")
    return p


def _load(step_file: Path, cfg: config.Config) -> list[Group]:
    groups = steps.load(step_file, cfg.builders, cfg.spec_dir)
    configured = {reviewer.role for reviewer in cfg.reviewers}
    for group in groups:
        unknown = set(group.reviews) - configured
        if unknown:
            raise ConfigError(
                f"group {group.id}: unconfigured reviewers: {', '.join(sorted(unknown))}"
            )
    return groups


def _find(groups: list[Group], group_id: str) -> Group:
    for g in groups:
        if g.id == group_id:
            return g
    raise ConfigError(f"unknown group {group_id!r}")


def _read(path: Path) -> str:
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            return stream.read()
    except (OSError, UnicodeError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e


def _preflight(groups: list[Group], cfg: config.Config, log: EventLog) -> None:
    """Run pre-flight's default checks over `groups`; warn and log each finding."""
    owner = {step.id: g.id for g in groups for step in g.steps}
    for f in preflight.preflight(groups, cfg, None, preflight.CHECKS):
        fail(f"warning: pre-flight finding: {f.line()}")
        log.mismatched(owner.get(f.step), f.step, "preflight", f.line())


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        cfg = config.load(args.config)
        log = EventLog(cfg)
        if args.command == "status":
            say(log.status())
            return EXIT_OK
        groups = _load(args.step_file, cfg)
        if args.command == "run" and not args.group_id:
            done = log.done()
            pending = [g for g in groups if g.id not in done]
        else:
            pending = [_find(groups, args.group_id)]
        if args.command == "upheld":
            text = _read(args.findings)
        elif args.command == "retest":
            text = _read(args.verdict)
        _preflight(pending, cfg, log)
        if args.command == "upheld":
            say(loop.run_upheld(cfg, pending[0], text, log))
        elif args.command == "retest":
            say(loop.run_retest(cfg, pending[0], text, log))
        elif args.command == "resume":
            say(loop.run_resume(cfg, pending[0], args.step_id, log))
        elif args.group_id:
            say(loop.run_group(cfg, pending[0], log, backends.get(cfg)))
        else:
            for commit in loop.run_section(cfg, groups, log):
                say(commit)
        return EXIT_OK
    except ForemanError as e:
        fail(str(e))
        return e.exit_code


# Waits for Foreman's pid to go, then pings once: the status file if Foreman
# wrote one, else a killed-or-crashed notice. Own session, so it outlives a
# process-group kill and even SIGKILL of Foreman.
_WATCH = """
while kill -0 "$1" 2>/dev/null; do sleep 3; done
sleep 1
if [ -s "$2" ]; then m=$(cat "$2"); else m="$4 died without reporting an exit (killed or crashed)"; fi
rm -f "$2"
"$5" "$3" "$m"
"""


def _arm_alert() -> tuple[str, str] | None:
    """Start the exit watcher for run/upheld/retest/resume; return its status file."""
    ping = os.path.expanduser("~/.local/bin/claude-ping")
    if os.environ.get("FOREMAN_NO_ALERT") or not os.access(ping, os.X_OK):
        return None
    quiet = io.StringIO()
    try:
        with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
            args = _parser().parse_args()
    except SystemExit:
        return None
    if args.command == "status":
        return None
    label = f"Foreman {args.command} {args.step_file.stem}"
    fd, status = tempfile.mkstemp(prefix="foreman-alert-")
    os.close(fd)
    tag = f"foreman:{args.step_file.stem}"
    subprocess.Popen(
        ["/bin/sh", "-c", _WATCH, "sh", str(os.getpid()), status, tag, label, ping],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda n, _f: sys.exit(128 + n))
    return label, status


def entry() -> NoReturn:
    armed = _arm_alert()
    code: object = 1
    try:
        code = main()
    except SystemExit as e:
        code = e.code
        raise
    except BaseException as e:
        code = f"crashed: {type(e).__name__}: {e}"[:200]
        raise
    finally:
        if armed:
            label, status = armed
            word = "finished" if code in (None, EXIT_OK) else f"STOPPED (exit {code})"
            tmp = status + ".tmp"
            with open(tmp, "w", encoding="utf-8") as out:
                out.write(f"{label} {word}: {events.LAST[0][-300:]}")
            os.replace(tmp, status)
    sys.exit(code)
