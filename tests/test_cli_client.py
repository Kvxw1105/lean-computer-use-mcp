from __future__ import annotations

import base64
import json
import subprocess

import pytest

from lean_computer_use_mcp.errors import UpstreamError
from lean_computer_use_mcp.upstream.cli_client import CliUpstreamClient


def test_act_with_refresh_sends_snapshot_action_snapshot(monkeypatch):
    client = CliUpstreamClient(binary="open-computer-use")
    captured: dict = {}

    def fake_subprocess(cmd, label):
        captured["cmd"] = cmd
        calls = json.loads(cmd[-1])
        assert [call["tool"] for call in calls] == [
            "get_app_state",
            "set_value",
            "get_app_state",
        ]
        assert calls[0]["args"] == {
            "app": "ChatGPT",
            "max_tree_nodes": 80,
            "max_tree_depth": 8,
            "text_limit": 160,
        }
        assert calls[1]["args"] == {
            "app": "ChatGPT",
            "element_index": "12",
            "value": "hi",
        }
        results = [
            {
                "result": {
                    "content": [{"type": "text", "text": "tree-1"}],
                    "isError": False,
                },
                "tool": "get_app_state",
            },
            {
                "result": {
                    "content": [{"type": "text", "text": "action-refreshed"}],
                    "isError": False,
                },
                "tool": "set_value",
            },
            {
                "result": {
                    "content": [
                        {"type": "text", "text": "tree-2"},
                        {
                            "type": "image",
                            "data": base64.b64encode(b"PNGDATA").decode("ascii"),
                        },
                    ],
                    "isError": False,
                },
                "tool": "get_app_state",
            },
        ]
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(results), stderr=""
        )

    monkeypatch.setattr(client, "_subprocess", fake_subprocess)
    text, image, payload = client.act_with_refresh(
        "ChatGPT",
        "set_value",
        {"app": "ChatGPT", "element_index": "12", "value": "hi"},
        80,
        8,
        160,
    )
    assert text == "tree-2"
    assert image == b"PNGDATA"
    assert len(payload["calls"]) == 3
    assert "--calls" in captured["cmd"]


def test_act_with_refresh_raises_on_failed_action(monkeypatch):
    client = CliUpstreamClient(binary="open-computer-use")

    def fake_subprocess(cmd, label):
        results = [
            {
                "result": {
                    "content": [{"type": "text", "text": "tree-1"}],
                    "isError": False,
                },
                "tool": "get_app_state",
            },
            {
                "result": {
                    "content": [
                        {"type": "text", "text": 'unknown element_index "999"'}
                    ],
                    "isError": True,
                },
                "tool": "set_value",
            },
        ]
        return subprocess.CompletedProcess(
            cmd, 1, stdout=json.dumps(results), stderr="tool call returned isError=true"
        )

    monkeypatch.setattr(client, "_subprocess", fake_subprocess)
    with pytest.raises(UpstreamError, match="unknown element_index"):
        client.act_with_refresh("ChatGPT", "set_value", {"app": "ChatGPT"}, 80, 8, 160)


def test_missing_binary_raises_through_real_subprocess():
    client = CliUpstreamClient(binary="definitely-not-a-real-binary-xyz")
    with pytest.raises(UpstreamError, match="binary not found"):
        client.list_apps()


def test_extract_call_raises_on_iserror():
    item = {
        "result": {
            "content": [{"type": "text", "text": 'unknown element_index "999"'}],
            "isError": True,
        },
        "tool": "click",
    }
    with pytest.raises(UpstreamError, match="unknown element_index"):
        CliUpstreamClient._extract_call(item)


def test_error_text_prefers_iserror_content():
    proc = subprocess.CompletedProcess(
        [],
        1,
        stdout=json.dumps(
            [
                {
                    "result": {
                        "content": [{"type": "text", "text": "boom"}],
                        "isError": True,
                    },
                    "tool": "click",
                }
            ]
        ),
        stderr="tool call returned isError=true",
    )
    assert CliUpstreamClient._error_text(proc) == "boom"


def test_parse_upstream_apps_format():
    text = (
        "ChatGPT -- ChatGPT [running, pid=15332, window=ChatGPT]\n"
        "Microsoft Edge -- Microsoft Edge [running, pid=24640, window=ChatGPT - Microsoft Edge]\n"
        "Notepad -- Notepad [not running, pid=0]\n"
    )
    apps = CliUpstreamClient.parse_apps(text)
    assert [app.name for app in apps] == ["ChatGPT", "Microsoft Edge", "Notepad"]
    assert apps[0].running is True
    assert apps[0].visible_windows == 1
    assert apps[2].running is False
    assert apps[2].visible_windows == 0


def test_decode_upstream_probes_gbk_and_utf8(monkeypatch):
    import locale
    import sys

    decode = CliUpstreamClient._decode_upstream
    monkeypatch.setattr(sys, "platform", "linux")  # non-Windows: utf-8 first
    assert decode("??".encode("utf-8")) == "??"
    assert decode("??".encode("gbk")) == "??"
    assert decode(b"") == ""
    assert decode(b"pure ascii") == "pure ascii"

    monkeypatch.setattr(sys, "platform", "win32")  # Windows: code page first
    monkeypatch.setattr(locale, "getpreferredencoding", lambda _: "cp936")
    assert decode("??".encode("gbk")) == "??"
    assert decode("??".encode("utf-8")) == "??"


def test_subprocess_decodes_gbk_stdout(monkeypatch):
    # The pinned upstream binary is not installed in CI; _subprocess probes
    # shutil.which before running, so resolve the name without a real binary.
    monkeypatch.setattr(
        "lean_computer_use_mcp.upstream.cli_client.shutil.which", lambda name: name
    )
    client = CliUpstreamClient(binary="open-computer-use")
    payload = json.dumps(
        {"content": [{"type": "text", "text": "??"}], "isError": False},
        ensure_ascii=False,
    ).encode("gbk")

    def fake_run(cmd, capture_output, timeout):
        return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    content = client._run("get_app_state", {"app": "Notepad"})
    assert content[0]["text"] == "??"
