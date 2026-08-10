"""Screenshot fingerprint for stale-state gating on trivial-tree apps."""

from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image

from lean_computer_use_mcp.server import LeanComputerUse
from lean_computer_use_mcp.state.fingerprint import fingerprint, image_fingerprint
from lean_computer_use_mcp.upstream.fake_client import FakeUpstreamClient

FIXTURES = Path(__file__).parent.parent / "examples" / "fixtures"


def make_png(
    box: tuple[int, int] | None = None, size: tuple[int, int] = (64, 64)
) -> bytes:
    """Light image with an optional dark 14px square - structural content so
    the perceptual hash (color-agnostic) can actually differ."""
    img = Image.new("RGB", size, (240, 240, 240))
    if box:
        for dx in range(14):
            for dy in range(14):
                x, y = box[0] + dx, box[1] + dy
                if x < size[0] and y < size[1]:
                    img.putpixel((x, y), (20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_png_with_box(size: tuple[int, int] = (64, 64), box=None) -> bytes:
    img = Image.new("RGB", size, (240, 240, 240))
    if box:
        draw = Image.new("RGB", size, (10, 10, 10))
        img.paste(draw.crop((box[0], box[1], box[0] + 8, box[1] + 8)), box)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_image_fingerprint_deterministic_and_sensitive():
    image_a = make_png(box=(0, 0))
    image_b = make_png(box=(40, 40))
    assert image_fingerprint(image_a) == image_fingerprint(image_a)
    assert image_fingerprint(image_a) != image_fingerprint(image_b)


def test_image_fingerprint_includes_dimensions():
    small = make_png(box=(0, 0), size=(64, 64))
    large = make_png(box=(0, 0), size=(128, 128))
    assert image_fingerprint(small) != image_fingerprint(large)


def test_image_fingerprint_catches_structural_change():
    plain = make_png_with_box(size=(64, 64))
    with_box = make_png_with_box(size=(64, 64), box=(40, 40))
    assert image_fingerprint(plain) != image_fingerprint(with_box)


def test_image_fingerprint_unreadable_returns_empty():
    assert image_fingerprint(b"not an image") == ""


class EmptyTreeImageUpstream(FakeUpstreamClient):
    """Empty UIA tree + controllable screenshot bytes."""

    def __init__(self, fixture_dir: Path, images: list[bytes]) -> None:
        super().__init__(fixture_dir)
        self.images = list(images)
        self.reads = 0
        self.actions = 0

    def get_app_state(self, app, max_tree_nodes, max_tree_depth, text_limit):
        self.reads += 1
        image = self.images[min(self.reads - 1, len(self.images) - 1)]
        return "", image

    def act_with_refresh(
        self, app, tool, args, max_tree_nodes, max_tree_depth, text_limit
    ):
        self.actions += 1
        return self._read_text("state_chatgpt_after_modal.txt"), None, {"fake": True}


class RichTreeImageUpstream(EmptyTreeImageUpstream):
    """Non-trivial tree + changing screenshot bytes (normal apps)."""

    def get_app_state(self, app, max_tree_nodes, max_tree_depth, text_limit):
        self.reads += 1
        image = self.images[min(self.reads - 1, len(self.images) - 1)]
        return self._read_text("state_chatgpt_control.txt"), image


def test_observe_stores_image_fingerprint_locally(settings):
    image = make_png(box=(0, 0))
    upstream = EmptyTreeImageUpstream(FIXTURES, [image])
    engine = LeanComputerUse(upstream, settings)
    observed = engine.observe("ChatGPT")
    assert observed["ok"] is True
    snapshot = engine.store.get("ChatGPT", observed["state_id"])
    assert snapshot.image_fingerprint == image_fingerprint(image)
    assert snapshot.image_fingerprint  # non-empty
    # Not model-visible and not in metrics: the fingerprint is a local gate.
    assert "image_fingerprint" not in observed
    rows = _observe_rows(settings.metrics_path)
    assert rows[0]["image_bytes"] == len(image)  # no extra bytes for the hash
    assert "image_fingerprint" not in rows[0]


def test_stale_gate_uses_image_fingerprint_on_empty_tree(settings):
    image_a = make_png(box=(0, 0))
    image_b = make_png(box=(40, 40))
    upstream = EmptyTreeImageUpstream(FIXTURES, [image_a, image_b])
    engine = LeanComputerUse(upstream, settings)
    observed = engine.observe("ChatGPT")
    result = engine.act(
        "ChatGPT", observed["state_id"], "click", element_index="1"
    )
    assert result["ok"] is False
    assert result["error"] == "STALE_STATE"
    assert result["signal"] == "image"
    assert upstream.actions == 0  # nothing executed


def test_stale_gate_passes_when_image_unchanged(settings):
    image = make_png(box=(0, 0))
    upstream = EmptyTreeImageUpstream(FIXTURES, [image, image])
    engine = LeanComputerUse(upstream, settings)
    observed = engine.observe("ChatGPT")
    result = engine.act(
        "ChatGPT", observed["state_id"], "click", element_index="1"
    )
    assert result["ok"] is True
    assert upstream.actions == 1


def test_rich_tree_ignores_screenshot_noise(settings):
    image_a = make_png(box=(0, 0))
    image_b = make_png(box=(40, 40))
    upstream = RichTreeImageUpstream(FIXTURES, [image_a, image_b])
    engine = LeanComputerUse(upstream, settings)
    observed = engine.observe("ChatGPT")
    result = engine.act(
        "ChatGPT", observed["state_id"], "click", element_index="1"
    )
    # The tree fingerprint is informative and unchanged: screenshot noise
    # (cursor blink, animation) must NOT reject normal apps.
    assert result["ok"] is True
    assert upstream.actions == 1


def test_stale_gate_signals_tree_when_tree_changes(settings):
    image = make_png(box=(0, 0))

    class Flipping(EmptyTreeImageUpstream):
        def get_app_state(self, app, max_tree_nodes, max_tree_depth, text_limit):
            self.reads += 1
            if self.reads >= 2:
                return self._read_text("state_chatgpt_after_modal.txt"), image
            return "", image

    engine = LeanComputerUse(Flipping(FIXTURES, [image, image]), settings)
    observed = engine.observe("ChatGPT")
    result = engine.act(
        "ChatGPT", observed["state_id"], "click", element_index="1"
    )
    assert result["ok"] is False
    assert result["error"] == "STALE_STATE"
    assert result["signal"] == "tree"


def test_tree_fingerprint_still_constant_for_empty_trees():
    """Regression guard: the tree fingerprint alone cannot detect change."""
    from lean_computer_use_mcp.models import StateSnapshot

    def snapshot(raw: str) -> StateSnapshot:
        snap = StateSnapshot(
            app="JianYing",
            window_title="JianYing",
            focused_element=None,
            controls=[],
            raw_text=raw,
            text_chars=len(raw),
            truncated_tree=False,
            truncated_text=False,
        )
        snap.fingerprint = fingerprint(snap)
        return snap

    assert snapshot("").fingerprint == snapshot("").fingerprint
    assert fingerprint(snapshot("")) == fingerprint(snapshot(""))


def _observe_rows(metrics_path: str) -> list[dict]:
    lines = Path(metrics_path).read_text(encoding="utf-8").splitlines()
    return [
        json.loads(line)
        for line in lines
        if line.strip() and json.loads(line)["tool"] == "cu_observe"
    ]
