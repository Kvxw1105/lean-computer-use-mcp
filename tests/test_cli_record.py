"""Record CLI: post-stop step summary and IME capture warning."""

from __future__ import annotations

import threading
from types import SimpleNamespace

from lean_computer_use_mcp.cli import _cmd_record, _print_recording_summary
from lean_computer_use_mcp.record.model import (
    RecordedStep,
    Recording,
    RecordingMetrics,
)


def _recording(ime_value: str | None = "hello world", ime_keys=None) -> Recording:
    steps = [
        RecordedStep(action="focus", window_title="ChatGPT"),
        RecordedStep(
            action="click",
            window_title="ChatGPT",
            x=150,
            y=250,
            target=None,
        ),
        RecordedStep(
            action="type_text",
            window_title="ChatGPT",
            value=ime_value,
            ime_text=ime_value,
            ime_keys=ime_keys or ["n", "i", "hao"],
        ),
    ]
    return Recording(
        name="demo",
        app="ChatGPT",
        description="demo flow",
        started_at=0.0,
        steps=steps,
        metrics=RecordingMetrics(events=3, steps=3, duration_ms=1234),
    )


def test_summary_lists_every_step(capsys):
    _print_recording_summary(_recording())
    out = capsys.readouterr().out
    assert "1. Bring ChatGPT to the foreground" in out
    assert "2. Click at (150, 250)" in out
    assert '3. Type \'hello world\' into' in out
    assert "Warning: type_text" not in out


def test_summary_warns_when_ime_text_missing(capsys):
    _print_recording_summary(_recording(ime_value=None, ime_keys=["n", "i", "hao"]))
    out = capsys.readouterr().out
    assert "3. Type IME sequence" in out
    assert "Warning: type_text step #3 has no captured composed text" in out


def test_cmd_record_prints_summary_and_next(monkeypatch, tmp_path, capsys):
    recording = _recording()
    state = {"stopped": False}

    class FakeRecorder:
        def __init__(self, **kwargs):
            # Pre-set: _cmd_record waits on stop_event before calling stop().
            self.stop_event = threading.Event()
            self.stop_event.set()

        def start(self):
            pass

        def stop(self):
            state["stopped"] = True
            return recording

    out_path = str(tmp_path / "demo.json")
    args = SimpleNamespace(
        fake=True,
        app="ChatGPT",
        out=out_path,
        seconds=0.0,
        snapshot_interval=60.0,
        description="demo flow",
        upstream=None,
        upstream_binary=None,
        metrics_path=None,
        no_overlay=True,
        state_ttl_seconds=None,
        act_overlay=False,
    )
    monkeypatch.setattr(
        "lean_computer_use_mcp.cli.build_upstream", lambda settings, fake: object()
    )
    monkeypatch.setattr("lean_computer_use_mcp.cli.Recorder", FakeRecorder)
    monkeypatch.setattr(
        "lean_computer_use_mcp.record.overlay.make_overlay",
        lambda enabled: SimpleNamespace(show=lambda: None, hide=lambda: None),
    )
    assert _cmd_record(args) == 0
    assert state["stopped"] is True
    out = capsys.readouterr().out
    assert "Recorded 3 steps (1234 ms)" in out
    assert "1. Bring ChatGPT to the foreground" in out
    assert "Next: lean-computer-use compile --in" in out
