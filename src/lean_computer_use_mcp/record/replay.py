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
    stale_retries: int = 0


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
        max_stale_retries: int = 3,
    ) -> None:
        self.server = server
        self.confirm = confirm or default_confirm
        self.yes = yes
        self.memory = memory  # optional Memory for execution feedback
        # Hard cap for automatic STALE_STATE recovery: each retry re-observes
        # (same preset/vision) and re-executes the step with the fresh
        # state_id; beyond this the step (and thus the run) fails.
        self.max_stale_retries = max_stale_retries

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
        outcome = self._attempt(index, step, app)
        retries = 0
        while (
            not outcome.ok
            and outcome.error == "STALE_STATE"
            and retries < self.max_stale_retries
        ):
            retries += 1
            # Re-observe (same preset/vision) and retry with the fresh
            # state_id; transient failures get a chance without touching
            # the confirmation policy.
            outcome = self._attempt(index, step, app, record_memory=False)
        outcome.stale_retries = retries
        return outcome

    def _press_key_with_recovery(
        self, app: str, state_id: str, key: str, commit: bool
    ) -> dict[str, Any]:
        """One press_key with capped re-observe recovery on STALE_STATE."""
        result = self.server.act(app, state_id, "press_key", key=key, commit=commit)
        retries = 0
        while (
            not result.get("ok")
            and result.get("error") == "STALE_STATE"
            and retries < self.max_stale_retries
        ):
            retries += 1
            state = self.server.observe(
                app,
                intent="interact",
                output_mode="controls",
                preset="control",
                vision="auto",
                max_results=40,
            )
            if not state.get("ok"):
                return result
            result = self.server.act(
                app, state["state_id"], "press_key", key=key, commit=commit
            )
        return result

    def _attempt(
        self,
        index: int,
        step: RecordedStep,
        app: str,
        record_memory: bool = True,
    ) -> ReplayOutcome:
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
        if (
            step.action == "drag"
            and step.x is not None
            and step.y is not None
            and step.to_x is not None
            and step.to_y is not None
        ):
            # Drags always replay by coordinates: the facade's drag action
            # requires all four from_*/to_* values and has no element-index
            # path (timeline/upload interactions are custom-rendered).
            self.server.upstream.focus_window(app)
            result = self.server.act(
                app,
                state_id,
                "drag",
                from_x=step.x,
                from_y=step.y,
                to_x=step.to_x,
                to_y=step.to_y,
                commit=step.commit,
            )
            resolution = "coords"
        elif matched is not None:
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
        elif step.action == "type_text" and not step.value and step.ime_keys:
            # IME composition sampling failed to recover text: replay the
            # original key sequence (letters + commit keys) - semantically
            # equivalent for Chinese input. Each key gets the same capped
            # STALE_STATE recovery as a content step.
            current_id = state_id
            result = {"ok": True}
            for key in step.ime_keys:
                result = self._press_key_with_recovery(app, current_id, key, step.commit)
                if not result.get("ok"):
                    break
                current_id = result.get("state_id") or current_id
            resolution = "focus"
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
            if step.action == "drag":
                message = "drag step has no usable from/to coordinates"
            else:
                message = (
                    f"recorded target {step.target} is not in the live tree and "
                    "no coordinate fallback exists for this action"
                )
            result = {
                "ok": False,
                "error": "ELEMENT_NOT_FOUND",
                "message": message,
            }
            resolution = "error"
        if self.memory is not None and record_memory:
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
