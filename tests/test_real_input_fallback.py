"""Facade-level real-input fallback and structured real-input errors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lean_computer_use_mcp.errors import (
    AppNotFoundError,
    RealInputFailedError,
    RealInputUnavailableError,
    UpstreamTimeoutError,
)
from lean_computer_use_mcp.server import LeanComputerUse
from lean_computer_use_mcp.upstream.fake_client import FakeUpstreamClient
from lean_computer_use_mcp.upstream.win_input import (
    CtypesWin32Input,
    WindowInfo,
    check_click_bounds,
)

FIXTURES = Path(__file__).parent.parent / "examples" / "fixtures"


class FakeFallbackInput:
    """Win32Input-protocol fake for the facade-owned fallback backend."""

    def __init__(
        self,
        window: WindowInfo | None = None,
        click_error: Exception | None = None,
    ) -> None:
        self.window = window or WindowInfo(hwnd=7, left=0, top=0, width=800, height=600)
        self.click_error = click_error
        self.clicks: list[tuple] = []
        self.found: list[str] = []

    def find_main_window(self, app: str) -> WindowInfo:
        self.found.append(app)
        return self.window

    def click(self, window, x, y, mouse_button="left", click_count=1) -> None:
        if self.click_error is not None:
            raise self.click_error
        self.clicks.append((window, x, y, mouse_button, click_count))


class RealFailUpstream(FakeUpstreamClient):
    """Fake upstream whose real-input path raises a configured error."""

    def __init__(self, fixture_dir: Path, error: Exception | None) -> None:
        super().__init__(fixture_dir)
        self.error = error
        self.real_calls: list[tuple] = []

    def real_input_click(self, app, x, y, mouse_button="left", click_count=1):
        self.real_calls.append((app, x, y, mouse_button, click_count))
        if self.error is not None:
            raise self.error


def _act_rows(metrics_path: str) -> list[dict]:
    lines = Path(metrics_path).read_text(encoding="utf-8").splitlines()
    return [
        json.loads(line)
        for line in lines
        if line.strip() and json.loads(line)["tool"] == "cu_act"
    ]


def _real_click_result(engine, upstream):
    observed = engine.observe("ChatGPT")
    return engine.act(
        "ChatGPT",
        observed["state_id"],
        "click",
        click_method="real",
        x=10,
        y=20,
    )


def test_no_fallback_when_upstream_succeeds(settings):
    upstream = RealFailUpstream(FIXTURES, error=None)
    backend = FakeFallbackInput()
    engine = LeanComputerUse(upstream, settings, real_input_fallback=backend)
    result = _real_click_result(engine, upstream)
    assert result["ok"] is True
    assert result["real_input"] == {"path": "upstream", "upstream_error": None}
    assert upstream.real_calls == [("ChatGPT", 10, 20, "left", 1)]
    assert backend.clicks == []
    rows = _act_rows(settings.metrics_path)
    assert rows[-1]["real_input_fallback"] is False


def test_fallback_when_upstream_real_input_unavailable(settings):
    upstream = RealFailUpstream(FIXTURES, error=RealInputUnavailableError("no backend"))
    backend = FakeFallbackInput()
    engine = LeanComputerUse(upstream, settings, real_input_fallback=backend)
    result = _real_click_result(engine, upstream)
    assert result["ok"] is True
    assert result["real_input"]["path"] == "fallback"
    assert "no backend" in result["real_input"]["upstream_error"]
    assert upstream.real_calls == [("ChatGPT", 10, 20, "left", 1)]
    assert backend.found == ["ChatGPT"]
    assert backend.clicks == [(backend.window, 10, 20, "left", 1)]
    rows = _act_rows(settings.metrics_path)
    assert rows[-1]["real_input_fallback"] is True
    assert rows[-1]["error"] is None


def test_fallback_when_upstream_window_not_found(settings):
    upstream = RealFailUpstream(
        FIXTURES,
        error=AppNotFoundError("no window", reason="window_not_found"),
    )
    backend = FakeFallbackInput()
    engine = LeanComputerUse(upstream, settings, real_input_fallback=backend)
    result = _real_click_result(engine, upstream)
    assert result["ok"] is True
    assert result["real_input"]["path"] == "fallback"
    assert backend.clicks == [(backend.window, 10, 20, "left", 1)]


def test_fallback_failure_returns_structured_error(settings):
    upstream = RealFailUpstream(FIXTURES, error=RealInputUnavailableError("no backend"))
    backend = FakeFallbackInput(
        click_error=RealInputFailedError("win32 boom", reason="win32_error")
    )
    engine = LeanComputerUse(upstream, settings, real_input_fallback=backend)
    result = _real_click_result(engine, upstream)
    assert result["ok"] is False
    assert result["error"] == "REAL_INPUT_FAILED"
    assert result["reason"] == "win32_error"
    assert "win32 boom" in result["message"]
    rows = _act_rows(settings.metrics_path)
    assert rows[-1]["error"] == "REAL_INPUT_FAILED"


def test_out_of_bounds_rejected_before_click(settings):
    upstream = RealFailUpstream(FIXTURES, error=RealInputUnavailableError("no backend"))
    backend = FakeFallbackInput()
    engine = LeanComputerUse(upstream, settings, real_input_fallback=backend)
    observed = engine.observe("ChatGPT")
    result = engine.act(
        "ChatGPT",
        observed["state_id"],
        "click",
        click_method="real",
        x=5000,
        y=20,
    )
    assert result["ok"] is False
    assert result["error"] == "REAL_INPUT_FAILED"
    assert result["reason"] == "out_of_bounds"
    assert backend.clicks == []  # nothing was injected


def test_check_click_bounds_pure():
    window = WindowInfo(hwnd=1, left=0, top=0, width=800, height=600)
    check_click_bounds(window, 0, 0)
    check_click_bounds(window, 799, 599)
    for x, y in [(-1, 0), (0, -1), (800, 0), (0, 600), (900, 700)]:
        with pytest.raises(RealInputFailedError) as excinfo:
            check_click_bounds(window, x, y)
        assert excinfo.value.reason == "out_of_bounds"


def test_ctypes_click_wraps_win32_failures():
    class BoomUser32:
        def SetCursorPos(self, *args):
            raise OSError("win32 refused")

        def mouse_event(self, *args):
            raise AssertionError("must not be reached")

    backend = object.__new__(CtypesWin32Input)
    backend._user32 = BoomUser32()
    window = WindowInfo(hwnd=1, left=0, top=0, width=800, height=600)
    with pytest.raises(RealInputFailedError) as excinfo:
        backend.click(window, 10, 20)
    assert excinfo.value.reason == "win32_error"
    assert "win32 refused" in str(excinfo.value)


def test_upstream_timeout_reason_is_structured():
    error = UpstreamTimeoutError("upstream call timed out: get_app_state")
    assert error.code == "UPSTREAM_ERROR"
    assert error.reason == "timeout"


def test_observe_error_response_includes_reason(settings):
    class TimeoutUpstream(FakeUpstreamClient):
        def get_app_state(self, app, max_tree_nodes, max_tree_depth, text_limit):
            raise UpstreamTimeoutError("upstream call timed out: get_app_state")

    engine = LeanComputerUse(TimeoutUpstream(FIXTURES), settings)
    result = engine.observe("ChatGPT")
    assert result["ok"] is False
    assert result["error"] == "UPSTREAM_ERROR"
    assert result["reason"] == "timeout"


def test_window_not_found_reason_surfaces(settings):
    class NotFoundUpstream(FakeUpstreamClient):
        def window_status(self, app):
            raise AppNotFoundError("no window", reason="window_not_found")

    engine = LeanComputerUse(NotFoundUpstream(FIXTURES), settings)
    result = engine.window("ChatGPT", "activate")
    assert result["ok"] is False
    assert result["error"] == "APP_NOT_FOUND"
    assert result["reason"] == "window_not_found"
