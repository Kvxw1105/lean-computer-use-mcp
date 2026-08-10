"""Drag gesture recording: press-move-release -> drag step, replay path."""

from __future__ import annotations

from pathlib import Path

from lean_computer_use_mcp.models import ControlNode, Frame
from lean_computer_use_mcp.record.model import (
    ElementTable,
    InputEvent,
    Recording,
    RecordedStep,
)
from lean_computer_use_mcp.record.replay import ReplayRunner
from lean_computer_use_mcp.record.steps import build_steps
from lean_computer_use_mcp.server import LeanComputerUse
from lean_computer_use_mcp.upstream.fake_client import FakeUpstreamClient

FIXTURES = Path(__file__).parent.parent / "examples" / "fixtures"

RECT = (100, 200, 1100, 900)
TITLE = "Demo Window"


def _node(
    index: str, role: str, name: str, x: int, y: int, w: int, h: int
) -> ControlNode:
    return ControlNode(
        index=index, role=role, name=name, frame=Frame(x=x, y=y, width=w, height=h)
    )


def _table(ts: float = 0.5) -> ElementTable:
    return ElementTable(
        ts=ts,
        window_title=TITLE,
        window_pid=42,
        elements=[_node("1", "button", "timeline", 0, 0, 500, 300)],
        text_chars=100,
        image_bytes=0,
        window_rect=RECT,
    )


def _event(
    kind: str, ts: float, x: int, y: int, button: str | None = None
) -> InputEvent:
    return InputEvent(
        ts=ts,
        kind=kind,
        x=x,
        y=y,
        button=button,
        window_title=TITLE,
        window_pid=42,
        window_rect=RECT,
    )


# --- step building -----------------------------------------------------------


def test_drag_press_move_release_builds_drag_step():
    events = [
        _event("mouse_down", 1.0, 150, 250, "left"),  # offset (50, 50)
        _event("mouse_move", 1.1, 200, 300),  # offset (100, 100)
        _event("mouse_move", 1.2, 250, 350),  # offset (150, 150)
        _event("mouse_up", 1.3, 250, 350, "left"),  # offset (150, 150)
    ]
    steps = build_steps(events, [_table()])
    assert [step.action for step in steps] == ["drag"]
    step = steps[0]
    assert (step.x, step.y) == (50, 50)
    assert (step.to_x, step.to_y) == (150, 150)
    assert step.window_title == TITLE
    assert step.matched is True  # down point hit the timeline element
    assert step.target is not None and step.target.name == "timeline"


def test_drag_uses_release_point_when_last_move_is_missing():
    events = [
        _event("mouse_down", 1.0, 150, 250, "left"),
        _event("mouse_move", 1.1, 260, 360),  # offset (160, 160)
        _event("mouse_up", 1.2, 300, 400, "left"),  # offset (200, 200)
    ]
    steps = build_steps(events, [_table()])
    assert [step.action for step in steps] == ["drag"]
    assert (steps[0].x, steps[0].y) == (50, 50)
    assert (steps[0].to_x, steps[0].to_y) == (200, 200)


def test_press_release_without_move_stays_click():
    events = [
        _event("mouse_down", 1.0, 150, 250, "left"),
        _event("mouse_up", 1.1, 150, 250, "left"),
    ]
    steps = build_steps(events, [_table()])
    assert [step.action for step in steps] == ["click"]


def test_jitter_below_threshold_stays_click():
    events = [
        _event("mouse_down", 1.0, 150, 250, "left"),
        _event("mouse_move", 1.1, 151, 251),
        _event("mouse_move", 1.2, 152, 252),
        _event("mouse_up", 1.3, 152, 252, "left"),
    ]
    steps = build_steps(events, [_table()])
    assert [step.action for step in steps] == ["click"]


def test_move_without_press_is_ignored():
    events = [_event("mouse_move", 1.0, 300, 300)]
    steps = build_steps(events, [_table()])
    assert steps == []


def test_right_button_drag_does_not_convert_left_click():
    events = [
        _event("mouse_down", 1.0, 150, 250, "left"),
        _event("mouse_down", 1.1, 150, 250, "right"),
        _event("mouse_move", 1.2, 300, 350),
        _event("mouse_up", 1.3, 300, 350, "right"),
        _event("mouse_up", 1.4, 300, 350, "left"),
    ]
    steps = build_steps(events, [_table()])
    assert [step.action for step in steps] == ["click"]


def test_drag_keeps_commit_flag_from_down_target():
    table = ElementTable(
        ts=0.5,
        window_title=TITLE,
        window_pid=42,
        elements=[_node("2", "button", "publish", 0, 0, 500, 300)],
        text_chars=100,
        image_bytes=0,
        window_rect=RECT,
    )
    events = [
        _event("mouse_down", 1.0, 150, 250, "left"),  # on publish
        _event("mouse_move", 1.1, 300, 400),
        _event("mouse_up", 1.2, 300, 400, "left"),
    ]
    steps = build_steps(events, [table])
    assert steps[0].action == "drag"
    assert steps[0].commit is True


def test_stop_mid_gesture_flushes_drag_at_end():
    events = [
        _event("mouse_down", 1.0, 150, 250, "left"),
        _event("mouse_move", 1.1, 300, 400),
    ]
    steps = build_steps(events, [_table()])
    assert [step.action for step in steps] == ["drag"]
    assert (steps[0].to_x, steps[0].to_y) == (200, 200)


# --- round-trip + replay -----------------------------------------------------


def test_drag_fields_survive_recording_roundtrip(tmp_path):
    recording = Recording(
        name="drag",
        app="ChatGPT",
        description="",
        started_at=1.0,
        steps=[
            RecordedStep(
                action="drag", window_title=TITLE, x=1, y=2, to_x=99, to_y=88
            )
        ],
    )
    path = tmp_path / "recording.json"
    recording.save(path)
    loaded = Recording.load(path)
    step = loaded.steps[0]
    assert step.action == "drag"
    assert (step.x, step.y) == (1, 2)
    assert (step.to_x, step.to_y) == (99, 88)
    assert step.describe() == "Drag from (1, 2) to (99, 88)"


class DragRecordingUpstream(FakeUpstreamClient):
    def __init__(self, fixture_dir):
        super().__init__(fixture_dir)
        self.drag_args: list[dict] = []

    def act_with_refresh(
        self, app, tool, args, max_tree_nodes, max_tree_depth, text_limit
    ):
        if tool == "drag":
            self.drag_args.append(args)
        return self._read_text("state_chatgpt_after_modal.txt"), None, {"fake": True}


def test_replay_drag_reaches_upstream_with_from_to(settings):
    upstream = DragRecordingUpstream(FIXTURES)
    server = LeanComputerUse(upstream, settings)
    recording = Recording(
        name="drag",
        app="ChatGPT",
        description="",
        started_at=1.0,
        steps=[
            RecordedStep(
                action="drag", window_title="ChatGPT", x=40, y=50, to_x=400, to_y=300
            )
        ],
    )
    runner = ReplayRunner(server, confirm=lambda index, step: True)
    result = runner.run(recording, dry_run=False)
    assert result.ok is True
    assert result.outcomes[0].resolution == "coords"
    assert upstream.drag_args == [
        {"app": "ChatGPT", "from_x": 40, "from_y": 50, "to_x": 400, "to_y": 300}
    ]
    # Metrics: drag adds text but never screenshot bytes.
    assert result.metrics_after["image_bytes"] == 0
    assert result.metrics_after["text_chars"] > 0


def test_replay_drag_missing_to_fails_with_element_not_found(settings):
    upstream = DragRecordingUpstream(FIXTURES)
    server = LeanComputerUse(upstream, settings)
    recording = Recording(
        name="drag",
        app="ChatGPT",
        description="",
        started_at=1.0,
        steps=[RecordedStep(action="drag", window_title="ChatGPT", x=40, y=50)],
    )
    runner = ReplayRunner(server, confirm=lambda index, step: True)
    result = runner.run(recording, dry_run=False)
    assert result.ok is False
    assert result.outcomes[0].error == "ELEMENT_NOT_FOUND"
    assert upstream.drag_args == []
