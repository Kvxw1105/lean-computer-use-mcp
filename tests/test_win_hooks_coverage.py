"""Coverage for win_hooks Win32 paths with stubbed ctypes backends."""

from __future__ import annotations

import ctypes
import threading

import pytest

import lean_computer_use_mcp.record.win_hooks as win_hooks_mod
from lean_computer_use_mcp.errors import RealInputUnavailableError
from lean_computer_use_mcp.record.win_hooks import (
    _KBDLLHOOKSTRUCT,
    _MSLLHOOKSTRUCT,
    _VK_R,
    _WM_HOTKEY,
    _WM_QUIT,
    ForegroundInfo,
    ImeSampler,
    WinForeground,
    WinInputHook,
)

_MOUSE_MOUSEMOVE = 0x0200
_MOUSE_LBUTTONDOWN = 0x0201
_MOUSE_LBUTTONUP = 0x0202
_MOUSE_RBUTTONDOWN = 0x0204
_MOUSE_RBUTTONUP = 0x0205
_MOUSE_MBUTTONDOWN = 0x0207
_MOUSE_MBUTTONUP = 0x0208
_MOUSE_WHEEL = 0x020A
_WM_KEYDOWN = 0x0100
_WM_KEYUP = 0x0101

# ctypes.addressof() does not keep the struct alive; hold a reference so the
# hook callback reads valid memory (same lifetime rule as the real hook).
_LIVE_STRUCTS: list[object] = []


class _FakeForeground:
    def __init__(self, info: ForegroundInfo | None = None):
        self.info = info or ForegroundInfo("ChatGPT", 123, (0, 0, 1000, 800))

    def current(self) -> ForegroundInfo:
        return self.info


class _FakeIme:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = 0

    def sample(self):
        self.calls += 1
        return self.results.pop(0) if self.results else (False, "", "")


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
    """user32/kernel32/imm32 stand-in: attribute access yields callables."""

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
    def __init__(self, user32=None, kernel32=None, imm32=None):
        self.user32 = user32 if user32 is not None else _FuncDll()
        self.kernel32 = kernel32 if kernel32 is not None else _FuncDll()
        self.imm32 = imm32 if imm32 is not None else _FuncDll()


@pytest.fixture
def windll_env(monkeypatch):
    user32 = _FuncDll()
    kernel32 = _FuncDll()
    imm32 = _FuncDll()
    monkeypatch.setattr(win_hooks_mod, "_IS_WINDOWS", True)
    monkeypatch.setattr(ctypes, "windll", _FakeWindll(user32, kernel32, imm32), raising=False)
    return user32, kernel32, imm32


# --- _win32_signatures + message loop ---------------------------------------


def _install_loop_behaviors(
    user32, kernel32, messages, hooks=(111, 222), peek_script=None
):
    state = {"messages": list(messages), "hooks": list(hooks)}
    if peek_script is not None:
        script = list(peek_script)
    else:
        script = None

    def peek(msg, hwnd, lo, hi, remove):
        if script is not None:
            item = script.pop(0) if script else (_WM_QUIT, 0)
            if item is None:
                return False
            msg._obj.message = item[0]
            msg._obj.wParam = item[1]
            return True
        return _fill_message(msg, state)

    user32.set_behavior(
        "SetWindowsHookExW", lambda *a: state["hooks"].pop(0) if state["hooks"] else 0
    )
    user32.set_behavior("RegisterHotKey", lambda *a: True)
    user32.set_behavior("PeekMessageW", peek)
    user32.set_behavior("UnregisterHotKey", lambda *a: True)
    user32.set_behavior("UnhookWindowsHookEx", lambda hook: True)
    user32.set_behavior("TranslateMessage", lambda msg: True)
    user32.set_behavior("DispatchMessageW", lambda msg: True)
    kernel32.set_behavior("GetCurrentThreadId", lambda: 99)
    kernel32.set_behavior("GetModuleHandleW", lambda name: 12345)


def _fill_message(msg, state) -> bool:
    if not state["messages"]:
        return False
    message = state["messages"].pop(0)
    if message is None:
        return False
    msg._obj.message = message[0]
    msg._obj.wParam = message[1]
    return True


def test_loop_runs_signatures_hooks_and_hotkey_stop(windll_env):
    user32, kernel32, _imm32 = windll_env
    _install_loop_behaviors(user32, kernel32, [(_WM_HOTKEY, 1)])
    hook = WinInputHook()
    hook._loop()
    assert hook.stop_event.is_set()
    assert hook._hooks == []
    assert user32._funcs["UnhookWindowsHookEx"]._impl is not None


def test_loop_stops_on_quit_message(windll_env):
    user32, kernel32, _imm32 = windll_env
    _install_loop_behaviors(user32, kernel32, [(_WM_QUIT, 0)])
    hook = WinInputHook()
    hook._loop()
    assert hook.stop_event.is_set()


def test_loop_dispatches_ordinary_messages_and_sleeps(windll_env):
    user32, kernel32, _imm32 = windll_env
    # Iteration 1: no message -> sleep. Iteration 2: an ordinary message gets
    # dispatched. Iteration 3: QUIT stops the loop.
    _install_loop_behaviors(
        user32, kernel32, [], peek_script=[None, (0x0100, 0), (_WM_QUIT, 0)]
    )
    hook = WinInputHook()
    hook._loop()
    assert hook.stop_event.is_set()


def test_loop_exits_when_stop_event_preset(windll_env):
    user32, kernel32, _imm32 = windll_env
    _install_loop_behaviors(user32, kernel32, [])
    hook = WinInputHook()
    hook.stop_event.set()
    hook._loop()
    assert hook._hooks == []


def test_loop_returns_off_windows(monkeypatch):
    monkeypatch.setattr(win_hooks_mod, "_IS_WINDOWS", False)
    hook = WinInputHook()
    hook._loop()  # must not touch ctypes.windll
    assert hook._thread_id == 0


def test_start_raises_off_windows(monkeypatch):
    monkeypatch.setattr(win_hooks_mod, "_IS_WINDOWS", False)
    hook = WinInputHook()
    with pytest.raises(RealInputUnavailableError, match="input hooks require Windows"):
        hook.start()


def test_start_runs_loop_on_daemon_thread(windll_env):
    user32, kernel32, _imm32 = windll_env
    _install_loop_behaviors(user32, kernel32, [(_WM_QUIT, 0)])
    hook = WinInputHook()
    hook.start()
    try:
        hook._thread.join(timeout=5)
        assert hook._thread is not None
        assert hook._thread.daemon is True
        assert hook.stop_event.is_set()  # QUIT reached the loop
    finally:
        hook.stop()


def test_start_twice_is_noop(monkeypatch):
    monkeypatch.setattr(win_hooks_mod, "_IS_WINDOWS", True)
    hook = WinInputHook()
    hook._thread = threading.Thread(target=lambda: None)
    hook.start()  # already started: returns early
    assert hook._thread is not None


def test_stop_with_live_thread_posts_quit_and_joins(windll_env):
    user32, _kernel32, _imm32 = windll_env
    user32.set_behavior("PostThreadMessageW", lambda *a: True)
    hook = WinInputHook()
    hook._thread_id = 4242

    def waiter():
        hook.stop_event.wait(30)

    hook._thread = threading.Thread(target=waiter, daemon=True)
    hook._thread.start()
    hook.stop()
    assert hook._thread is None
    assert hook.stop_event.is_set()
    assert user32._funcs["PostThreadMessageW"]._impl is not None


def test_stop_without_thread_is_noop():
    hook = WinInputHook()
    hook.stop()
    assert hook._thread is None


# --- WinForeground ----------------------------------------------------------


class _FgUser32:
    def __init__(self, hwnd=7, title="ChatGPT", pid=4242):
        self.hwnd = hwnd
        self.title = title
        self.pid = pid
        self.calls: list[str] = []

    def GetForegroundWindow(self):
        self.calls.append("GetForegroundWindow")
        return self.hwnd

    def GetWindowTextLengthW(self, hwnd):
        return len(self.title)

    def GetWindowTextW(self, hwnd, buf, max_count):
        buf.value = self.title
        return len(self.title)

    def GetWindowThreadProcessId(self, hwnd, pid):
        pid._obj.value = self.pid
        return self.pid

    def GetWindowRect(self, hwnd, rect):
        rect._obj.left = 10
        rect._obj.top = 20
        rect._obj.right = 1010
        rect._obj.bottom = 820
        return True


def test_foreground_current_returns_metadata(monkeypatch):
    fake = _FgUser32()
    monkeypatch.setattr(ctypes, "windll", _FakeWindll(user32=fake), raising=False)
    info = WinForeground().current()
    assert info.window_title == "ChatGPT"
    assert info.window_pid == 4242
    assert info.rect == (10, 20, 1010, 820)


def test_foreground_current_empty_when_no_window(monkeypatch):
    fake = _FgUser32(hwnd=0)
    monkeypatch.setattr(ctypes, "windll", _FakeWindll(user32=fake), raising=False)
    info = WinForeground().current()
    assert info == ForegroundInfo("", 0, None)


# --- ImeSampler backends and edge branches ----------------------------------


def test_ime_ensure_backends_keeps_injected_instances():
    user32 = object()
    imm32 = object()
    sampler = ImeSampler(user32=user32, imm32=imm32)
    sampler._ensure_backends()
    assert sampler._user32 is user32
    assert sampler._imm32 is imm32


def test_ime_ensure_backends_raises_off_windows(monkeypatch):
    monkeypatch.setattr(win_hooks_mod, "_IS_WINDOWS", False)
    with pytest.raises(RealInputUnavailableError, match="IME sampling requires Windows"):
        ImeSampler()._ensure_backends()


def test_ime_ensure_backends_initializes_from_windll(windll_env):
    user32, _kernel32, imm32 = windll_env
    ImeSampler()._ensure_backends()
    # Signatures are pinned on the real backend functions.
    assert imm32._funcs["ImmGetContext"].restype is not None
    assert user32._funcs["GetGUIThreadInfo"].argtypes is not None


def test_read_string_empty_when_size_zero():
    class FakeImm:
        def ImmGetCompositionStringW(self, imc, flag, buf, size):
            return 0

    sampler = ImeSampler(user32=object(), imm32=FakeImm())
    assert sampler._read_string(100, 0x0008) == ""


def test_read_string_empty_when_write_fails():
    class FakeImm:
        def __init__(self):
            self.calls = 0

        def ImmGetCompositionStringW(self, imc, flag, buf, size):
            self.calls += 1
            return 10 if self.calls == 1 else -1

    sampler = ImeSampler(user32=object(), imm32=FakeImm())
    assert sampler._read_string(100, 0x0008) == ""


def test_sample_returns_closed_when_no_foreground():
    class FakeUser:
        def GetForegroundWindow(self):
            return 0

    sampler = ImeSampler(user32=FakeUser(), imm32=object())
    assert sampler.sample() == (False, "", "")


def test_sample_returns_closed_when_gui_thread_info_fails():
    class FakeUser:
        def GetForegroundWindow(self):
            return 7

        def GetWindowThreadProcessId(self, hwnd, out):
            out._obj.value = 42
            return 42

        def GetGUIThreadInfo(self, tid, gui):
            return False

    sampler = ImeSampler(user32=FakeUser(), imm32=object())
    assert sampler.sample() == (False, "", "")


def test_sample_returns_closed_when_no_focus():
    class FakeUser:
        def GetForegroundWindow(self):
            return 7

        def GetWindowThreadProcessId(self, hwnd, out):
            out._obj.value = 42
            return 42

        def GetGUIThreadInfo(self, tid, gui):
            gui._obj.hwndFocus = 0
            return True

    sampler = ImeSampler(user32=FakeUser(), imm32=object())
    assert sampler.sample() == (False, "", "")


def test_sample_returns_closed_when_no_input_context():
    class FakeUser:
        def GetForegroundWindow(self):
            return 7

        def GetWindowThreadProcessId(self, hwnd, out):
            out._obj.value = 42
            return 42

        def GetGUIThreadInfo(self, tid, gui):
            gui._obj.hwndFocus = 5
            return True

    class FakeImm:
        def ImmGetContext(self, focus):
            return 0

    sampler = ImeSampler(user32=FakeUser(), imm32=FakeImm())
    assert sampler.sample() == (False, "", "")


# --- mouse/keyboard callbacks -----------------------------------------------


def _mouse_lparam(x: int, y: int, mouse_data: int = 0) -> int:
    struct = _MSLLHOOKSTRUCT()
    struct.pt.x = x
    struct.pt.y = y
    struct.mouseData = mouse_data
    _LIVE_STRUCTS.append(struct)
    return ctypes.addressof(struct)


def _key_lparam(vk: int) -> int:
    struct = _KBDLLHOOKSTRUCT()
    struct.vkCode = vk
    _LIVE_STRUCTS.append(struct)
    return ctypes.addressof(struct)


def test_on_mouse_full_drag_gesture():
    hook = WinInputHook(foreground=_FakeForeground(), ime=_FakeIme())
    hook._on_mouse(0, _MOUSE_LBUTTONDOWN, _mouse_lparam(10, 10))
    hook._on_mouse(0, _MOUSE_MOUSEMOVE, _mouse_lparam(50, 50))
    hook._on_mouse(0, _MOUSE_MOUSEMOVE, _mouse_lparam(50, 50))  # suppressed move
    hook._on_mouse(0, _MOUSE_LBUTTONUP, _mouse_lparam(51, 51))
    kinds = [(event.kind, event.button, event.x, event.y) for event in hook.events]
    assert kinds == [
        ("mouse_down", "left", 10, 10),
        ("mouse_move", None, 50, 50),
        ("mouse_move", None, 50, 50),  # pending move flushed before release
        ("mouse_up", "left", 51, 51),
    ]


def test_on_mouse_right_middle_and_wheel():
    hook = WinInputHook(foreground=_FakeForeground(), ime=_FakeIme())
    hook._on_mouse(0, _MOUSE_RBUTTONDOWN, _mouse_lparam(1, 2))
    hook._on_mouse(0, _MOUSE_RBUTTONUP, _mouse_lparam(1, 2))
    hook._on_mouse(0, _MOUSE_MBUTTONDOWN, _mouse_lparam(3, 4))
    hook._on_mouse(0, _MOUSE_MBUTTONUP, _mouse_lparam(3, 4))
    hook._on_mouse(0, _MOUSE_WHEEL, _mouse_lparam(5, 6, mouse_data=0x00780000))
    hook._on_mouse(0, _MOUSE_WHEEL, _mouse_lparam(5, 6, mouse_data=0xFF880000))
    assert [event.kind for event in hook.events] == [
        "mouse_down",
        "mouse_up",
        "mouse_down",
        "mouse_up",
        "wheel",
        "wheel",
    ]
    assert hook.events[1].button == "right"
    assert hook.events[3].button == "middle"
    assert [event.wheel_delta for event in hook.events[4:]] == [120, -120]


def test_on_mouse_ignores_negative_code_without_windll():
    hook = WinInputHook()
    assert hook._on_mouse(-1, 0, 0) == 0  # _call_next with no hooks


def test_on_keyboard_records_down_up_and_schedules_ime_poll():
    ime = _FakeIme([(True, "ni", "")])
    hook = WinInputHook(foreground=_FakeForeground(), ime=ime)
    hook._on_keyboard(0, _WM_KEYDOWN, _key_lparam(0x4E))
    hook._on_keyboard(0, _WM_KEYUP, _key_lparam(0x4E))
    assert [event.kind for event in hook.events] == ["key_down", "key_up"]
    assert hook.events[0].ime_open is True
    assert hook.events[0].ime_composition == "ni"
    assert hook._ime_poll_index == 0  # open IME without commit schedules a poll


def test_on_keyboard_committed_text_skips_poll():
    ime = _FakeIme([(True, "", "\u5b57")])
    hook = WinInputHook(foreground=_FakeForeground(), ime=ime)
    hook._on_keyboard(0, _WM_KEYDOWN, _key_lparam(0x5A))
    assert hook.events[0].ime_commit == "\u5b57"
    assert hook._ime_poll_at is None


def test_on_keyboard_stop_combo_filters_event(windll_env):
    user32, _kernel32, _imm32 = windll_env
    user32.set_behavior(
        "GetAsyncKeyState", lambda vk: 0x8000 if vk in (0x11, 0x10) else 0
    )
    hook = WinInputHook(stop_vk=_VK_R, foreground=_FakeForeground(), ime=_FakeIme())
    assert hook._is_stop_combo(_VK_R) is True
    hook._on_keyboard(0, _WM_KEYDOWN, _key_lparam(_VK_R))
    assert hook.events == []  # hotkey never recorded
    assert hook._is_stop_combo(0x41) is False  # non-stop key


def test_call_next_forwards_when_hooks_installed(windll_env):
    user32, _kernel32, _imm32 = windll_env
    forwarded = []
    user32.set_behavior("CallNextHookEx", lambda *a: forwarded.append(a) or 7)
    hook = WinInputHook()
    hook._hooks = [999]
    assert hook._call_next(0, 1, 2) == 7
    assert forwarded == [(999, 0, 1, 2)]


def test_record_invokes_on_event_callback():
    seen = []
    hook = WinInputHook(
        foreground=_FakeForeground(),
        ime=_FakeIme(),
        on_event=lambda event: seen.append(event.kind),
    )
    hook._on_mouse(0, _MOUSE_LBUTTONDOWN, _mouse_lparam(1, 1))
    assert seen == ["mouse_down"]


# --- delayed poll guards ----------------------------------------------------


def test_poll_ime_noop_when_nothing_scheduled():
    hook = WinInputHook(ime=_FakeIme())
    hook._poll_ime()  # both guards None: return without sampling


def test_poll_ime_noop_when_index_out_of_range():
    hook = WinInputHook(ime=_FakeIme([(True, "", "\u5b57")]))
    hook._ime_poll_index = 5
    hook._ime_poll_at = 0.0
    hook._poll_ime()  # index beyond events: return, sampler untouched
    assert hook.events == []
