"""Virtual-key mapping used to turn raw keyboard events into intent steps.

Pure functions only: everything here is testable without a desktop. The
mapping covers Latin layout keys; IME-composed text (for example Chinese
input) is intentionally not captured and must be added to the generated
skill by hand (see docs/RECORDING.md).
"""

from __future__ import annotations

# Standard Windows virtual-key codes used by this package.
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_PAUSE = 0x13
VK_CAPITAL = 0x14
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21  # PageUp
VK_NEXT = 0x22  # PageDown
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_SNAPSHOT = 0x2C  # PrintScreen
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_LWIN = 0x5B
VK_RWIN = 0x5C
# Low-level hooks report the left/right variants of Shift/Ctrl/Alt (0xA0-0xA5);
# they must canonicalize to the same names as the generic VK codes.
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5

_MODIFIERS = {
    VK_SHIFT: "Shift",
    VK_LSHIFT: "Shift",
    VK_RSHIFT: "Shift",
    VK_CONTROL: "Control",
    VK_LCONTROL: "Control",
    VK_RCONTROL: "Control",
    VK_MENU: "Alt",
    VK_LMENU: "Alt",
    VK_RMENU: "Alt",
    VK_LWIN: "Win",
    VK_RWIN: "Win",
}

#: Canonical names for non-printable keys (used in press_key steps).
VK_NAMES = {
    VK_BACK: "Backspace",
    VK_TAB: "Tab",
    VK_RETURN: "Enter",
    VK_PAUSE: "Pause",
    VK_CAPITAL: "CapsLock",
    VK_ESCAPE: "Escape",
    VK_SPACE: "Space",
    VK_PRIOR: "PageUp",
    VK_NEXT: "PageDown",
    VK_END: "End",
    VK_HOME: "Home",
    VK_LEFT: "Left",
    VK_UP: "Up",
    VK_RIGHT: "Right",
    VK_DOWN: "Down",
    VK_SNAPSHOT: "PrintScreen",
    VK_INSERT: "Insert",
    VK_DELETE: "Delete",
}
for _f in range(1, 25):
    VK_NAMES[0x70 + _f - 1] = f"F{_f}"

#: Printable keys -> (unshifted, shifted) character pairs.
_PRINTABLE: dict[int, tuple[str, str]] = {VK_SPACE: (" ", " ")}
for _code in range(0x30, 0x3A):  # 0-9
    _PRINTABLE[_code] = (chr(_code), chr(_code))
for _code in range(0x41, 0x5B):  # A-Z keys type lowercase, shift gives uppercase
    _PRINTABLE[_code] = (chr(_code + 0x20), chr(_code))
for _code in range(0x60, 0x6A):  # numpad 0-9
    _PRINTABLE[_code] = (chr(_code - 0x30), chr(_code - 0x30))
_PRINTABLE.update(
    {
        0xBA: (";", ":"),
        0xBB: ("=", "+"),
        0xBC: (",", "<"),
        0xBD: ("-", "_"),
        0xBE: (".", ">"),
        0xBF: ("/", "?"),
        0xC0: ("`", "~"),
        0xDB: ("[", "{"),
        0xDC: ("\\", "|"),
        0xDD: ("]", "}"),
        0xDE: ("'", '"'),
        0xE2: ("\\", "|"),
    }
)


def is_modifier(vk: int) -> bool:
    return vk in _MODIFIERS


def is_printable(vk: int) -> bool:
    return vk in _PRINTABLE


def vk_name(vk: int) -> str:
    """Canonical key name; modifiers use their long form."""
    if vk in _MODIFIERS:
        return _MODIFIERS[vk]
    if vk in _PRINTABLE:
        if vk == VK_SPACE:
            return "Space"
        return chr(vk)  # A-Z, 0-9, numpad digits or an OEM character
    return VK_NAMES.get(vk, f"VK_{vk:#x}")


def vk_to_char(vk: int, shift: bool = False) -> str | None:
    """Map a printable virtual key to one character (Latin layout)."""
    pair = _PRINTABLE.get(vk)
    if pair is None:
        return None
    return pair[1 if shift else 0]


def combo_name(modifiers: set[str], vk: int) -> str:
    """Build a canonical combo string, for example ``Control+Shift+R``."""
    parts = [name for name in ("Control", "Shift", "Alt", "Win") if name in modifiers]
    if vk in _MODIFIERS:
        return "+".join(parts) or _MODIFIERS[vk]
    base = vk_to_char(vk, shift=True) or vk_name(vk)
    if base == " ":
        base = "Space"
    parts.append(base.upper() if len(base) == 1 else base)
    return "+".join(parts)
