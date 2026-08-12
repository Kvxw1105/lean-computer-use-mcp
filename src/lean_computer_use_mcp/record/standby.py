"""Standby recording mode: a global hotkey starts recording the foreground
window without typing the ``record`` command.

The hotkey is registered system-wide with ``RegisterHotKey`` so it works
while another app has focus. If the combination is already taken by
another program, registration fails with ``ERROR_HOTKEY_ALREADY_REGISTERED``
(1409) and :class:`HotkeyListener.register` returns ``False`` - the CLI
reports the conflict and suggests an alternative instead of silently
stealing the key.

Platform split: hotkey parsing and the state machine are pure Python;
Win32 registration uses the same message-loop pattern as
``record/win_hooks.py`` and stays lazy, so the module imports everywhere.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import threading

from lean_computer_use_mcp.errors import RealInputUnavailableError

_IS_WINDOWS = sys.platform == "win32"

_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008

_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012

#: Human modifier name -> Win32 MOD_* flag. ``win`` is the Windows key.
_MODIFIERS: dict[str, int] = {
    "ctrl": _MOD_CONTROL,
    "shift": _MOD_SHIFT,
    "alt": _MOD_ALT,
    "win": _MOD_WIN,
}

#: Human key name -> virtual-key code for keys without a single character.
_NAMED_KEYS: dict[str, int] = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Parse ``"ctrl+shift+space"`` into ``(modifiers, vk)``.

    At least one modifier is required - a bare key as a global hotkey would
    swallow keystrokes from every app. Unknown modifiers or keys raise
    ``ValueError`` with the accepted values in the message.
    """
    parts = [part.strip().lower() for part in spec.split("+")]
    if not parts:
        raise ValueError("empty hotkey")
    *mod_names, key_name = parts
    if not mod_names:
        raise ValueError(
            f"hotkey {spec!r} needs at least one modifier "
            f"(ctrl/shift/alt/win), e.g. 'ctrl+shift+space'"
        )
    modifiers = 0
    for name in mod_names:
        flag = _MODIFIERS.get(name)
        if flag is None:
            raise ValueError(
                f"unknown modifier {name!r} in hotkey {spec!r}; "
                f"use ctrl, shift, alt or win"
            )
        modifiers |= flag
    key = key_name
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    else:
        vk = _NAMED_KEYS.get(key)
        if vk is None:
            raise ValueError(
                f"unknown key {key_name!r} in hotkey {spec!r}; use a letter, "
                f"digit, f1-f24 or one of "
                f"{', '.join(sorted(_NAMED_KEYS))}"
            )
    return modifiers, vk


def describe_hotkey(modifiers: int, vk: int) -> str:
    """Reverse of :func:`parse_hotkey`: flags + vk -> readable spec."""
    names = [name for name, flag in _MODIFIERS.items() if modifiers & flag]
    if not names:
        return f"vk=0x{vk:02X}"
    names = sorted(names)
    for name, code in _NAMED_KEYS.items():
        if code == vk:
            return "+".join(names) + "+" + name
    if 0x70 <= vk <= 0x87:
        return "+".join(names) + f"+F{vk - 0x70 + 1}"
    char = chr(vk) if 32 <= vk <= 126 else f"vk=0x{vk:02X}"
    return "+".join(names) + "+" + char


def _signatures() -> None:
    """Pin 64-bit-safe signatures for the four user32 calls standby uses."""
    user32 = ctypes.windll.user32
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


class HotkeyListener:
    """Registers one global hotkey and waits for it on a message-loop thread.

    ``register()`` returns ``False`` when the combination is already taken
    (``ERROR_HOTKEY_ALREADY_REGISTERED``) - never raises, so the CLI can
    print a conflict hint and exit cleanly.
    """

    _ERROR_HOTKEY_ALREADY_REGISTERED = 1409

    def __init__(self, vk: int, modifiers: int) -> None:
        self.vk = vk
        self.modifiers = modifiers
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._registered = False
        self._pressed = threading.Event()

    def register(self) -> bool:
        """Register the hotkey; ``False`` when already taken by another app."""
        if not _IS_WINDOWS:
            return False
        _signatures()
        ok = ctypes.windll.user32.RegisterHotKey(
            None, 1, self.modifiers, self.vk
        )
        if not ok:
            return False
        self._registered = True
        self._thread = threading.Thread(
            target=self._loop, name="lean-cu-standby", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        """Wake the loop (even mid-``wait``), unregister, and stop the thread.

        ``UnregisterHotKey`` must happen before ``join``: the loop thread
        clears ``_registered`` when it exits, so checking it after the join
        would leak the hotkey.
        """
        self._pressed.set()  # never block the main thread again
        if self._registered:
            ctypes.windll.user32.UnregisterHotKey(None, 1)
            self._registered = False
        if self._thread is not None and self._thread.is_alive() and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
            self._thread.join(timeout=5)
        self._thread = None

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the hotkey fires (True) or ``stop()`` (False)."""
        return self._pressed.wait(timeout)

    def _loop(self) -> None:
        """Message loop: set ``_pressed`` on WM_HOTKEY, exit on WM_QUIT.

        Registration state stays with the listener: :meth:`stop` unregisters
        before joining, so the loop never clears it on exit.
        """
        if not _IS_WINDOWS:
            return
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        msg = wt.MSG()
        while not self._pressed.is_set():
            got = ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if got in (0, -1):
                break
            if msg.message == _WM_QUIT:
                break
            if msg.message == _WM_HOTKEY and msg.wParam == 1:
                self._pressed.set()
                break
            ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))


def require_windows() -> None:
    """Raise where a real hotkey listener needs Win32 (never in --fake)."""
    if not _IS_WINDOWS:
        raise RealInputUnavailableError("standby hotkey requires Windows")
