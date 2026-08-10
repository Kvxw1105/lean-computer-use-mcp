"""Extract atomic components and task templates from a recording.

Every step becomes a component; concrete values are parameterized so two
recordings that differ only in the typed value share one component. The
task template is simply the ordered component-id sequence, which lets a
future task reuse pieces instead of the whole.
"""

from __future__ import annotations

from lean_computer_use_mcp.memory.model import (
    Component,
    TaskTemplate,
    fingerprint,
    slugify,
)
from lean_computer_use_mcp.models import Frame
from lean_computer_use_mcp.record.model import RecordedStep, Recording

VALUE_PLACEHOLDER = "{value}"


def component_id_from_step(app: str, step: RecordedStep) -> str:
    """Fingerprint a step so replay feedback can update its component."""
    target = step.target
    return fingerprint(
        app, step.action, target.role if target else "", target.name if target else ""
    )


def _value_template(step: RecordedStep) -> str | None:
    if step.action == "type_text" and step.value:
        return VALUE_PLACEHOLDER
    return None


def _preconditions(recording: Recording, step: RecordedStep) -> list[str]:
    """Names that must be visible before the step (from the first snapshot)."""
    if not step.target or not step.target.name:
        return []
    if not recording.snapshots:
        return []
    first = recording.snapshots[0]
    names = {element.name for element in first.elements}
    return [step.target.name] if step.target.name in names else []


def _frame(step: RecordedStep) -> Frame | None:
    return step.target.frame if step.target and step.target.frame else None


def extract_components(
    recording: Recording, step_descriptions: dict[int, str] | None = None
) -> tuple[list[Component], TaskTemplate]:
    """Build (unique components, task template) for one recording.

    ``step_descriptions`` maps 1-based step indices to semantic one-liners
    produced by ``memory.enrich`` (LLM-assisted naming); they become the
    component description so memory reads naturally after enrichment.
    """
    step_descriptions = step_descriptions or {}
    components: dict[str, Component] = {}
    ordered: list[Component] = []
    for index, step in enumerate(recording.steps, start=1):
        component = Component(
            app=recording.app,
            action=step.action,
            role=step.target.role if step.target else "",
            name=step.target.name if step.target else "",
            frame=_frame(step),
            x=step.x,
            y=step.y,
            value_template=_value_template(step),
            key=step.key,
            direction=step.direction,
            pages=step.pages,
            preconditions=_preconditions(recording, step),
            description=step_descriptions.get(index, ""),
            created_at=recording.started_at,
        )
        existing = components.get(component.id)
        if existing is None:
            components[component.id] = component
            ordered.append(component)
    template = TaskTemplate(
        id=slugify(recording.name),
        name=recording.name,
        app=recording.app,
        description=recording.description,
        component_ids=[component.id for component in ordered],
        created_at=recording.started_at,
    )
    return ordered, template


def resolve_template(
    library_components: dict[str, Component], template: TaskTemplate
) -> list[RecordedStep]:
    """Rebuild steps from a template's component ids (missing ids are skipped)."""
    steps: list[RecordedStep] = []
    for component_id in template.component_ids:
        component = library_components.get(component_id)
        if component is not None:
            steps.append(component.to_step())
    return steps
