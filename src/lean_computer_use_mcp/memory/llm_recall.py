"""LLM-assisted intent-to-component mapping (composition).

The deterministic scorer (``memory/retrieve.py``) handles exact token
overlap, but real intents rarely share tokens with learned component names
("?????" vs. "preview canvas"). ``LlmRecallMapper`` sends the intent
plus a compact library digest to an OpenAI-compatible chat API and asks for
an ordered, validated list of component ids. The deterministic
``retrieve.compose_plan`` remains the fallback whenever the endpoint is
missing, fails, or returns nothing valid.

Privacy: the digest contains component ids, roles, names and descriptions
only - no coordinates, window titles, screenshots, or raw trees.
"""

from __future__ import annotations

import json
import re
from typing import Any

from lean_computer_use_mcp.memory.model import MemoryLibrary
from lean_computer_use_mcp.memory.retrieve import RecallPlan
from lean_computer_use_mcp.memory.llm_client import TextLlmClient
from lean_computer_use_mcp.vision.base import VisionProvider
from lean_computer_use_mcp.vision.pool import ProviderPool

_MAX_PLAN_STEPS = 12
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

_SYSTEM_PROMPT = """You compose a desktop-automation plan from a library of atomic UI components.
Given the user's intent and the library, return the ordered component ids that
best achieve the intent.

Return ONLY one JSON object:
{"components": ["jianyingpro::click::pane::preview-canvas", "..."]}

Rules:
- ids must come from the library below; every id appears at most once.
- Reuse existing components whose semantics match part of the intent, even
  when the user phrased it differently (different language, synonyms).
- Order matters: sequence the ids in execution order.
- If the intent cannot be achieved with the available components, return
  {"components": []} - never invent ids and never skip required steps.
- Prefer the smallest useful sequence (2-8 components). No markdown, no
  commentary, no code fences."""


def build_library_digest(
    library: MemoryLibrary, app: str | None = None
) -> str:
    """One line per component: id | action | role | name | description."""
    lines: list[str] = []
    for component in sorted(library.components.values(), key=lambda c: c.id):
        if app and component.app.lower() != app.lower():
            continue
        role = component.role or "-"
        name = component.name or "-"
        description = component.description or ""
        lines.append(f"{component.id} | {component.action} | {role} | {name} | {description}")
    return "\n".join(lines)


def parse_component_ids(content: str, library: MemoryLibrary) -> list[str]:
    """Validate the LLM's plan: known ids, unique, order preserved."""
    text = content.strip()
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return []
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return []
    ids: list[str] = []
    seen: set[str] = set()
    for component_id in payload.get("components", []):
        if not isinstance(component_id, str):
            continue
        if component_id in seen or component_id not in library.components:
            continue
        seen.add(component_id)
        ids.append(component_id)
        if len(ids) >= _MAX_PLAN_STEPS:
            break
    return ids


class LlmRecallMapper:
    """Text-only OpenAI-compatible client used to map intents to components.

    Routes through :class:`TextLlmClient` / :class:`ProviderPool` so a dead
    endpoint fails over to the next configured provider with the same
    cooldowns as the vision tier (401/403: 10 min, transient: 30 s). Keys
    are never logged - only endpoint hosts are.
    """

    name = "llm-recall"

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
            purpose="LLM recall",
            timeout_seconds=timeout_seconds,
            transport=transport,
            providers=providers,
            pool=pool,
        )

    def compose(
        self, library: MemoryLibrary, intent: str, app: str | None = None
    ) -> RecallPlan:
        user = f"Intent: {intent}\n\nLibrary:\n{build_library_digest(library, app=app)}"
        content = self._client.complete(_SYSTEM_PROMPT, user, max_tokens=1024)
        ids = parse_component_ids(content, library)
        plan_app = app or (library.components[ids[0]].app if ids else "")
        return RecallPlan(
            kind="components",
            app=plan_app,
            steps=[library.components[component_id].to_step() for component_id in ids],
            score=2.0 if ids else 0.0,
            tentative=True,
            source="llm:" + ",".join(ids),
        )
