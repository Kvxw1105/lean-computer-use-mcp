"""Coverage for the WinGlowOverlay Win32 path with stubbed ctypes backends."""

from __future__ import annotations

import ctypes
import threading

import pytest
from PIL import Image

import lean_computer_use_mcp.record.overlay as overlay_mod
from lean_computer_use_mcp.errors import RealInputUnavailableError
from lean_computer_use_mcp.record.overlay import (
    _WM_HIDE,
    _WM_QUIT,
    _WM_UPDATE,
    WinGlowOverlay,
    _def_window_proc,
    premultiply_alpha,
    render_edge,
    render_glow,
    to_bgra_bytes,
)

_WM_NULL = 0x0000


class _FakeWinFunc:
    def __init__(self, impl=None):
        self.restype = None
        self.argtypes = None
        self._impl = impl

    def __call__(self, *args):
        if self._impl is None:
            return 0
        return self._impl(*args)


class _FuncDll:
    def __init__(self):
        self._funcs: dict[str, _FakeWinFunc] = {}
        self._behaviors: dict[str, object] = {}

    def set_behavior(self, name, impl) -> None:
        self._behaviors[name] = impl
        self._funcs.pop(name, None)

    def __getattr__(self, name):
        func = self._funcs.get(name)
        if func is None:
            func = self._funcs[name] = _FakeWinFunc(self._behaviors.get(name))
        return func


class _FakeWindll:
    def __init__(self):
        self.user32 = _FuncDll()
        self.kernel32 = _FuncDll()
        self.gdi32 = _FuncDll()
        self.shcore = _FuncDll()


class _QueuedMessages:
    """GetMessageW stand-in that blocks until items arrive (None = quit)."""

    def __init__(self):
        self.items: list[tuple[int, int] | None] = []
        self._condition = threading.Condition()

    def add(self, item):
        with self._condition:
            self.items.append(item)
            self._condition.notify()

    def add_front(self, item):
        with self._condition:
            self.items.insert(0, item)
            self._condition.notify()

    def __call__(self, msg, *args):
        with self._condition:
            while not self.items:
                self._condition.wait()
            item = self.items.pop(0)
        if item is None:
            return 0
        msg._obj.message = item[0]
        msg._obj.wParam = item[1]
        return 1


class _PopScript:
    """Non-blocking PeekMessageW/GetMessageW: None = no message."""

    def __init__(self, items):
        self.items = list(items)

    def __call__(self, msg, *args):
        if not self.items:
            return 0
        item = self.items.pop(0)
        if item is None:
            return 0
        msg._obj.message = item[0]
        msg._obj.wParam = item[1]
        return 1


@pytest.fixture
def win_env(monkeypatch):
    windll = _FakeWindll()
    monkeypatch.setattr(overlay_mod, "_IS_WINDOWS", True)
    monkeypatch.setattr(ctypes, "windll", windll)
    monkeypatch.setattr(overlay_mod, "_user32_cache", None)
    monkeypatch.setattr(overlay_mod, "_gdi32_cache", None)
    return windll


def _install_run_behaviors(windll, get_message, create_hwnds=(1, 2, 3, 4)):
    hwnd_pool = list(create_hwnds)
    windll.user32.set_behavior("RegisterClassW", lambda *a: 1000)
    windll.user32.set_behavior(
        "CreateWindowExW", lambda *a: hwnd_pool.pop(0) if hwnd_pool else 0
    )
    windll.user32.set_behavior("ShowWindow", lambda *a: True)
    windll.user32.set_behavior("GetMessageW", get_message)
    windll.user32.set_behavior("TranslateMessage", lambda msg: True)
    windll.user32.set_behavior("DispatchMessageW", lambda msg: True)
    windll.user32.set_behavior("DestroyWindow", lambda hwnd: True)
    windll.user32.set_behavior("PostQuitMessage", lambda *a: None)
    windll.user32.set_behavior("PostThreadMessageW", lambda *a: True)
    windll.user32.set_behavior(
        "GetSystemMetrics",
        _MetricsScript({76: 0, 77: 0, 78: 1920, 79: 1080, 0: 1024, 1: 768}),
    )
    windll.user32.set_behavior("SetProcessDPIAware", lambda: True)
    windll.user32.set_behavior("DefWindowProcW", lambda *a: 7)
    windll.kernel32.set_behavior("GetModuleHandleW", lambda name: 12345)
    windll.kernel32.set_behavior("GetCurrentThreadId", lambda: 4242)
    windll.shcore.set_behavior("SetProcessDpiAwareness", lambda value: None)


class _MetricsScript:
    def __init__(self, values):
        self.values = dict(values)
        self.calls: list[int] = []

    def __call__(self, index):
        self.calls.append(index)
        return self.values.get(index, 0)


def _install_gdi_behaviors(windll):
    buffer = ctypes.create_string_buffer(1024 * 1024)

    def create_dib_section(hdc, bmi, colors, bits, handle, flags):
        bits._obj.value = ctypes.addressof(buffer)
        return 30

    windll.gdi32.set_behavior("GetDeviceCaps", lambda hdc, index: 96)
    windll.gdi32.set_behavior("CreateCompatibleDC", lambda hdc: 20)
    windll.gdi32.set_behavior("CreateDIBSection", create_dib_section)
    windll.gdi32.set_behavior("SelectObject", lambda hdc, obj: 40)
    windll.gdi32.set_behavior("DeleteObject", lambda obj: True)
    windll.gdi32.set_behavior("DeleteDC", lambda hdc: True)
    windll.user32.set_behavior("GetDC", lambda hwnd: 10)
    windll.user32.set_behavior("ReleaseDC", lambda hwnd, hdc: True)
    windll.user32.set_behavior("UpdateLayeredWindow", lambda *a: True)
    return buffer


# --- pure helpers -----------------------------------------------------------


def test_render_glow_band_wider_than_screen_returns_empty():
    img = render_glow(2, 2)
    assert img.size == (2, 2)
    assert img.getpixel((0, 0))[3] == 0


def test_render_edge_nonpositive_length_returns_empty():
    img = render_edge("top", 0, 14, 5, (1, 2, 3), (4, 5, 6))
    assert img.size == (1, 1)


def test_premultiply_alpha_converts_non_rgba():
    img = Image.new("RGB", (4, 4), (255, 0, 0))
    out = premultiply_alpha(img)
    assert out.mode == "RGBA"
    assert out.getpixel((0, 0)) == (255, 0, 0, 255)


def test_to_bgra_bytes_converts_non_rgba():
    img = Image.new("RGB", (4, 4), (255, 0, 0))
    data = to_bgra_bytes(img)
    assert data[:4] == b"\x00\x00\xff\xff"  # B,G,R,A


def test_def_window_proc_delegates(win_env):
    win_env.user32.set_behavior("DefWindowProcW", lambda *a: 7)
    assert _def_window_proc(1, 2, 3, 4) == 7


# --- lifecycle --------------------------------------------------------------


def test_show_raises_off_windows(monkeypatch):
    monkeypatch.setattr(overlay_mod, "_IS_WINDOWS", False)
    with pytest.raises(RealInputUnavailableError, match="overlay requires Windows"):
        WinGlowOverlay().show()


def test_show_returns_when_already_shown(win_env):
    overlay = WinGlowOverlay()
    overlay._hwnds = [1]
    overlay.show()  # no second thread
    assert overlay._thread is None


def test_show_starts_thread_and_posts_update(win_env):
    messages = _QueuedMessages()
    _install_run_behaviors(win_env, messages)
    win_env.user32.set_behavior(
        "PostThreadMessageW",
        lambda tid, msg, wparam, lparam: messages.add_front((_WM_UPDATE, 1)) or True,
    )

    overlay = WinGlowOverlay(animate=False)
    overlay.show()
    messages.add((_WM_HIDE, 0))
    messages.add(None)
    overlay._thread.join(timeout=5)
    assert overlay._thread_id == 4242
    assert overlay._hwnds == []  # loop finished and cleared
    assert overlay._strips == []


def test_hide_clears_when_thread_finished(win_env):
    overlay = WinGlowOverlay()
    overlay._hwnds = [1]
    done = threading.Event()
    thread = threading.Thread(target=done.set, daemon=True)
    thread.start()
    thread.join()
    overlay._thread = thread
    overlay.hide()
    assert overlay._hwnds == []
    assert overlay._thread is not None  # hide only clears hwnds for dead threads


def test_hide_posts_message_and_joins_live_thread(win_env):
    gate = threading.Event()
    overlay = WinGlowOverlay()
    overlay._thread_id = 4242
    overlay._hwnds = [1]
    overlay._thread = threading.Thread(target=gate.wait, daemon=True)
    overlay._thread.start()
    overlay.hide()
    gate.set()  # release the waiter after the join timeout
    assert overlay._thread is None


# --- message loop internals -------------------------------------------------


def test_run_full_lifecycle(win_env):
    _install_run_behaviors(
        win_env,
        _PopScript([(_WM_NULL, 0), (_WM_UPDATE, 1), (_WM_HIDE, 0), None]),
    )
    overlay = WinGlowOverlay(animate=False)
    overlay._run()
    assert overlay._hwnds == []
    assert overlay._strips == []
    assert overlay._width == 1920
    assert overlay._height == 1080


def test_run_register_failure_sets_ready(win_env):
    win_env.user32.set_behavior("RegisterClassW", lambda *a: 0)
    overlay = WinGlowOverlay(animate=False)
    overlay._run()
    assert overlay._ready.is_set()
    assert overlay._hwnds == []


def test_animate_returns_when_disabled_or_no_windows(win_env):
    _install_run_behaviors(win_env, _PopScript([]))
    overlay = WinGlowOverlay(animate=False)
    overlay._hwnds = []
    overlay._animate(win_env.gdi32, win_env.user32)  # disabled
    overlay._animate_on = True
    overlay._animate(win_env.gdi32, win_env.user32)  # no windows


def test_animate_hide_message_destroys_and_returns(win_env):
    _install_gdi_behaviors(win_env)
    win_env.user32.set_behavior("PeekMessageW", _PopScript([(_WM_HIDE, 0)]))
    win_env.user32.set_behavior("DestroyWindow", lambda hwnd: True)
    win_env.user32.set_behavior("PostQuitMessage", lambda *a: None)
    overlay = WinGlowOverlay()
    overlay._hwnds = [1]
    overlay._strips = [(0, 0, 100, 14)]
    overlay._animate(win_env.gdi32, win_env.user32)
    assert overlay._hwnds == [1]  # cleared by _run, not by _animate


def test_animate_quit_message_returns(win_env):
    _install_gdi_behaviors(win_env)
    win_env.user32.set_behavior("PeekMessageW", _PopScript([(_WM_QUIT, 0)]))
    overlay = WinGlowOverlay()
    overlay._hwnds = [1]
    overlay._strips = [(0, 0, 100, 14)]
    overlay._animate(win_env.gdi32, win_env.user32)


def test_animate_dispatches_ordinary_and_sleeps(win_env):
    _install_gdi_behaviors(win_env)
    # First peek: no message -> sleep. Second: ordinary message -> dispatch.
    # Third: HIDE -> cleanup + return.
    win_env.user32.set_behavior(
        "PeekMessageW", _PopScript([None, (_WM_NULL, 0), (_WM_HIDE, 0)])
    )
    win_env.user32.set_behavior("DestroyWindow", lambda hwnd: True)
    win_env.user32.set_behavior("PostQuitMessage", lambda *a: None)
    overlay = WinGlowOverlay()
    overlay._hwnds = [1]
    overlay._strips = [(0, 0, 100, 14)]
    overlay._animate(win_env.gdi32, win_env.user32)


def test_render_frame_updates_each_strip(win_env):
    _install_gdi_behaviors(win_env)
    overlay = WinGlowOverlay()
    overlay._hwnds = [1]
    overlay._strips = [(0, 0, 100, 14)]
    overlay._render_frame(phase=0.0)
    assert win_env.user32._funcs["UpdateLayeredWindow"]._impl is not None


def test_apply_update_early_returns(win_env):
    _install_gdi_behaviors(win_env)
    win_env.user32.set_behavior("GetDC", lambda hwnd: 0)
    overlay = WinGlowOverlay()
    overlay._apply_update(1, win_env.gdi32, win_env.user32, 0, 0, 100, 14, b"data")


def test_apply_update_no_mem_dc(win_env):
    _install_gdi_behaviors(win_env)
    win_env.gdi32.set_behavior("CreateCompatibleDC", lambda hdc: 0)
    overlay = WinGlowOverlay()
    overlay._apply_update(1, win_env.gdi32, win_env.user32, 0, 0, 100, 14, b"data")


def test_apply_update_no_bitmap(win_env):
    _install_gdi_behaviors(win_env)
    win_env.gdi32.set_behavior("CreateDIBSection", lambda *a: 0)
    overlay = WinGlowOverlay()
    overlay._apply_update(1, win_env.gdi32, win_env.user32, 0, 0, 100, 14, b"data")


def test_virtual_screen_rect_primary_fallback(win_env):
    metrics = _MetricsScript({76: 0, 77: 0, 78: 0, 79: 0, 0: 1024, 1: 768})
    win_env.user32.set_behavior("GetSystemMetrics", metrics)
    overlay = WinGlowOverlay()
    assert overlay._virtual_screen_rect() == (0, 0, 1024, 768)
    assert metrics.calls == [76, 77, 78, 79, 0, 1]


def test_make_dpi_aware_falls_back(win_env):
    win_env.shcore.set_behavior(
        "SetProcessDpiAwareness", lambda value: (_ for _ in ()).throw(OSError("no"))
    )
    overlay = WinGlowOverlay()
    overlay._make_dpi_aware()  # user32.SetProcessDPIAware succeeds
    win_env.user32.set_behavior(
        "SetProcessDPIAware", lambda: (_ for _ in ()).throw(OSError("no"))
    )
    overlay._make_dpi_aware()  # both fail silently
