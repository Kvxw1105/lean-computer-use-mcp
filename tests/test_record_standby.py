"""Standby recording: hotkey parsing, conflict detection, standby loop."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from lean_computer_use_mcp.cli import _cmd_record
from lean_computer_use_mcp.record import standby
from lean_computer_use_mcp.record.model import (
    RecordedStep,
    Recording,
    RecordingMetrics,
)
from lean_computer_use_mcp.record.standby import (
    HotkeyListener,
    describe_hotkey,
    parse_hotkey,
)

_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004


def test_parse_hotkey_combinations():
    assert parse_hotkey("ctrl+shift+space") == (_MOD_CONTROL | _MOD_SHIFT, 0x20)
    assert parse_hotkey("alt+r") == (_MOD_ALT, ord("R"))
    assert parse_hotkey("Ctrl+Shift+F9") == (_MOD_CONTROL | _MOD_SHIFT, 0x70 + 9 - 1)
    assert parse_hotkey("win+7") == (0x0008, ord("7"))
    assert parse_hotkey("shift+up") == (_MOD_SHIFT, 0x26)


def test_parse_hotkey_requires_modifier():
    with pytest.raises(ValueError, match="at least one modifier"):
        parse_hotkey("space")
    with pytest.raises(ValueError, match="at least one modifier"):
        parse_hotkey("a")


def test_parse_hotkey_rejects_unknown_parts():
    with pytest.raises(ValueError, match="unknown modifier"):
        parse_hotkey("super+a")
    with pytest.raises(ValueError, match="unknown key"):
        parse_hotkey("ctrl+clap")
    with pytest.raises(ValueError):
        parse_hotkey("")


def test_describe_hotkey_roundtrip():
    for spec in ("ctrl+shift+space", "alt+r", "ctrl+shift+f9", "win+up"):
        modifiers, vk = parse_hotkey(spec)
        assert parse_hotkey(describe_hotkey(modifiers, vk)) == (modifiers, vk)


class _DllFunc:
    """ctypes-style function object: callable with assignable restype."""

    def __init__(self, impl):
        self._impl = impl
        self.restype = None
        self.argtypes = None

    def __call__(self, *args, **kwargs):
        return self._impl(*args, **kwargs)


class _FakeWindll:
    def __init__(self, register_ok: bool = True):
        self.register_ok = register_ok
        self.registered = False
        self.unregistered = False
        self.messages: list = []
        self.user32 = SimpleNamespace(
            RegisterHotKey=_DllFunc(self._register),
            UnregisterHotKey=_DllFunc(self._unregister),
            PostThreadMessageW=_DllFunc(self._post),
            GetMessageW=_DllFunc(lambda lpmsg, hwnd, wmin, wmax: 0),
            TranslateMessage=_DllFunc(lambda msg: 1),
            DispatchMessageW=_DllFunc(lambda msg: 1),
        )
        self.kernel32 = SimpleNamespace(
            GetCurrentThreadId=_DllFunc(lambda: 4242),
        )

    def _register(self, hwnd, ident, modifiers, vk):
        if not self.register_ok:
            return 0
        self.registered = True
        return 1

    def _unregister(self, hwnd, ident):
        self.unregistered = True
        return 1

    def _post(self, thread_id, msg, wparam, lparam):
        self.messages.append(msg)
        return 1


def test_listener_register_conflict_returns_false(monkeypatch):
    windll = _FakeWindll(register_ok=False)
    monkeypatch.setattr(standby, "_IS_WINDOWS", True)
    monkeypatch.setattr("ctypes.windll", windll)
    listener = HotkeyListener(0x20, _MOD_CONTROL | _MOD_SHIFT)
    assert listener.register() is False
    assert windll.registered is False


def test_listener_register_wait_and_stop(monkeypatch):
    windll = _FakeWindll(register_ok=True)
    monkeypatch.setattr(standby, "_IS_WINDOWS", True)
    monkeypatch.setattr("ctypes.windll", windll)
    listener = HotkeyListener(0x20, _MOD_CONTROL | _MOD_SHIFT)
    assert listener.register() is True
    assert windll.registered is True
    assert listener.wait(timeout=0.05) is False  # nothing pressed yet
    listener._pressed.set()  # simulate the message loop seeing WM_HOTKEY
    assert listener.wait(timeout=0.5) is True
    listener.stop()
    assert windll.unregistered is True


def _recording() -> Recording:
    return Recording(
        name="demo",
        app="ChatGPT",
        description="demo flow",
        started_at=0.0,
        steps=[RecordedStep(action="focus", window_title="ChatGPT")],
        metrics=RecordingMetrics(events=1, steps=1, duration_ms=500),
    )


def _standby_args(tmp_path, **overrides) -> SimpleNamespace:
    values = dict(
        fake=True,
        app=None,
        standby=True,
        hotkey="ctrl+shift+space",
        out=str(tmp_path / "demo.json"),
        seconds=0.0,
        snapshot_interval=60.0,
        description="",
        upstream=None,
        upstream_binary=None,
        metrics_path=None,
        no_overlay=True,
        state_ttl_seconds=None,
        act_overlay=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_cmd_record_standby_conflict_hint(monkeypatch, tmp_path, capsys):
    class TakenListener:
        def __init__(self, vk, modifiers):
            pass

        def register(self):
            return False

        def stop(self):
            pass

    monkeypatch.setattr(standby, "HotkeyListener", TakenListener)
    args = _standby_args(tmp_path, fake=False)
    assert _cmd_record(args) == 2
    captured = capsys.readouterr()
    assert "already registered by another program" in captured.err
    assert "--hotkey ctrl+alt+space" in captured.err


def test_cmd_record_standby_fake_skips_without_foreground(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(
        "lean_computer_use_mcp.cli.build_upstream", lambda settings, fake: object()
    )
    args = _standby_args(tmp_path)
    assert _cmd_record(args) == 0
    out = capsys.readouterr().out
    assert "[standby] no recordable foreground window; skipping" in out


def test_cmd_record_standby_records_foreground_window(
    monkeypatch, tmp_path, capsys
):
    recording = _recording()
    state = {"stopped": False}

    class FakeRecorder:
        def __init__(self, **kwargs):
            self.stop_event = threading.Event()
            self.stop_event.set()

        def start(self):
            pass

        def stop(self):
            state["stopped"] = True
            return recording

    class FakeNoopForeground:
        def __init__(self):
            pass

        def current(self):
            return SimpleNamespace(
                window_title="ChatGPT", window_pid=15332, rect=(0, 0, 800, 600)
            )

    monkeypatch.setattr(
        "lean_computer_use_mcp.cli.build_upstream", lambda settings, fake: object()
    )
    monkeypatch.setattr("lean_computer_use_mcp.cli.Recorder", FakeRecorder)
    monkeypatch.setattr(
        "lean_computer_use_mcp.record.recorder.NoopForeground", FakeNoopForeground
    )
    monkeypatch.setattr(
        "lean_computer_use_mcp.record.overlay.make_overlay",
        lambda enabled: SimpleNamespace(show=lambda: None, hide=lambda: None),
    )
    out_path = str(tmp_path / "demo.json")
    args = _standby_args(tmp_path, out=out_path)
    assert _cmd_record(args) == 0
    assert state["stopped"] is True
    out = capsys.readouterr().out
    assert "[standby] recording 'ChatGPT' (pid 15332)" in out
    assert "Recorded 1 steps (500 ms)" in out
    assert "[standby] recorded ->" in out


def test_cmd_record_requires_app_without_standby(monkeypatch, tmp_path, capsys):
    args = _standby_args(tmp_path, standby=False, app=None)
    assert _cmd_record(args) == 2
    captured = capsys.readouterr()
    assert "--app is required" in captured.err
