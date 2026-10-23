"""Command line: parses argv, lints, calls loop, maps each ForemanError to its exit code."""

import argparse
import sys
from pathlib import Path
from typing import NoReturn

from foreman import backends, config, loop, steps
from foreman.errors import EXIT_OK, ConfigError, ForemanError, OverloadedStep
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
    sub.add_parser("status", help="print the one-line status")
    return p


def _load(step_file: Path) -> list[Group]:
    groups = steps.load(step_file)
    problems = steps.lint(groups)
    if problems:
        raise OverloadedStep("; ".join(problems))
    return groups


def _find(groups: list[Group], group_id: str) -> Group:
    for g in groups:
        if g.id == group_id:
            return g
    raise ConfigError(f"unknown group {group_id!r}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        cfg = config.load(args.config)
        log = EventLog(cfg)
        if args.command == "status":
            say(log.status())
            return EXIT_OK
        groups = _load(args.step_file)
        if args.command == "upheld":
            try:
                text = args.findings.read_text(encoding="utf-8")
            except OSError as e:
                raise ConfigError(f"cannot read {args.findings}: {e}") from e
            say(loop.run_upheld(cfg, _find(groups, args.group_id), text, log))
        elif args.group_id:
            say(
                loop.run_group(
                    cfg, _find(groups, args.group_id), log, backends.get(cfg)
                )
            )
        else:
            for commit in loop.run_section(cfg, groups, log):
                say(commit)
        return EXIT_OK
    except ForemanError as e:
        fail(str(e))
        return e.exit_code


def entry() -> NoReturn:
    sys.exit(main())
