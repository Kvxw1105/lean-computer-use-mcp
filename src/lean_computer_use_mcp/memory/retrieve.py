"""Retrieval and composition: map an intent onto learned components.

The scorer is intentionally local and deterministic: the planner model never
pays tokens to re-discover a component that memory already knows. Chinese and
Latin text are both handled via word tokens plus CJK character bigrams.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from lean_computer_use_mcp.memory.extract import resolve_template
from lean_computer_use_mcp.memory.model import (
    Component,
    MemoryLibrary,
    TaskTemplate,
    norm_name,
)
from lean_computer_use_mcp.record.model import RecordedStep

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[a-z0-9]+")

#: Template reuse requires a confident intent match.
TEMPLATE_THRESHOLD = 1.5
#: Weak component hits below this are ignored.
COMPONENT_THRESHOLD = 0.8


def analyze(text: str) -> tuple[list[str], set[str]]:
    """Word tokens plus CJK character bigrams for fuzzy matching."""
    lowered = (text or "").lower()
    words = _WORD_RE.findall(lowered)
    cjk = "".join(_CJK_RE.findall(lowered))
    bigrams = {cjk[index : index + 2] for index in range(len(cjk) - 1)}
    bigrams.update(cjk)
    return words, bigrams


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def name_overlap(query: str, name: str) -> float:
    """How well a query explains a target name (0..2)."""
    query_norm = norm_name(query)
    name_norm = norm_name(name)
    if not name_norm:
        return 0.0
    if name_norm in query_norm or query_norm in name_norm:
        return 2.0
    _q_words, q_grams = analyze(query)
    _n_words, n_grams = analyze(name)
    return min(2.0, jaccard(q_grams, n_grams) * 2.0)


def score_component(
    component: Component,
    query: str,
    app: str | None = None,
    template_names: list[str] | None = None,
) -> float:
    """Score one component for an intent (higher is better)."""
    score = 0.0
    if app and norm_name(app) == norm_name(component.app):
        score += 1.0
    candidates = [component.name] + list(component.aliases)
    score += max((name_overlap(query, name) for name in candidates), default=0.0)
    if component.role and norm_name(component.role) in norm_name(query):
        score += 0.3
    for template_name in template_names or []:
        score += name_overlap(query, template_name) * 0.5
    score += min(0.75, component.hits * 0.25)  # popularity
    staleness = component.misses / (component.hits + component.misses + 1)
    score -= staleness * 1.0
    return round(score, 3)


def search(
    library: MemoryLibrary,
    query: str,
    app: str | None = None,
    limit: int = 8,
    threshold: float = COMPONENT_THRESHOLD,
) -> list[tuple[Component, float]]:
    """Rank components for an intent; templates contribute via their names."""
    template_by_component: dict[str, list[str]] = {}
    for template in library.templates.values():
        for component_id in template.component_ids:
            template_by_component.setdefault(component_id, []).append(template.name)
    scored: list[tuple[Component, float]] = []
    for component in library.components.values():
        if app and norm_name(component.app) != norm_name(app):
            continue
        template_names = template_by_component.get(component.id)
        if not _has_signal(component, query, template_names):
            continue  # app context alone must not pull in unrelated components
        score = score_component(
            component, query, app=app, template_names=template_names
        )
        if score >= threshold:
            scored.append((component, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


def _has_signal(
    component: Component, query: str, template_names: list[str] | None
) -> bool:
    """Require a semantic hook beyond the app: a name/alias/template match or an
    explicit action word in the intent (for glue components such as press_key)."""
    if component.name and name_overlap(query, component.name) >= 0.5:
        return True
    if any(name_overlap(query, alias) >= 0.5 for alias in component.aliases):
        return True
    if any(name_overlap(query, name) >= 0.5 for name in template_names or []):
        return True
    query_norm = norm_name(query)
    return any(word in query_norm for word in component.action.split("_") if word)


def score_template(template: TaskTemplate, query: str, app: str | None = None) -> float:
    score = (
        name_overlap(query, template.name)
        + name_overlap(query, template.description) * 0.5
    )
    if app and norm_name(app) == norm_name(template.app):
        score += 1.0
    return round(score, 3)


@dataclass
class RecallPlan:
    """A composed plan: either a learned template or a tentative component chain."""

    kind: str  # "template" | "components"
    app: str
    steps: list[RecordedStep] = field(default_factory=list)
    score: float = 0.0
    tentative: bool = False
    source: str = ""  # template name or component ids joined

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "app": self.app,
            "score": self.score,
            "tentative": self.tentative,
            "source": self.source,
            "steps": [step.to_dict() for step in self.steps],
        }


def compose_plan(
    library: MemoryLibrary,
    query: str,
    app: str | None = None,
) -> RecallPlan:
    """Reuse a learned template when confident; otherwise chain components."""
    best_template: tuple[TaskTemplate, float] | None = None
    for template in library.templates.values():
        score = score_template(template, query, app=app)
        if score >= TEMPLATE_THRESHOLD and (
            best_template is None or score > best_template[1]
        ):
            best_template = (template, score)
    if best_template is not None:
        template, score = best_template
        steps = resolve_template(library.components, template)
        return RecallPlan(
            kind="template",
            app=template.app,
            steps=steps,
            score=score,
            tentative=False,
            source=template.name,
        )
    ranked = search(library, query, app=app)
    steps = [component.to_step() for component, _score in ranked]
    return RecallPlan(
        kind="components",
        app=app or (ranked[0][0].app if ranked else ""),
        steps=steps,
        score=sum(score for _component, score in ranked),
        tentative=True,
        source=", ".join(component.id for component, _score in ranked),
    )
