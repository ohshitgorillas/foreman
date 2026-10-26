"""Foreman's 7.1f check on a brief it is about to dispatch, and the cut of a red brief."""

from dataclasses import dataclass, replace

from foreman import brief, forms

MAX_FILES = 5  # 7.1f: more than five files in scope


@dataclass(frozen=True)
class Overload:
    test: str  # "files" | "page" | "behaviors"
    found: int
    limit: int

    def line(self) -> str:
        """`OVERLOADED: <test> <found>, more than <limit>`."""
        return f"OVERLOADED: {self.test} {self.found}, more than {self.limit}"


@dataclass(frozen=True)
class Cut:
    parts: tuple[forms.Red, ...]  # dispatched in order; empty when refused
    refused: tuple[Overload, ...]  # empty when parts is not


def files(paths: list[str]) -> Overload | None:
    """An Overload when more than MAX_FILES paths are in scope."""
    if len(paths) > MAX_FILES:
        return Overload("files", len(paths), MAX_FILES)
    return None


def page(text: str, limit: int) -> Overload | None:
    """An Overload when the text is longer than limit characters."""
    if len(text) > limit:
        return Overload("page", len(text), limit)
    return None


def behaviors(form: forms.Red, limit: int) -> Overload | None:
    """An Overload when the form holds more than limit behaviors."""
    if len(form.behaviors) > limit:
        return Overload("behaviors", len(form.behaviors), limit)
    return None


def check(text: str, paths: list[str], page_limit: int) -> list[Overload]:
    """Every failing files and page test of a rendered brief."""
    found = (files(paths), page(text, page_limit))
    return [o for o in found if o is not None]


def cut_red(
    form: forms.Red,
    paths: list[str],
    view_paths: dict[str, str],
    page_limit: int,
    behavior_limit: int,
) -> Cut:
    """Pass a red form whole, cut it into runs of whole behaviors, or refuse it."""

    def render(part: forms.Red) -> str:
        return brief.render(part, paths=paths, view_paths=view_paths, red_ids=[])

    def sub(items: tuple[forms.Behavior, ...]) -> forms.Red:
        return replace(form, behaviors=items)

    whole = check(render(form), paths, page_limit)
    if not whole and behaviors(form, behavior_limit) is None:
        return Cut((form,), ())
    refused = [o for o in whole if o.test == "files"]
    for b in form.behaviors:
        o = page(render(sub((b,))), page_limit)
        if o is not None:
            refused.append(o)
            break
    if refused:
        return Cut((), tuple(refused))
    parts: list[forms.Red] = []
    run: list[forms.Behavior] = []
    for b in form.behaviors:
        grown = (*run, b)
        if run and (
            len(grown) > behavior_limit
            or page(render(sub(grown)), page_limit) is not None
        ):
            parts.append(sub(tuple(run)))
            run = [b]
        else:
            run = list(grown)
    parts.append(sub(tuple(run)))
    return Cut(tuple(parts), ())
