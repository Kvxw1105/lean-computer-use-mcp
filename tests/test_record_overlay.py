"""Recording glow overlay: platform-neutral render math + injectable lifecycle."""

from __future__ import annotations

import sys

import pytest
from PIL import Image

from lean_computer_use_mcp.errors import RealInputUnavailableError
from lean_computer_use_mcp.record.overlay import (
    NoopOverlay,
    WinGlowOverlay,
    make_overlay,
    premultiply_alpha,
    render_glow,
    to_bgra_bytes,
)


def test_render_glow_transparent_center_opaque_edges():
    img = render_glow(320, 200)
    assert img.mode == "RGBA"
    assert img.size == (320, 200)
    assert img.getpixel((160, 100))[3] == 0
    assert img.getpixel((0, 100))[3] == 255
    assert img.getpixel((319, 100))[3] == 255
    assert img.getpixel((160, 0))[3] == 255
    assert img.getpixel((160, 199))[3] == 255


def test_render_glow_fades_inward():
    img = render_glow(320, 200, band=10, inner=3)
    outer = img.getpixel((0, 100))[3]
    mid = img.getpixel((5, 100))[3]
    inner_px = img.getpixel((9, 100))[3]
    assert outer == 255
    assert 0 < mid < outer
    assert 0 <= inner_px < mid


def test_render_glow_small_screen_clamps_band():
    img = render_glow(8, 8)
    assert img.size == (8, 8)
    assert img.mode == "RGBA"


def test_render_glow_empty_screen():
    img = render_glow(0, 0)
    assert img.size == (0, 0)


def test_premultiply_alpha():
    img = Image.new("RGBA", (1, 1), (100, 200, 50, 128))
    r, g, b, a = premultiply_alpha(img).getpixel((0, 0))
    assert (r, g, b, a) == (50, 100, 25, 128)


def test_to_bgra_bytes_layout():
    img = Image.new("RGBA", (1, 1), (1, 2, 3, 4))
    assert to_bgra_bytes(img) == bytes([3, 2, 1, 4])


def test_dib_bytes_match_screen_size():
    img = render_glow(640, 360)
    assert len(to_bgra_bytes(premultiply_alpha(img))) == 640 * 360 * 4


def test_noop_overlay_is_idempotent():
    overlay = NoopOverlay()
    overlay.show()
    overlay.show()
    overlay.hide()
    overlay.hide()


def test_make_overlay_disabled_returns_noop():
    assert isinstance(make_overlay(False), NoopOverlay)


def test_make_overlay_platform_selection():
    overlay = make_overlay(True)
    if sys.platform == "win32":
        assert isinstance(overlay, WinGlowOverlay)
    else:
        assert isinstance(overlay, NoopOverlay)


def test_win_overlay_requires_windows():
    if sys.platform == "win32":
        pytest.skip("guard is only exercised off Windows")
    with pytest.raises(RealInputUnavailableError):
        WinGlowOverlay().show()
