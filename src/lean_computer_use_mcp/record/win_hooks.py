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
from dataclasses import dataclass, replace
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

_GCS_COMPSTR = 0x0008
_GCS_RESULTSTR = 0x0800

_MOUSE_MOUSEMOVE = 0x0200
_MOUSE_LBUTTONDOWN = 0x0201
_MOUSE_LBUTTONUP = 0x0202
_MOUSE_RBUTTONDOWN = 0x0204
_MOUSE_RBUTTONUP = 0x0205
_MOUSE_MBUTTONDOWN = 0x0207
_MOUSE_MBUTTONUP = 0x0208
_MOUSE_WHEEL = 0x020A

#: Mouse-move throttling during drags: a move is recorded only when it
#: moved at least this far from the last recorded move, or when at least
#: this much time has elapsed since it, whichever comes first. This keeps
#: recording.json small on long timeline/upload drags while preserving
#: the gesture shape (press point, last position, release point).
_MOVE_MIN_DISTANCE = 2
_MOVE_MIN_INTERVAL = 0.03


def should_record_move(
    last: tuple[int, int, float] | None,
    x: int,
    y: int,
    now: float,
    min_distance: float = _MOVE_MIN_DISTANCE,
    min_interval: float = _MOVE_MIN_INTERVAL,
) -> bool:
    """Whether a mouse move should be recorded given the last one.

    ``last`` is the ``(x, y, monotonic_ts)`` of the last recorded move;
    ``None`` (fresh gesture) always records. Moves closer than
    ``min_distance`` pixels and faster than ``min_interval`` seconds are
    merged into one event.
    """
    if last is None:
        return True
    dx = x - last[0]
    dy = y - last[1]
    moved = dx * dx + dy * dy >= min_distance * min_distance
    return moved or now - last[2] >= min_interval

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


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("flags", wt.DWORD),
        ("hwndActive", wt.HWND),
        ("hwndFocus", wt.HWND),
        ("hwndCapture", wt.HWND),
        ("hwndMenuOwner", wt.HWND),
        ("hwndMoveSize", wt.HWND),
        ("hwndCaret", wt.HWND),
        ("rcCaret", wt.RECT),
    ]


# Windows-only callback type. ``WINFUNCTYPE`` does not exist in ``ctypes`` on
# other platforms; ``CFUNCTYPE`` keeps the module importable everywhere (same
# ABI fallback as ``record/overlay.py``). Only Win32 paths build callbacks.
if _IS_WINDOWS:
    _HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wt.WPARAM, wt.LPARAM)
else:
    _HOOKPROC = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_int, wt.WPARAM, wt.LPARAM)


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


class ImeSampler:
    """Samples the foreground window's IME state on every key event.

    Best effort, Windows-only: reads the focus window's input context
    (``ImmGetContext`` + ``ImmGetCompositionStringW``) and reports
    ``(ime_open, composition, newly_committed)``. A commit is detected when
    the result string differs from the previously sampled one; identical
    repeated commits (typing the same word twice) are reported as
    composition transitions by the step builder, which keeps the raw key
    sequence as the always-correct fallback. Any failure returns a closed
    state so the recorder never breaks.
    """

    def __init__(
        self, user32: Any | None = None, imm32: Any | None = None
    ) -> None:
        self._user32 = user32
        self._imm32 = imm32
        self._last_result = ""

    def _ensure_backends(self) -> None:
        if self._user32 is not None and self._imm32 is not None:
            return
        if not _IS_WINDOWS:
            raise RealInputUnavailableError("IME sampling requires Windows")
        user32 = ctypes.windll.user32
        imm32 = ctypes.windll.imm32
        imm32.ImmGetContext.restype = ctypes.c_void_p
        imm32.ImmGetContext.argtypes = [wt.HWND]
        imm32.ImmGetOpenStatus.restype = wt.BOOL
        imm32.ImmGetOpenStatus.argtypes = [ctypes.c_void_p]
        imm32.ImmGetCompositionStringW.restype = ctypes.c_long
        imm32.ImmGetCompositionStringW.argtypes = [
            ctypes.c_void_p,
            wt.DWORD,
            ctypes.c_void_p,
            wt.DWORD,
        ]
        imm32.ImmReleaseContext.restype = wt.BOOL
        imm32.ImmReleaseContext.argtypes = [wt.HWND, ctypes.c_void_p]
        user32.GetGUIThreadInfo.restype = wt.BOOL
        user32.GetGUIThreadInfo.argtypes = [wt.DWORD, ctypes.POINTER(_GUITHREADINFO)]
        self._user32 = user32
        self._imm32 = imm32

    def _read_string(self, imc: int, flag: int) -> str:
        size = self._imm32.ImmGetCompositionStringW(imc, flag, None, 0)
        if size <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(size // 2 + 1)
        written = self._imm32.ImmGetCompositionStringW(imc, flag, buf, size)
        if written <= 0:
            return ""
        return buf.value

    def sample(self) -> tuple[bool, str, str]:
        """Return ``(ime_open, composition, committed_since_last_sample)``."""
        try:
            self._ensure_backends()
            foreground = self._user32.GetForegroundWindow()
            if not foreground:
                return False, "", ""
            tid = wt.DWORD()
            self._user32.GetWindowThreadProcessId(foreground, ctypes.byref(tid))
            gui = _GUITHREADINFO()
            gui.cbSize = ctypes.sizeof(_GUITHREADINFO)
            if not self._user32.GetGUIThreadInfo(tid.value, ctypes.byref(gui)):
                return False, "", ""
            focus = gui.hwndFocus
            if not focus:
                return False, "", ""
            imc = self._imm32.ImmGetContext(focus)
            if not imc:
                return False, "", ""
            try:
                if not self._imm32.ImmGetOpenStatus(imc):
                    return False, "", ""
                composition = self._read_string(imc, _GCS_COMPSTR)
                result = self._read_string(imc, _GCS_RESULTSTR)
                committed = result if result and result != self._last_result else ""
                if result:
                    self._last_result = result
                return True, composition, committed
            finally:
                self._imm32.ImmReleaseContext(focus, imc)
        except (RealInputUnavailableError, OSError, AttributeError, ValueError):
            return False, "", ""


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
        ime: ImeSampler | None = None,
        ime_poll_delay: float = 0.02,
    ) -> None:
        self.stop_vk = stop_vk
        self.stop_event = stop_event or threading.Event()
        self.foreground = foreground or WinForeground()
        self.on_event: Callable[[InputEvent], None] | None = on_event
        self.ime = ime or ImeSampler()
        self.events: list[InputEvent] = []
        self._buttons_down: set[str] = set()
        self._last_move: tuple[int, int, float] | None = None
        self._pending_move: tuple[int, int, float] | None = None
        self._thread: threading.Thread | None = None
        self._hooks: list[int] = []
        self._procs: list[Any] = []
        self._thread_id = 0
        #: Delayed IME re-sample: the composition string can lag the key
        #: message by a few ms (fast short compositions), so an open IME
        #: with no commit yet schedules one more sample after this delay.
        self.ime_poll_delay = ime_poll_delay
        self._ime_poll_index: int | None = None
        self._ime_poll_at: float | None = None

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
                # Delayed IME re-samples need the loop to wake even when
                # no input arrives, so poll instead of blocking forever.
                self._poll_ime()
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    if msg.message == _WM_QUIT:
                        self.stop_event.set()
                        break
                    if msg.message == _WM_HOTKEY and msg.wParam == 1:
                        self.stop_event.set()
                        break
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                if not self.stop_event.is_set():
                    time.sleep(0.005)
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

    def _on_mouse_move(self, x: int, y: int) -> None:
        """Record a throttled mouse move during a drag gesture."""
        now = time.monotonic()
        if not should_record_move(self._last_move, x, y, now):
            self._pending_move = (x, y, now)
            return
        self._record(self._capture("mouse_move", x=x, y=y))
        self._last_move = (x, y, now)
        self._pending_move = None

    def _flush_pending_move(self) -> None:
        """Record the newest suppressed move right before release.

        Keeps the final drag position in the stream even when the cursor
        stopped for a moment before the button was released.
        """
        if self._pending_move is None:
            return
        x, y, _ts = self._pending_move
        self._record(self._capture("mouse_move", x=x, y=y))
        self._last_move = (x, y, time.monotonic())
        self._pending_move = None


    def _schedule_ime_poll(self, index: int, ime_open: bool, committed: str) -> None:
        """Arrange one delayed re-sample for a recorded key event.

        The IME can update the composition (or commit text) a few
        milliseconds after the key message, so a key that left the IME
        open with no commit yet gets one follow-up sample after
        ``ime_poll_delay``; the result is folded back into the event.
        """
        if ime_open and not committed:
            self._ime_poll_index = index
            self._ime_poll_at = time.monotonic() + self.ime_poll_delay

    def _poll_ime(self) -> None:
        """Run the delayed IME re-sample when it is due.

        Folds a late composition/commit back into the recorded key event
        (via ``dataclasses.replace``: events are frozen) so fast short
        compositions (two-letter pinyin) are not lost. The raw key
        sequence in the event remains the always-correct replay fallback
        either way.
        """
        if self._ime_poll_at is None or self._ime_poll_index is None:
            return
        if time.monotonic() < self._ime_poll_at:
            return
        index = self._ime_poll_index
        self._ime_poll_at = None
        self._ime_poll_index = None
        if index >= len(self.events):
            return
        try:
            ime_open, composition, committed = self.ime.sample()
        except Exception:  # noqa: BLE001 - a failed re-sample never breaks recording
            return
        event = self.events[index]
        self.events[index] = replace(
            event,
            ime_open=event.ime_open or ime_open,
            ime_composition=event.ime_composition or composition,
            ime_commit=event.ime_commit or committed,
        )


    def _on_mouse(self, code: int, wparam: int, lparam: int) -> int:
        if code >= 0 and lparam:
            data = ctypes.cast(lparam, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
            if wparam == _MOUSE_MOUSEMOVE and self._buttons_down:
                # Moves only matter between press and release (drag
                # gestures); outside a drag they would flood the
                # recording. During a drag, moves closer than
                # _MOVE_MIN_DISTANCE px or faster than _MOVE_MIN_INTERVAL s
                # are merged into the last recorded move.
                self._on_mouse_move(int(data.pt.x), int(data.pt.y))
            elif wparam == _MOUSE_LBUTTONDOWN:
                self._buttons_down.add("left")
                self._last_move = None
                self._pending_move = None
                self._record(
                    self._capture(
                        "mouse_down", x=int(data.pt.x), y=int(data.pt.y), button="left"
                    )
                )
            elif wparam == _MOUSE_RBUTTONDOWN:
                self._buttons_down.add("right")
                self._last_move = None
                self._pending_move = None
                self._record(
                    self._capture(
                        "mouse_down", x=int(data.pt.x), y=int(data.pt.y), button="right"
                    )
                )
            elif wparam == _MOUSE_MBUTTONDOWN:
                self._buttons_down.add("middle")
                self._last_move = None
                self._pending_move = None
                self._record(
                    self._capture(
                        "mouse_down",
                        x=int(data.pt.x),
                        y=int(data.pt.y),
                        button="middle",
                    )
                )
            elif wparam == _MOUSE_LBUTTONUP:
                self._flush_pending_move()
                self._buttons_down.discard("left")
                self._record(
                    self._capture(
                        "mouse_up", x=int(data.pt.x), y=int(data.pt.y), button="left"
                    )
                )
            elif wparam == _MOUSE_RBUTTONUP:
                self._flush_pending_move()
                self._buttons_down.discard("right")
                self._record(
                    self._capture(
                        "mouse_up", x=int(data.pt.x), y=int(data.pt.y), button="right"
                    )
                )
            elif wparam == _MOUSE_MBUTTONUP:
                self._flush_pending_move()
                self._buttons_down.discard("middle")
                self._record(
                    self._capture(
                        "mouse_up", x=int(data.pt.x), y=int(data.pt.y), button="middle"
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
            ime_open, composition, committed = self.ime.sample()
            if wparam == 0x0100:  # WM_KEYDOWN
                event = self._capture(
                    "key_down",
                    vk=vk,
                    ime_open=ime_open,
                    ime_composition=composition,
                    ime_commit=committed,
                )
                self._record(event)
                self._schedule_ime_poll(len(self.events) - 1, ime_open, committed)
            elif wparam == 0x0101:  # WM_KEYUP
                self._record(
                    self._capture(
                        "key_up",
                        vk=vk,
                        ime_open=ime_open,
                        ime_composition=composition,
                        ime_commit=committed,
                    )
                )
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
