"""LLM-assisted memory refinement.

Deterministic extraction is good at the exact, scriptable parts (fingerprint
dedupe, value parameterization). It is bad at the semantic parts: "Font size"
vs "字号", "captions" vs "subtitles", two components that are really one, or
two templates that differ only in parameters. ``LlmRefiner`` sends a compact
library digest to an OpenAI-compatible chat API and gets back structured
suggestions (aliases / merges / descriptions / generalizations).

Suggestions are never applied automatically: ``apply_suggestions`` is only
called with ``--apply`` after the user or the driving agent reviews them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from lean_computer_use_mcp.memory.library import Memory
from lean_computer_use_mcp.memory.llm_client import TextLlmClient
from lean_computer_use_mcp.memory.model import MemoryLibrary, TaskTemplate, slugify
from lean_computer_use_mcp.vision.base import VisionProvider
from lean_computer_use_mcp.vision.pool import ProviderPool

_SYSTEM_PROMPT = """You are the memory curator of a desktop-automation skill library.
You are given atomic UI components and task templates learned from real user
demonstrations. Your job is to make the library smarter over time by finding
semantic equivalences that scripts cannot see.

Return ONLY one JSON object with this exact shape:
{
  "aliases": [{"component_id": "...", "alias": "...", "reason": "..."}],
  "merges": [{"keep_id": "...", "into_id": "...", "reason": "..."}],
  "descriptions": [{"component_id": "...", "description": "..."}],
  "generalizations": [{"template_ids": ["..."], "suggested_name": "...", "reason": "..."}]
}

Rules:
- Aliases: only high-confidence synonyms in the same app context (different
  language labels, abbreviations, product-specific naming). Never suggest an
  alias for a generic action component (focus, type_text, press_key).
- Merges: only when two components in the SAME app are the same atomic action
  under different names/roles; keep the one with more hits, merge into it.
- Descriptions: one short English sentence per component describing when the
  action applies (e.g. "Opens the text panel in the video editor").
- Generalizations: only when two templates share most component ids and the
  difference is a parameter (e.g. a value); suggest a generalized name.
- When unsure, omit. Never fabricate ids: only use ids present in the input.
- No markdown, no commentary, no code fences."""


@dataclass
class AliasSuggestion:
    component_id: str
    alias: str
    reason: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"component_id": self.component_id, "alias": self.alias, "reason": self.reason}


@dataclass
class MergeSuggestion:
    keep_id: str
    into_id: str
    reason: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"keep_id": self.keep_id, "into_id": self.into_id, "reason": self.reason}


@dataclass
class DescriptionSuggestion:
    component_id: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {"component_id": self.component_id, "description": self.description}


@dataclass
class GeneralizationSuggestion:
    template_ids: list[str] = field(default_factory=list)
    suggested_name: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_ids": list(self.template_ids),
            "suggested_name": self.suggested_name,
            "reason": self.reason,
        }


@dataclass
class RefineSuggestions:
    aliases: list[AliasSuggestion] = field(default_factory=list)
    merges: list[MergeSuggestion] = field(default_factory=list)
    descriptions: list[DescriptionSuggestion] = field(default_factory=list)
    generalizations: list[GeneralizationSuggestion] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.aliases or self.merges or self.descriptions or self.generalizations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "aliases": [item.to_dict() for item in self.aliases],
            "merges": [item.to_dict() for item in self.merges],
            "descriptions": [item.to_dict() for item in self.descriptions],
            "generalizations": [item.to_dict() for item in self.generalizations],
        }

    def summary(self) -> str:
        parts = [
            f"{len(self.aliases)} aliases",
            f"{len(self.merges)} merges",
            f"{len(self.descriptions)} descriptions",
            f"{len(self.generalizations)} generalizations",
        ]
        return ", ".join(parts)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RefineSuggestions":
        """Rebuild suggestions from a saved/reviewed JSON file."""
        suggestions = cls()
        for item in data.get("aliases", []) or []:
            if isinstance(item, dict):
                suggestions.aliases.append(
                    AliasSuggestion(
                        str(item.get("component_id", "")),
                        str(item.get("alias", "")),
                        str(item.get("reason", "")),
                    )
                )
        for item in data.get("merges", []) or []:
            if isinstance(item, dict):
                suggestions.merges.append(
                    MergeSuggestion(
                        str(item.get("keep_id", "")),
                        str(item.get("into_id", "")),
                        str(item.get("reason", "")),
                    )
                )
        for item in data.get("descriptions", []) or []:
            if isinstance(item, dict):
                suggestions.descriptions.append(
                    DescriptionSuggestion(
                        str(item.get("component_id", "")),
                        str(item.get("description", "")),
                    )
                )
        for item in data.get("generalizations", []) or []:
            if isinstance(item, dict):
                suggestions.generalizations.append(
                    GeneralizationSuggestion(
                        [str(tid) for tid in item.get("template_ids", [])],
                        str(item.get("suggested_name", "")),
                        str(item.get("reason", "")),
                    )
                )
        return suggestions


def _extract_json(text: str) -> dict[str, Any]:
    """Tolerate code fences and prose around the JSON reply."""
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("refine response contained no JSON object")
    return json.loads(text[start : end + 1])


def parse_suggestions(text: str) -> RefineSuggestions:
    """Parse the model's reply into structured suggestions (never raises on noise)."""
    data = _extract_json(text)
    suggestions = RefineSuggestions()
    for item in data.get("aliases", []) or []:
        if isinstance(item, dict) and item.get("component_id") and item.get("alias"):
            suggestions.aliases.append(
                AliasSuggestion(
                    str(item["component_id"]),
                    str(item["alias"]),
                    str(item.get("reason", "")),
                )
            )
    for item in data.get("merges", []) or []:
        if isinstance(item, dict) and item.get("keep_id") and item.get("into_id"):
            suggestions.merges.append(
                MergeSuggestion(
                    str(item["keep_id"]),
                    str(item["into_id"]),
                    str(item.get("reason", "")),
                )
            )
    for item in data.get("descriptions", []) or []:
        if isinstance(item, dict) and item.get("component_id") and item.get("description"):
            suggestions.descriptions.append(
                DescriptionSuggestion(str(item["component_id"]), str(item["description"]))
            )
    for item in data.get("generalizations", []) or []:
        if isinstance(item, dict) and item.get("template_ids"):
            suggestions.generalizations.append(
                GeneralizationSuggestion(
                    [str(tid) for tid in item["template_ids"]],
                    str(item.get("suggested_name", "")),
                    str(item.get("reason", "")),
                )
            )
    return suggestions


def library_digest(library: MemoryLibrary) -> str:
    """Compact, model-friendly rendering of the library (no raw data)."""
    lines = ["COMPONENTS:"]
    for component in sorted(library.components.values(), key=lambda item: item.id):
        lines.append(
            "- {id} | app={app} | action={action} | role={role} | name={name} | "
            "hits={hits} | misses={misses} | aliases={aliases}".format(
                id=component.id,
                app=component.app,
                action=component.action,
                role=component.role or "-",
                name=component.name or "-",
                hits=component.hits,
                misses=component.misses,
                aliases=",".join(component.aliases) or "-",
            )
        )
    lines.append("TEMPLATES:")
    for template in library.templates.values():
        lines.append(
            f"- {template.id} | app={template.app} | name={template.name} | "
            f"description={template.description} | steps={','.join(template.component_ids)}"
        )
    return "\n".join(lines)


class LlmRefiner:
    """Text-only OpenAI-compatible chat client used for memory curation.

    Routes through :class:`TextLlmClient` / :class:`ProviderPool` so a dead
    endpoint fails over to the next configured provider with the same
    cooldowns as the vision tier (401/403: 10 min, transient: 30 s). Keys
    are never logged - only endpoint hosts are.
    """

    name = "llm-memory"

    def __init__(
        self,
        api_base: str | None,
        api_key: str | None,
        model: str | None,
        timeout_seconds: float = 60.0,
        transport: Any | None = None,
        providers: tuple[VisionProvider, ...] = (),
        pool: ProviderPool | None = None,
    ) -> None:
        self.api_base = api_base
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._client = TextLlmClient(
            api_base,
            api_key,
            model,
            purpose="memory refinement",
            timeout_seconds=timeout_seconds,
            transport=transport,
            providers=providers,
            pool=pool,
        )

    def refine(self, library: MemoryLibrary) -> RefineSuggestions:
        content = self._client.complete(_SYSTEM_PROMPT, library_digest(library))
        return parse_suggestions(content)


def apply_suggestions(memory: Memory, suggestions: RefineSuggestions) -> dict[str, int]:
    """Apply reviewed suggestions. Only id-valid entries are applied."""
    applied = {"aliases": 0, "merges": 0, "descriptions": 0, "generalizations": 0}
    for suggestion in suggestions.aliases:
        if memory.add_alias(suggestion.component_id, suggestion.alias):
            applied["aliases"] += 1
    for suggestion in suggestions.descriptions:
        component = memory.library.components.get(suggestion.component_id)
        if component is not None and suggestion.description:
            component.description = suggestion.description
            applied["descriptions"] += 1
    for suggestion in suggestions.merges:
        keep = memory.library.components.get(suggestion.keep_id)
        into = memory.library.components.get(suggestion.into_id)
        if keep is None or into is None or keep.app != into.app:
            continue
        for alias in [keep.name, *keep.aliases]:
            if alias and alias not in into.aliases:
                into.aliases.append(alias)
        into.hits += keep.hits
        into.misses += keep.misses
        del memory.library.components[suggestion.keep_id]
        for template in memory.library.templates.values():
            template.component_ids = [
                suggestion.into_id if component_id == suggestion.keep_id else component_id
                for component_id in template.component_ids
            ]
        applied["merges"] += 1
    for suggestion in suggestions.generalizations:
        if not suggestion.template_ids:
            continue
        targets: list[TaskTemplate] = []
        missing = False
        for template_id in suggestion.template_ids:
            template = memory.library.templates.get(template_id)
            if template is None:
                missing = True
                break
            targets.append(template)
        if missing or not targets:
            continue
        base = max(targets, key=lambda item: item.hits)
        merged_ids: list[str] = []
        for template in targets:
            for component_id in template.component_ids:
                if component_id not in merged_ids:
                    merged_ids.append(component_id)
        name = (suggestion.suggested_name or base.name).strip()
        new_id = slugify(name) if name else base.id
        existing = memory.library.templates.get(new_id)
        if existing is not None and existing.id not in [t.id for t in targets]:
            merged = existing
            others = list(targets)
        else:
            merged = existing if existing in targets else TaskTemplate(
                id=new_id,
                name=name or base.name,
                app=base.app,
                description=base.description,
                component_ids=list(merged_ids),
                hits=0,
                created_at=base.created_at,
            )
            if merged.id not in memory.library.templates:
                memory.library.templates[merged.id] = merged
            others = [template for template in targets if template.id != merged.id]
        for component_id in merged_ids:
            if component_id not in merged.component_ids:
                merged.component_ids.append(component_id)
        for template in others:
            merged.hits += template.hits
            del memory.library.templates[template.id]
        if suggestion.reason and suggestion.reason not in merged.description:
            merged.description = f"{merged.description}; {suggestion.reason}".strip("; ")
        applied["generalizations"] += 1

    memory.save()
    return applied
