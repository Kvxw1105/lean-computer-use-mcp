"""LLM-assisted semantic enrichment of recorded steps.

Deterministic extraction (``memory/extract.py``) fingerprints a step from
whatever the accessibility tree provided. For UIA-blind apps (custom-rendered
editors such as JianYing) the tree is nearly empty, so steps fall back to
coordinates and the fingerprint degenerates to ``app::click::::`` - one
anonymous blob that can never be reused semantically.

``LlmEnricher`` sends a compact, text-only digest of the recording to an
OpenAI-compatible chat API and asks for one semantic label per step (role,
target name, one-line description). Labels are validated and merged into a
copy of the recording: semantic targets replace empty ones, so the compiled
skill and the extracted components become reusable atomic units. Without an
endpoint, on a network failure, or on invalid output the recording is
returned unchanged - the deterministic path stays authoritative.

Privacy: the digest contains the app name, the step sequence (action,
coordinates, values, keys) and the first snapshot's element names as a
vocabulary hint. No screenshots, window titles, or raw accessibility trees
are sent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import Any

from lean_computer_use_mcp.memory.llm_client import TextLlmClient
from lean_computer_use_mcp.record.model import ElementRef, RecordedStep, Recording
from lean_computer_use_mcp.vision.base import VisionProvider
from lean_computer_use_mcp.vision.pool import ProviderPool

#: Roles the LLM is allowed to assign; keeps fingerprints stable across runs.
ROLE_ALLOWLIST = frozenset(
    {
        "button",
        "edit",
        "combobox",
        "slider",
        "checkbox",
        "radio",
        "list",
        "list_item",
        "menu",
        "menu_item",
        "tab",
        "tab_item",
        "window",
        "text",
        "image",
        "scrollbar",
        "link",
        "dialog",
        "pane",
        "group",
        "split_button",
        "spin_button",
        "status_bar",
        "title_bar",
        "custom",
    }
)

_VOCAB_LIMIT = 12
_NAME_MAX = 48
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

_SYSTEM_PROMPT = """You are the semantic naming layer of a desktop-automation recorder.
A user demonstrated a workflow; the recorder captured intent steps, but for
custom-rendered apps the accessibility tree is empty, so steps only have
coordinates. Your job: give each step a semantic role and target name so the
workflow can be reused as atomic building blocks by future tasks.

Return ONLY one JSON object with this exact shape:
{"steps": [{"index": 1, "role": "button", "name": "font-size",
"description": "Opens the font size control."}]}

Rules:
- index must match one of the step indices in the input (1-based).
- role must be one of: button, edit, combobox, slider, checkbox, radio, list,
  list_item, menu, menu_item, tab, tab_item, window, text, image, scrollbar,
  link, dialog, pane, group, split_button, spin_button, status_bar, title_bar,
  custom.
- name: short lowercase semantic label (1-4 words) for the UI element the
  step targets. Infer it from step order, coordinates, the visible element
  vocabulary, and domain knowledge of the app. Never invent ids, file names,
  or on-screen text.
- description: one short English sentence describing when this step applies.
- A step already carrying a semantic target role/name (button, edit,
  slider, ...) should keep it (return it unchanged or omit it).
- A target that is only the window itself (role window/pane/title_bar, or
  name equal to the app or window title) is NOT semantic - treat that step
  as coordinate-only and name the real control.
- Omit steps you cannot name with confidence. No markdown, no commentary, no
  code fences."""


@dataclass(frozen=True)
class StepLabel:
    """One validated semantic label for a recorded step."""

    index: int  # 1-based step index in the recording
    role: str
    name: str
    description: str = ""


@dataclass
class EnrichmentResult:
    """Validated labels plus coverage statistics."""

    labels: list[StepLabel] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)  # indices rejected by validation

    @property
    def named(self) -> int:
        return len(self.labels)

    def by_index(self) -> dict[int, StepLabel]:
        return {label.index: label for label in self.labels}


def build_digest(recording: Recording) -> str:
    """Compact text digest of a recording (privacy-filtered)."""
    lines = [f"App: {recording.app}", f"Workflow: {len(recording.steps)} steps"]
    vocabulary: list[str] = []
    if recording.snapshots:
        vocabulary = [
            element.name
            for element in recording.snapshots[0].elements
            if element.name
        ][:_VOCAB_LIMIT]
    if vocabulary:
        lines.append("Visible element vocabulary: " + ", ".join(vocabulary))
    window_names = {
        step.window_title for step in recording.steps if step.window_title
    }
    for table in recording.snapshots:
        if table.window_title:
            window_names.add(table.window_title)
    for index, step in enumerate(recording.steps, start=1):
        parts = [str(index), step.action]
        if (
            step.target
            and step.target.name
            and not _window_only_target(step, recording.app, window_names)
        ):
            parts.append(f"role={step.target.role or '?'}")
            parts.append(f"name={step.target.name}")
        elif step.target and step.target.name:
            parts.append("(window-level target; coordinate step)")
        if step.x is not None and step.y is not None:
            parts.append(f"xy=({step.x},{step.y})")
        if step.value:
            parts.append(f"value={step.value!r}")
        if step.key:
            parts.append(f"key={step.key}")
        if step.direction:
            parts.append(f"direction={step.direction} pages={step.pages}")
        lines.append("  " + " ".join(parts))
    return "\n".join(lines)


def parse_labels(content: str, step_count: int) -> EnrichmentResult:
    """Parse and validate the LLM's JSON response.

    Invalid indices, unknown roles, and empty names are dropped into
    ``skipped``; everything else becomes a :class:`StepLabel`.
    """
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
            return EnrichmentResult()
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return EnrichmentResult()
    result = EnrichmentResult()
    seen: set[int] = set()
    for item in payload.get("steps", []):
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            result.skipped.append(0)
            continue
        if index < 1 or index > step_count or index in seen:
            result.skipped.append(index)
            continue
        role = str(item.get("role", "")).strip().lower()
        name = str(item.get("name", "")).strip().lower()
        if role not in ROLE_ALLOWLIST or not name:
            result.skipped.append(index)
            continue
        description = str(item.get("description", "")).strip()
        seen.add(index)
        result.labels.append(
            StepLabel(
                index=index,
                role=role,
                name=name[:_NAME_MAX],
                description=description,
            )
        )
    return result


def _window_only_target(
    step: RecordedStep, app: str, window_names: set[str]
) -> bool:
    """True when the step's target is only the window itself.

    UIA-thin apps often resolve every click to the window node; such a target
    carries no semantics and must be replaced, not preserved.
    """
    target = step.target
    if target is None or not target.name:
        return True
    if target.role in {"window", "pane", "title_bar"}:
        return True
    normalized = target.name.strip().lower()
    return normalized == app.strip().lower() or normalized in {
        name.strip().lower() for name in window_names if name
    }


def enrich_recording(
    recording: Recording, result: EnrichmentResult
) -> Recording:
    """Return a copy of the recording with semantic targets applied.

    Only steps whose target is missing, unnamed, or window-only are enriched;
    an existing semantic target is never overwritten.
    """
    if not result.labels:
        return recording
    window_names = {step.window_title for step in recording.steps if step.window_title}
    for table in recording.snapshots:
        if table.window_title:
            window_names.add(table.window_title)
    by_index = result.by_index()
    steps: list[RecordedStep] = []
    for index, step in enumerate(recording.steps, start=1):
        label = by_index.get(index)
        if label is not None and (
            step.target is None
            or not step.target.name
            or _window_only_target(step, recording.app, window_names)
        ):
            target = ElementRef(role=label.role, name=label.name)
            steps.append(
                replace(
                    step,
                    target=target,
                    matched=step.matched or bool(step.target is None),
                )
            )
        else:
            steps.append(step)
    return replace(recording, steps=steps)


class LlmEnricher:
    """Text-only OpenAI-compatible client used to name recorded steps.

    Routes through :class:`TextLlmClient` / :class:`ProviderPool` so a dead
    endpoint fails over to the next configured provider with the same
    cooldowns as the vision tier (401/403: 10 min, transient: 30 s). Keys
    are never logged - only endpoint hosts are.
    """

    name = "llm-enrich"

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
            purpose="semantic enrichment",
            timeout_seconds=timeout_seconds,
            transport=transport,
            providers=providers,
            pool=pool,
        )

    def enrich(self, recording: Recording) -> EnrichmentResult:
        content = self._client.complete(_SYSTEM_PROMPT, build_digest(recording))
        return parse_labels(content, len(recording.steps))
