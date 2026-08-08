"""Screen-edge recording glow overlay (Windows-first).

A borderless, click-through, always-on-top layered window that draws a soft
blue-purple glow around the screen edges while a demonstration is being
recorded. It never consumes input (``WS_EX_TRANSPARENT``) and never appears in
recordings: the artifact is text-only by design.

The rendering math (:func:`render_glow`, :func:`premultiply_alpha`,
:func:`to_bgra_bytes`) is platform-neutral and unit-testable. The Win32 window
(:class:`WinGlowOverlay`) follows the same lazy-call pattern as
``record/win_hooks.py``: the module imports everywhere, and only Windows
instantiates real handles. Use :class:`NoopOverlay` or ``make_overlay(False)``
for fake demos, tests and non-Windows platforms.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
import os
import sys
import threading
import time
from typing import Protocol

from PIL import Image, ImageChops, ImageOps

from lean_computer_use_mcp.errors import RealInputUnavailableError

_IS_WINDOWS = sys.platform == "win32"

# Window styles and messages.
_WS_POPUP = 0x80000000
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_HWND_TOPMOST = -1
_SWP_NOACTIVATE = 0x0010
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SW_SHOWNOACTIVATE = 4
_WM_APP = 0x8000
_WM_HIDE = _WM_APP + 1
_WM_UPDATE = _WM_APP + 2

# Layered-window compositing.
_AC_SRC_ALPHA = 0x01
_ULW_ALPHA = 0x00000002
_BI_RGB = 0
_DIB_RGB_COLORS = 0

# Virtual-screen metrics (physical pixels when DPI aware).
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79
_SM_CXSCREEN = 0
_SM_CYSCREEN = 1


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER)]


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HICON),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


if ctypes.sizeof(ctypes.c_void_p) == 8:
    _LRESULT = ctypes.c_ssize_t
else:
    _LRESULT = ctypes.c_long

if _IS_WINDOWS:
    _WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
else:
    _WNDPROC = ctypes.CFUNCTYPE(_LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


def _def_window_proc(
    hwnd: int, msg: int, wparam: int, lparam: int
) -> int:
    return _user32().DefWindowProcW(hwnd, msg, wparam, lparam)


def render_glow(
    width: int,
    height: int,
    band: int = 14,
    inner: int = 5,
    color_left: tuple[int, int, int] = (76, 111, 247),
    color_right: tuple[int, int, int] = (166, 88, 248),
    phase: float = 0.0,
    waves: float = 2.5,
    amplitude: float = 0.0,
) -> Image.Image:
    """Build an RGBA glow frame: transparent center, blue-purple screen edges.

    ``band`` is the glow thickness in pixels; ``inner`` is the fully opaque
    part at the very edge. The rest fades quadratically to zero so the glow
    looks soft instead of boxy. Colors blend left -> right across the screen.

    ``phase``/``waves``/``amplitude`` add a soft travelling wave along the
    edges: alpha is scaled by ``1 - amplitude*0.5*(1 - sin(2*pi*(waves*p -
    phase)))`` with ``p`` running around the screen perimeter so the four
    edges form one continuous flow. ``phase`` is measured in turns (0..1 for
    one full cycle). ``amplitude=0`` (default) keeps the frame static and
    exactly matches the original glow.
    """
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if width <= 0 or height <= 0:
        return img
    band = max(2, min(band, min(width, height) // 2))
    inner = max(0, min(inner, band - 1))
    if band >= width or band >= height:
        return img

    # Horizontal color blend across the whole screen, then reused per edge.
    edge = Image.new("RGB", (2, 1))
    edge.putpixel((0, 0), color_left)
    edge.putpixel((1, 0), color_right)
    color_full = edge.resize((width, height), Image.Resampling.BILINEAR)

    # Per-edge alpha: 255 at the screen edge, quadratic fade inward.
    alpha = Image.new("L", (1, band))
    for i in range(band):
        if i < inner:
            alpha.putpixel((0, i), 255)
        else:
            t = (i - inner) / max(1, band - inner)
            alpha.putpixel((0, i), int(255 * (1.0 - t) ** 2))
    top_mask = _edge_mask(alpha, width, band, phase, waves, amplitude, 0)
    bottom_mask = _edge_mask(alpha, width, band, phase, waves, amplitude, 1)
    left_mask = _edge_mask(alpha, height, band, phase, waves, amplitude, 2)
    right_mask = _edge_mask(alpha, height, band, phase, waves, amplitude, 3)

    mask = Image.new("L", (width, height), 0)
    for region, origin in (
        (top_mask, (0, 0)),
        (bottom_mask, (0, height - band)),
        (left_mask, (0, 0)),
        (right_mask, (width - band, 0)),
    ):
        layer = Image.new("L", (width, height), 0)
        layer.paste(region, origin)
        mask = ImageChops.lighter(mask, layer)
    r, g, b = color_full.split()
    return Image.merge("RGBA", (r, g, b, mask))


def _wave_factor(position: float, phase: float, waves: float, amplitude: float) -> float:
    """Soft travelling-wave alpha factor in ``[1-amplitude, 1]``.

    ``phase`` is in turns (0..1); the wave is 2*pi periodic in it.
    """
    if amplitude <= 0.0:
        return 1.0
    wave = math.sin(2.0 * math.pi * (waves * position - phase))
    return 1.0 - amplitude * 0.5 * (1.0 - wave)


def _edge_mask(
    profile: Image.Image,
    length: int,
    band: int,
    phase: float,
    waves: float,
    amplitude: float,
    direction: int,
) -> Image.Image:
    """Build a (length x band) alpha mask with a wave along the edge.

    ``direction`` orders the wave so the four edges form one continuous flow
    around the screen: 0 = top L->R, 1 = bottom R->L, 2 = left B->T,
    3 = right T->B. The per-column factor is built with ``putdata`` and the
    band profile with a vectorized multiply, so a 24 fps animation stays
    cheap (no per-pixel Python loop over the band).
    """
    factors = [0] * length
    for x in range(length):
        position = x / max(1, length - 1)
        if direction in (1, 2):
            position = 1.0 - position
        factors[x] = int(255 * _wave_factor(position, phase, waves, amplitude))
    factor_img = Image.new("L", (length, 1))
    factor_img.putdata(factors)
    if direction in (2, 3):
        # Vertical edges: mask(x, y) = profile(x) * factor(y), shape (band,
        # length). The left edge starts at the screen edge (profile index 0);
        # the right edge is mirrored because its region begins inward.
        profile_img = profile.rotate(90, expand=True).resize(
            (band, length), Image.Resampling.BILINEAR
        )
        factor_img = factor_img.resize(
            (band, length), Image.Resampling.BILINEAR
        )
        if direction == 3:
            profile_img = ImageOps.mirror(profile_img)
    else:
        # Horizontal edges: mask(x, y) = profile(y) * factor(x), (length,
        # band). The top edge starts at the screen edge; the bottom edge is
        # flipped because its region begins inward.
        profile_img = profile.resize(
            (length, band), Image.Resampling.BILINEAR
        )
        factor_img = factor_img.resize(
            (length, band), Image.Resampling.BILINEAR
        )
        if direction == 1:
            profile_img = ImageOps.flip(profile_img)
    return ImageChops.multiply(profile_img, factor_img)


def premultiply_alpha(img: Image.Image) -> Image.Image:
    """Convert straight alpha to premultiplied alpha (AC_SRC_ALPHA needs it)."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    r, g, b, a = img.split()
    return Image.merge(
        "RGBA",
        (
            ImageChops.multiply(r, a),
            ImageChops.multiply(g, a),
            ImageChops.multiply(b, a),
            a,
        ),
    )


def to_bgra_bytes(img: Image.Image) -> bytes:
    """32-bit top-down DIB bytes in B,G,R,A order for UpdateLayeredWindow."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    r, g, b, a = img.split()
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


class Overlay(Protocol):
    """Indicator lifecycle used by the CLI; injectable in tests."""

    def show(self) -> None: ...

    def hide(self) -> None: ...


class NoopOverlay:
    """Does nothing; used for fake demos, tests and non-Windows platforms."""

    def show(self) -> None:
        return None

    def hide(self) -> None:
        return None


def make_overlay(enabled: bool = True) -> Overlay:
    """Pick the real overlay on Windows or a no-op fallback elsewhere."""
    if not enabled or not _IS_WINDOWS:
        return NoopOverlay()
    return WinGlowOverlay()


class WinGlowOverlay:
    """Click-through topmost glow window; all Win32 access is lazy.

    The window is created on a dedicated message-loop thread (same pattern as
    ``record/win_hooks.py``). Rendering and the ``UpdateLayeredWindow`` call
    happen on that thread too, so the window is always owned and updated by
    its creating thread.
    """

    def __init__(
        self,
        band: int = 14,
        inner: int = 5,
        color_left: tuple[int, int, int] = (76, 111, 247),
        color_right: tuple[int, int, int] = (166, 88, 248),
        animate: bool = True,
        fps: float = 24.0,
        cycle_seconds: float = 2.0,
        waves: float = 2.5,
        amplitude: float = 0.3,
        scale: float = 0.5,
    ) -> None:
        self._band = band
        self._inner = inner
        self._color_left = color_left
        self._color_right = color_right
        self._animate_on = animate
        self._fps = fps
        self._cycle_seconds = cycle_seconds
        self._waves = waves
        self._amplitude = amplitude
        self._scale = scale
        self._hwnd: int | None = None
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._wndproc: _WNDPROC | None = None
        self._pending: tuple[int, int, bytes] | None = None
        self._origin = (0, 0)
        self._width = 0
        self._height = 0
        self._seq = 0  # unique window-class name per show(), so re-show works

    def show(self) -> None:
        if not _IS_WINDOWS:
            raise RealInputUnavailableError("recording overlay requires Windows")
        if self._hwnd is not None:
            return
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, name="lean-cu-overlay", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(10.0):
            raise RuntimeError("overlay window creation timed out")
        if self._hwnd is None:
            raise RuntimeError("overlay window creation failed")
        width, height = self._virtual_screen_rect()[2:]
        if width <= 0 or height <= 0:
            raise RuntimeError("cannot determine screen size")
        glow = render_glow(
            width,
            height,
            band=self._band,
            inner=self._inner,
            color_left=self._color_left,
            color_right=self._color_right,
        )
        self._pending = (width, height, to_bgra_bytes(premultiply_alpha(glow)))
        _user32().PostThreadMessageW(self._thread_id, _WM_UPDATE, 1, 0)

    def hide(self) -> None:
        thread = self._thread
        if thread is None or not thread.is_alive():
            self._hwnd = None
            return
        _user32().PostThreadMessageW(self._thread_id, _WM_HIDE, 0, 0)
        thread.join(timeout=5.0)
        self._thread = None

    def _run(self) -> None:
        user32 = _user32()
        gdi32 = _gdi32()
        self._make_dpi_aware()
        instance = ctypes.windll.kernel32.GetModuleHandleW(None)
        class_name = f"LeanCuGlowOverlay{os.getpid()}_{self._seq}"
        self._seq += 1
        self._wndproc = _WNDPROC(_def_window_proc)
        wc = _WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = instance
        wc.lpszClassName = class_name
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            self._ready.set()
            return
        x, y, width, height = self._virtual_screen_rect()
        self._origin = (x, y)
        self._width = width
        self._height = height
        hwnd = user32.CreateWindowExW(
            _WS_EX_LAYERED
            | _WS_EX_TRANSPARENT
            | _WS_EX_TOOLWINDOW
            | _WS_EX_NOACTIVATE,
            class_name,
            None,
            _WS_POPUP,
            x,
            y,
            width,
            height,
            None,
            None,
            instance,
            None,
        )
        if not hwnd:
            self._ready.set()
            return
        user32.SetWindowPos(
            hwnd,
            _HWND_TOPMOST,
            x,
            y,
            0,
            0,
            _SWP_NOACTIVATE | _SWP_NOMOVE | _SWP_NOSIZE,
        )
        user32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
        self._hwnd = hwnd
        self._thread_id = int(ctypes.windll.kernel32.GetCurrentThreadId())
        self._ready.set()
        msg = wt.MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:
                break
            if msg.message == _WM_UPDATE:
                self._animate(hwnd, gdi32, user32)
            elif msg.message == _WM_HIDE:
                user32.DestroyWindow(hwnd)
                user32.PostQuitMessage(0)
            else:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        self._hwnd = None

    def _animate(
        self, hwnd: int, gdi32: ctypes.WinDLL, user32: ctypes.WinDLL
    ) -> None:
        """Apply the first frame, then run the low-cost wave animation.

        Runs on the window-owner thread; the loop sleeps toward the next
        frame and polls for hide/quit messages with ``PeekMessageW``, so
        ``hide()`` still interrupts immediately. Animation frames are
        rendered at ``scale`` resolution (default 0.5x) - the layered
        window stretches them for free, cutting the per-frame DIB bytes by
        4x while the soft glow stays visually identical.
        """
        if self._pending is not None:
            self._apply_update(hwnd, gdi32, user32, *self._pending)
            self._pending = None
        if not self._animate_on or self._width <= 0 or self._height <= 0:
            return
        width = max(1, int(self._width * self._scale))
        height = max(1, int(self._height * self._scale))
        band = max(2, int(self._band * self._scale))
        inner = max(1, min(int(self._inner * self._scale), band - 1))
        frame_time = 1.0 / max(1.0, self._fps)
        phase_step = frame_time / max(0.5, self._cycle_seconds)  # turns per frame
        phase = 0.0
        msg = wt.MSG()
        while True:
            glow = render_glow(
                width,
                height,
                band=band,
                inner=inner,
                color_left=self._color_left,
                color_right=self._color_right,
                phase=phase,
                waves=self._waves,
                amplitude=self._amplitude,
            )
            self._apply_update(
                hwnd,
                gdi32,
                user32,
                width,
                height,
                to_bgra_bytes(premultiply_alpha(glow)),
            )
            phase = (phase + phase_step) % 1.0
            deadline = time.monotonic() + frame_time
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    if msg.message == _WM_HIDE:
                        user32.DestroyWindow(hwnd)
                        user32.PostQuitMessage(0)
                        return
                    if msg.message == _WM_QUIT:
                        return
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                    continue
                time.sleep(min(0.005, remaining))

    def _apply_update(
        self,
        hwnd: int,
        gdi32: ctypes.WinDLL,
        user32: ctypes.WinDLL,
        width: int,
        height: int,
        data: bytes,
    ) -> None:
        hdc_screen = user32.GetDC(None)
        if not hdc_screen:
            return
        try:
            hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
            if not hdc_mem:
                return
            bmi = _BITMAPINFO()
            header = bmi.bmiHeader
            header.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            header.biWidth = width
            header.biHeight = -height  # top-down DIB
            header.biPlanes = 1
            header.biBitCount = 32
            header.biCompression = _BI_RGB
            bits = ctypes.c_void_p()
            hbm = gdi32.CreateDIBSection(
                hdc_screen,
                ctypes.byref(bmi),
                _DIB_RGB_COLORS,
                ctypes.byref(bits),
                None,
                0,
            )
            if not hbm or not bits.value:
                gdi32.DeleteDC(hdc_mem)
                return
            previous = gdi32.SelectObject(hdc_mem, hbm)
            ctypes.memmove(bits, data, len(data))
            x, y = self._origin
            destination = _POINT(x, y)
            size = _SIZE(width, height)
            source = _POINT(0, 0)
            blend = _BLENDFUNCTION(0, 0, 255, _AC_SRC_ALPHA)
            user32.UpdateLayeredWindow(
                hwnd,
                hdc_screen,
                ctypes.byref(destination),
                ctypes.byref(size),
                hdc_mem,
                ctypes.byref(source),
                0,
                ctypes.byref(blend),
                _ULW_ALPHA,
            )
            gdi32.SelectObject(hdc_mem, previous)
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(hdc_mem)
        finally:
            user32.ReleaseDC(None, hdc_screen)

    def _virtual_screen_rect(self) -> tuple[int, int, int, int]:
        user32 = _user32()
        x = user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
        y = user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
        width = user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
        height = user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
        if width <= 0 or height <= 0:
            width = user32.GetSystemMetrics(_SM_CXSCREEN)
            height = user32.GetSystemMetrics(_SM_CYSCREEN)
            x = y = 0
        return x, y, width, height

    def _make_dpi_aware(self) -> None:
        """Per-monitor DPI awareness so metrics are physical pixels."""
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001 - already aware or unavailable
            try:
                _user32().SetProcessDPIAware()
            except Exception:  # noqa: BLE001
                pass


_user32_cache: ctypes.WinDLL | None = None
_gdi32_cache: ctypes.WinDLL | None = None


def _user32() -> ctypes.WinDLL:
    """user32 with explicit signatures (64-bit safe)."""
    global _user32_cache
    if _user32_cache is None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.restype = wt.HINSTANCE
        kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        user32.RegisterClassW.restype = wt.ATOM
        user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        user32.CreateWindowExW.restype = wt.HWND
        user32.CreateWindowExW.argtypes = [
            wt.DWORD,
            wt.LPCWSTR,
            wt.LPCWSTR,
            wt.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wt.HWND,
            wt.HMENU,
            wt.HINSTANCE,
            wt.LPVOID,
        ]
        user32.SetWindowPos.restype = wt.BOOL
        user32.SetWindowPos.argtypes = [
            wt.HWND,
            wt.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wt.UINT,
        ]
        user32.ShowWindow.restype = wt.BOOL
        user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        user32.GetMessageW.restype = wt.BOOL
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wt.MSG),
            wt.HWND,
            wt.UINT,
            wt.UINT,
        ]
        user32.PeekMessageW.restype = wt.BOOL
        user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wt.MSG),
            wt.HWND,
            wt.UINT,
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
        user32.DestroyWindow.restype = wt.BOOL
        user32.DestroyWindow.argtypes = [wt.HWND]
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
        user32.GetSystemMetrics.restype = ctypes.c_int
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetDC.restype = wt.HDC
        user32.GetDC.argtypes = [wt.HWND]
        user32.ReleaseDC.restype = ctypes.c_int
        user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
        user32.UpdateLayeredWindow.restype = wt.BOOL
        user32.UpdateLayeredWindow.argtypes = [
            wt.HWND,
            wt.HDC,
            ctypes.POINTER(_POINT),
            ctypes.POINTER(_SIZE),
            wt.HDC,
            ctypes.POINTER(_POINT),
            wt.COLORREF,
            ctypes.POINTER(_BLENDFUNCTION),
            wt.DWORD,
        ]
        user32.DefWindowProcW.restype = _LRESULT
        user32.DefWindowProcW.argtypes = [
            wt.HWND,
            wt.UINT,
            wt.WPARAM,
            wt.LPARAM,
        ]
        _user32_cache = user32
    return _user32_cache


def _gdi32() -> ctypes.WinDLL:
    """gdi32 with explicit signatures (64-bit safe)."""
    global _gdi32_cache
    if _gdi32_cache is None:
        gdi32 = ctypes.windll.gdi32
        gdi32.GetDeviceCaps.restype = ctypes.c_int
        gdi32.GetDeviceCaps.argtypes = [wt.HDC, ctypes.c_int]
        gdi32.CreateCompatibleDC.restype = wt.HDC
        gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
        gdi32.CreateDIBSection.restype = wt.HBITMAP
        gdi32.CreateDIBSection.argtypes = [
            wt.HDC,
            ctypes.POINTER(_BITMAPINFO),
            wt.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            wt.HANDLE,
            wt.DWORD,
        ]
        gdi32.SelectObject.restype = wt.HGDIOBJ
        gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
        gdi32.DeleteObject.restype = wt.BOOL
        gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
        gdi32.DeleteDC.restype = wt.BOOL
        gdi32.DeleteDC.argtypes = [wt.HDC]
        _gdi32_cache = gdi32
    return _gdi32_cache


__all__ = [
    "NoopOverlay",
    "Overlay",
    "WinGlowOverlay",
    "make_overlay",
    "premultiply_alpha",
    "render_glow",
    "to_bgra_bytes",
]
