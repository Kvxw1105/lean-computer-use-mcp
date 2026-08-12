from __future__ import annotations

import ctypes

import pytest

import lean_computer_use_mcp.upstream.win_input as win_input_mod
from lean_computer_use_mcp.errors import (
    AmbiguousTargetError,
    AppNotFoundError,
    RealInputFailedError,
    RealInputUnavailableError,
    UpstreamError,
)
from lean_computer_use_mcp.upstream.win_input import (
    CtypesWin32Input,
    WindowInfo,
)

_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010


class FakeShcore:
    def __init__(self):
        self.raise_error: Exception | None = None
        self.calls: list[object] = []

    def SetProcessDpiAwareness(self, value):
        self.calls.append(value)
        if self.raise_error is not None:
            raise self.raise_error


class FakeUser32:
    def __init__(self):
        self.windows: dict[int, dict] = {}
        self.enum_order: list[int] = []
        self.calls: list[tuple] = []
        self.mouse_events: list[int] = []
        self.dpi_fallback_error: Exception | None = None
        self.show_error: Exception | None = None
        self.cursor_error: Exception | None = None

    def add_window(
        self,
        hwnd: int,
        pid: int,
        title: str,
        left: int,
        top: int,
        right: int,
        bottom: int,
        visible: bool = True,
    ) -> None:
        self.windows[hwnd] = {
            "hwnd": hwnd,
            "pid": pid,
            "title": title,
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "visible": visible,
        }
        self.enum_order.append(hwnd)

    def IsWindowVisible(self, hwnd):
        self.calls.append(("IsWindowVisible", hwnd))
        return self.windows[hwnd]["visible"]

    def GetWindowTextLengthW(self, hwnd):
        self.calls.append(("GetWindowTextLengthW", hwnd))
        return len(self.windows[hwnd]["title"])

    def GetWindowTextW(self, hwnd, buf, max_count):
        self.calls.append(("GetWindowTextW", hwnd))
        buf.value = self.windows[hwnd]["title"]
        return len(buf.value)

    def GetWindowThreadProcessId(self, hwnd, pid):
        pid.value = self.windows[hwnd]["pid"]
        return 0

    def GetWindowRect(self, hwnd, rect):
        w = self.windows[hwnd]
        rect.left = w["left"]
        rect.top = w["top"]
        rect.right = w["right"]
        rect.bottom = w["bottom"]
        return True

    def EnumWindows(self, callback, lparam):
        for hwnd in self.enum_order:
            if not callback(hwnd, lparam):
                break

    def SetCursorPos(self, x, y):
        self.calls.append(("SetCursorPos", x, y))
        if self.cursor_error is not None:
            raise self.cursor_error

    def mouse_event(self, flags, dx, dy, data, extra):
        self.mouse_events.append(flags)

    def ShowWindow(self, hwnd, cmd):
        self.calls.append(("ShowWindow", hwnd, cmd))
        if self.show_error is not None:
            raise self.show_error

    def SetForegroundWindow(self, hwnd):
        self.calls.append(("SetForegroundWindow", hwnd))

    def SetProcessDPIAware(self):
        self.calls.append("SetProcessDPIAware")
        if self.dpi_fallback_error is not None:
            raise self.dpi_fallback_error


class FakeKernel32:
    def __init__(self, processes: list[tuple[int, str]] | None = None):
        self.processes = list(processes or [])
        self.calls: list[str] = []
        self.snapshot_fails = False
        self._index = 0

    def CreateToolhelp32Snapshot(self, flags, pid):
        self.calls.append("CreateToolhelp32Snapshot")
        if self.snapshot_fails:
            return ctypes.c_void_p(-1).value
        return 12345

    def Process32FirstW(self, snapshot, entry):
        self.calls.append("Process32FirstW")
        if not self.processes:
            return False
        self._index = 0
        self._fill(entry, self.processes[0])
        return True

    def Process32NextW(self, snapshot, entry):
        self.calls.append("Process32NextW")
        self._index += 1
        if self._index >= len(self.processes):
            return False
        self._fill(entry, self.processes[self._index])
        return True

    def _fill(self, entry, item) -> None:
        pid, exe = item
        entry.th32ProcessID = pid
        entry.szExeFile = exe

    def CloseHandle(self, handle):
        self.calls.append("CloseHandle")


class FakeWindll:
    def __init__(self, user32, kernel32, shcore):
        self.user32 = user32
        self.kernel32 = kernel32
        self.shcore = shcore


@pytest.fixture
def win32_env(monkeypatch):
    user32 = FakeUser32()
    kernel32 = FakeKernel32()
    shcore = FakeShcore()
    monkeypatch.setattr(win_input_mod, "_IS_WINDOWS", True)
    monkeypatch.setattr(ctypes, "windll", FakeWindll(user32, kernel32, shcore), raising=False)
    monkeypatch.setattr(
        ctypes,
        "WINFUNCTYPE",
        lambda *args, **kwargs: (lambda func: func),
        raising=False,
    )
    # byref() wraps its target in an opaque CArgObject; pass the object through
    # so the fake user32 can write .value/.left/... directly.
    monkeypatch.setattr(ctypes, "byref", lambda obj, offset=0: obj, raising=False)
    return user32, kernel32, shcore


def _make_input(win32_env) -> CtypesWin32Input:
    return CtypesWin32Input()


def _rect_window(hwnd: int = 1) -> WindowInfo:
    return WindowInfo(hwnd=hwnd, left=100, top=50, width=1000, height=600)


def test_constructor_raises_off_windows(monkeypatch):
    monkeypatch.setattr(win_input_mod, "_IS_WINDOWS", False)
    with pytest.raises(RealInputUnavailableError, match="requires Windows"):
        CtypesWin32Input()


def test_dpi_awareness_falls_back_and_tolerates_failure(win32_env):
    user32, _kernel32, shcore = win32_env
    shcore.raise_error = OSError("no shcore")
    CtypesWin32Input()
    assert "SetProcessDPIAware" in user32.calls
    user32.dpi_fallback_error = OSError("no dpi either")
    CtypesWin32Input()  # both paths fail silently


def test_find_windows_sorts_largest_first_and_filters(win32_env):
    user32, kernel32, _shcore = win32_env
    kernel32.processes = [(101, "ChatGPT.exe"), (102, "explorer.exe")]
    user32.add_window(1, 101, "ChatGPT", 0, 0, 800, 600)
    user32.add_window(2, 101, "ChatGPT - Settings", 100, 100, 300, 200)
    user32.add_window(3, 102, "Taskbar", 0, 0, 100, 50)
    user32.add_window(4, 101, "Hidden ChatGPT", 0, 0, 10, 10, visible=False)
    user32.add_window(5, 101, "", 0, 0, 10, 10)
    inp = _make_input(win32_env)
    windows = inp.find_windows("ChatGPT")
    assert [w.hwnd for w in windows] == [1, 2]
    assert windows[0].width == 800 and windows[0].height == 600
    assert windows[1].width == 200
    assert ("IsWindowVisible", 4) in user32.calls
    assert ("GetWindowTextLengthW", 5) in user32.calls


def test_find_windows_raises_when_no_match(win32_env):
    user32, kernel32, _shcore = win32_env
    kernel32.processes = [(101, "explorer.exe")]
    user32.add_window(1, 101, "Taskbar", 0, 0, 100, 50)
    inp = _make_input(win32_env)
    with pytest.raises(AppNotFoundError, match="window not found"):
        inp.find_windows("ChatGPT")


def test_find_main_window_returns_largest(win32_env):
    user32, kernel32, _shcore = win32_env
    kernel32.processes = [(101, "ChatGPT.exe")]
    user32.add_window(1, 101, "ChatGPT", 0, 0, 800, 600)
    user32.add_window(2, 101, "ChatGPT Small", 100, 100, 300, 200)
    inp = _make_input(win32_env)
    assert inp.find_main_window("ChatGPT").hwnd == 1


def test_window_status_reports_occlusion_and_ambiguity(win32_env):
    user32, kernel32, _shcore = win32_env
    kernel32.processes = [(101, "ChatGPT.exe"), (102, "explorer.exe")]
    # z-order (EnumWindows topmost-first): main ChatGPT, full-screen explorer,
    # then a second ChatGPT window below the explorer.
    user32.add_window(1, 101, "ChatGPT", 100, 100, 900, 700)
    user32.add_window(2, 102, "Maximized Explorer", 0, 0, 1920, 1080)
    user32.add_window(3, 101, "ChatGPT - Second", 1200, 100, 1400, 300)
    inp = _make_input(win32_env)
    status = inp.window_status("ChatGPT")
    assert status.ambiguous is True
    assert status.main.info.hwnd == 1
    assert status.main.occluded is False
    assert status.candidates[1].info.hwnd == 3
    assert status.candidates[1].occluded is True
    assert status.candidates[1].covered_by == ("Maximized Explorer",)


def test_window_status_raises_when_no_match(win32_env):
    user32, kernel32, _shcore = win32_env
    kernel32.processes = [(101, "explorer.exe")]
    user32.add_window(1, 101, "Taskbar", 0, 0, 100, 50)
    inp = _make_input(win32_env)
    with pytest.raises(AppNotFoundError, match="window not found"):
        inp.window_status("ChatGPT")


def test_resolve_raises_when_ambiguous(win32_env):
    user32, kernel32, _shcore = win32_env
    kernel32.processes = [(101, "ChatGPT.exe")]
    user32.add_window(1, 101, "ChatGPT", 0, 0, 800, 600)
    user32.add_window(2, 101, "ChatGPT", 100, 100, 300, 200)
    inp = _make_input(win32_env)
    with pytest.raises(AmbiguousTargetError, match="2 windows match"):
        inp.activate_window("ChatGPT")


def test_resolve_by_exact_or_substring_title(win32_env):
    user32, kernel32, _shcore = win32_env
    kernel32.processes = [(101, "ChatGPT.exe")]
    user32.add_window(1, 101, "ChatGPT", 0, 0, 800, 600)
    user32.add_window(2, 101, "ChatGPT - Settings", 100, 100, 300, 200)
    inp = _make_input(win32_env)
    # Exact title match wins without ambiguity.
    assert inp.activate_window("ChatGPT", title="ChatGPT - Settings").hwnd == 2
    # Case-insensitive substring narrowing also resolves to one window.
    assert inp.maximize_window("ChatGPT", title="settings").hwnd == 2
    # A substring that matches nothing must not guess: raise.
    with pytest.raises(AmbiguousTargetError, match="0 windows match"):
        inp.activate_window("ChatGPT", title="nomatch")


def test_activate_window_restores_and_foregrounds(win32_env):
    user32, kernel32, _shcore = win32_env
    kernel32.processes = [(101, "ChatGPT.exe")]
    user32.add_window(1, 101, "ChatGPT", 0, 0, 800, 600)
    inp = _make_input(win32_env)
    info = inp.activate_window("ChatGPT")
    assert info.hwnd == 1
    assert ("ShowWindow", 1, 9) in user32.calls
    assert ("SetForegroundWindow", 1) in user32.calls


def test_maximize_window_uses_sw_maximize(win32_env):
    user32, kernel32, _shcore = win32_env
    kernel32.processes = [(101, "ChatGPT.exe")]
    user32.add_window(1, 101, "ChatGPT", 0, 0, 800, 600)
    inp = _make_input(win32_env)
    info = inp.maximize_window("ChatGPT")
    assert info.hwnd == 1
    assert ("ShowWindow", 1, 3) in user32.calls


def test_focus_main_window_restores(win32_env):
    user32, kernel32, _shcore = win32_env
    kernel32.processes = [(101, "ChatGPT.exe")]
    user32.add_window(1, 101, "ChatGPT", 0, 0, 800, 600)
    inp = _make_input(win32_env)
    info = inp.focus_main_window("ChatGPT")
    assert info.hwnd == 1
    assert ("ShowWindow", 1, 9) in user32.calls


def test_show_and_foreground_wraps_win32_errors(win32_env):
    user32, kernel32, _shcore = win32_env
    kernel32.processes = [(101, "ChatGPT.exe")]
    user32.add_window(1, 101, "ChatGPT", 0, 0, 800, 600)
    user32.show_error = OSError("access denied")
    inp = _make_input(win32_env)
    with pytest.raises(RealInputFailedError, match="Win32 window state change failed"):
        inp.activate_window("ChatGPT")


def test_click_sends_cursor_and_mouse_events(win32_env):
    user32, _kernel32, _shcore = win32_env
    inp = _make_input(win32_env)
    inp.click(_rect_window(), 10, 20, mouse_button="right", click_count=2)
    assert ("SetCursorPos", 110, 70) in user32.calls
    assert user32.mouse_events == [
        _MOUSEEVENTF_RIGHTDOWN,
        _MOUSEEVENTF_RIGHTUP,
        _MOUSEEVENTF_RIGHTDOWN,
        _MOUSEEVENTF_RIGHTUP,
    ]


def test_click_defaults_to_left_single(win32_env):
    user32, _kernel32, _shcore = win32_env
    inp = _make_input(win32_env)
    inp.click(_rect_window(), 5, 5)
    assert user32.mouse_events == [_MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP]


def test_click_rejects_unsupported_button(win32_env):
    inp = _make_input(win32_env)
    with pytest.raises(UpstreamError, match="unsupported mouse_button"):
        inp.click(_rect_window(), 5, 5, mouse_button="hover")


def test_click_wraps_win32_failures(win32_env):
    user32, _kernel32, _shcore = win32_env
    user32.cursor_error = OSError("cursor boom")
    inp = _make_input(win32_env)
    with pytest.raises(RealInputFailedError, match="Win32 real click failed"):
        inp.click(_rect_window(), 5, 5)


def test_click_requires_windows(monkeypatch):
    monkeypatch.setattr(win_input_mod, "_IS_WINDOWS", False)
    inp = object.__new__(CtypesWin32Input)
    with pytest.raises(RealInputUnavailableError, match="requires Windows"):
        inp.click(_rect_window(), 5, 5)


def test_process_names_handles_snapshot_failure(win32_env):
    _user32, kernel32, _shcore = win32_env
    kernel32.snapshot_fails = True
    inp = _make_input(win32_env)
    assert inp._process_names() == {}
    assert kernel32.calls == ["CreateToolhelp32Snapshot"]


def test_process_names_handles_first_failure(win32_env):
    _user32, kernel32, _shcore = win32_env
    inp = _make_input(win32_env)
    assert inp._process_names() == {}
    assert kernel32.calls == [
        "CreateToolhelp32Snapshot",
        "Process32FirstW",
        "CloseHandle",
    ]


def test_process_names_collects_all_entries(win32_env):
    _user32, kernel32, _shcore = win32_env
    kernel32.processes = [(101, "a.exe"), (102, "b.exe"), (103, "c.exe")]
    inp = _make_input(win32_env)
    assert inp._process_names() == {101: "a.exe", 102: "b.exe", 103: "c.exe"}
    assert kernel32.calls.count("Process32NextW") == 3
    assert "CloseHandle" in kernel32.calls


def test_process_names_off_windows_returns_empty(monkeypatch):
    monkeypatch.setattr(win_input_mod, "_IS_WINDOWS", False)
    inp = object.__new__(CtypesWin32Input)
    assert inp._process_names() == {}


def test_find_windows_requires_windows(monkeypatch):
    monkeypatch.setattr(win_input_mod, "_IS_WINDOWS", False)
    inp = object.__new__(CtypesWin32Input)
    with pytest.raises(RealInputUnavailableError, match="window management"):
        inp.find_windows("ChatGPT")
