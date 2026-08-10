"""Window-management tools: cu_window facade + Win32 client helpers."""

from __future__ import annotations

import json
from pathlib import Path

from lean_computer_use_mcp.errors import AppNotFoundError
from lean_computer_use_mcp.server import LeanComputerUse
from lean_computer_use_mcp.upstream.cli_client import CliUpstreamClient
from lean_computer_use_mcp.upstream.fake_client import FakeUpstreamClient
from lean_computer_use_mcp.upstream.win_input import (
    WindowCandidate,
    WindowInfo,
    WindowStatus,
    coverage_state,
    filter_candidates,
)

FIXTURES = Path(__file__).parent.parent / "examples" / "fixtures"


def make_window(
    hwnd: int,
    title: str,
    left: int = 0,
    top: int = 0,
    width: int = 800,
    height: int = 600,
) -> WindowCandidate:
    info = WindowInfo(hwnd=hwnd, left=left, top=top, width=width, height=height)
    return WindowCandidate(info=info, title=title)


# --- pure helpers ------------------------------------------------------------


def test_coverage_state_full_cover_detects_occlusion():
    target = make_window(1, "target", left=100, top=100, width=400, height=300)
    coverer = make_window(2, "Notepad", left=0, top=0, width=2000, height=1200)
    occluded, titles = coverage_state(target.info, [("Notepad", coverer.info)])
    assert occluded is True
    assert titles == ("Notepad",)


def test_coverage_state_partial_overlap_is_not_occlusion():
    target = make_window(1, "target", left=100, top=100, width=400, height=300)
    coverer = make_window(2, "Editor", left=150, top=150, width=200, height=200)
    occluded, titles = coverage_state(target.info, [("Editor", coverer.info)])
    assert occluded is False
    assert titles == ()


def test_coverage_state_disjoint_windows():
    target = make_window(1, "target", left=100, top=100, width=400, height=300)
    other = make_window(2, "Terminal", left=900, top=900, width=300, height=200)
    occluded, titles = coverage_state(target.info, [("Terminal", other.info)])
    assert occluded is False


def test_coverage_state_reports_all_coverers():
    target = make_window(1, "target", left=0, top=0, width=100, height=100)
    above = [
        ("A", make_window(2, "A", left=-10, top=-10, width=500, height=500).info),
        ("B", make_window(3, "B", left=-20, top=-20, width=600, height=600).info),
        ("C", make_window(4, "C", left=5, top=5, width=50, height=50).info),
    ]
    occluded, titles = coverage_state(target.info, above)
    assert occluded is True
    assert titles == ("A", "B")


def test_filter_candidates_title_substring():
    candidates = (
        make_window(1, "JianYing - 2026-08-01"),
        make_window(2, "JianYing - 2026-08-02"),
        make_window(3, "Export Dialog"),
    )
    assert [c.title for c in filter_candidates(candidates, "2026-08-02")] == [
        "JianYing - 2026-08-02"
    ]
    assert len(filter_candidates(candidates, None)) == 3
    assert len(filter_candidates(candidates, "")) == 3
    assert filter_candidates(candidates, "  ") == candidates
    assert filter_candidates(candidates, "not-there") == ()


def test_filter_candidates_case_insensitive():
    candidates = (make_window(1, "ChatGPT Main"),)
    assert filter_candidates(candidates, "chatgpt") == candidates


# --- CliUpstreamClient delegation -------------------------------------------


class FakeWinInputBackend:
    """Win32Input-protocol fake used by CliUpstreamClient tests."""

    def __init__(self, status: WindowStatus) -> None:
        self.status = status
        self.activated: list[tuple[str, str | None]] = []
        self.maximized: list[tuple[str, str | None]] = []
        self.focus_calls = 0

    def find_main_window(self, app: str) -> WindowInfo:
        return self.status.main.info

    def find_windows(self, app: str) -> list[WindowInfo]:
        return [c.info for c in self.status.candidates]

    def window_status(self, app: str) -> WindowStatus:
        return self.status

    def focus_main_window(self, app: str) -> WindowInfo:
        self.focus_calls += 1
        return self.status.main.info

    def activate_window(self, app: str, title: str | None = None) -> WindowInfo:
        self.activated.append((app, title))
        return self.status.main.info

    def maximize_window(self, app: str, title: str | None = None) -> WindowInfo:
        self.maximized.append((app, title))
        return self.status.main.info

    def click(self, window, x, y, mouse_button="left", click_count=1) -> None:
        pass


def test_cli_client_window_status_delegates():
    status = WindowStatus(
        app="JianYing",
        candidates=(make_window(1, "JianYing"),),
        main=make_window(1, "JianYing"),
        ambiguous=False,
    )
    backend = FakeWinInputBackend(status)
    client = CliUpstreamClient(binary="unused", win_input=backend)
    assert client.window_status("JianYing") == status
    assert client.activate_window("JianYing", "2026") is status.main.info
    assert client.maximize_window("JianYing") is status.main.info
    assert backend.activated == [("JianYing", "2026")]
    assert backend.maximized == [("JianYing", None)]


# --- facade cu_window --------------------------------------------------------


def make_status(*candidates: WindowCandidate, app: str = "ChatGPT") -> WindowStatus:
    return WindowStatus(
        app=app,
        candidates=tuple(candidates),
        main=candidates[0],
        ambiguous=len(candidates) > 1,
    )


class WindowStubUpstream(FakeUpstreamClient):
    """Fake upstream with recorded window actions."""

    def __init__(
        self,
        fixture_dir: Path,
        status: WindowStatus | None = None,
        app_not_found: bool = False,
    ) -> None:
        super().__init__(fixture_dir)
        self.status = status
        self.app_not_found = app_not_found
        self.activated: list[tuple[str, str | None]] = []
        self.maximized: list[tuple[str, str | None]] = []
        self.status_reads = 0

    def window_status(self, app: str) -> WindowStatus:
        if self.app_not_found:
            raise AppNotFoundError(f"no window for app {app!r}")
        if self.status is None:
            raise AssertionError("test did not provide a status")
        self.status_reads += 1
        return self.status

    def activate_window(self, app: str, title: str | None = None) -> WindowInfo:
        self.activated.append((app, title))
        return self.status.main.info

    def maximize_window(self, app: str, title: str | None = None) -> WindowInfo:
        self.maximized.append((app, title))
        return self.status.main.info


def _window_rows(metrics_path: str) -> list[dict]:
    lines = Path(metrics_path).read_text(encoding="utf-8").splitlines()
    return [
        json.loads(line)
        for line in lines
        if line.strip() and json.loads(line)["tool"] == "cu_window"
    ]


def test_window_list_returns_candidates_without_side_effects(settings):
    status = make_status(
        make_window(1, "ChatGPT"), make_window(2, "ChatGPT - Settings")
    )
    upstream = WindowStubUpstream(FIXTURES, status)
    engine = LeanComputerUse(upstream, settings)
    result = engine.window("ChatGPT", "list")
    assert result["ok"] is True
    assert result["action"] == "list"
    assert result["ambiguous"] is True
    assert [c["title"] for c in result["candidates"]] == [
        "ChatGPT",
        "ChatGPT - Settings",
    ]
    assert result["main"]["hwnd"] == 1
    assert upstream.activated == []
    assert upstream.maximized == []
    rows = _window_rows(settings.metrics_path)
    assert len(rows) == 1
    assert rows[0]["nodes"] == 2
    assert rows[0]["image_bytes"] == 0
    assert rows[0]["text_chars"] > 0
    assert rows[0]["error"] is None


def test_window_list_title_narrows_candidates(settings):
    status = make_status(
        make_window(1, "ChatGPT"), make_window(2, "ChatGPT - Settings")
    )
    upstream = WindowStubUpstream(FIXTURES, status)
    engine = LeanComputerUse(upstream, settings)
    result = engine.window("ChatGPT", "list", title="settings")
    assert result["ok"] is True
    assert [c["title"] for c in result["candidates"]] == ["ChatGPT - Settings"]
    assert result["ambiguous"] is False


def test_window_activate_ambiguous_never_guesses(settings):
    status = make_status(
        make_window(1, "ChatGPT"), make_window(2, "ChatGPT - Settings")
    )
    upstream = WindowStubUpstream(FIXTURES, status)
    engine = LeanComputerUse(upstream, settings)
    result = engine.window("ChatGPT", "activate")
    assert result["ok"] is False
    assert result["error"] == "AMBIGUOUS_TARGET"
    assert len(result["candidates"]) == 2
    assert upstream.activated == []  # nothing executed, no guessing
    rows = _window_rows(settings.metrics_path)
    assert rows[0]["error"] == "AMBIGUOUS_TARGET"
    assert rows[0]["nodes"] == 2


def test_window_activate_title_narrows_and_reports_occlusion(settings):
    occluded = WindowCandidate(
        info=WindowInfo(hwnd=1, left=0, top=0, width=800, height=600),
        title="ChatGPT - Main",
        occluded=True,
        covered_by=("Notepad",),
    )
    status = make_status(occluded, make_window(2, "ChatGPT - Settings"))
    upstream = WindowStubUpstream(FIXTURES, status)
    engine = LeanComputerUse(upstream, settings)
    result = engine.window("ChatGPT", "activate", title="Main")
    assert result["ok"] is True  # occlusion is a status, not an error
    assert upstream.activated == [("ChatGPT", "ChatGPT - Main")]
    assert result["was_occluded"] is True
    assert result["window"]["occluded"] is True
    assert result["window"]["covered_by"] == ["Notepad"]
    assert "Notepad" in result["message"]
    assert result["ambiguous"] is False


def test_window_maximize_calls_upstream(settings):
    status = make_status(make_window(1, "ChatGPT - Main"))
    upstream = WindowStubUpstream(FIXTURES, status)
    engine = LeanComputerUse(upstream, settings)
    result = engine.window("ChatGPT", "maximize")
    assert result["ok"] is True
    assert upstream.maximized == [("ChatGPT", "ChatGPT - Main")]
    assert result["action"] == "maximize"
    assert result["window"]["title"] == "ChatGPT - Main"


def test_window_app_not_found(settings):
    upstream = WindowStubUpstream(FIXTURES, app_not_found=True)
    engine = LeanComputerUse(upstream, settings)
    result = engine.window("Notepad", "list")
    assert result["ok"] is False
    assert result["error"] == "APP_NOT_FOUND"
    rows = _window_rows(settings.metrics_path)
    assert rows[0]["error"] == "APP_NOT_FOUND"


def test_window_unknown_action(settings):
    status = make_status(make_window(1, "ChatGPT"))
    upstream = WindowStubUpstream(FIXTURES, status)
    engine = LeanComputerUse(upstream, settings)
    result = engine.window("ChatGPT", "minimize")
    assert result["ok"] is False
    assert result["error"] == "UNSUPPORTED_ACTION"


def test_window_fake_client_returns_fake_status(settings):
    engine = LeanComputerUse(FakeUpstreamClient(FIXTURES), settings)
    result = engine.window("ChatGPT", "list")
    assert result["ok"] is True
    assert result["main"]["hwnd"] == 4242
    assert result["ambiguous"] is False
