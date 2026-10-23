"""Brief text and standing lines: renders a brief and a resume brief."""

from pathlib import Path
from string import Template

from foreman.config import Config
from foreman.run import Result
from foreman.steps import Step

_TEMPLATES = Path(__file__).parent / "templates"


def _template(name: str) -> Template:
    return Template((_TEMPLATES / name).read_text())


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- none"


def render(cfg: Config, step: Step, head: str, earlier: dict[str, str]) -> str:
    """Render the brief for a step; `earlier` maps step id to its returned output."""
    if step.brief:
        return step.brief
    if earlier:
        blocks = "\n\n".join(f"From step {k}:\n{v}" for k, v in earlier.items())
        earlier_text = f"\nEarlier outputs this step needs:\n{blocks}\n"
    else:
        earlier_text = ""
    return _template("brief.md").substitute(
        step_id=step.id,
        design_ref=step.design_ref,
        head=head,
        paths=_bullets(step.paths),
        done_check=" ".join(step.done_check),
        size=step.size,
        earlier=earlier_text,
        standing_lines=_bullets(cfg.standing_lines),
    )


def render_resume(step: Step, failures: Result) -> str:
    """Render the resume brief, quoting the exact failing output."""
    return _template("resume.md").substitute(
        step_id=step.id,
        paths=_bullets(step.paths),
        cmd=" ".join(failures.cmd),
        code=failures.code,
        out=failures.out,
        err=failures.err,
    )
