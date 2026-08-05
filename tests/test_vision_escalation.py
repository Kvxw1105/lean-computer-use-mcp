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


class BlindAppUpstream(FakeUpstreamClient):
    """UIA-blind app: a trivial tree plus a real screenshot."""

    def get_app_state(self, app, max_tree_nodes, max_tree_depth, text_limit):
        return self._read_text("state_capcut_empty.txt"), _png_bytes()


def _settings(tmp_path, **overrides) -> Settings:
    base = {
        "metrics_path": str(tmp_path / "metrics.jsonl"),
        "image_cache_root": str(tmp_path / "media"),
        "vision_engine": "fake",
    }
    base.update(overrides)
    return Settings(**base)


def test_auto_escalates_when_base_table_is_thin(tmp_path) -> None:
    settings = _settings(tmp_path, vision_upgrade_engine="fake_llm")
    engine = LeanComputerUse(BlindAppUpstream(FIXTURES), settings)
    result = engine.observe("JianyingPro", vision="auto")
    assert result["ok"] is True
    vision = result["vision"]
    assert vision["triggered"] is True
    assert vision["reason"] == "auto_escalate"
    assert vision["engine"] == "fake_llm"
    assert vision["escalated"] is True
    assert vision["escalated_from"] == "fake"
    assert vision["upgrade"] == {
        "attempted": True,
        "suppressed": False,
        "engine": "fake_llm",
        "base_engine": "fake",
        "base_elements": 2,
    }
    assert len(vision["elements"]) >= 3
    summary = engine.metrics_summary()
    assert summary["vision_calls"] == 2
    assert summary["vision_upgrade_calls"] == 1
    assert summary["vision_elements"] >= 3


def test_auto_keeps_base_table_when_sufficient(tmp_path) -> None:
    settings = _settings(tmp_path, vision_upgrade_engine="fake_llm", vision_upgrade_min_elements=1)
    engine = LeanComputerUse(BlindAppUpstream(FIXTURES), settings)
    result = engine.observe("JianyingPro", vision="auto")
    vision = result["vision"]
    assert vision["engine"] == "fake"
    assert vision["reason"] == "empty_tree"
    assert "escalated" not in vision
    assert "upgrade" not in vision
    summary = engine.metrics_summary()
    assert summary["vision_calls"] == 1
    assert summary["vision_upgrade_calls"] == 0


def test_auto_escalation_suppressed_by_cooldown(tmp_path) -> None:
    settings = _settings(
        tmp_path, vision_upgrade_engine="fake_llm", vision_upgrade_cooldown_seconds=60
    )
    engine = LeanComputerUse(BlindAppUpstream(FIXTURES), settings)
    first = engine.observe("JianyingPro", vision="auto")
    assert first["vision"]["escalated"] is True
    second = engine.observe("JianyingPro", vision="auto")
    vision = second["vision"]
    assert vision["engine"] == "fake"
    assert "escalated" not in vision
    assert vision["upgrade"]["suppressed"] is True
    assert vision["upgrade"]["reason"] == "cooldown"
    assert vision["upgrade"]["cooldown_seconds"] > 0
    summary = engine.metrics_summary()
    assert summary["vision_calls"] == 3  # base x2 + one upgrade
    assert summary["vision_upgrade_calls"] == 1


def test_auto_escalation_passes_intent_as_hint(tmp_path) -> None:
    settings = _settings(tmp_path, vision_upgrade_engine="fake_llm")
    engine = LeanComputerUse(BlindAppUpstream(FIXTURES), settings)
    engine.observe("JianyingPro", vision="auto", intent="找到导出按钮")
    upgrade = engine._vision_upgrade
    assert upgrade is not None
    assert upgrade.calls == ["找到导出按钮"]


def test_auto_escalation_not_suppressed_by_boot_clock(tmp_path, monkeypatch) -> None:
    """Regression: a freshly booted machine (time.monotonic() < cooldown) must
    not suppress the first escalation as if it were within a cooldown."""
    import lean_computer_use_mcp.server as server_module

    monkeypatch.setattr(server_module.time, "monotonic", lambda: 5.0)
    settings = _settings(
        tmp_path, vision_upgrade_engine="fake_llm", vision_upgrade_cooldown_seconds=60
    )
    engine = LeanComputerUse(BlindAppUpstream(FIXTURES), settings)
    result = engine.observe("JianyingPro", vision="auto")
    vision = result["vision"]
    assert vision["escalated"] is True
    assert vision["reason"] == "auto_escalate"


def test_no_upgrade_engine_configured_keeps_base(tmp_path) -> None:
    settings = _settings(tmp_path)
    engine = LeanComputerUse(BlindAppUpstream(FIXTURES), settings)
    result = engine.observe("JianyingPro", vision="auto")
    vision = result["vision"]
    assert vision["engine"] == "fake"
    assert "upgrade" not in vision
    assert engine.metrics_summary()["vision_calls"] == 1


def test_vision_on_never_escalates(tmp_path) -> None:
    settings = _settings(tmp_path, vision_upgrade_engine="fake_llm")
    engine = LeanComputerUse(BlindAppUpstream(FIXTURES), settings)
    result = engine.observe("JianyingPro", vision="on")
    vision = result["vision"]
    assert vision["engine"] == "fake"
    assert vision["reason"] == "requested"
    assert "escalated" not in vision
    assert engine.metrics_summary()["vision_calls"] == 1


def test_unavailable_upgrade_degrades_gracefully(tmp_path) -> None:
    settings = _settings(tmp_path, vision_upgrade_engine="llm")  # no api config
    engine = LeanComputerUse(BlindAppUpstream(FIXTURES), settings)
    result = engine.observe("JianyingPro", vision="auto")
    vision = result["vision"]
    assert vision["engine"] == "fake"  # base table kept
    assert vision["upgrade"]["suppressed"] is True
    assert vision["upgrade"]["reason"] == "unavailable"
    # unavailability is cached: a second observe short-circuits before any call
    result2 = engine.observe("JianyingPro", vision="auto")
    assert result2["vision"]["upgrade"]["reason"] == "unavailable"
    assert engine.metrics_summary()["vision_upgrade_calls"] == 0
