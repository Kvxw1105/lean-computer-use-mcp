"""Recorder live step stream + action-triggered snapshots."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from lean_computer_use_mcp.record.model import InputEvent
from lean_computer_use_mcp.record.recorder import Recorder
from lean_computer_use_mcp.upstream.fake_client import FakeUpstreamClient

FIXTURES = Path(__file__).parent.parent / "examples" / "fixtures"
RECT = (100, 200, 1100, 900)
TITLE = "ChatGPT"


class FakeHook:
    def __init__(self):
        self.events = []
        self.stop_event = threading.Event()
        self.on_event = None

    def start(self):
        pass

    def stop(self):
        self.stop_event.set()


class FakeForeground:
    def current(self):
        return SimpleNamespace(window_title=TITLE, window_pid=15332, rect=RECT)


def _mouse_down(x: int = 150, y: int = 250) -> InputEvent:
    return InputEvent(
        ts=time.time(),
        kind="mouse_down",
        x=x,
        y=y,
        button="left",
        window_title=TITLE,
        window_pid=15332,
        window_rect=RECT,
    )


def _key_down(vk: int = 0x41) -> InputEvent:
    return InputEvent(
        ts=time.time(),
        kind="key_down",
        vk=vk,
        window_title=TITLE,
        window_pid=15332,
        window_rect=RECT,
    )


def _recorder(on_steps=None, snapshot_interval: float = 60.0) -> tuple[Recorder, FakeHook]:
    hook = FakeHook()
    recorder = Recorder(
        upstream=FakeUpstreamClient(FIXTURES),
        app=TITLE,
        snapshot_interval=snapshot_interval,
        hook=hook,
        foreground=FakeForeground(),
        on_steps=on_steps,
    )
    return recorder, hook


def _emit(hook: FakeHook, event: InputEvent) -> None:
    """Simulate the real hook path: append the event, then notify the recorder."""
    hook.events.append(event)
    hook.on_event(event)


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _wait_for_snapshot(recorder: Recorder, timeout: float = 5.0) -> bool:
    return _wait_until(lambda: recorder.snapshot_count >= 1, timeout)


def test_live_steps_published_on_hook_event():
    published: list = []
    recorder, hook = _recorder(on_steps=lambda steps: published.extend(steps))
    recorder.start()
    try:
        _emit(hook, _mouse_down(150, 250))
        assert _wait_until(lambda: any(step.action == "click" for step in published))
    finally:
        recorder.stop()


def test_live_publish_is_incremental():
    seen: list[int] = []
    recorder, hook = _recorder(on_steps=lambda steps: seen.append(len(steps)))
    recorder.start()
    try:
        _emit(hook, _mouse_down(150, 250))
        _emit(hook, _mouse_down(300, 350))
        assert _wait_until(lambda: len(seen) >= 2)
        # Each publish carries only the newly recognized steps: two clicks
        # arrive in two batches of one, never duplicated.
        assert sum(seen) == 2
    finally:
        recorder.stop()


def test_action_snapshot_triggered_by_mouse_down():
    recorder, hook = _recorder()
    recorder.start()
    try:
        assert _wait_for_snapshot(recorder)
        before = recorder.snapshot_count
        _emit(hook, _mouse_down(150, 250))
        assert _wait_until(lambda: recorder.snapshot_count >= before + 1)
    finally:
        recorder.stop()


def test_typing_does_not_trigger_snapshot():
    recorder, hook = _recorder()
    recorder.start()
    try:
        assert _wait_for_snapshot(recorder)
        count = recorder.snapshot_count
        _emit(hook, _key_down())
        time.sleep(1.0)
        assert recorder.snapshot_count == count
    finally:
        recorder.stop()


def test_action_snapshot_throttled():
    recorder, hook = _recorder()
    recorder._on_hook_event(_mouse_down(150, 250))
    stamp = recorder._last_action_snapshot_at
    assert stamp > 0
    recorder._on_hook_event(_mouse_down(160, 260))
    assert recorder._last_action_snapshot_at == stamp
