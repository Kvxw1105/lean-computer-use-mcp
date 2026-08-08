"""Learning and feedback operations over the procedural memory.

``learn`` ingests a recording: new components are added, known components get
a hit (they were reused), and the task template is upserted. ``record_use``
is the execution feedback loop: successes raise popularity and teach the
effect (elements that appeared after the action); failures raise staleness
so retrieval ranks the component lower next time.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from lean_computer_use_mcp.memory.extract import (
    component_id_from_step,
    extract_components,
)
from lean_computer_use_mcp.memory.model import Component, MemoryLibrary, TaskTemplate
from lean_computer_use_mcp.memory.retrieve import RecallPlan, compose_plan, search
from lean_computer_use_mcp.record.model import Recording, RecordedStep

#: Cap for learned effect names per component.
_MAX_EFFECT = 8


class Memory:
    """Persistent procedural memory backed by one JSON file."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.library = MemoryLibrary()
        if self.path is not None and self.path.exists():
            self.library = MemoryLibrary.load(self.path)

    def save(self) -> None:
        if self.path is not None:
            self.library.save(self.path)

    def learn(
        self,
        recording: Recording,
        step_descriptions: dict[int, str] | None = None,
    ) -> dict[str, int]:
        """Ingest one recording; returns added/updated counts.

        ``step_descriptions`` (1-based step index -> one-liner) come from
        ``memory.enrich`` and become component descriptions.
        """
        components, template = extract_components(
            recording, step_descriptions=step_descriptions
        )
        added = 0
        for component in components:
            existing = self.library.components.get(component.id)
            if existing is None:
                self.library.components[component.id] = component
                added += 1
            else:
                existing.hits += 1
                existing.last_used_at = max(existing.last_used_at, recording.started_at)
        existing_template = self.library.templates.get(template.id)
        if existing_template is None:
            self.library.templates[template.id] = template
        else:
            existing_template.component_ids = template.component_ids
            existing_template.description = (
                template.description or existing_template.description
            )
            existing_template.hits += 1
        self.save()
        return {"components_added": added, "templates": len(self.library.templates)}

    def record_use(
        self,
        app: str,
        step: RecordedStep,
        ok: bool,
        effect_names: list[str] | None = None,
    ) -> None:
        """Feedback after one replayed step (creates a component on the fly)."""
        component_id = component_id_from_step(app, step)
        component = self.library.components.get(component_id)
        if component is None:
            component = Component(
                app=app,
                action=step.action,
                role=step.target.role if step.target else "",
                name=step.target.name if step.target else "",
                created_at=time.time(),
            )
            self.library.components[component_id] = component
        if ok:
            component.hits += 1
            for name in effect_names or []:
                if name and name not in component.effect:
                    component.effect.append(name)
            component.effect = component.effect[:_MAX_EFFECT]
        else:
            component.misses += 1
        component.last_used_at = time.time()
        self.save()

    def add_alias(self, component_id: str, alias: str) -> bool:
        component = self.library.components.get(component_id)
        if component is None:
            return False
        alias = alias.strip()
        if alias and alias not in component.aliases:
            component.aliases.append(alias)
            self.save()
        return True

    def search(
        self, query: str, app: str | None = None, limit: int = 8
    ) -> list[tuple[Component, float]]:
        return search(self.library, query, app=app, limit=limit)

    def recall(self, query: str, app: str | None = None) -> RecallPlan:
        return compose_plan(self.library, query, app=app)

    def stats(self) -> dict[str, Any]:
        components = self.library.components.values()
        return {
            "components": len(components),
            "templates": len(self.library.templates),
            "total_hits": sum(component.hits for component in components),
            "total_misses": sum(component.misses for component in components),
            "path": str(self.path) if self.path else None,
        }

    def template(self, template_id: str) -> TaskTemplate | None:
        return self.library.templates.get(template_id)
