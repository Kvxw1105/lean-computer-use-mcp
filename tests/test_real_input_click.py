from __future__ import annotations

import json
from pathlib import Path

from lean_computer_use_mcp.server import LeanComputerUse
from lean_computer_use_mcp.upstream.cli_client import CliUpstreamClient
from lean_computer_use_mcp.upstream.fake_client import FakeUpstreamClient
from lean_computer_use_mcp.upstream.win_input import (
    WindowInfo,
    matches_app,
    screen_point,
)

FIXTURES = Path(__file__).parent.parent / "examples" / "fixtures"


class RealClickRecordingUpstream(FakeUpstreamClient):
    """Fake upstream that records real-input clicks and upstream action calls."""

    def __init__(self, fixture_dir):
        super().__init__(fixture_dir)
        self.real_clicks: list[tuple] = []
        self.action_calls: list[tuple] = []
        self.state_reads = 0

    def real_input_click(self, app, x, y, mouse_button="left", click_count=1):
        self.real_clicks.append((app, x, y, mouse_button, click_count))

    def act_with_refresh(
        self, app, tool, args, max_tree_nodes, max_tree_depth, text_limit
    ):
        self.action_calls.append((tool, args))
        return super().act_with_refresh(
            app, tool, args, max_tree_nodes, max_tree_depth, text_limit
        )


class FingerprintFlipUpstream(RealClickRecordingUpstream):
    """Returns a different tree at the act-time freshness gate read."""

    def get_app_state(self, app, max_tree_nodes, max_tree_depth, text_limit):
        self.state_reads += 1
        if self.state_reads >= 2:
            return self._read_text("state_chatgpt_after_modal.txt"), None
        return self._read_text("state_chatgpt_control.txt"), None


class FakeWinInput:
    """Injected Win32Input backend for the CLI client mapping test."""

    def __init__(self):
        self.window = WindowInfo(hwnd=12345, left=100, top=50, width=1000, height=600)
        self.found_app: str | None = None
        self.clicks: list[tuple] = []

    def find_main_window(self, app):
        self.found_app = app
        return self.window

    def click(self, window, x, y, mouse_button="left", click_count=1):
        self.clicks.append((window, x, y, mouse_button, click_count))

    def focus_main_window(self, app):
        self.found_app = app
        return self.window


def _act_rows(metrics_path: str) -> list[dict]:
    lines = Path(metrics_path).read_text(encoding="utf-8").splitlines()
    return [
        json.loads(line)
        for line in lines
        if line.strip() and json.loads(line)["tool"] == "cu_act"
    ]


def test_matches_app_pure():
    assert matches_app("JianyingPro", "剪映专业版", "JianyingPro")
    assert matches_app("JianyingPro.exe", "剪映专业版", "JianyingPro.exe")
    assert matches_app("JianyingPro", "剪映专业版", "剪映")
    assert not matches_app("JianyingPro", "剪映专业版", "chrome")
    assert not matches_app("", "", "")


def test_screen_point_pure():
    window = WindowInfo(hwnd=1, left=100, top=50, width=1000, height=600)
    assert screen_point(window, 10, 20) == (110, 70)


def test_focus_window_delegates_to_win_input():
    backend = FakeWinInput()
    client = CliUpstreamClient(binary="unused", win_input=backend)
    client.focus_window("ChatGPT")
    assert backend.found_app == "ChatGPT"


def test_real_click_requires_coordinates(settings):
    engine = LeanComputerUse(RealClickRecordingUpstream(FIXTURES), settings)
    observed = engine.observe("ChatGPT")
    result = engine.act("ChatGPT", observed["state_id"], "click", click_method="real")
    assert result["ok"] is False
    assert result["error"] == "ELEMENT_NOT_FOUND"


def test_real_click_rejects_element_index(settings):
    engine = LeanComputerUse(RealClickRecordingUpstream(FIXTURES), settings)
    observed = engine.observe("ChatGPT")
    result = engine.act(
        "ChatGPT",
        observed["state_id"],
        "click",
        element_index="12",
        click_method="real",
    )
    assert result["ok"] is False
    assert result["error"] == "ELEMENT_NOT_FOUND"


def test_real_click_executes_input_and_records_metrics(settings):
    upstream = RealClickRecordingUpstream(FIXTURES)
    engine = LeanComputerUse(upstream, settings)
    observed = engine.observe("ChatGPT")
    result = engine.act(
        "ChatGPT", observed["state_id"], "click", click_method="real", x=10, y=20
    )
    assert result["ok"] is True
    assert result["action"] == "click"
    assert upstream.real_clicks == [("ChatGPT", 10, 20, "left", 1)]
    assert upstream.action_calls == []  # real input bypasses upstream action tools

    rows = _act_rows(settings.metrics_path)
    assert len(rows) == 1
    assert rows[0]["action"] == "click"
    assert rows[0]["error"] is None
    assert rows[0]["text_chars"] > 0
    assert rows[0]["image_bytes"] >= 0
    assert rows[0]["nodes"] >= 0


def test_real_click_rejects_stale_state_before_input(settings):
    upstream = FingerprintFlipUpstream(FIXTURES)
    engine = LeanComputerUse(upstream, settings)
    observed = engine.observe("ChatGPT")
    result = engine.act(
        "ChatGPT", observed["state_id"], "click", click_method="real", x=10, y=20
    )
    assert result["ok"] is False
    assert result["error"] == "STALE_STATE"
    assert upstream.real_clicks == []
    assert upstream.action_calls == []


def test_real_click_unavailable_without_win32_backend(settings):
    upstream = FakeUpstreamClient(FIXTURES)  # base implementation raises
    engine = LeanComputerUse(upstream, settings)
    observed = engine.observe("ChatGPT")
    result = engine.act(
        "ChatGPT", observed["state_id"], "click", click_method="real", x=10, y=20
    )
    assert result["ok"] is False
    assert result["error"] == "REAL_INPUT_UNAVAILABLE"


def test_cli_client_uses_injected_win_input():
    fake = FakeWinInput()
    client = CliUpstreamClient(win_input=fake)
    client.real_input_click("JianyingPro", 10, 20, mouse_button="right")
    assert fake.found_app == "JianyingPro"
    assert fake.clicks == [(fake.window, 10, 20, "right", 1)]


def test_real_click_method_ignored_for_non_click_actions(settings):
    upstream = RealClickRecordingUpstream(FIXTURES)
    engine = LeanComputerUse(upstream, settings)
    observed = engine.observe("ChatGPT")
    result = engine.act(
        "ChatGPT",
        observed["state_id"],
        "drag",
        click_method="real",
        from_x=1,
        from_y=2,
        to_x=3,
        to_y=4,
    )
    assert result["ok"] is True
    assert upstream.real_clicks == []
    assert upstream.action_calls == [
        ("drag", {"app": "ChatGPT", "from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4})
    ]
