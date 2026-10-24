"""Command line: parses argv, lints, calls loop, maps each ForemanError to its exit code."""

import argparse
import sys
from pathlib import Path
from typing import NoReturn

from foreman import backends, config, loop, steps
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
    sub.add_parser("status", help="print the one-line status")
    return p


def _load(step_file: Path, cfg: config.Config) -> list[Group]:
    groups = steps.load(step_file)
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        cfg = config.load(args.config)
        log = EventLog(cfg)
        if args.command == "status":
            say(log.status())
            return EXIT_OK
        groups = _load(args.step_file, cfg)
        if args.command == "upheld":
            text = _read(args.findings)
            say(loop.run_upheld(cfg, _find(groups, args.group_id), text, log))
        elif args.command == "retest":
            text = _read(args.verdict)
            say(loop.run_retest(cfg, _find(groups, args.group_id), text, log))
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
