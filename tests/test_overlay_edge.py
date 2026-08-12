from __future__ import annotations

import ctypes

import pytest

import lean_computer_use_mcp.record.overlay as overlay_mod
from lean_computer_use_mcp.record.overlay import WinGlowOverlay

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


class _PopScript:
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
    monkeypatch.setattr(ctypes, "windll", windll, raising=False)
    monkeypatch.setattr(overlay_mod, "_user32_cache", None)
    monkeypatch.setattr(overlay_mod, "_gdi32_cache", None)
    return windll


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


def _install_loop_basics(windll, get_script):
    windll.user32.set_behavior("RegisterClassW", lambda *a: 1000)
    windll.user32.set_behavior("CreateWindowExW", lambda *a: 1)
    windll.user32.set_behavior("ShowWindow", lambda *a: True)
    windll.user32.set_behavior("GetMessageW", get_script)
    windll.user32.set_behavior("DestroyWindow", lambda hwnd: True)
    windll.user32.set_behavior("PostQuitMessage", lambda *a: None)
    windll.user32.set_behavior("TranslateMessage", lambda msg: True)
    windll.user32.set_behavior("DispatchMessageW", lambda msg: True)
    windll.user32.set_behavior("PostThreadMessageW", lambda *a: True)
    windll.user32.set_behavior("SetProcessDPIAware", lambda: True)
    windll.user32.set_behavior("DefWindowProcW", lambda *a: 7)
    windll.kernel32.set_behavior("GetModuleHandleW", lambda name: 12345)
    windll.kernel32.set_behavior("GetCurrentThreadId", lambda: 4242)
    windll.shcore.set_behavior("SetProcessDpiAwareness", lambda value: None)


def test_show_times_out_when_thread_never_ready(win_env, monkeypatch):
    overlay = WinGlowOverlay()
    overlay._run = lambda: None  # never sets _ready
    monkeypatch.setattr(overlay._ready, "wait", lambda timeout: False)
    with pytest.raises(RuntimeError, match="timed out"):
        overlay.show()
    overlay._thread.join(timeout=2)


def test_show_raises_when_no_windows_created(win_env):
    overlay = WinGlowOverlay()
    overlay._run = lambda: overlay._ready.set()  # ready but no windows
    with pytest.raises(RuntimeError, match="creation failed"):
        overlay.show()
    overlay._thread.join(timeout=2)


def test_run_skips_nonpositive_strips(win_env):
    # Virtual screen 1920x10: band clamps to 5, left/right strips have
    # height 0 and are skipped; only top/bottom windows are created.
    created = []

    def create_window(*args):
        created.append(args[3:7])
        return 1

    win_env.user32.set_behavior("RegisterClassW", lambda *a: 1000)
    win_env.user32.set_behavior(
        "GetSystemMetrics",
        lambda index: {76: 0, 77: 0, 78: 1920, 79: 10, 0: 1024, 1: 768}[index],
    )
    win_env.user32.set_behavior("CreateWindowExW", create_window)
    win_env.user32.set_behavior("ShowWindow", lambda *a: True)
    win_env.user32.set_behavior("GetMessageW", _PopScript([None]))
    win_env.user32.set_behavior("DestroyWindow", lambda hwnd: True)
    win_env.user32.set_behavior("PostQuitMessage", lambda *a: None)
    win_env.user32.set_behavior("SetProcessDPIAware", lambda: True)
    win_env.user32.set_behavior("DefWindowProcW", lambda *a: 7)
    win_env.kernel32.set_behavior("GetModuleHandleW", lambda name: 12345)
    win_env.kernel32.set_behavior("GetCurrentThreadId", lambda: 4242)
    win_env.shcore.set_behavior("SetProcessDpiAwareness", lambda value: None)

    overlay = WinGlowOverlay(animate=False)
    overlay._run()
    assert len(created) == 2  # only top and bottom strips
    assert overlay._hwnds == []


def test_run_skips_failed_window_creation(win_env):
    pool = [1, 0, 3, 4]
    win_env.user32.set_behavior("RegisterClassW", lambda *a: 1000)
    win_env.user32.set_behavior("CreateWindowExW", lambda *a: pool.pop(0) if pool else 0)
    win_env.user32.set_behavior(
        "GetSystemMetrics",
        lambda index: {76: 0, 77: 0, 78: 1920, 79: 1080, 0: 1024, 1: 768}[index],
    )
    win_env.user32.set_behavior("ShowWindow", lambda *a: True)
    win_env.user32.set_behavior("GetMessageW", _PopScript([None]))
    win_env.user32.set_behavior("DestroyWindow", lambda hwnd: True)
    win_env.user32.set_behavior("PostQuitMessage", lambda *a: None)
    win_env.user32.set_behavior("SetProcessDPIAware", lambda: True)
    win_env.user32.set_behavior("DefWindowProcW", lambda *a: 7)
    win_env.kernel32.set_behavior("GetModuleHandleW", lambda name: 12345)
    win_env.kernel32.set_behavior("GetCurrentThreadId", lambda: 4242)
    win_env.shcore.set_behavior("SetProcessDpiAwareness", lambda value: None)

    overlay = WinGlowOverlay(animate=False)
    overlay._run()
    assert overlay._hwnds == []  # loop exited; hwnds were cleared


def test_animate_breaks_when_frame_deadline_passed(win_env, monkeypatch):
    _install_gdi_behaviors(win_env)
    # Frame 1: deadline reached exactly on the first inner check (break).
    # Frame 2: fresh deadline, then one peek consumes the HIDE message.
    ticks = iter([100.0, 100.0417, 100.05, 100.06, 100.1])
    monkeypatch.setattr(overlay_mod.time, "monotonic", lambda: next(ticks))
    win_env.user32.set_behavior(
        "PeekMessageW", _PopScript([(overlay_mod._WM_HIDE, 0)])
    )
    win_env.user32.set_behavior("DestroyWindow", lambda hwnd: True)
    win_env.user32.set_behavior("PostQuitMessage", lambda *a: None)
    overlay = WinGlowOverlay()
    overlay._hwnds = [1]
    overlay._strips = [(0, 0, 100, 14)]
    overlay._animate(win_env.gdi32, win_env.user32)


def test_render_frame_stops_at_short_strip_list(win_env):
    _install_gdi_behaviors(win_env)
    overlay = WinGlowOverlay()
    overlay._hwnds = [1, 2]
    overlay._strips = [(0, 0, 100, 14)]  # fewer strips than windows
    overlay._render_frame(phase=0.0)
    assert overlay._hwnds == [1, 2]
