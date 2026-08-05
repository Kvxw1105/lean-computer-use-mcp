"""Coordinate one recording session: input hook plus periodic element snapshots.

The recorder samples the app's accessibility tree a few times per minute
(never screenshots) so clicks and wheel actions can be attributed to semantic
elements at record time. Everything stored is text: parsed controls plus
character/byte counts for metrics. The final :class:`Recording` is a JSON
artifact that can be compiled into a SKILL.md and replayed.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Protocol

from lean_computer_use_mcp.metrics.logger import MetricsLogger
from lean_computer_use_mcp.parse.tree_parser import parse_state
from lean_computer_use_mcp.record.model import (
    ElementTable,
    InputEvent,
    Recording,
    RecordingMetrics,
    RecordedStep,
)
from lean_computer_use_mcp.record.steps import build_steps
from lean_computer_use_mcp.upstream.base import UpstreamClient

#: Snapshot budget: a mid-size read preset is enough to capture click targets.
_SNAPSHOT_BUDGET = (160, 10, 600)


class InputHook(Protocol):
    """Injectable hook backend (WinInputHook on Windows, fake in tests)."""

    events: list[InputEvent]
    stop_event: threading.Event

    def start(self) -> None: ...

    def stop(self) -> None: ...


class Foreground(Protocol):
    def current(self) -> Any: ...


class NoopHook:
    """Captures nothing; used by ``--fake`` demos and tests."""

    def __init__(self) -> None:
        self.events: list[InputEvent] = []
        self.stop_event = threading.Event()

    def start(self) -> None:
        return None

    def stop(self) -> None:
        self.stop_event.set()


class NoopForeground:
    """Reports an empty foreground; used with :class:`NoopHook`."""

    def current(self) -> Any:
        return None


class Recorder:
    def __init__(
        self,
        upstream: UpstreamClient,
        app: str,
        snapshot_interval: float = 3.0,
        hook: InputHook | None = None,
        foreground: Foreground | None = None,
        metrics: MetricsLogger | None = None,
        name: str | None = None,
        description: str = "",
    ) -> None:
        self.upstream = upstream
        self.app = app
        self.snapshot_interval = max(0.5, snapshot_interval)
        self.metrics = metrics
        self.name = name or f"{app}-recorded"
        self.description = description
        self._hook: InputHook | None = hook
        self._foreground = foreground
        self._snapshots: list[ElementTable] = []
        self._snapshot_errors = 0
        self._sampler: threading.Thread | None = None
        self._started_at = 0.0

    def _hook_backend(self) -> InputHook:
        if self._hook is None:
            from lean_computer_use_mcp.record.win_hooks import WinInputHook

            self._hook = WinInputHook()
        return self._hook

    @property
    def stop_event(self):
        """Event signaled when recording should stop (hotkey or timer)."""
        return self._hook_backend().stop_event

    @property
    def snapshot_count(self) -> int:
        """Number of element snapshots taken so far (for tests/CLI)."""
        return len(self._snapshots)

    def start(self) -> None:
        hook = self._hook_backend()
        hook.start()
        self._started_at = time.time()
        self._sampler = threading.Thread(
            target=self._sample_loop, name="lean-cu-sampler", daemon=True
        )
        self._sampler.start()

    def stop(self) -> Recording:
        hook = self._hook_backend()
        hook.stop()
        if self._sampler is not None:
            self._sampler.join(timeout=5)
        events = list(hook.events)
        steps = build_steps(events, self._snapshots)
        steps.insert(0, RecordedStep(action="focus", window_title=self.app))
        ended = time.time()
        metrics = RecordingMetrics(
            events=len(events),
            steps=len(steps),
            snapshots=len(self._snapshots),
            snapshot_errors=self._snapshot_errors,
            nodes=sum(len(table.elements) for table in self._snapshots),
        )
        recording = Recording(
            name=self.name,
            app=self.app,
            description=self.description,
            started_at=self._started_at,
            snapshots=list(self._snapshots),
            steps=steps,
            metrics=metrics,
        )
        payload = json.dumps(recording.to_dict(), ensure_ascii=False)
        metrics.text_chars = len(payload)
        metrics.image_bytes = sum(table.image_bytes for table in self._snapshots)
        metrics.duration_ms = round((ended - self._started_at) * 1000)
        recording.metrics = metrics
        if self.metrics is not None:
            self.metrics.record(
                tool="cu_record",
                app=self.app,
                text_chars=len(payload),
                image_bytes=metrics.image_bytes,
                image_payloads=0,
                nodes=metrics.nodes,
                truncated=False,
                latency_ms=metrics.duration_ms,
                error=None,
            )
        return recording

    def _sample_loop(self) -> None:
        hook = self._hook_backend()
        while not hook.stop_event.is_set():
            started = time.time()
            try:
                raw, image = self.upstream.get_app_state(self.app, *_SNAPSHOT_BUDGET)
                title, _focused, controls = parse_state(raw)
                foreground = self._foreground.current() if self._foreground else None
                rect = getattr(foreground, "rect", None)
                self._snapshots.append(
                    ElementTable(
                        ts=time.time(),
                        window_title=title,
                        window_pid=getattr(foreground, "window_pid", 0) or 0,
                        elements=controls,
                        text_chars=len(raw),
                        image_bytes=len(image) if image else 0,
                        window_rect=tuple(rect) if rect else None,
                    )
                )
            except Exception:  # noqa: BLE001 - sampling must never kill the session
                self._snapshot_errors += 1
            elapsed = time.time() - started
            hook.stop_event.wait(max(0.1, self.snapshot_interval - elapsed))
