"""Replay planner/runner: element-first matching, coords fallback, gates."""

from __future__ import annotations

import json
from pathlib import Path

from lean_computer_use_mcp.models import Frame
from lean_computer_use_mcp.record.model import ElementRef, RecordedStep, Recording
from lean_computer_use_mcp.record.replay import ReplayRunner, match_target
from lean_computer_use_mcp.server import LeanComputerUse
from lean_computer_use_mcp.upstream.fake_client import FakeUpstreamClient

FIXTURES = Path(__file__).parent.parent / "examples" / "fixtures"


class ReplayRecordingUpstream(FakeUpstreamClient):
    """Fake upstream that records focus calls, real clicks and state reads."""

    def __init__(self, fixture_dir):
        super().__init__(fixture_dir)
        self.focus_calls: list[str] = []
        self.real_clicks: list[tuple] = []
        self.state_reads = 0

    def focus_window(self, app):
        self.focus_calls.append(app)

    def real_input_click(self, app, x, y, mouse_button="left", click_count=1):
        self.real_clicks.append((app, x, y, mouse_button, click_count))

    def get_app_state(self, app, max_tree_nodes, max_tree_depth, text_limit):
        self.state_reads += 1
        return super().get_app_state(app, max_tree_nodes, max_tree_depth, text_limit)


def _recording() -> Recording:
    return Recording(
        name="demo",
        app="ChatGPT",
        description="Demo workflow",
        started_at=1700000000.0,
        steps=[
            RecordedStep(action="focus", window_title="ChatGPT"),
            RecordedStep(
                action="click",
                window_title="ChatGPT",
                target=ElementRef(
                    role="button",
                    name="\u6700\u5c0f\u5316",
                    frame=Frame(2532, 13, 120, 96),
                ),
                x=2600,
                y=60,
                matched=True,
            ),
            RecordedStep(
                action="click", window_title="ChatGPT", x=400, y=300, matched=False
            ),
            RecordedStep(action="type_text", window_title="ChatGPT", value="hello"),
            RecordedStep(
                action="press_key", window_title="ChatGPT", key="Enter", commit=True
            ),
        ],
    )


def _controls_sample() -> list[dict]:
    return [
        {"index": "12", "role": "button", "name": "Minimize"},
        {"index": "13", "role": "button", "name": "Restore"},
    ]


def test_match_target_scores():
    controls = _controls_sample()
    target = ElementRef(role="button", name="Minimize")
    hit = match_target(controls, [], target)
    assert hit is not None and hit[0] == "controls" and hit[1]["index"] == "12"
    assert match_target(controls, [], ElementRef(role="button", name="Missing")) is None
    assert (
        match_target(controls, [], ElementRef(role="", name="restore")) is not None
    )  # case-insensitive
    vision = [
        {
            "role": "button",
            "text": "Minimize",
            "frame": {"x": 0, "y": 0, "width": 10, "height": 10},
        }
    ]
    hit = match_target([], vision, ElementRef(role="button", name="Minimize"))
    assert hit is not None and hit[0] == "vision"


def test_dry_run_never_touches_upstream(settings):
    upstream = ReplayRecordingUpstream(FIXTURES)
    server = LeanComputerUse(upstream, settings)
    runner = ReplayRunner(server, confirm=lambda index, step: True)
    result = runner.run(_recording(), dry_run=True)
    assert result.ok is True
    assert result.completed == 5
    assert all(item.resolution == "preview" for item in result.outcomes)
    assert upstream.state_reads == 0
    assert upstream.focus_calls == []
    assert result.metrics_after.get("calls", 0) == 0  # no metrics rows written


def test_replay_element_path_and_coords_fallback(settings):
    upstream = ReplayRecordingUpstream(FIXTURES)
    server = LeanComputerUse(upstream, settings)
    runner = ReplayRunner(server, confirm=lambda index, step: True)
    result = runner.run(_recording(), dry_run=False)
    assert result.ok is True
    resolutions = [item.resolution for item in result.outcomes]
    assert resolutions == ["window", "element", "coords", "focus", "focus"]
    assert upstream.focus_calls == [
        "ChatGPT",
        "ChatGPT",
    ]  # start focus + coords fallback
    assert len(upstream.real_clicks) == 1
    assert upstream.real_clicks[0][0] == "ChatGPT"
    assert upstream.real_clicks[0][1] == 400 and upstream.real_clicks[0][2] == 300


def test_replay_declined_step_is_failure(settings):
    server = LeanComputerUse(ReplayRecordingUpstream(FIXTURES), settings)
    runner = ReplayRunner(server, confirm=lambda index, step: False)
    result = runner.run(_recording(), dry_run=False)
    assert result.ok is False
    declined = [item for item in result.outcomes if item.error == "declined"]
    assert len(declined) == 4  # all content-level steps declined


def test_replay_metrics_assertion(settings):
    upstream = ReplayRecordingUpstream(FIXTURES)
    server = LeanComputerUse(upstream, settings)
    runner = ReplayRunner(server, confirm=lambda index, step: True)
    result = runner.run(_recording(), dry_run=False)
    after = result.metrics_after
    assert after["observe_calls"] == 4  # one fresh observe per content step
    assert after["action_calls"] == 4
    assert after["image_bytes"] == 0  # no screenshots forwarded
    assert after["text_chars"] > 0
    assert after["nodes"] > 0
    rows = [
        json.loads(line)
        for line in Path(settings.metrics_path).read_text(encoding="utf-8").splitlines()
    ]
    assert all(row["tool"] in {"cu_observe", "cu_act"} for row in rows)
    assert all(row["image_bytes"] == 0 for row in rows)


def test_replay_yes_preconfirms_without_confirm_calls(settings):
    calls: list[int] = []
    upstream = ReplayRecordingUpstream(FIXTURES)
    server = LeanComputerUse(upstream, settings)
    runner = ReplayRunner(
        server, confirm=lambda index, step: calls.append(index) or True, yes=True
    )
    result = runner.run(_recording(), dry_run=False)
    assert result.ok is True
    assert calls == []
