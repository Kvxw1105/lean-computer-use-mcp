"""Recording glow overlay: platform-neutral render math + injectable lifecycle."""

from __future__ import annotations

import sys

import pytest
from PIL import Image

from lean_computer_use_mcp.errors import RealInputUnavailableError
from lean_computer_use_mcp.record.overlay import (
    NoopOverlay,
    WinGlowOverlay,
    _edge_mask,
    _wave_factor,
    make_overlay,
    premultiply_alpha,
    render_edge,
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


def test_wave_factor_range_and_static_default():
    for position in (0.0, 0.25, 0.5, 1.0):
        factor = _wave_factor(position, phase=1.0, waves=2.5, amplitude=0.5)
        assert 1.0 - 0.5 <= factor <= 1.0
    assert _wave_factor(0.3, phase=0.0, waves=2.5, amplitude=0.0) == 1.0


def test_wave_factor_is_periodic_in_phase():

    a = _wave_factor(0.25, phase=0.0, waves=2.5, amplitude=0.3)
    b = _wave_factor(0.25, phase=1.0, waves=2.5, amplitude=0.3)
    assert abs(a - b) < 1e-9


def test_render_glow_animated_frames_differ_and_stay_soft():
    frame_a = render_glow(320, 200, amplitude=0.5, phase=0.0)
    frame_b = render_glow(320, 200, amplitude=0.5, phase=3.14159)
    assert frame_a.tobytes() != frame_b.tobytes()
    # Alpha modulation stays in [0.5*255, 255] along an edge for amp=0.5.
    alphas = [frame_a.getpixel((x, 0))[3] for x in range(0, 320, 8)]
    assert min(alphas) >= int(255 * 0.5)
    assert max(alphas) <= 255
    # The centre stays fully transparent while animating.
    assert frame_a.getpixel((160, 100))[3] == 0


def test_render_glow_edges_form_one_continuous_flow():
    frame = render_glow(320, 200, band=8, amplitude=0.5, phase=0.0)
    left = frame.getpixel((0, 0))[3]
    middle = frame.getpixel((160, 0))[3]
    # waves=2.5 puts x=0 on a wave trough and x=160 on a peak, so alpha must
    # differ along one edge - proof the wave travels, not just pulses.
    assert left != middle


def test_edge_mask_profile_times_factor():
    from PIL import Image

    profile = Image.new("L", (1, 4))
    for i in range(4):
        profile.putpixel((0, i), 200)
    mask = _edge_mask(profile, 64, 4, phase=0.0, waves=2.5, amplitude=0.5, direction=0)
    assert mask.size == (64, 4)
    assert max(mask.getdata()) <= 200
    assert min(mask.getdata()) >= int(200 * 0.5) - 1


def test_edge_mask_vertical_edge_keeps_profile_across_width():
    from PIL import Image

    profile = Image.new("L", (1, 4))
    for i, value in enumerate((200, 150, 100, 50)):
        profile.putpixel((0, i), value)
    mask = _edge_mask(profile, 64, 4, phase=0.0, waves=2.5, amplitude=0.0, direction=2)
    # Left edge: alpha fades with x (inward), constant along y at a fixed x.
    assert mask.size == (4, 64)
    assert mask.getpixel((0, 10)) == 200
    assert mask.getpixel((0, 40)) == 200
    assert mask.getpixel((3, 10)) == 50


def test_edge_mask_right_edge_mirrors_profile():
    from PIL import Image

    profile = Image.new("L", (1, 4))
    for i, value in enumerate((200, 150, 100, 50)):
        profile.putpixel((0, i), value)
    mask = _edge_mask(profile, 64, 4, phase=0.0, waves=2.5, amplitude=0.0, direction=3)
    # Right edge region starts inward: x=0 is the innermost pixel (profile
    # tail), x=3 touches the screen edge (profile head).
    assert mask.getpixel((0, 10)) == 50
    assert mask.getpixel((3, 10)) == 200


def test_render_edge_horizontal_strip_shape_and_blend():
    img = render_edge("top", 320, 10, 3, (76, 111, 247), (166, 88, 248))
    assert img.size == (320, 10)
    assert img.mode == "RGBA"
    left = img.getpixel((0, 1))
    right = img.getpixel((319, 1))
    # blue at the left, purple at the right (red channel rises); both opaque
    assert left[0] < left[2]
    assert right[0] < right[2]
    assert left[0] < right[0]
    assert left[3] == 255
    assert right[3] == 255


def test_render_edge_vertical_strip_shape_and_fade():
    img = render_edge("left", 200, 10, 3, (76, 111, 247), (166, 88, 248))
    assert img.size == (10, 200)
    assert img.getpixel((0, 100))[3] == 255
    assert img.getpixel((9, 100))[3] < 255


def test_render_edge_animated_frames_differ():
    a = render_edge("top", 320, 10, 3, (76, 111, 247), (166, 88, 248),
                    phase=0.0, amplitude=0.5)
    b = render_edge("top", 320, 10, 3, (76, 111, 247), (166, 88, 248),
                    phase=0.5, amplitude=0.5)
    assert a.tobytes() != b.tobytes()
    static = render_edge("top", 320, 10, 3, (76, 111, 247), (166, 88, 248))
    assert static.getpixel((160, 1))[3] == 255


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


def test_wave_factor_comet_sharpens_crests():
    # Peak position (wave == 1.0, sin(pi/2)): comet brightens the crest.
    peak_plain = _wave_factor(0.25, phase=0.0, waves=1.0, amplitude=0.3)
    peak_comet = _wave_factor(0.25, phase=0.0, waves=1.0, amplitude=0.3, comet=0.55)
    assert peak_comet > peak_plain
    # Trough position (wave == -1.0, sin(3*pi/2)): comet dims it.
    trough_plain = _wave_factor(0.75, phase=0.0, waves=1.0, amplitude=0.3)
    trough_comet = _wave_factor(0.75, phase=0.0, waves=1.0, amplitude=0.3, comet=0.55)
    assert trough_comet < trough_plain
    # All-zero keeps the static default.
    assert _wave_factor(0.3, phase=0.0, waves=2.5, amplitude=0.0, comet=0.0) == 1.0


def test_pulse_factor_breathes_with_phase():
    from lean_computer_use_mcp.record.overlay import _pulse_factor

    assert _pulse_factor(0.0, pulse=0.0) == 1.0
    # Peak breath at phase=0.25 (sin(pi/2) == 1), trough at 0.75.
    assert _pulse_factor(0.25, pulse=0.25) > _pulse_factor(0.75, pulse=0.25)
    assert _pulse_factor(0.25, pulse=0.25) == 1.0
    assert _pulse_factor(0.75, pulse=0.25) == 1.0 - 0.25
    for phase in (0.0, 0.25, 0.5, 0.75):
        value = _pulse_factor(phase, pulse=0.25)
        assert 1.0 - 0.25 <= value <= 1.0


def test_render_glow_comet_pulse_creates_energy_flow():
    plain = render_glow(320, 200, amplitude=0.5, phase=0.0)
    comet = render_glow(320, 200, amplitude=0.5, comet=0.55, pulse=0.25, phase=0.0)
    assert comet.tobytes() != plain.tobytes()
    # Energy pulse: the brightest crest alpha can exceed the plain wave's.
    comet_alphas = [comet.getpixel((x, 0))[3] for x in range(0, 320)]
    plain_alphas = [plain.getpixel((x, 0))[3] for x in range(0, 320)]
    assert max(comet_alphas) >= max(plain_alphas)
    # Breathing: the whole edge dims at the other half-cycle.
    dimmed = render_glow(320, 200, amplitude=0.5, comet=0.55, pulse=0.25, phase=0.5)
    assert sum(dimmed.getpixel((x, 0))[3] for x in range(0, 320)) < sum(
        comet.getpixel((x, 0))[3] for x in range(0, 320)
    )


def test_render_edge_comet_pulse_defaults_stay_static():
    static = render_edge(
        "top", 320, 14, 5, (76, 111, 247), (166, 88, 248), amplitude=0.0
    )
    assert static.tobytes() != b""  # sanity: strip renders
    # Defaults (comet=0, pulse=0) reproduce the original static frame.
    again = render_edge(
        "top",
        320,
        14,
        5,
        (76, 111, 247),
        (166, 88, 248),
        amplitude=0.0,
        comet=0.0,
        pulse=0.0,
    )
    assert again.tobytes() == static.tobytes()


def test_win_overlay_scifi_defaults_and_render_passthrough(monkeypatch):
    overlay = WinGlowOverlay()
    assert overlay._comet == 0.55
    assert overlay._pulse == 0.25
    seen = {}

    def fake_render_edge(*args, **kwargs):
        seen["kwargs"] = kwargs
        return Image.new("RGBA", (4, 4), (0, 0, 0, 0))

    monkeypatch.setattr(
        "lean_computer_use_mcp.record.overlay.render_edge", fake_render_edge
    )
    overlay._hwnds = [1]
    overlay._strips = [(0, 0, 100, 14)]
    overlay._render_frame(0.25)
    assert seen["kwargs"]["comet"] == 0.55
    assert seen["kwargs"]["pulse"] == 0.25
