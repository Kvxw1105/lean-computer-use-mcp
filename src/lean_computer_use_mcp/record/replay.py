"""Replay a recorded workflow through the facade.

The runner re-observes the app before every step, matches the recorded target
by role/name in the live accessibility tree (or by frame from the vision
engine when the tree is thin), and only falls back to recorded coordinates
with the real-input click path for custom-rendered UIs. Confirmation is a
policy decision: every content-level step prompts by default and ``yes``
pre-confirms the whole plan. Window-level steps (focus) never prompt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from lean_computer_use_mcp.record.model import (
    CONTENT_ACTIONS,
    ElementRef,
    Recording,
    RecordedStep,
)
from lean_computer_use_mcp.server import LeanComputerUse

#: Minimum semantic match score to reuse a recorded target.
_MATCH_THRESHOLD = 0.75

ConfirmFn = Callable[[int, RecordedStep], bool]


def default_confirm(step_index: int, step: RecordedStep) -> bool:
    reply = (
        input(f"Replay step {step_index}: {step.describe()} — execute? [y/N] ")
        .strip()
        .lower()
    )
    return reply in {"y", "yes"}


@dataclass
class ReplayOutcome:
    step: int
    action: str
    resolution: str  # preview | window | element | coords | vision | error
    ok: bool
    confirmed: bool = False
    error: str | None = None
    text_chars: int = 0
    image_bytes: int = 0
    nodes: int = 0


@dataclass
class ReplayRunResult:
    ok: bool
    completed: int
    outcomes: list[ReplayOutcome] = field(default_factory=list)
    metrics_before: dict[str, int | float] = field(default_factory=dict)
    metrics_after: dict[str, int | float] = field(default_factory=dict)


def _name_score(target_name: str, candidate: str) -> float:
    target = target_name.strip().lower()
    candidate = candidate.strip().lower()
    if not target or not candidate:
        return 0.0
    if target == candidate:
        return 1.0
    if target in candidate or candidate in target:
        return 0.8
    return 0.0


def match_target(
    controls: list[dict[str, Any]],
    vision_elements: list[dict[str, Any]],
    target: ElementRef,
) -> tuple[str, dict[str, Any], float] | None:
    """Best live match for a recorded target.

    Returns ``(source, element, score)`` where source is ``"controls"`` or
    ``"vision"``. Controls keep their UIA ``index``; vision elements carry a
    screenshot-space ``frame`` used for real-input clicks.
    """
    best: tuple[str, dict[str, Any], float] | None = None
    for control in controls:
        if target.role and control.get("role") == target.role:
            score = max(_name_score(target.name, control.get("name", "")), 0.5)
        else:
            score = _name_score(target.name, control.get("name", ""))
        if score >= _MATCH_THRESHOLD and (best is None or score > best[2]):
            best = ("controls", control, score)
    for element in vision_elements:
        score = max(
            _name_score(target.name, element.get("text", "")),
            _name_score(target.name, element.get("role", "")),
        )
        if score >= _MATCH_THRESHOLD and (best is None or score > best[2]):
            best = ("vision", element, score)
    return best


class ReplayRunner:
    """Execute a recording against a facade instance (uses its state gates)."""

    def __init__(
        self,
        server: LeanComputerUse,
        confirm: ConfirmFn | None = None,
        yes: bool = False,
        memory: Any | None = None,
    ) -> None:
        self.server = server
        self.confirm = confirm or default_confirm
        self.yes = yes
        self.memory = memory  # optional Memory for execution feedback

    def run(
        self,
        recording: Recording,
        dry_run: bool = True,
        on_step: Callable[[int, ReplayOutcome], None] | None = None,
    ) -> ReplayRunResult:
        metrics_before = self.server.metrics_summary()
        outcomes: list[ReplayOutcome] = []
        for index, step in enumerate(recording.steps, start=1):
            if dry_run:
                outcome = ReplayOutcome(
                    step=index,
                    action=step.action,
                    resolution="preview",
                    ok=True,
                    confirmed=True,
                )
            else:
                outcome = self._execute(index, step, recording.app)
            outcomes.append(outcome)
            if on_step is not None:
                on_step(index, outcome)
        metrics_after = self.server.metrics_summary()
        return ReplayRunResult(
            ok=all(item.ok for item in outcomes),
            completed=sum(1 for item in outcomes if item.ok),
            outcomes=outcomes,
            metrics_before=metrics_before,
            metrics_after=metrics_after,
        )

    def _execute(self, index: int, step: RecordedStep, app: str) -> ReplayOutcome:
        if step.action not in CONTENT_ACTIONS and step.action != "focus":
            return ReplayOutcome(
                step=index,
                action=step.action,
                resolution="error",
                ok=False,
                error=f"unsupported recorded action: {step.action}",
            )
        if step.action == "focus":
            self.server.upstream.focus_window(app)
            if self.memory is not None:
                self.memory.record_use(app, step, True)
            return ReplayOutcome(
                step=index,
                action=step.action,
                resolution="window",
                ok=True,
                confirmed=True,
            )
        confirmed = self.yes or self.confirm(index, step)
        if not confirmed:
            return ReplayOutcome(
                step=index,
                action=step.action,
                resolution="error",
                ok=False,
                confirmed=False,
                error="declined",
            )
        intent = step.target.name if step.target and step.target.name else "interact"
        state = self.server.observe(
            app,
            intent=intent,
            output_mode="controls",
            preset="control",
            vision="auto",
            max_results=40,
        )
        text_chars = len(json.dumps(state, ensure_ascii=False))
        nodes = len(state.get("controls", []))
        image_bytes = int((state.get("screenshot") or {}).get("bytes", 0))
        if not state.get("ok"):
            return ReplayOutcome(
                step=index,
                action=step.action,
                resolution="error",
                ok=False,
                confirmed=True,
                error=str(state.get("error", "observe failed")),
                text_chars=text_chars,
                nodes=nodes,
            )
        state_id = state["state_id"]
        if step.target is not None:
            matched = match_target(
                state.get("controls", []),
                (state.get("vision") or {}).get("elements", []),
                step.target,
            )
        else:
            matched = None
        if matched is not None:
            source, element, _score = matched
            if source == "controls":
                result = self.server.act(
                    app,
                    state_id,
                    step.action,
                    element_index=element["index"],
                    value=step.value,
                    key=step.key,
                    direction=step.direction,
                    pages=step.pages,
                    commit=step.commit,
                )
                resolution = "element"
            else:
                frame = element.get("frame") or {}
                cx = frame.get("x", 0) + frame.get("width", 0) / 2
                cy = frame.get("y", 0) + frame.get("height", 0) / 2
                result = self.server.act(
                    app,
                    state_id,
                    "click",
                    click_method="real",
                    x=int(cx),
                    y=int(cy),
                    mouse_button="left",
                    commit=step.commit,
                )
                resolution = "vision"
        elif step.action == "click" and step.x is not None:
            self.server.upstream.focus_window(app)
            result = self.server.act(
                app,
                state_id,
                "click",
                click_method="real",
                x=step.x,
                y=step.y,
                mouse_button="left",
                commit=step.commit,
            )
            resolution = "coords"
        elif step.action in {"type_text", "press_key"}:
            result = self.server.act(
                app,
                state_id,
                step.action,
                value=step.value,
                key=step.key,
                commit=step.commit,
            )
            resolution = "focus"
        else:
            result = {
                "ok": False,
                "error": "ELEMENT_NOT_FOUND",
                "message": f"recorded target {step.target} is not in the live tree and "
                "no coordinate fallback exists for this action",
            }
            resolution = "error"
        if self.memory is not None:
            effect: list[str] = []
            if result.get("ok"):
                delta = result.get("delta") or {}
                for node in delta.get("added", []):
                    if node.get("name"):
                        effect.append(node["name"])
                for pair in delta.get("changed", []):
                    after = pair.get("after") or {}
                    if after.get("name"):
                        effect.append(after["name"])
            self.memory.record_use(
                app, step, bool(result.get("ok")), effect_names=effect or None
            )
        return ReplayOutcome(
            step=index,
            action=step.action,
            resolution=resolution,
            ok=bool(result.get("ok")),
            confirmed=True,
            error=result.get("error") if not result.get("ok") else None,
            text_chars=text_chars,
            image_bytes=image_bytes,
            nodes=nodes,
        )
