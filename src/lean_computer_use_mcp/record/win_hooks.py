"""Windows-only global input capture for the recorder.

Uses low-level ``WH_MOUSE_LL``/``WH_KEYBOARD_LL`` hooks on a dedicated
message-loop thread so a user demonstration can be captured without the
recorder owning the foreground window. The module stays importable on other
platforms; every Win32 call is made lazily inside methods that raise
:class:`~lean_computer_use_mcp.errors.RealInputUnavailableError` off Windows.

Privacy: events carry window titles and coordinates, never screenshots and
never clipboard content. The stop hotkey (default ``Ctrl+Shift+R``) is
registered with ``RegisterHotKey`` and filtered from the event stream.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from lean_computer_use_mcp.errors import RealInputUnavailableError
from lean_computer_use_mcp.record.model import InputEvent

_IS_WINDOWS = sys.platform == "win32"

_WH_MOUSE_LL = 14
_WH_KEYBOARD_LL = 13
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_VK_CONTROL = 0x11
_VK_SHIFT = 0x10
_VK_R = 0x52

_MOUSE_LBUTTONDOWN = 0x0201
_MOUSE_RBUTTONDOWN = 0x0204
_MOUSE_MBUTTONDOWN = 0x0207
_MOUSE_WHEEL = 0x020A


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wt.POINT),
        ("mouseData", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wt.WPARAM, wt.LPARAM)


def _win32_signatures() -> None:
    """Pin 64-bit-safe signatures for every user32/kernel32 call.

    Without explicit restypes, ctypes defaults to ``c_int`` (32-bit), which
    truncates 64-bit handles like ``SetWindowsHookExW`` results - the hooks
    would silently never install. Called once from :class:`WinInputHook`.
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentThreadId.restype = wt.DWORD
    kernel32.GetModuleHandleW.restype = wt.HINSTANCE
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,
        _HOOKPROC,
        wt.HINSTANCE,
        wt.DWORD,
    ]
    user32.UnhookWindowsHookEx.restype = wt.BOOL
    user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.CallNextHookEx.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        wt.WPARAM,
        wt.LPARAM,
    ]
    user32.RegisterHotKey.restype = wt.BOOL
    user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
    user32.UnregisterHotKey.restype = wt.BOOL
    user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
    user32.GetMessageW.restype = wt.BOOL
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wt.MSG),
        wt.HWND,
        wt.UINT,
        wt.UINT,
    ]
    user32.PostThreadMessageW.restype = wt.BOOL
    user32.PostThreadMessageW.argtypes = [
        wt.DWORD,
        wt.UINT,
        wt.WPARAM,
        wt.LPARAM,
    ]


@dataclass(frozen=True)
class ForegroundInfo:
    window_title: str
    window_pid: int
    rect: tuple[int, int, int, int] | None


class WinForeground:
    """Cheap foreground-window metadata capture (title, pid, screen rect)."""

    def current(self) -> ForegroundInfo:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ForegroundInfo("", 0, None)
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return ForegroundInfo(
            buf.value,
            int(pid.value),
            (rect.left, rect.top, rect.right, rect.bottom),
        )


class WinInputHook:
    """Global mouse/keyboard hook; captures InputEvents on a daemon thread.

    ``stop`` is signaled by the registered hotkey or by calling ``stop()``
    from another thread (which posts ``WM_QUIT`` to wake the message loop).
    """

    def __init__(
        self,
        stop_vk: int = _VK_R,
        stop_event: threading.Event | None = None,
        foreground: WinForeground | None = None,
        on_event: Callable[[InputEvent], None] | None = None,
    ) -> None:
        self.stop_vk = stop_vk
        self.stop_event = stop_event or threading.Event()
        self.foreground = foreground or WinForeground()
        self.on_event: Callable[[InputEvent], None] | None = on_event
        self.events: list[InputEvent] = []
        self._thread: threading.Thread | None = None
        self._hooks: list[int] = []
        self._procs: list[Any] = []
        self._thread_id = 0

    def start(self) -> None:
        if not _IS_WINDOWS:
            raise RealInputUnavailableError("input hooks require Windows")
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="lean-cu-hooks", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self._thread is not None and self._thread.is_alive() and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
            self._thread.join(timeout=5)
        self._thread = None

    def _loop(self) -> None:
        if not _IS_WINDOWS:
            return
        _win32_signatures()
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        module = kernel32.GetModuleHandleW(None)
        mouse_proc = _HOOKPROC(self._on_mouse)
        key_proc = _HOOKPROC(self._on_keyboard)
        self._procs = [mouse_proc, key_proc]
        mouse_hook = user32.SetWindowsHookExW(_WH_MOUSE_LL, mouse_proc, module, 0)
        key_hook = user32.SetWindowsHookExW(_WH_KEYBOARD_LL, key_proc, module, 0)
        self._hooks = [int(mouse_hook or 0), int(key_hook or 0)]
        user32.RegisterHotKey(None, 1, _MOD_CONTROL | _MOD_SHIFT, self.stop_vk)
        try:
            msg = wt.MSG()
            while not self.stop_event.is_set():
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == 0:  # WM_QUIT
                    break
                if msg.message == _WM_HOTKEY and msg.wParam == 1:
                    self.stop_event.set()
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnregisterHotKey(None, 1)
            for hook in self._hooks:
                if hook:
                    user32.UnhookWindowsHookEx(hook)
            self._hooks = []

    def _capture(self, kind: str, **extra: Any) -> InputEvent:
        info = self.foreground.current()
        return InputEvent(
            ts=time.time(),
            kind=kind,
            window_title=info.window_title,
            window_pid=info.window_pid,
            window_rect=info.rect,
            **extra,
        )

    def _record(self, event: InputEvent) -> None:
        """Append an event and notify the recorder (live steps, snapshots)."""
        self.events.append(event)
        if self.on_event is not None:
            self.on_event(event)

    def _on_mouse(self, code: int, wparam: int, lparam: int) -> int:
        if code >= 0 and lparam:
            data = ctypes.cast(lparam, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
            if wparam == _MOUSE_LBUTTONDOWN:
                self._record(
                    self._capture(
                        "mouse_down", x=int(data.pt.x), y=int(data.pt.y), button="left"
                    )
                )
            elif wparam == _MOUSE_RBUTTONDOWN:
                self._record(
                    self._capture(
                        "mouse_down", x=int(data.pt.x), y=int(data.pt.y), button="right"
                    )
                )
            elif wparam == _MOUSE_MBUTTONDOWN:
                self._record(
                    self._capture(
                        "mouse_down",
                        x=int(data.pt.x),
                        y=int(data.pt.y),
                        button="middle",
                    )
                )
            elif wparam == _MOUSE_WHEEL:
                delta = ctypes.c_short((data.mouseData >> 16) & 0xFFFF).value
                self._record(
                    self._capture(
                        "wheel",
                        x=int(data.pt.x),
                        y=int(data.pt.y),
                        wheel_delta=int(delta),
                    )
                )
        return self._call_next(code, wparam, lparam)

    def _on_keyboard(self, code: int, wparam: int, lparam: int) -> int:
        if code >= 0 and lparam:
            data = ctypes.cast(lparam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
            vk = int(data.vkCode)
            if self._is_stop_combo(vk):
                return self._call_next(code, wparam, lparam)
            if wparam == 0x0100:  # WM_KEYDOWN
                self._record(self._capture("key_down", vk=vk))
            elif wparam == 0x0101:  # WM_KEYUP
                self._record(self._capture("key_up", vk=vk))
        return self._call_next(code, wparam, lparam)

    def _is_stop_combo(self, vk: int) -> bool:
        if vk != self.stop_vk:
            return False
        user32 = ctypes.windll.user32
        ctrl = bool(user32.GetAsyncKeyState(_VK_CONTROL) & 0x8000)
        shift = bool(user32.GetAsyncKeyState(_VK_SHIFT) & 0x8000)
        return ctrl and shift

    def _call_next(self, code: int, wparam: int, lparam: int) -> int:
        if self._hooks and self._hooks[0]:
            return ctypes.windll.user32.CallNextHookEx(
                self._hooks[0], code, wparam, lparam
            )
        return 0
