"""Real input injection for Windows (ctypes), used by the CLI upstream client.

Windows-specific by design: `CtypesWin32Input` resolves the target window by
process name or window title, then moves the real cursor and injects mouse
events through the real input queue (`SetCursorPos` + `mouse_event`).
Synthetic `PostMessage` clicks are ignored by custom-rendered apps such as
JianYing, so this explicit opt-in path exists for coordinate clicks that must
reach the app's input queue.

Coordinate contract: `x`/`y` are screenshot pixel offsets, the same space
`cu_observe` uses for vision elements. The physical window rect from
`GetWindowRect` (this process is set per-monitor DPI aware) is added to
produce the absolute screen point, matching the upstream screenshot space.

The module stays importable on non-Windows platforms; Win32 API access is
lazy inside method bodies.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time
from dataclasses import dataclass
from typing import Any, Protocol

from lean_computer_use_mcp.errors import (
    AppNotFoundError,
    RealInputUnavailableError,
    UpstreamError,
)

_IS_WINDOWS = sys.platform == "win32"

_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040

_BUTTON_FLAGS = {
    "left": (_MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP),
    "right": (_MOUSEEVENTF_RIGHTDOWN, _MOUSEEVENTF_RIGHTUP),
    "middle": (_MOUSEEVENTF_MIDDLEDOWN, _MOUSEEVENTF_MIDDLEUP),
}

_TH32CS_SNAPPROCESS = 0x00000002


@dataclass(frozen=True)
class WindowInfo:
    """One top-level window plus its physical screen rect."""

    hwnd: int
    left: int
    top: int
    width: int
    height: int


def matches_app(exe_name: str, window_title: str, app: str) -> bool:
    """Pure matcher: app matches process name (with/without .exe) or title."""
    needle = app.strip().lower()
    if not needle:
        return False
    if needle.endswith(".exe"):
        needle = needle[:-4]
    exe = exe_name.strip().lower()
    if exe.endswith(".exe"):
        exe = exe[:-4]
    if exe == needle:
        return True
    return needle in window_title.lower()


def screen_point(window: WindowInfo, x: int, y: int) -> tuple[int, int]:
    """Map screenshot-pixel offsets to an absolute physical screen point."""
    return (window.left + int(x), window.top + int(y))


class Win32Input(Protocol):
    """Injectable real-input backend (faked in unit tests)."""

    def find_main_window(self, app: str) -> WindowInfo: ...

    def focus_main_window(self, app: str) -> WindowInfo: ...

    def click(
        self,
        window: WindowInfo,
        x: int,
        y: int,
        mouse_button: str = "left",
        click_count: int = 1,
    ) -> None: ...


class CtypesWin32Input:
    """ctypes implementation of Win32Input for Windows."""

    def __init__(self) -> None:
        if not _IS_WINDOWS:
            raise RealInputUnavailableError("real-input click requires Windows")
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32
        self._make_dpi_aware()
        self._enum_callback: Any = None  # keep the callback object alive

    def _make_dpi_aware(self) -> None:
        """Per-monitor DPI awareness so rects and cursor coords are physical."""
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001 - already aware or unavailable
            try:
                self._user32.SetProcessDPIAware()
            except Exception:  # noqa: BLE001
                pass

    def find_main_window(self, app: str) -> WindowInfo:
        if not _IS_WINDOWS:
            raise RealInputUnavailableError("real-input click requires Windows")
        exe_by_pid = self._process_names()
        best: tuple[WindowInfo, int] | None = None
        for (
            hwnd,
            pid,
            title,
            left,
            top,
            right,
            bottom,
        ) in self._titled_visible_windows():
            if not matches_app(exe_by_pid.get(pid, ""), title, app):
                continue
            area = (right - left) * (bottom - top)
            if best is None or area > best[1]:
                best = (
                    WindowInfo(
                        hwnd=int(hwnd),
                        left=int(left),
                        top=int(top),
                        width=int(right - left),
                        height=int(bottom - top),
                    ),
                    area,
                )
        if best is None:
            raise AppNotFoundError(f"real-input window not found for app {app!r}")
        return best[0]

    def focus_main_window(self, app: str) -> WindowInfo:
        """Restore and foreground the app's main window (window-level action)."""
        window = self.find_main_window(app)
        self._user32.ShowWindow(window.hwnd, 9)  # SW_RESTORE
        self._user32.SetForegroundWindow(window.hwnd)
        time.sleep(0.2)
        return window

    def click(
        self,
        window: WindowInfo,
        x: int,
        y: int,
        mouse_button: str = "left",
        click_count: int = 1,
    ) -> None:
        if not _IS_WINDOWS:
            raise RealInputUnavailableError("real-input click requires Windows")
        flags = _BUTTON_FLAGS.get(mouse_button)
        if flags is None:
            raise UpstreamError(
                f"unsupported mouse_button for real click: {mouse_button!r}"
            )
        sx, sy = screen_point(window, x, y)
        self._user32.SetCursorPos(sx, sy)
        time.sleep(0.15)
        down, up = flags
        for _ in range(max(1, click_count)):
            self._user32.mouse_event(down, 0, 0, 0, 0)
            time.sleep(0.08)
            self._user32.mouse_event(up, 0, 0, 0, 0)
            time.sleep(0.12)

    def _titled_visible_windows(self) -> list[tuple[int, int, str, int, int, int, int]]:
        entries: list[tuple[int, int, str, int, int, int, int]] = []
        enum_proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def callback(hwnd: int, _lparam: int) -> bool:
            if not self._user32.IsWindowVisible(hwnd):
                return True
            length = self._user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            self._user32.GetWindowTextW(hwnd, buf, length + 1)
            pid = wt.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            rect = wt.RECT()
            self._user32.GetWindowRect(hwnd, ctypes.byref(rect))
            entries.append(
                (
                    int(hwnd),
                    int(pid.value),
                    buf.value,
                    rect.left,
                    rect.top,
                    rect.right,
                    rect.bottom,
                )
            )
            return True

        self._enum_callback = enum_proc(callback)
        self._user32.EnumWindows(self._enum_callback, 0)
        return entries

    def _process_names(self) -> dict[int, str]:
        names: dict[int, str] = {}
        if not _IS_WINDOWS:
            return names

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wt.DWORD),
                ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        snapshot = self._kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if snapshot == ctypes.c_void_p(-1).value or snapshot == -1:
            return names
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not self._kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                return names
            while True:
                names[int(entry.th32ProcessID)] = entry.szExeFile
                if not self._kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        finally:
            self._kernel32.CloseHandle(snapshot)
        return names
