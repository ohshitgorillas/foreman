"""Exact dispatched text for validated forms."""

from typing import assert_never

from foreman import forms
from foreman.errors import ConfigError


def render(
    form: forms.Form,
    *,
    paths: list[str],
    view_paths: dict[str, str],
    red_ids: list[str],
    juror_reasons: tuple[str, ...] = (),
) -> str:
    """Render validated data verbatim; view identity and evidence resolve upstream."""
    if juror_reasons and not isinstance(form, forms.Build):
        raise ConfigError("juror_reasons: allowed only for a build retry")
    parts: list[str] = []
    match form:
        case forms.Red():
            parts.append("red")
            if form.origin is not None:
                parts.append(_origin(form.origin))
            for behavior in form.behaviors:
                parts.extend(
                    (
                        f"behavior: {behavior.text}",
                        f"view: {view_paths[behavior.source]}",
                    )
                )
                if behavior.replaces is not None:
                    parts.append(f"replaces: {behavior.replaces}")
        case forms.Port() | forms.Repair() | forms.Moved():
            parts.append(form.shape)
            if isinstance(form, forms.Port):
                parts.append(_origin(form.origin))
            elif isinstance(form, forms.Moved):
                parts.extend(
                    f'lines: {change.anchor} | before: "{change.before}" | after: "{change.after}"'
                    for change in form.changes
                )
            parts.extend(f"files: {path}" for path in paths)
            parts.extend(f"view: {view_paths[source]}" for source in form.sources)
            if isinstance(form, forms.Repair):
                parts.extend(f"failure: {failure}" for failure in form.failures)
        case forms.Finding() | forms.BuilderFinding():
            parts.extend(form.lines)
        case forms.Stub():
            parts.extend(("stub", form.step, *paths))
        case forms.Build():
            parts.extend((form.step, *paths, *red_ids, *juror_reasons))
        case forms.Rewrite():
            parts.append(form.line)
        case forms.Judgment():
            parts.extend(("judgment", f"spec: {form.spec}"))
            parts.extend(f"step: {step}" for step in form.steps)
            line_count = form.step.count("\n") + 1
            parts.extend((f"step-lines: {line_count}", form.step))
            parts.extend(f"red: {test_id}" for test_id in form.red_ids)
            parts.extend(f"report: {report}" for report in form.reports)
            parts.append("end judgment")
        case forms.DiffReview() | forms.TestReview():
            if form.scope == "range":
                assert form.range is not None
                parts.append(form.range)
            elif isinstance(form, forms.TestReview):
                parts.append("working tree")
            if isinstance(form, forms.TestReview):
                parts.extend(f"spec: {spec}" for spec in form.specs)
        case _:
            assert_never(form)
    if (
        isinstance(form, (forms.Stub, forms.Build, forms.Rewrite, forms.BuilderFinding))
        and form.exclude
    ):
        parts.append("exclude: " + " ".join(form.exclude))
    return "\n".join(parts)


def _origin(origin: forms.Origin) -> str:
    label = "step" if len(origin.steps) == 1 else "steps"
    return f"design: {origin.design}, Order {label} {', '.join(origin.steps)}"
