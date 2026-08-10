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
    AmbiguousTargetError,
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


@dataclass(frozen=True)
class WindowCandidate:
    """One matching top-level window plus window-management state."""

    info: WindowInfo
    title: str
    occluded: bool = False
    covered_by: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "hwnd": self.info.hwnd,
            "rect": {
                "left": self.info.left,
                "top": self.info.top,
                "width": self.info.width,
                "height": self.info.height,
            },
            "occluded": self.occluded,
            "covered_by": list(self.covered_by),
        }


@dataclass(frozen=True)
class WindowStatus:
    """All matching windows for one app, largest first, plus occlusion info."""

    app: str
    candidates: tuple[WindowCandidate, ...]
    main: WindowCandidate
    ambiguous: bool


def coverage_state(
    target: WindowInfo, coverers: list[tuple[str, WindowInfo]]
) -> tuple[bool, tuple[str, ...]]:
    """Return (fully_covered, covering_titles) for a window rect.

    ``coverers`` are (title, WindowInfo) pairs for windows above ``target`` in
    z-order. A window counts as occluding only when its rect fully contains
    the target rect. Pure and platform-neutral so unit tests need no Win32.
    """
    covered_by: list[str] = []
    for title, window in coverers:
        if (
            window.left <= target.left
            and window.top <= target.top
            and window.left + window.width >= target.left + target.width
            and window.top + window.height >= target.top + target.height
        ):
            covered_by.append(title)
    return bool(covered_by), tuple(covered_by)


def filter_candidates(
    candidates: tuple[WindowCandidate, ...], title: str | None
) -> tuple[WindowCandidate, ...]:
    """Narrow candidates by a case-insensitive title substring.

    An empty/None query keeps every candidate; zero or multiple matches are
    the caller's ambiguity problem (never guess).
    """
    if not title or not title.strip():
        return candidates
    needle = title.strip().lower()
    return tuple(c for c in candidates if needle in c.title.lower())


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

    def find_windows(self, app: str) -> list[WindowInfo]: ...

    def window_status(self, app: str) -> WindowStatus: ...

    def focus_main_window(self, app: str) -> WindowInfo: ...

    def activate_window(self, app: str, title: str | None = None) -> WindowInfo: ...

    def maximize_window(self, app: str, title: str | None = None) -> WindowInfo: ...

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

    def find_windows(self, app: str) -> list[WindowInfo]:
        """Every titled visible window matching ``app``, largest area first.

        Multi-instance apps (e.g. a splash plus a main window) return several
        entries; the caller decides whether to pick the largest or ask.
        """
        if not _IS_WINDOWS:
            raise RealInputUnavailableError("window management requires Windows")
        exe_by_pid = self._process_names()
        windows: list[WindowInfo] = []
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
            windows.append(
                WindowInfo(
                    hwnd=int(hwnd),
                    left=int(left),
                    top=int(top),
                    width=int(right - left),
                    height=int(bottom - top),
                )
            )
        if not windows:
            raise AppNotFoundError(f"real-input window not found for app {app!r}")
        windows.sort(key=lambda w: w.width * w.height, reverse=True)
        return windows

    def find_main_window(self, app: str) -> WindowInfo:
        return self.find_windows(app)[0]

    def window_status(self, app: str) -> WindowStatus:
        """All matching windows with z-order occlusion state.

        EnumWindows enumerates top-level windows topmost-first, so every
        entry before the target in that order may visually cover it.
        """
        entries = self._titled_visible_windows()
        exe_by_pid = self._process_names()
        matching: list[tuple[WindowInfo, int, str]] = []
        for index, (hwnd, pid, title, left, top, right, bottom) in enumerate(entries):
            if not matches_app(exe_by_pid.get(pid, ""), title, app):
                continue
            matching.append(
                (
                    WindowInfo(
                        hwnd=int(hwnd),
                        left=int(left),
                        top=int(top),
                        width=int(right - left),
                        height=int(bottom - top),
                    ),
                    index,
                    title,
                )
            )
        if not matching:
            raise AppNotFoundError(f"real-input window not found for app {app!r}")
        candidates: list[WindowCandidate] = []
        for info, index, title in matching:
            above: list[tuple[str, WindowInfo]] = []
            for entry in entries[:index]:
                _hwnd, _pid, entry_title, left, top, right, bottom = entry
                above.append(
                    (
                        entry_title,
                        WindowInfo(
                            hwnd=int(_hwnd),
                            left=int(left),
                            top=int(top),
                            width=int(right - left),
                            height=int(bottom - top),
                        ),
                    )
                )
            occluded, covered_by = coverage_state(info, above)
            candidates.append(
                WindowCandidate(
                    info=info, title=title, occluded=occluded, covered_by=covered_by
                )
            )
        candidates.sort(
            key=lambda c: c.info.width * c.info.height, reverse=True
        )
        main = candidates[0]
        return WindowStatus(
            app=app,
            candidates=tuple(candidates),
            main=main,
            ambiguous=len(candidates) > 1,
        )

    def _resolve(
        self, app: str, title: str | None
    ) -> WindowCandidate:
        """Pick exactly one window; never guess among several matches."""
        status = self.window_status(app)
        if title:
            exact = [c for c in status.candidates if c.title == title]
            if len(exact) == 1:
                return exact[0]
        narrowed = filter_candidates(status.candidates, title)
        if len(narrowed) != 1:
            raise AmbiguousTargetError(
                f"{len(narrowed)} windows match app {app!r} title {title!r}: "
                + ", ".join(c.title for c in narrowed)
            )
        return narrowed[0]

    def focus_main_window(self, app: str) -> WindowInfo:
        """Restore and foreground the app's main window (window-level action)."""
        window = self.find_main_window(app)
        self._user32.ShowWindow(window.hwnd, 9)  # SW_RESTORE
        self._user32.SetForegroundWindow(window.hwnd)
        time.sleep(0.2)
        return window

    def activate_window(self, app: str, title: str | None = None) -> WindowInfo:
        """Restore + foreground one window; ambiguous matches raise."""
        chosen = self._resolve(app, title)
        self._user32.ShowWindow(chosen.info.hwnd, 9)  # SW_RESTORE
        self._user32.SetForegroundWindow(chosen.info.hwnd)
        time.sleep(0.2)
        return chosen.info

    def maximize_window(self, app: str, title: str | None = None) -> WindowInfo:
        """Restore + maximize + foreground one window; ambiguous matches raise."""
        chosen = self._resolve(app, title)
        self._user32.ShowWindow(chosen.info.hwnd, 3)  # SW_MAXIMIZE
        self._user32.SetForegroundWindow(chosen.info.hwnd)
        time.sleep(0.2)
        return chosen.info

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
