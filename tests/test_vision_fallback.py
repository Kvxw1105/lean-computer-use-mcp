from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from lean_computer_use_mcp.config import Settings
from lean_computer_use_mcp.server import LeanComputerUse
from lean_computer_use_mcp.upstream.fake_client import FakeUpstreamClient

FIXTURES = Path(__file__).parent.parent / "examples" / "fixtures"


def _png_bytes(width: int = 64, height: int = 48) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(40, 40, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


class RecordingUpstream(FakeUpstreamClient):
    """Fake upstream that records every act_with_refresh call."""

    def __init__(self, fixture_dir: Path) -> None:
        super().__init__(fixture_dir)
        self.action_calls: list[dict] = []

    def act_with_refresh(self, app, tool, args, max_tree_nodes, max_tree_depth, text_limit):
        self.action_calls.append({"tool": tool, "args": args})
        return super().act_with_refresh(app, tool, args, max_tree_nodes, max_tree_depth, text_limit)


class BlindAppUpstream(FakeUpstreamClient):
    """UIA-blind app (JianYing-style): a trivial tree plus a real screenshot."""

    def get_app_state(self, app, max_tree_nodes, max_tree_depth, text_limit):
        return self._read_text("state_capcut_empty.txt"), _png_bytes()


def test_act_click_by_coordinates(fake_upstream, settings) -> None:
    upstream = RecordingUpstream(FIXTURES)
    engine = LeanComputerUse(upstream, settings)
    observed = engine.observe("ChatGPT")
    result = engine.act("ChatGPT", observed["state_id"], "click", x=120, y=340)
    assert result["ok"] is True
    call = upstream.action_calls[-1]
    assert call["tool"] == "click"
    assert call["args"]["x"] == 120
    assert call["args"]["y"] == 340
    assert "element_index" not in call["args"]


def test_act_drag_by_coordinates(fake_upstream, settings) -> None:
    upstream = RecordingUpstream(FIXTURES)
    engine = LeanComputerUse(upstream, settings)
    observed = engine.observe("ChatGPT")
    result = engine.act(
        "ChatGPT", observed["state_id"], "drag",
        from_x=10, from_y=20, to_x=300, to_y=400,
    )
    assert result["ok"] is True
    call = upstream.action_calls[-1]
    assert call["tool"] == "drag"
    assert call["args"] == {
        "app": "ChatGPT",
        "from_x": 10,
        "from_y": 20,
        "to_x": 300,
        "to_y": 400,
    }


def test_act_click_ambiguous_target_rejected_before_upstream(fake_upstream, settings) -> None:
    upstream = RecordingUpstream(FIXTURES)
    engine = LeanComputerUse(upstream, settings)
    observed = engine.observe("ChatGPT")
    result = engine.act("ChatGPT", observed["state_id"], "click", element_index="12", x=1, y=2)
    assert result["ok"] is False
    assert result["error"] == "AMBIGUOUS_TARGET"
    assert upstream.action_calls == []


def test_act_click_missing_target_rejected(fake_upstream, settings) -> None:
    upstream = RecordingUpstream(FIXTURES)
    engine = LeanComputerUse(upstream, settings)
    observed = engine.observe("ChatGPT")
    result = engine.act("ChatGPT", observed["state_id"], "click")
    assert result["ok"] is False
    assert result["error"] == "ELEMENT_NOT_FOUND"
    assert upstream.action_calls == []


def test_act_drag_missing_coordinate_rejected(fake_upstream, settings) -> None:
    upstream = RecordingUpstream(FIXTURES)
    engine = LeanComputerUse(upstream, settings)
    observed = engine.observe("ChatGPT")
    result = engine.act("ChatGPT", observed["state_id"], "drag", from_x=1, from_y=2, to_x=3)
    assert result["ok"] is False
    assert result["error"] == "ELEMENT_NOT_FOUND"
    assert upstream.action_calls == []


def test_observe_triggers_vision_on_empty_tree(tmp_path) -> None:
    upstream = BlindAppUpstream(FIXTURES)
    settings = Settings(
        metrics_path=str(tmp_path / "metrics.jsonl"),
        image_cache_root=str(tmp_path / "media"),
        vision_engine="fake",
    )
    engine = LeanComputerUse(upstream, settings)
    result = engine.observe("JianyingPro")
    assert result["ok"] is True
    assert result["vision"]["triggered"] is True
    assert result["vision"]["engine"] == "fake"
    assert result["vision"]["reason"] == "empty_tree"
    assert result["vision"]["elements"]
    assert result["vision"]["image_bytes"] > 0
    summary = engine.metrics_summary()
    assert summary["vision_calls"] == 1
    assert summary["vision_elements"] > 0
    assert summary["vision_image_bytes"] > 0


def test_observe_vision_auto_skips_rich_tree(fake_upstream, settings) -> None:
    engine = LeanComputerUse(fake_upstream, settings)
    result = engine.observe("ChatGPT", vision="auto")
    assert result["ok"] is True
    assert result["vision"]["triggered"] is False


def test_observe_vision_off_disables_fallback(tmp_path) -> None:
    upstream = BlindAppUpstream(FIXTURES)
    settings = Settings(
        metrics_path=str(tmp_path / "metrics.jsonl"),
        image_cache_root=str(tmp_path / "media"),
        vision_engine="fake",
    )
    engine = LeanComputerUse(upstream, settings)
    result = engine.observe("JianyingPro", vision="off")
    assert result["ok"] is True
    assert result["vision"]["triggered"] is False


def test_observe_vision_unavailable_degrades_gracefully(tmp_path) -> None:
    upstream = BlindAppUpstream(FIXTURES)
    settings = Settings(
        metrics_path=str(tmp_path / "metrics.jsonl"),
        image_cache_root=str(tmp_path / "media"),
        vision_engine="none",
    )
    engine = LeanComputerUse(upstream, settings)
    result = engine.observe("JianyingPro", vision="on")
    assert result["ok"] is True
    assert result["vision"]["triggered"] is False
    assert "error" in result["vision"]


def test_metrics_summary_includes_vision_aggregates(tmp_path) -> None:
    from lean_computer_use_mcp.metrics.logger import MetricsLogger

    logger = MetricsLogger(str(tmp_path / "metrics.jsonl"))
    logger.record(
        tool="cu_observe",
        text_chars=100,
        image_bytes=50,
        vision_calls=1,
        vision_image_bytes=50,
        vision_latency_ms=120,
        vision_elements=7,
    )
    logger.record(tool="cu_observe", text_chars=80, image_bytes=0)
    summary = logger.summary()
    assert summary["vision_calls"] == 1
    assert summary["vision_image_bytes"] == 50
    assert summary["vision_latency_ms"] == 120
    assert summary["vision_elements"] == 7