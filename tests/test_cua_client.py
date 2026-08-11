from __future__ import annotations

import json
import subprocess

import pytest

from lean_computer_use_mcp.errors import AppNotFoundError, UpstreamError
from lean_computer_use_mcp.parse.tree_parser import parse_state
from lean_computer_use_mcp.upstream.cua_client import (
    CuaUpstreamClient,
    render_elements,
)

NOTEPAD_PAYLOAD = {
    "snapshot_id": "s00000001",
    "pid": 14192,
    "window_id": 367070896,
    "element_count": 29,
    "elements_complete": True,
    "elements": [
        {
            "element_index": 0,
            "element_token": "s00000001:0",
            "role": "Document",
            "label": "文本编辑器",
            "value": "hellohellohello",
            "depth": 2,
            "frame": {"x": 564, "y": 552, "w": 2138, "h": 1006},
        },
        {
            "element_index": 4,
            "element_token": "s00000001:4",
            "role": "Button",
            "label": "添加新标签页",
            "depth": 4,
            "frame": {"x": 1150, "y": 422, "w": 64, "h": 48},
        },
    ],
    "tree_markdown": "- [0] Document ...",
    "screenshot_png_b64": "aGVsbG8=",  # "hello" as PNG stand-in
    "screenshot_width": 2142,
    "screenshot_height": 1232,
}

APPS_PAYLOAD = {
    "apps": [
        {
            "name": "Notepad.exe",
            "pid": 14192,
            "running": True,
            "windows": [{"window_id": 367070896, "is_on_screen": True}],
        },
        {
            "name": "JianyingPro.exe",
            "pid": 40756,
            "running": True,
            "windows": [],
        },
    ]
}

WINDOWS_PAYLOAD = {
    "_legacy_windows": [
        {
            "window_id": 367070896,
            "pid": 14192,
            "title": "*hellohellohello - Notepad",
            "x": 562,
            "y": 392,
            "width": 2142,
            "height": 1232,
            "is_on_screen": True,
            "minimized": False,
        },
        {
            "window_id": 999,
            "pid": 14192,
            "title": "background helper",
            "x": -32000,
            "y": -32000,
            "width": 100,
            "height": 100,
            "is_on_screen": False,
            "minimized": True,
        },
    ]
}


def _client(monkeypatch, calls: dict | None = None, win_input=None):
    client = CuaUpstreamClient(binary="cua-driver", win_input=win_input)
    registry: dict = {} if calls is None else calls
    client._daemon_checked = True

    def fake_call(tool, args):
        registry[tool] = args
        if tool == "list_apps":
            return APPS_PAYLOAD
        if tool == "list_windows":
            return WINDOWS_PAYLOAD
        if tool == "get_window_state":
            return dict(NOTEPAD_PAYLOAD)
        return {"ok": True}

    monkeypatch.setattr(client, "_call", fake_call)
    monkeypatch.setattr(client, "_daemon_running", lambda: True)
    return client


def test_render_elements_is_parseable_by_tree_parser():
    text = render_elements("Notepad", "*hellohellohello - Notepad", 14192, NOTEPAD_PAYLOAD)
    title, focused, controls = parse_state(text)
    assert title == "*hellohellohello - Notepad"
    assert focused is None
    assert len(controls) == 2
    assert controls[0].index == "0"
    assert controls[0].role == "Document"
    assert controls[0].name == "文本编辑器"
    assert controls[0].value == "hellohellohello"
    assert controls[0].frame is not None
    assert controls[0].frame.x == 564
    assert controls[1].role == "Button"
    assert "Invoke" in controls[1].actions


def test_list_apps_maps_cua_payload(monkeypatch):
    client = _client(monkeypatch)
    apps = client.list_apps()
    assert [a.name for a in apps] == ["Notepad.exe", "JianyingPro.exe"]
    assert apps[0].running is True
    assert apps[0].visible_windows == 1
    assert apps[0].details["source"] == "cua-driver"


def test_get_app_state_resolves_main_window_and_extracts_image(monkeypatch):
    client = _client(monkeypatch)
    text, image = client.get_app_state("Notepad", 80, 8, 160)
    assert image == b"hello"
    assert "Window: \"*hellohellohello - Notepad\"" in text
    assert "App=Notepad (pid 14192)" in text


def test_get_app_state_matches_app_without_extension(monkeypatch):
    client = _client(monkeypatch)
    _, _ = client.get_app_state("notepad", 80, 8, 160)
    # Resolution succeeded; extension-less query matched Notepad.exe.


def test_get_app_state_unknown_app_raises(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        client,
        "_apps_payload",
        lambda: {"apps": [{"name": "Other.exe", "pid": 1, "running": True, "windows": []}]},
    )
    monkeypatch.setattr(client, "_windows_payload", lambda: {"_legacy_windows": []})
    with pytest.raises(AppNotFoundError):
        client.get_app_state("MissingApp", 80, 8, 160)


def test_act_with_refresh_click_element_index(monkeypatch):
    client = _client(monkeypatch, calls={})
    text, image, payload = client.act_with_refresh(
        "Notepad", "click", {"app": "Notepad", "element_index": "4"}, 80, 8, 160
    )
    assert payload["cua_tool"] == "click"
    # Element-index click goes through the background UIA Invoke path.
    assert image == b"hello"


def test_act_with_refresh_click_coordinates(monkeypatch):
    registry = {}
    client = _client(monkeypatch, calls=registry)
    client.act_with_refresh(
        "Notepad",
        "click",
        {"app": "Notepad", "x": 100, "y": 200, "mouse_button": "right"},
        80,
        8,
        160,
    )
    assert registry["click"]["pid"] == 14192
    assert registry["click"]["window_id"] == 367070896
    assert registry["click"]["x"] == 100
    assert registry["click"]["y"] == 200
    assert registry["click"]["button"] == "right"


def test_act_with_refresh_maps_all_actions(monkeypatch):
    cases = {
        "set_value": {"value": "hi", "element_index": "12"},
        "type_text": {"value": "hello"},
        "press_key": {"key": "return"},
        "scroll": {"direction": "down", "pages": 2},
        "drag": {"from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4},
        "secondary_action": {"x": 10, "y": 20},
    }
    for action, args in cases.items():
        registry = {}
        client = _client(monkeypatch, calls=registry)
        client.act_with_refresh("Notepad", action, args, 80, 8, 160)
        tool = registry_key(registry)
        assert tool is not None, action
        if action == "set_value":
            assert registry[tool]["value"] == "hi"
        if action == "type_text":
            assert registry[tool]["text"] == "hello"
        if action == "press_key":
            assert registry[tool]["key"] == "return"
        if action == "scroll":
            assert registry[tool]["amount"] == 2
        if action == "drag":
            assert registry[tool]["to_x"] == 3
        if action == "secondary_action":
            assert tool == "right_click"
            assert registry[tool]["x"] == 10


def registry_key(registry: dict) -> str | None:
    for key in ("click", "right_click", "double_click", "drag", "set_value",
                "scroll", "type_text", "press_key", "get_window_state"):
        if key in registry:
            return key
    return None


def test_act_with_refresh_unknown_action_raises(monkeypatch):
    client = _client(monkeypatch)
    with pytest.raises(UpstreamError):
        client.act_with_refresh("Notepad", "teleport", {}, 80, 8, 160)


def test_refusal_maps_background_unavailable(monkeypatch):
    client = CuaUpstreamClient(binary="cua-driver")

    def fake_run(*args, **kwargs):
        return (0, "background_unavailable: Chromium content drops posted events")

    monkeypatch.setattr(client, "_run_binary", fake_run)
    with pytest.raises(UpstreamError) as exc_info:
        client._call("click", {"pid": 1})
    assert exc_info.value.reason == "background_unavailable"


def test_refusal_maps_window_not_found(monkeypatch):
    client = CuaUpstreamClient(binary="cua-driver")

    def fake_run(*args, **kwargs):
        return (0, "No window with window_id 1 exists. Call `list_windows` for candidates.")

    monkeypatch.setattr(client, "_run_binary", fake_run)
    with pytest.raises(UpstreamError) as exc_info:
        client._call("get_window_state", {"pid": 1, "window_id": 1})
    assert exc_info.value.reason == "window_not_found"


def test_daemon_auto_start_when_not_running(monkeypatch):
    client = CuaUpstreamClient(binary="cua-driver")
    state = {"running": False, "popen": None}

    def fake_status():
        return (0, "Cua Driver daemon is running" if state["running"] else "not running")

    def fake_popen(cmd, **kwargs):
        state["popen"] = cmd
        state["running"] = True
        return object()

    monkeypatch.setattr(client, "_run_binary", lambda *a, **k: fake_status())
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(client, "_daemon_running", lambda: state["running"])
    client._ensure_daemon()
    assert state["popen"] is not None
    assert "--no-overlay" in state["popen"]


def test_render_empty_tree_stays_trivial_for_fingerprint_gate():
    payload = {
        "snapshot_id": "s2",
        "pid": 40756,
        "window_id": 48958436,
        "elements_complete": False,
        "elements": [
            {"element_index": 0, "role": "Window", "label": "剪映专业版", "depth": 0},
            {"element_index": 1, "role": "Window", "label": "JianyingPro", "depth": 1},
        ],
    }
    text = render_elements("JianyingPro", "剪映专业版", 40756, payload)
    title, _, controls = parse_state(text)
    assert title == "剪映专业版"
    # 2 controls -> the facade's _tree_is_trivial gate treats this as a
    # self-drawn app and falls back to the screenshot fingerprint.
    assert len(controls) == 2


def test_call_passes_json_via_stdin(monkeypatch):
    client = CuaUpstreamClient(binary="cua-driver")
    captured: dict = {}

    def fake_run_binary(*args, stdin_text=None):
        captured["args"] = args
        captured["stdin"] = stdin_text
        return (0, json.dumps({"ok": True}))

    monkeypatch.setattr(client, "_run_binary", fake_run_binary)
    client._call("get_screen_size", {})
    assert captured["args"] == ("call", "get_screen_size")
    assert json.loads(captured["stdin"]) == {}


def test_build_upstream_selects_backend():
    from lean_computer_use_mcp.cli import build_upstream
    from lean_computer_use_mcp.config import Settings
    from lean_computer_use_mcp.upstream.cli_client import CliUpstreamClient
    from lean_computer_use_mcp.upstream.fake_client import FakeUpstreamClient

    assert isinstance(
        build_upstream(Settings(upstream_kind="cua-driver")), CuaUpstreamClient
    )
    assert isinstance(
        build_upstream(Settings(upstream_kind="open-computer-use")), CliUpstreamClient
    )
    assert isinstance(build_upstream(Settings(), fake=True), FakeUpstreamClient)
