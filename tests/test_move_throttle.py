"""Mouse-move throttling during drag recording.

A drag can emit hundreds of raw ``mouse_move`` events per second; the hook
merges moves closer than 2px or faster than 30ms into one recorded event so
``recording.json`` stays small while the drag step still gets exact
press/release coordinates. The throttle state machine is pure Python and is
exercised on every platform; hook objects use stub foreground/IME backends.
"""

from __future__ import annotations

from lean_computer_use_mcp.models import ControlNode, Frame
from lean_computer_use_mcp.record.model import ElementTable, InputEvent
from lean_computer_use_mcp.record.steps import build_steps
from lean_computer_use_mcp.record.win_hooks import (
    ForegroundInfo,
    ImeSampler,
    WinInputHook,
    should_record_move,
)


class _FakeForeground:
    def current(self) -> ForegroundInfo:
        return ForegroundInfo("Test Window", 42, (0, 0, 1000, 800))


def _hook(monkeypatch, step: float = 0.0001) -> WinInputHook:
    monkeypatch.setattr(
        "lean_computer_use_mcp.record.win_hooks.time.monotonic",
        _FakeClock(0.0, step=step),
    )
    return WinInputHook(foreground=_FakeForeground(), ime=ImeSampler())


class _FakeClock:
    def __init__(self, start: float, step: float = 0.0001) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


def _table() -> ElementTable:
    node = ControlNode(
        index="1", role="button", name="timeline",
        frame=Frame(x=0, y=0, width=500, height=300),
    )
    return ElementTable(
        ts=0.5,
        window_title="Test Window",
        window_pid=42,
        elements=[node],
        text_chars=100,
        image_bytes=0,
        window_rect=(0, 0, 1000, 800),
    )


def _event(kind: str, ts: float, x: int, y: int) -> InputEvent:
    return InputEvent(
        ts=ts,
        kind=kind,
        x=x,
        y=y,
        button="left" if kind != "mouse_move" else None,
        window_title="Test Window",
        window_pid=42,
        window_rect=(0, 0, 1000, 800),
    )


# --- pure decision function ------------------------------------------------


def test_should_record_move_first_move_always_records():
    assert should_record_move(None, 10, 10, 0.0) is True


def test_should_record_move_merges_close_fast_moves():
    last = (10, 10, 0.0)
    assert should_record_move(last, 11, 11, 0.01) is False


def test_should_record_move_records_when_far_enough():
    last = (10, 10, 0.0)
    assert should_record_move(last, 12, 10, 0.01) is True  # exactly 2px
    assert should_record_move(last, 10, 12, 0.01) is True
    assert should_record_move(last, 13, 13, 0.01) is True


def test_should_record_move_records_when_slow_enough():
    last = (10, 10, 0.0)
    assert should_record_move(last, 10, 10, 0.03) is True  # exactly 30ms
    assert should_record_move(last, 10, 10, 0.5) is True


def test_should_record_move_suppresses_subthreshold_diagonal():
    last = (10, 10, 0.0)
    assert should_record_move(last, 11, 11, 0.01) is False  # dist ~1.41 < 2


# --- hook integration (stub backends, runs on every platform) ---------------


def test_on_mouse_move_throttles_burst(monkeypatch):
    hook = _hook(monkeypatch)
    for x in (100, 101, 100, 101, 100, 101, 100, 101):  # 1px jitter, fast
        hook._on_mouse_move(x, 100)
    moves = [e for e in hook.events if e.kind == "mouse_move"]
    assert len(moves) == 1  # jitter merged into the first move
    assert (moves[0].x, moves[0].y) == (100, 100)


def test_on_mouse_move_records_slow_progression(monkeypatch):
    hook = _hook(monkeypatch, step=0.001)  # 1ms per move: record every 30th
    for _ in range(200):
        hook._on_mouse_move(100, 100)  # no movement: time threshold only
    moves = [e for e in hook.events if e.kind == "mouse_move"]
    assert 1 <= len(moves) <= 10  # bounded well below the raw 200
    assert (moves[-1].x, moves[-1].y) == (100, 100)


def test_pending_move_flushed_on_release(monkeypatch):
    hook = _hook(monkeypatch)
    hook._on_mouse_move(100, 100)  # recorded
    for x in (101, 100, 101, 100, 101):  # 1px jitter: all suppressed
        hook._on_mouse_move(x, 100)
    hook._flush_pending_move()
    moves = [e for e in hook.events if e.kind == "mouse_move"]
    assert len(moves) == 2
    assert (moves[-1].x, moves[-1].y) == (101, 100)  # newest suppressed pos


def test_mouse_down_resets_throttle_state(monkeypatch):
    hook = _hook(monkeypatch)
    hook._on_mouse_move(100, 100)
    hook._on_mouse_move(101, 100)  # suppressed
    hook._buttons_down.add("left")
    hook._last_move = None
    hook._pending_move = None
    hook._on_mouse_move(102, 100)  # fresh gesture: must record
    moves = [e for e in hook.events if e.kind == "mouse_move"]
    assert len(moves) == 2
    assert (moves[-1].x, moves[-1].y) == (102, 100)


# --- step building with a throttled stream ----------------------------------


def test_throttled_stream_builds_one_drag_step():
    events = [
        _event("mouse_down", 1.0, 150, 250),
        _event("mouse_move", 1.01, 151, 251),  # suppressed by throttle
        _event("mouse_move", 1.02, 200, 300),  # recorded (far)
        _event("mouse_up", 1.03, 205, 305),
    ]
    steps = build_steps(events, [_table()])
    assert [step.action for step in steps] == ["drag"]
    assert (steps[0].x, steps[0].y) == (150, 250)  # press offset
    assert (steps[0].to_x, steps[0].to_y) == (205, 305)  # release offset
