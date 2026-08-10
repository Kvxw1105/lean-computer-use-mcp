import json
from pathlib import Path

from lean_computer_use_mcp.config import Settings
from lean_computer_use_mcp.server import LeanComputerUse
from lean_computer_use_mcp.upstream.fake_client import FakeUpstreamClient

FIXTURES = Path(__file__).parent.parent / "examples" / "fixtures"


def test_find_app_returns_visible_running_apps(fake_upstream, settings):
    engine = LeanComputerUse(fake_upstream, settings)
    result = engine.find_app()
    assert result["apps"]
    assert all(app["running"] for app in result["apps"])
    assert all(app["visible_windows"] > 0 for app in result["apps"])


def test_observe_returns_compact_controls(fake_upstream, settings):
    engine = LeanComputerUse(fake_upstream, settings)
    result = engine.observe("ChatGPT", output_mode="controls", max_results=5)
    assert result["ok"] is True
    assert result["state_id"]
    assert len(result["controls"]) <= 5
    assert result["screenshot"]["data"] is None


def test_act_rejects_stale_state(fake_upstream, settings):
    engine = LeanComputerUse(fake_upstream, settings)
    result = engine.act("ChatGPT", "bogus-state", "click", element_index="12")
    assert result["ok"] is False
    assert result["error"] == "STALE_STATE"


def test_act_returns_delta_and_new_state(fake_upstream, settings):
    engine = LeanComputerUse(fake_upstream, settings)
    observed = engine.observe("ChatGPT")
    result = engine.act(
        "ChatGPT",
        observed["state_id"],
        "set_value",
        element_index="12",
        value="hello",
    )
    assert result["ok"] is True
    assert result["state_id"] != observed["state_id"]
    assert result["delta"]["modal_detected"] is True


def test_batch_is_capped_at_three(fake_upstream, settings):
    engine = LeanComputerUse(fake_upstream, settings)
    observed = engine.observe("ChatGPT")
    steps = [
        {"action": "set_value", "element_index": "12", "value": "1"},
        {"action": "set_value", "element_index": "12", "value": "2"},
        {"action": "set_value", "element_index": "12", "value": "3"},
        {"action": "set_value", "element_index": "12", "value": "4"},
    ]
    result = engine.batch("ChatGPT", observed["state_id"], steps, max_actions=10)
    assert result["completed"] <= 3


class FakeOverlay:
    """Recording overlay double for lifecycle assertions."""

    def __init__(self):
        self.shows = 0
        self.hides = 0
        self.visible = False

    def show(self):
        self.shows += 1
        self.visible = True

    def hide(self):
        self.hides += 1
        self.visible = False


def test_act_toggles_overlay_around_action(fake_upstream, settings):
    overlay = FakeOverlay()
    engine = LeanComputerUse(fake_upstream, settings, overlay=overlay)
    observed = engine.observe("ChatGPT")
    result = engine.act(
        "ChatGPT",
        observed["state_id"],
        "set_value",
        element_index="12",
        value="hello",
    )
    assert result["ok"] is True
    assert overlay.shows == 1
    assert overlay.hides == 1
    assert overlay.visible is False  # never left glowing after an action


def test_stale_state_never_toggles_overlay(fake_upstream, settings):
    overlay = FakeOverlay()
    engine = LeanComputerUse(fake_upstream, settings, overlay=overlay)
    result = engine.act("ChatGPT", "bogus-state", "click", element_index="12")
    assert result["ok"] is False
    assert overlay.shows == 0
    assert overlay.hides == 0


def test_batch_toggles_overlay_per_action(fake_upstream, settings):
    overlay = FakeOverlay()
    engine = LeanComputerUse(fake_upstream, settings, overlay=overlay)
    observed = engine.observe("ChatGPT")
    result = engine.batch(
        "ChatGPT",
        observed["state_id"],
        [{"action": "set_value", "element_index": "12", "value": "hello"}],
    )
    assert result["ok"] is True
    assert overlay.shows == 1
    assert overlay.hides == 1


def test_act_overlay_env_flag(monkeypatch):
    monkeypatch.setenv("LEAN_CU_ACT_OVERLAY", "1")
    assert Settings.from_env().act_overlay_enabled is True
    monkeypatch.setenv("LEAN_CU_ACT_OVERLAY", "true")
    assert Settings.from_env().act_overlay_enabled is True
    monkeypatch.setenv("LEAN_CU_ACT_OVERLAY", "0")
    assert Settings.from_env().act_overlay_enabled is False
    monkeypatch.delenv("LEAN_CU_ACT_OVERLAY")
    assert Settings.from_env().act_overlay_enabled is False


def test_metrics_are_recorded(fake_upstream, settings):
    engine = LeanComputerUse(fake_upstream, settings)
    engine.observe("ChatGPT")
    summary = engine.metrics_summary()
    assert summary["observe_calls"] == 1
    assert summary["text_chars"] > 0


class RecordingUpstream(FakeUpstreamClient):
    """Fake upstream that records every call for assertion."""

    def __init__(self, fixture_dir):
        super().__init__(fixture_dir)
        self.calls: list[tuple] = []

    def get_app_state(self, app, max_tree_nodes, max_tree_depth, text_limit):
        self.calls.append(("get_app_state", app))
        return super().get_app_state(app, max_tree_nodes, max_tree_depth, text_limit)

    def act_with_refresh(self, app, tool, args, max_tree_nodes, max_tree_depth, text_limit):
        self.calls.append(("act_with_refresh", app, tool, args))
        return super().act_with_refresh(app, tool, args, max_tree_nodes, max_tree_depth, text_limit)


class FingerprintFlipUpstream(FakeUpstreamClient):
    """Returns the modal fixture for the act-time freshness gate read."""

    def __init__(self, fixture_dir):
        super().__init__(fixture_dir)
        self.state_reads = 0
        self.action_calls = 0

    def get_app_state(self, app, max_tree_nodes, max_tree_depth, text_limit):
        self.state_reads += 1
        if self.state_reads >= 2:
            return self._read_text("state_chatgpt_after_modal.txt"), None
        return self._read_text("state_chatgpt_control.txt"), None

    def act_with_refresh(self, app, tool, args, max_tree_nodes, max_tree_depth, text_limit):
        self.action_calls += 1
        return self._read_text("state_chatgpt_after_modal.txt"), None, {"fake": True}


def test_stale_state_id_never_reaches_upstream(fake_upstream, settings):
    engine = LeanComputerUse(RecordingUpstream(FIXTURES), settings)
    result = engine.act("ChatGPT", "bogus-state", "click", element_index="12")
    assert result["ok"] is False
    assert result["error"] == "STALE_STATE"
    assert engine.upstream.calls == []


def test_act_rejects_live_fingerprint_change_without_action(fake_upstream, settings):
    upstream = FingerprintFlipUpstream(FIXTURES)
    engine = LeanComputerUse(upstream, settings)
    observed = engine.observe("ChatGPT")
    result = engine.act(
        "ChatGPT",
        observed["state_id"],
        "click",
        element_index="12",
    )
    assert result["ok"] is False
    assert result["error"] == "STALE_STATE"
    assert result["current_state_id"] != observed["state_id"]
    assert upstream.action_calls == 0
    # The live gate snapshot becomes the current state.
    assert engine.store.current("ChatGPT") == result["current_state_id"]


def test_act_stale_rejection_is_recorded_in_metrics(fake_upstream, settings):
    engine = LeanComputerUse(FakeUpstreamClient(FIXTURES), settings)
    engine.act("ChatGPT", "bogus-state", "click", element_index="12")
    summary = engine.metrics_summary()
    assert summary["errors"] == 1
    assert summary["stale_rejections"] == 1
    assert summary["action_calls"] == 1


def test_find_app_records_metrics(fake_upstream, settings):
    engine = LeanComputerUse(FakeUpstreamClient(FIXTURES), settings)
    engine.find_app()
    summary = engine.metrics_summary()
    assert summary["calls"] == 1
    assert summary["nodes"] > 0
    assert summary["text_chars"] > 0


def test_act_records_latency_and_nodes(fake_upstream, settings):
    engine = LeanComputerUse(FakeUpstreamClient(FIXTURES), settings)
    observed = engine.observe("ChatGPT")
    result = engine.act(
        "ChatGPT",
        observed["state_id"],
        "set_value",
        element_index="12",
        value="hello",
    )
    assert result["ok"] is True
    summary = engine.metrics_summary()
    assert summary["nodes"] > 0
    assert summary["avg_latency_ms"] >= 0
    rows = [
        json.loads(line)
        for line in Path(settings.metrics_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    action_rows = [row for row in rows if row["tool"] == "cu_act"]
    assert action_rows
    assert isinstance(action_rows[0]["latency_ms"], int)
    assert action_rows[0]["nodes"] > 0
    assert action_rows[0]["error"] is None

def test_build_action_args_matches_upstream_schemas():
    kwargs = dict(
        app="ChatGPT",
        element_index="12",
        value="hi",
        key="Enter",
        direction="down",
        pages=2.0,
        click_method="accessibility",
        mouse_button="right",
        secondary_action="Invoke",
    )
    build = LeanComputerUse._build_action_args
    assert build("click", **kwargs) == {
        "app": "ChatGPT",
        "element_index": "12",
        "click_method": "accessibility",
        "mouse_button": "right",
    }
    assert build("set_value", **kwargs) == {"app": "ChatGPT", "element_index": "12", "value": "hi"}
    assert build("scroll", **kwargs) == {
        "app": "ChatGPT",
        "element_index": "12",
        "direction": "down",
        "pages": 2.0,
    }
    assert build("type_text", **kwargs) == {"app": "ChatGPT", "text": "hi"}
    assert build("press_key", **kwargs) == {"app": "ChatGPT", "key": "Enter"}
    assert build("secondary_action", **kwargs) == {
        "app": "ChatGPT",
        "element_index": "12",
        "action": "Invoke",
    }

def test_to_mcp_advertises_facade_version(fake_upstream, settings):
    from lean_computer_use_mcp import __version__

    engine = LeanComputerUse(fake_upstream, settings)
    mcp = engine.to_mcp()
    assert mcp.instructions == f"lean-computer-use-mcp v{__version__}"
