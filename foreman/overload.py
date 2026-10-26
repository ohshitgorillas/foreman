"""Foreman's 7.1f check on a brief it is about to dispatch, and the cut of a red brief."""

from dataclasses import dataclass

from foreman import forms

MAX_FILES = 5  # 7.1f: more than five files in scope


@dataclass(frozen=True)
class Overload:
    test: str  # "files" | "page" | "behaviors"
    found: int
    limit: int

    def line(self) -> str:
        """`OVERLOADED: <test> <found>, more than <limit>`."""
        raise NotImplementedError


@dataclass(frozen=True)
class Cut:
    parts: tuple[forms.Red, ...]  # dispatched in order; empty when refused
    refused: tuple[Overload, ...]  # empty when parts is not


def files(paths: list[str]) -> Overload | None:
    """An Overload when more than MAX_FILES paths are in scope."""
    raise NotImplementedError


def page(text: str, limit: int) -> Overload | None:
    """An Overload when the text is longer than limit characters."""
    raise NotImplementedError


def behaviors(form: forms.Red, limit: int) -> Overload | None:
    """An Overload when the form holds more than limit behaviors."""
    raise NotImplementedError


def check(text: str, paths: list[str], page_limit: int) -> list[Overload]:
    """Every failing files and page test of a rendered brief."""
    raise NotImplementedError


def cut_red(
    form: forms.Red,
    paths: list[str],
    view_paths: dict[str, str],
    page_limit: int,
    behavior_limit: int,
) -> Cut:
    """Pass a red form whole, cut it into runs of whole behaviors, or refuse it."""
    raise NotImplementedError
