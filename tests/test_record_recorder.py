"""Recorder session: hook + sampler + metrics, fully injectable."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from lean_computer_use_mcp.metrics.logger import MetricsLogger
from lean_computer_use_mcp.record.model import InputEvent, Recording
from lean_computer_use_mcp.record.recorder import Recorder
from lean_computer_use_mcp.upstream.fake_client import FakeUpstreamClient

FIXTURES = Path(__file__).parent.parent / "examples" / "fixtures"
RECT = (100, 200, 1100, 900)


class FakeHook:
    def __init__(self, events=None):
        self.events = list(events or [])
        self.stop_event = threading.Event()
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True
        self.stop_event.set()


class FakeForeground:
    def current(self):
        return SimpleNamespace(window_title="ChatGPT", window_pid=15332, rect=RECT)


def _events() -> list[InputEvent]:
    now = time.time()
    return [
        InputEvent(
            ts=now,
            kind="mouse_down",
            x=100 + 2532 + 60,
            y=200 + 13 + 48,
            button="left",
            window_title="ChatGPT",
            window_pid=15332,
            window_rect=RECT,
        ),
        InputEvent(
            ts=now + 0.2,
            kind="key_down",
            vk=0x41,
            window_title="ChatGPT",
            window_pid=15332,
            window_rect=RECT,
        ),
    ]


def _wait_for_snapshot(recorder: Recorder, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if recorder.snapshot_count >= 1:
            return True
        time.sleep(0.05)
    return False


def test_recorder_captures_steps_and_metrics(tmp_path):
    metrics_path = str(tmp_path / "metrics.jsonl")
    hook = FakeHook(_events())
    recorder = Recorder(
        upstream=FakeUpstreamClient(FIXTURES),
        app="ChatGPT",
        snapshot_interval=0.5,
        hook=hook,
        foreground=FakeForeground(),
        metrics=MetricsLogger(metrics_path),
    )
    recorder.start()
    assert _wait_for_snapshot(recorder)
    recording = recorder.stop()
    assert hook.started and hook.stopped
    assert recording.steps[0].action == "focus"
    actions = [step.action for step in recording.steps[1:]]
    assert "click" in actions
    assert "type_text" in actions
    assert recording.metrics.events == 2
    assert recording.metrics.snapshots >= 1
    assert recording.metrics.nodes > 0
    assert recording.metrics.image_bytes == 0
    rows = [
        json.loads(line)
        for line in Path(metrics_path).read_text(encoding="utf-8").splitlines()
    ]
    record_rows = [row for row in rows if row["tool"] == "cu_record"]
    assert len(record_rows) == 1
    assert record_rows[0]["text_chars"] > 0
    assert record_rows[0]["image_bytes"] == 0
    assert record_rows[0]["nodes"] == recording.metrics.nodes


def test_recorder_empty_session_still_produces_focus_step(tmp_path):
    hook = FakeHook()
    recorder = Recorder(
        upstream=FakeUpstreamClient(FIXTURES),
        app="ChatGPT",
        snapshot_interval=0.5,
        hook=hook,
        foreground=FakeForeground(),
        metrics=MetricsLogger(str(tmp_path / "metrics.jsonl")),
    )
    recorder.start()
    assert _wait_for_snapshot(recorder)
    recording = recorder.stop()
    assert [step.action for step in recording.steps] == ["focus"]


def test_recording_round_trip_json(tmp_path):
    hook = FakeHook(_events())
    recorder = Recorder(
        upstream=FakeUpstreamClient(FIXTURES),
        app="ChatGPT",
        snapshot_interval=0.5,
        hook=hook,
        foreground=FakeForeground(),
    )
    recorder.start()
    assert _wait_for_snapshot(recorder)
    recording = recorder.stop()
    path = tmp_path / "recording.json"
    recording.save(path)
    loaded = Recording.load(path)
    assert loaded.name == recording.name
    assert len(loaded.steps) == len(recording.steps)
    assert loaded.steps[1].matched is True  # click inside the Text button frame
    assert loaded.metrics.snapshots == recording.metrics.snapshots
