from __future__ import annotations
import base64
import json
import time
from typing import Any
from mcp.server.fastmcp import FastMCP
from lean_computer_use_mcp.config import Settings
from lean_computer_use_mcp.diff.engine import diff
from lean_computer_use_mcp.errors import (
    AppNotFoundError,
    LeanComputerUseError,
    StaleStateError,
)
from lean_computer_use_mcp.media.cache import ImageCache
from lean_computer_use_mcp.metrics.logger import MetricsLogger
from lean_computer_use_mcp.models import StateSnapshot
from lean_computer_use_mcp.parse.topk import filter_controls
from lean_computer_use_mcp.parse.tree_parser import detect_truncation, parse_state
from lean_computer_use_mcp.state.fingerprint import fingerprint
from lean_computer_use_mcp.state.store import StateStore
from lean_computer_use_mcp.upstream.base import UpstreamClient
from lean_computer_use_mcp.vision.base import (
    GroundingResult,
    VisionConfig,
    VisionEngine,
    VisionEngineUnavailable,
)
from lean_computer_use_mcp.vision.ocr import build_engine

_BUDGETS = {
    "control": (80, 8, 160),
    "read": (160, 10, 600),
    "expand": (320, 16, 900),
    "deep": (800, 28, 1200),
}
_ACTION_TOOLS = {
    "click",
    "drag",
    "set_value",
    "scroll",
    "type_text",
    "press_key",
    "secondary_action",
}
_VALID_OUTPUT_MODES = {"controls", "reading", "visual", "full"}


class LeanComputerUse:
    def __init__(
        self, upstream: UpstreamClient, settings: Settings | None = None
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.upstream = upstream
        self.store = StateStore(
            self.settings.state_ttl_seconds, self.settings.state_max_entries
        )
        self.metrics = MetricsLogger(self.settings.metrics_path)
        self.images = ImageCache(self.settings.image_cache_root)
        self._vision: VisionEngine | None = None
        self._vision_upgrade: VisionEngine | None = None
        self._vision_upgrade_unavailable = False
        self._last_vision_upgrade_at = 0.0

    def find_app(self, query: str | None = None) -> dict[str, Any]:
        started = time.time()
        apps = [
            app
            for app in self.upstream.list_apps()
            if app.running and app.visible_windows > 0
        ]
        if query:
            needle = query.lower()
            apps = [app for app in apps if needle in app.name.lower()]
        response = {"apps": [app.__dict__ for app in apps]}
        self.metrics.record(
            tool="cu_find_app",
            app=None,
            text_chars=len(json.dumps(response, ensure_ascii=False)),
            image_bytes=0,
            image_payloads=0,
            nodes=len(apps),
            truncated=False,
            latency_ms=_elapsed_ms(started),
            error=None,
        )
        return response

    def observe(
        self,
        app: str,
        intent: str = "",
        output_mode: str = "controls",
        include_screenshot: bool = False,
        max_results: int = 20,
        preset: str | None = None,
        vision: str = "auto",
    ) -> dict[str, Any]:
        started = time.time()
        try:
            if output_mode not in _VALID_OUTPUT_MODES:
                return {
                    "ok": False,
                    "error": "UNSUPPORTED_ACTION",
                    "message": f"unknown output_mode: {output_mode}",
                }
            nodes, depth, text = self._budget(output_mode, preset)
            raw, image = self.upstream.get_app_state(app, nodes, depth, text)
            return self._observe_from_raw(
                app,
                raw,
                image,
                output_mode,
                include_screenshot,
                max_results,
                intent,
                budget=(nodes, depth, text),
                latency_ms=_elapsed_ms(started),
                vision=vision,
            )
        except AppNotFoundError as exc:
            self._record_observe_error(started, app, output_mode, exc.code)
            return {"ok": False, "error": exc.code, "message": str(exc)}
        except LeanComputerUseError as exc:
            self._record_observe_error(started, app, output_mode, exc.code)
            return {"ok": False, "error": exc.code, "message": str(exc)}

    def act(
        self,
        app: str,
        state_id: str,
        action: str,
        element_index: str | None = None,
        value: str | None = None,
        key: str | None = None,
        direction: str | None = None,
        pages: float = 1.0,
        click_method: str | None = None,
        mouse_button: str | None = None,
        secondary_action: str | None = None,
        x: int | None = None,
        y: int | None = None,
        from_x: int | None = None,
        from_y: int | None = None,
        to_x: int | None = None,
        to_y: int | None = None,
        commit: bool = False,
    ) -> dict[str, Any]:
        started = time.time()
        try:
            if action not in _ACTION_TOOLS:
                return {
                    "ok": False,
                    "error": "UNSUPPORTED_ACTION",
                    "message": f"unknown action: {action}",
                }
            if action == "click":
                has_index = element_index is not None
                has_coords = x is not None and y is not None
                if has_index and has_coords:
                    return {
                        "ok": False,
                        "error": "AMBIGUOUS_TARGET",
                        "message": "click accepts element_index OR x/y, not both",
                    }
                if not has_index and not has_coords:
                    return {
                        "ok": False,
                        "error": "ELEMENT_NOT_FOUND",
                        "message": "click requires element_index or x/y",
                    }
            if action == "click" and click_method == "real":
                if has_index or not has_coords:
                    return {
                        "ok": False,
                        "error": "ELEMENT_NOT_FOUND",
                        "message": "click_method 'real' requires x/y only (element_index is not supported)",
                    }
            if action == "drag":
                missing = [
                    name
                    for name, value in (
                        ("from_x", from_x),
                        ("from_y", from_y),
                        ("to_x", to_x),
                        ("to_y", to_y),
                    )
                    if value is None
                ]
                if missing:
                    return {
                        "ok": False,
                        "error": "ELEMENT_NOT_FOUND",
                        "message": f"drag requires {', '.join(missing)}",
                    }
            before = self.store.get(app, state_id)
            nodes, depth, text = before.budget or _BUDGETS["control"]
            # Live freshness gate: re-read the window at the snapshot budget and
            # compare fingerprints before any action executes. A changed tree
            # (navigation, modal, window move, focus change) invalidates the plan.
            raw_gate, image_gate = self.upstream.get_app_state(app, nodes, depth, text)
            gate = self._build_snapshot(
                app, raw_gate, image_gate, budget=(nodes, depth, text)
            )
            if gate.fingerprint != before.fingerprint:
                self.store.put(gate)
                self._record_act_error(
                    started,
                    app,
                    action,
                    "STALE_STATE",
                    commit,
                    nodes=len(gate.controls),
                )
                return {
                    "ok": False,
                    "error": "STALE_STATE",
                    "current_state_id": gate.state_id,
                    "message": (
                        f"State for app {app!r} changed since snapshot {before.state_id}; "
                        "re-observe before acting."
                    ),
                }
            args = self._build_action_args(
                action,
                app=app,
                element_index=element_index,
                value=value,
                key=key,
                direction=direction,
                pages=pages,
                click_method=click_method,
                mouse_button=mouse_button,
                secondary_action=secondary_action,
                x=x,
                y=y,
                from_x=from_x,
                from_y=from_y,
                to_x=to_x,
                to_y=to_y,
            )
            if action == "click" and click_method == "real":
                raw, image = self._real_click(
                    app, x, y, mouse_button, nodes, depth, text
                )
            else:
                raw, image, _ = self.upstream.act_with_refresh(
                    app, action, args, nodes, depth, text
                )
            after = self._build_snapshot(app, raw, image, budget=(nodes, depth, text))
            self.store.put(after)
            delta = diff(gate, after)
            self.metrics.record(
                tool="cu_act",
                app=app,
                action=action,
                text_chars=len(raw),
                image_bytes=len(image) if image else 0,
                image_payloads=0,
                nodes=len(after.controls),
                truncated=after.truncated_tree or after.truncated_text,
                latency_ms=_elapsed_ms(started),
                error=None,
                commit=commit,
            )
            return {
                "ok": True,
                "state_id": after.state_id,
                "action": action,
                "state_changed": delta.window_title_changed
                or bool(delta.added or delta.removed or delta.changed),
                "delta": delta.to_dict(),
            }
        except StaleStateError as exc:
            self._record_act_error(started, app, action, "STALE_STATE", commit)
            return {
                "ok": False,
                "error": exc.code,
                "current_state_id": exc.current_state_id,
                "message": str(exc),
            }
        except LeanComputerUseError as exc:
            error_code = "COMMIT_UNCERTAIN" if commit else exc.code
            self._record_act_error(started, app, action, error_code, commit)
            return {"ok": False, "error": error_code, "message": str(exc)}

    def _record_observe_error(
        self,
        started: float,
        app: str,
        output_mode: str,
        error_code: str | None,
    ) -> None:
        self.metrics.record(
            tool="cu_observe",
            app=app,
            output_mode=output_mode,
            text_chars=0,
            image_bytes=0,
            image_payloads=0,
            nodes=0,
            truncated=False,
            latency_ms=_elapsed_ms(started),
            error=error_code,
        )

    def _record_act_error(
        self,
        started: float,
        app: str,
        action: str,
        error_code: str | None,
        commit: bool,
        nodes: int = 0,
    ) -> None:
        self.metrics.record(
            tool="cu_act",
            app=app,
            action=action,
            text_chars=0,
            image_bytes=0,
            image_payloads=0,
            nodes=nodes,
            truncated=False,
            latency_ms=_elapsed_ms(started),
            error=error_code,
            commit=commit,
        )

    def batch(
        self,
        app: str,
        state_id: str,
        steps: list[dict[str, Any]],
        max_actions: int = 3,
        fail_fast: bool = True,
    ) -> dict[str, Any]:
        max_actions = min(max_actions, 3)
        current_state_id = state_id
        results: list[dict[str, Any]] = []
        for index, step in enumerate(steps[:max_actions], start=1):
            result = self.act(app=app, state_id=current_state_id, **step)
            results.append(
                {
                    "step": index,
                    "ok": result.get("ok", False),
                    "action": step.get("action"),
                    "error": result.get("error"),
                    "state_id": result.get("state_id"),
                }
            )
            if not result.get("ok"):
                if fail_fast:
                    return {
                        "ok": False,
                        "completed": index - 1,
                        "state_id": current_state_id,
                        "results": results,
                        "stopped_reason": result.get("error"),
                    }
                continue
            current_state_id = result.get("state_id")
        return {
            "ok": all(item["ok"] for item in results),
            "completed": len(results),
            "state_id": current_state_id,
            "results": results,
            "stopped_reason": None,
        }

    def metrics_summary(self) -> dict[str, Any]:
        summary = self.metrics.summary()
        summary.update(self.store.stats())
        return summary

    def to_mcp(self) -> FastMCP:
        mcp = FastMCP("lean-computer-use")
        engine = self

        @mcp.tool()
        def cu_find_app(query: str | None = None) -> dict[str, Any]:
            """List running apps with visible windows; optional name filter."""
            return engine.find_app(query)

        @mcp.tool()
        def cu_observe(
            app: str,
            intent: str = "",
            output_mode: str = "controls",
            include_screenshot: bool = False,
            max_results: int = 20,
            preset: str | None = None,
            vision: str = "auto",
        ) -> dict[str, Any]:
            """Compact state read for one app; returns top-K controls and a state_id."""
            return engine.observe(
                app,
                intent,
                output_mode,
                include_screenshot,
                max_results,
                preset,
                vision,
            )

        @mcp.tool()
        def cu_act(
            app: str,
            state_id: str,
            action: str,
            element_index: str | None = None,
            value: str | None = None,
            key: str | None = None,
            direction: str | None = None,
            pages: float = 1.0,
            click_method: str | None = None,
            mouse_button: str | None = None,
            secondary_action: str | None = None,
            x: int | None = None,
            y: int | None = None,
            from_x: int | None = None,
            from_y: int | None = None,
            to_x: int | None = None,
            to_y: int | None = None,
            commit: bool = False,
        ) -> dict[str, Any]:
            """Execute one bounded action against a state_id; stale states are rejected."""
            return engine.act(
                app,
                state_id,
                action,
                element_index,
                value,
                key,
                direction,
                pages,
                click_method,
                mouse_button,
                secondary_action,
                x,
                y,
                from_x,
                from_y,
                to_x,
                to_y,
                commit,
            )

        @mcp.tool()
        def cu_batch(
            app: str,
            state_id: str,
            steps: list[dict[str, Any]],
            max_actions: int = 3,
            fail_fast: bool = True,
        ) -> dict[str, Any]:
            """Run a bounded, fail-fast action sequence against one app."""
            return engine.batch(app, state_id, steps, max_actions, fail_fast)

        @mcp.tool()
        def cu_metrics() -> dict[str, Any]:
            """Return aggregate cost and error metrics for this process."""
            return engine.metrics_summary()

        return mcp

    def _real_click(
        self,
        app: str,
        x: int | None,
        y: int | None,
        mouse_button: str | None,
        nodes: int,
        depth: int,
        text: int | str,
    ) -> tuple[str, bytes | None]:
        """Windows real-input click, then a post snapshot at the same budget."""
        self.upstream.real_input_click(
            app, int(x or 0), int(y or 0), mouse_button or "left"
        )
        time.sleep(0.25)
        return self.upstream.get_app_state(app, nodes, depth, text)

    @staticmethod
    def _build_action_args(
        action: str,
        *,
        app: str,
        element_index: str | None,
        value: str | None,
        key: str | None,
        direction: str | None,
        pages: float,
        click_method: str | None,
        mouse_button: str | None,
        secondary_action: str | None,
        x: int | None = None,
        y: int | None = None,
        from_x: int | None = None,
        from_y: int | None = None,
        to_x: int | None = None,
        to_y: int | None = None,
    ) -> dict[str, Any]:
        """Build upstream args for one action.
        The upstream schemas forbid additional properties, so each action only
        receives the fields it declares.
        """
        if action == "click":
            args = {"app": app}
            if element_index is not None:
                args["element_index"] = element_index
            if x is not None and y is not None:
                args["x"] = x
                args["y"] = y
            if click_method is not None:
                args["click_method"] = click_method
            if mouse_button is not None:
                args["mouse_button"] = mouse_button
            return args
        if action == "drag":
            return {
                "app": app,
                "from_x": from_x,
                "from_y": from_y,
                "to_x": to_x,
                "to_y": to_y,
            }
        if action == "set_value":
            return {"app": app, "element_index": element_index, "value": value}
        if action == "scroll":
            return {
                "app": app,
                "element_index": element_index,
                "direction": direction,
                "pages": pages,
            }
        if action == "type_text":
            return {"app": app, "text": value}
        if action == "press_key":
            return {"app": app, "key": key}
        if action == "secondary_action":
            return {
                "app": app,
                "element_index": element_index,
                "action": secondary_action,
            }
        return {"app": app}

    def _budget(
        self, output_mode: str, preset: str | None
    ) -> tuple[int, int, int | str]:
        if preset is not None:
            if preset not in _BUDGETS:
                raise LeanComputerUseError(f"unknown preset: {preset}")
            return _BUDGETS[preset]
        return _BUDGETS["control" if output_mode in {"controls", "visual"} else "read"]

    def _vision_engine(self) -> VisionEngine:
        """Lazily build the configured vision engine.
        Raises ``VisionEngineUnavailable`` when no engine is configured or its
        optional dependencies are missing; callers degrade instead of crashing.
        """
        if self._vision is None:
            self._vision = build_engine(
                VisionConfig(
                    engine=self.settings.vision_engine,
                    api_base=self.settings.vision_api_base,
                    api_key=self.settings.vision_api_key,
                    model=self.settings.vision_model,
                )
            )
        return self._vision
    def _upgrade_vision_engine(self) -> VisionEngine | None:
        """Lazily build the escalation engine (for example ``llm``) when configured."""
        engine_name = self.settings.vision_upgrade_engine
        if not engine_name or engine_name == "none" or self._vision_upgrade_unavailable:
            return None
        if self._vision_upgrade is None:
            try:
                self._vision_upgrade = build_engine(
                    VisionConfig(
                        engine=engine_name,
                        api_base=self.settings.vision_api_base,
                        api_key=self.settings.vision_api_key,
                        model=self.settings.vision_model,
                    )
                )
            except VisionEngineUnavailable:
                self._vision_upgrade_unavailable = True
                return None
        return self._vision_upgrade

    def _escalate_vision(
        self,
        image: bytes,
        base: GroundingResult,
        intent: str,
    ) -> tuple[GroundingResult | None, dict[str, Any] | None]:
        """Auto escalation with throttling: upgrade engine when the base table is thin.

        Returns ``(upgrade_result, suppression_info)``. Exactly one item is
        non-``None`` when an escalation decision was made; both are ``None`` when
        escalation does not apply. Suppression is reported (cooldown or
        unavailable engine) so callers can surface why the base table was kept.
        """
        if len(base.elements) >= self.settings.vision_upgrade_min_elements:
            return None, None
        engine_name = self.settings.vision_upgrade_engine
        if not engine_name or engine_name == "none":
            return None, None
        if self._vision_upgrade_unavailable:
            return None, {"attempted": False, "suppressed": True, "reason": "unavailable"}
        engine = self._upgrade_vision_engine()
        if engine is None:
            return None, None
        remaining = (
            self._last_vision_upgrade_at
            + self.settings.vision_upgrade_cooldown_seconds
            - time.monotonic()
        ) if self._last_vision_upgrade_at > 0 else -1.0
        if remaining > 0:
            return None, {
                "attempted": False,
                "suppressed": True,
                "reason": "cooldown",
                "cooldown_seconds": round(remaining, 1),
            }
        try:
            result = engine.ground(image, hint=intent)
        except VisionEngineUnavailable as exc:
            self._vision_upgrade_unavailable = True
            return None, {"attempted": True, "suppressed": True, "reason": "unavailable", "error": str(exc)}
        self._last_vision_upgrade_at = time.monotonic()
        return result, None


    def _observe_from_raw(
        self,
        app: str,
        raw: str,
        image: bytes | None,
        output_mode: str,
        include_screenshot: bool,
        max_results: int,
        intent: str,
        budget: tuple[int, int, int | str],
        latency_ms: int,
        vision: str = "auto",
    ) -> dict[str, Any]:
        title, focused, controls = parse_state(raw)
        tree_truncated, text_truncated = detect_truncation(raw)
        snapshot = StateSnapshot(
            app=app,
            window_title=title,
            focused_element=focused,
            controls=controls,
            raw_text=raw,
            text_chars=len(raw),
            truncated_tree=tree_truncated,
            truncated_text=text_truncated,
            budget=budget,
        )
        snapshot.fingerprint = fingerprint(snapshot)
        vision_out: dict[str, Any] = {
            "engine": None,
            "triggered": False,
            "reason": None,
            "elements": [],
            "image_bytes": 0,
            "latency_ms": 0,
        }
        if (
            image
            and vision in {"auto", "on"}
            and (vision == "on" or len(controls) <= 2)
        ):
            try:
                engine = self._vision_engine()
                result = engine.ground(image)
                vision_out = {
                    "engine": result.engine,
                    "triggered": True,
                    "reason": "requested" if vision == "on" else "empty_tree",
                    "elements": [element.to_dict() for element in result.elements],
                    "image_bytes": result.image_bytes,
                    "latency_ms": result.latency_ms,
                }
                snapshot.vision_elements = result.elements
                if vision == "auto":
                    upgrade_result, suppressed = self._escalate_vision(image, result, intent)
                    if suppressed is not None:
                        vision_out["upgrade"] = suppressed
                    elif upgrade_result is not None:
                        vision_out = {
                            "engine": upgrade_result.engine,
                            "triggered": True,
                            "reason": "auto_escalate",
                            "escalated": True,
                            "escalated_from": result.engine,
                            "elements": [element.to_dict() for element in upgrade_result.elements],
                            "image_bytes": vision_out["image_bytes"] + upgrade_result.image_bytes,
                            "latency_ms": vision_out["latency_ms"] + upgrade_result.latency_ms,
                            "upgrade": {
                                "attempted": True,
                                "suppressed": False,
                                "engine": upgrade_result.engine,
                                "base_engine": result.engine,
                                "base_elements": len(result.elements),
                            },
                        }
                        snapshot.vision_elements = upgrade_result.elements
            except VisionEngineUnavailable as exc:
                vision_out["error"] = str(exc)
        self.store.put(snapshot)
        selected = filter_controls(
            controls, output_mode=output_mode, intent=intent, max_results=max_results
        )
        screenshot = {"path": None, "bytes": 0, "data": None}
        image_payloads = 0
        if image:
            path = self.images.store_bytes(image)
            screenshot["path"] = str(path)
            screenshot["bytes"] = len(image)
            if include_screenshot or output_mode == "visual":
                screenshot["data"] = base64.b64encode(image).decode("ascii")
                image_payloads = 1
        self.metrics.record(
            tool="cu_observe",
            app=app,
            output_mode=output_mode,
            text_chars=len(raw),
            image_bytes=len(image) if image else 0,
            image_payloads=image_payloads,
            nodes=len(controls),
            truncated=tree_truncated or text_truncated,
            latency_ms=latency_ms,
            vision_calls=(1 if vision_out.get("triggered") else 0) + (1 if vision_out.get("escalated") else 0),
            vision_image_bytes=vision_out.get("image_bytes") or 0,
            vision_latency_ms=vision_out.get("latency_ms") or 0,
            vision_elements=len(vision_out.get("elements") or []),
            vision_upgrades=1 if vision_out.get("escalated") else 0,
            error=None,
        )
        return {
            "ok": True,
            "state_id": snapshot.state_id,
            "app": app,
            "window_title": title,
            "focused_element": focused,
            "controls": [control.to_dict() for control in selected],
            "truncated": {"tree": tree_truncated, "text": text_truncated},
            "screenshot": screenshot,
            "vision": vision_out,
        }

    def _build_snapshot(
        self,
        app: str,
        raw: str,
        image: bytes | None,
        budget: tuple[int, int, int | str] | None = None,
    ) -> StateSnapshot:
        title, focused, controls = parse_state(raw)
        tree_truncated, text_truncated = detect_truncation(raw)
        snapshot = StateSnapshot(
            app=app,
            window_title=title,
            focused_element=focused,
            controls=controls,
            raw_text=raw,
            text_chars=len(raw),
            truncated_tree=tree_truncated,
            truncated_text=text_truncated,
            budget=budget,
        )
        snapshot.fingerprint = fingerprint(snapshot)
        if image:
            path = self.images.store_bytes(image)
            snapshot.image_path = str(path)
            snapshot.image_bytes = len(image)
        return snapshot


def _elapsed_ms(started: float) -> int:
    return round((time.time() - started) * 1000)
