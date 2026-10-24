"""Harness-owned check: format, then the check command, in the worktree."""

import io
import json
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

from foreman import run
from foreman.config import Config
from foreman.errors import ForemanError, RulingNeeded
from foreman.run import Result
from foreman.steps import Step


@dataclass(frozen=True)
class Verdict:
    green: bool
    result: Result


def verify(cfg: Config, wt: Path) -> Verdict:
    """Run format_cmd, then check_cmd, in wt. A failed format is red and final."""
    formatted = run.run(cfg.format_cmd, wt)
    if formatted.code != 0:
        return Verdict(False, formatted)
    checked = run.run(cfg.check_cmd, wt)
    return Verdict(checked.code == 0, checked)


def check_step(step: Step, wt: Path) -> Verdict | None:
    """Run step.check in wt; None when it is empty, green when the code is expect_code."""
    if not step.check:
        return None
    result = run.run(step.check, wt)
    return Verdict(result.code == step.expect_code, result)


@dataclass(frozen=True)
class RepairSnapshot:
    """Caller-owned evidence; capture before repair and persist with producer evidence."""

    red_ids: tuple[str, ...]
    membership: tuple[str, ...]
    assertions: tuple[tuple[str, tuple], ...]


def _assertions(path: Path) -> tuple:
    """Fingerprint lexical assertions, including their enclosing def/class names.

    Preserve every assert statement and calls named assert*, raises, warns or fail.
    This deliberately covers the entire source file, including helper assertions.
    It does not establish semantic equivalence of fixtures, bindings or callees.
    """
    try:
        source = path.read_bytes()
        compile(source, str(path), "exec")
        tokens = list(tokenize.tokenize(io.BytesIO(source).readline))
    except (OSError, SyntaxError, ValueError, tokenize.TokenError) as exc:
        raise RulingNeeded(f"cannot preserve assertions in {path}: {exc}") from exc
    ignored = {tokenize.ENCODING, tokenize.COMMENT, tokenize.NL}
    tokens = [t for t in tokens if t.type not in ignored]
    signatures = []
    scopes: list[tuple[int, str]] = []
    depth = 0
    pending = None
    for i, token in enumerate(tokens):
        if token.type == tokenize.INDENT:
            depth += 1
            if pending is not None:
                scopes.append((depth, pending))
                pending = None
        elif token.type == tokenize.DEDENT:
            scopes = [scope for scope in scopes if scope[0] < depth]
            depth -= 1
        elif token.string in {"def", "class"}:
            pending = tokens[i + 1].string
        elif token.type == tokenize.NEWLINE and tokens[i + 1].type != tokenize.INDENT:
            pending = None
        if token.type != tokenize.NAME:
            continue
        statement = token.string == "assert"
        call = (
            token.string.startswith("assert")
            or token.string in {"raises", "warns", "fail"}
        ) and tokens[i + 1].string == "("
        if not statement and not call:
            continue
        start = i
        # Retain the receiver too, so self.assertEqual and other.assertEqual differ.
        while start >= 2 and tokens[start - 1].string == ".":
            start -= 2
        end = i + 1
        brackets = 0
        while end < len(tokens):
            current = tokens[end]
            if current.string in {"(", "[", "{"}:
                brackets += 1
            elif current.string in {")", "]", "}"}:
                brackets -= 1
                if call and brackets == 0:
                    end += 1
                    break
            if (
                statement
                and brackets == 0
                and (current.type == tokenize.NEWLINE or current.string == ";")
            ):
                break
            end += 1
        signatures.append(
            (
                tuple(name for _, name in scopes) + ((pending,) if pending else ()),
                tuple((t.type, t.string) for t in tokens[start:end]),
            )
        )
    return tuple(signatures)


def capture_repair(wt: Path, red_ids: tuple[str, ...]) -> RepairSnapshot:
    """Capture exact collection and lexical assertions without running the tests.

    File-wide membership and assertion preservation is conservative: unrelated
    tests in the same files cannot be added, removed or changed by a repair.
    Collection imports tests and may execute their import/collection hooks.
    """
    red_ids = tuple(red_ids)
    if not red_ids or len(set(red_ids)) != len(red_ids):
        raise RulingNeeded("repair requires distinct original red selectors")
    files = []
    for selector in red_ids:
        file, separator, name = selector.partition("::")
        path = Path(file)
        if (
            not separator
            or not name
            or not file.startswith("tests/")
            or path.suffix != ".py"
            or ".." in path.parts
            or path.as_posix() != file
            or any(c.isspace() for c in file)
            or any(ord(c) < 32 or ord(c) == 127 for c in selector)
            or not (wt / path).resolve().is_relative_to(wt.resolve())
        ):
            raise RulingNeeded(f"invalid original red selector: {selector!r}")
        if file not in files:
            files.append(file)
    assertions = tuple((file, _assertions(wt / file)) for file in files)
    # pytest's collection hook supplies structured membership, never red evidence.
    # Keep terminal presentation and configured verbosity out of the protocol.
    script = """import contextlib, io, json, sys
import pytest
items = []
class Collection:
    def pytest_collection_finish(self, session):
        items.extend(item.nodeid for item in session.items)
output = io.StringIO()
with contextlib.redirect_stdout(output):
    code = pytest.main(['--collect-only', '-q', '-o', 'addopts=', '--', *sys.argv[1:]], plugins=[Collection()])
print(json.dumps({'membership': items, 'output': output.getvalue()}))
sys.exit(code)
"""
    command = [sys.executable, "-c", script, *files]
    try:
        result = run.run(command, wt, timeout=60)
    except ForemanError as exc:
        raise RulingNeeded(f"cannot collect repair selectors: {exc}") from exc
    if result.code != 0:
        raise RulingNeeded(
            f"cannot collect repair selectors: {result.out}\n{result.err}"
        )
    try:
        payload = json.loads(result.out)
        collected = payload["membership"]
        if not isinstance(collected, list) or not all(
            isinstance(item, str) for item in collected
        ):
            raise ValueError("invalid collection membership")
        membership = tuple(collected)
    except (ValueError, TypeError, KeyError) as exc:
        raise RulingNeeded("invalid structured repair collection") from exc
    if len(set(membership)) != len(membership) or not set(red_ids) <= set(membership):
        raise RulingNeeded("original red selector missing or ambiguous in collection")
    return RepairSnapshot(red_ids, membership, assertions)


def validate_repair(original: RepairSnapshot, wt: Path) -> None:
    """Re-read original evidence; never derive a new red set from repair reports."""
    current = capture_repair(wt, original.red_ids)
    if current.membership != original.membership:
        raise RulingNeeded("repair changed original selector membership")
    if current.assertions != original.assertions:
        raise RulingNeeded("repair changed original assertions")
