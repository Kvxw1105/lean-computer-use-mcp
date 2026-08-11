from __future__ import annotations

import json
import subprocess
import sys

import pytest

from lean_computer_use_mcp.errors import (
    RealInputUnavailableError,
    UpstreamError,
    UpstreamTimeoutError,
)
from lean_computer_use_mcp.upstream.cli_client import CliUpstreamClient
from lean_computer_use_mcp.upstream.win_input import (
    WindowCandidate,
    WindowInfo,
    WindowStatus,
)


def _proc(
    stdout: str, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def test_get_app_state_sends_args_and_returns_content(monkeypatch):
    client = CliUpstreamClient(binary="open-computer-use")
    captured: dict = {}

    def fake_subprocess(cmd, label):
        captured["cmd"] = cmd
        return _proc(json.dumps({"content": [{"type": "text", "text": "tree"}]}))

    monkeypatch.setattr(client, "_subprocess", fake_subprocess)
    text, image = client.get_app_state("ChatGPT", 80, 8, 160)
    assert text == "tree"
    assert image is None
    assert captured["cmd"][-3:] == [
        "get_app_state",
        "--args",
        json.dumps(
            {
                "app": "ChatGPT",
                "max_tree_nodes": 80,
                "max_tree_depth": 8,
                "text_limit": 160,
            },
            ensure_ascii=False,
        ),
    ]


def test_run_raises_with_error_text_on_nonzero_exit(monkeypatch):
    client = CliUpstreamClient(binary="open-computer-use")

    def fake_subprocess(cmd, label):
        return _proc("not json", returncode=1, stderr="boom stderr")

    monkeypatch.setattr(client, "_subprocess", fake_subprocess)
    with pytest.raises(UpstreamError, match="upstream call failed.*boom stderr"):
        client.list_apps()


def test_run_raises_when_content_is_not_a_list(monkeypatch):
    client = CliUpstreamClient(binary="open-computer-use")

    def fake_subprocess(cmd, label):
        return _proc(json.dumps({"content": "oops"}))

    monkeypatch.setattr(client, "_subprocess", fake_subprocess)
    with pytest.raises(UpstreamError, match="unexpected upstream payload shape"):
        client.get_app_state("ChatGPT", 80, 8, 160)


def test_run_calls_raises_when_payload_is_not_array(monkeypatch):
    client = CliUpstreamClient(binary="open-computer-use")

    def fake_subprocess(cmd, label):
        return _proc(json.dumps({"content": []}))

    monkeypatch.setattr(client, "_subprocess", fake_subprocess)
    with pytest.raises(UpstreamError, match="non-array"):
        client.act_with_refresh("ChatGPT", "click", {"app": "ChatGPT"}, 80, 8, 160)


def test_subprocess_timeout_raises_structured_error(monkeypatch):
    client = CliUpstreamClient(binary="open-computer-use")
    monkeypatch.setattr(
        "lean_computer_use_mcp.upstream.cli_client.shutil.which",
        lambda _: "open-computer-use",
    )

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=60)

    monkeypatch.setattr(
        "lean_computer_use_mcp.upstream.cli_client.subprocess.run", fake_run
    )
    with pytest.raises(UpstreamTimeoutError, match="timed out: list_apps"):
        client._subprocess(["open-computer-use", "call", "list_apps"], "list_apps")


def test_parse_invalid_json_raises_upstream_error():
    with pytest.raises(UpstreamError, match="invalid JSON"):
        CliUpstreamClient._parse("<<<not json>>>", "list_apps")


def test_error_text_falls_back_to_stderr_when_stdout_is_not_json():
    proc = _proc("raw output", returncode=1, stderr="stderr boom")
    assert CliUpstreamClient._error_text(proc) == "stderr boom"


def test_error_text_skips_non_error_items_then_falls_back():
    proc = _proc(
        json.dumps(
            [
                {
                    "result": {
                        "content": [{"type": "text", "text": "fine"}],
                        "isError": False,
                    }
                },
                {"result": {"content": [], "isError": False}},
            ]
        ),
        returncode=1,
        stderr="real stderr",
    )
    assert CliUpstreamClient._error_text(proc) == "real stderr"


def test_extract_handles_bad_base64_and_raw_bytes():
    text, image = CliUpstreamClient._extract(
        [
            {"type": "image", "data": "a"},
            {"type": "image", "image": b"raw-bytes"},
            {"type": "text", "text": "hello"},
        ]
    )
    assert text == "hello"
    assert image == b"raw-bytes"


def test_extract_call_raises_on_non_dict_result():
    with pytest.raises(UpstreamError, match="call-sequence item shape"):
        CliUpstreamClient._extract_call({"tool": "click", "result": ["not", "a", "dict"]})


def test_parse_apps_skips_blank_and_unparsable_lines():
    text = "\n\nnot an app line\nChatGPT -- ChatGPT [running, window=ChatGPT]\n"
    apps = CliUpstreamClient.parse_apps(text)
    assert [app.name for app in apps] == ["ChatGPT"]


def test_list_apps_parses_array_payload(monkeypatch):
    client = CliUpstreamClient(binary="open-computer-use")

    def fake_subprocess(cmd, label):
        return _proc(
            json.dumps(
                [
                    {
                        "type": "text",
                        "text": "ChatGPT -- ChatGPT [running, window=ChatGPT]\n",
                    }
                ]
            )
        )

    monkeypatch.setattr(client, "_subprocess", fake_subprocess)
    apps = client.list_apps()
    assert [app.name for app in apps] == ["ChatGPT"]


def test_list_apps_raises_when_no_apps_found(monkeypatch):
    client = CliUpstreamClient(binary="open-computer-use")

    def fake_subprocess(cmd, label):
        return _proc(json.dumps({"content": [{"type": "text", "text": "nothing here"}]}))

    monkeypatch.setattr(client, "_subprocess", fake_subprocess)
    with pytest.raises(UpstreamError, match="returned no apps"):
        client.list_apps()


def test_act_with_refresh_raises_when_sequence_aborted(monkeypatch):
    client = CliUpstreamClient(binary="open-computer-use")

    def fake_subprocess(cmd, label):
        return _proc(
            json.dumps(
                [
                    {
                        "result": {
                            "content": [{"type": "text", "text": "one"}],
                            "isError": False,
                        }
                    }
                ]
            )
        )

    monkeypatch.setattr(client, "_subprocess", fake_subprocess)
    with pytest.raises(UpstreamError, match="aborted before completion"):
        client.act_with_refresh("ChatGPT", "click", {"app": "ChatGPT"}, 80, 8, 160)


def test_window_actions_raise_without_win32_backend(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    client = CliUpstreamClient(binary="unused", win_input=None)
    assert client._win_input is None
    with pytest.raises(RealInputUnavailableError, match="real-input click"):
        client.real_input_click("ChatGPT", 1, 2)
    with pytest.raises(RealInputUnavailableError, match="focus_window"):
        client.focus_window("ChatGPT")
    with pytest.raises(RealInputUnavailableError, match="window_status"):
        client.window_status("ChatGPT")
    with pytest.raises(RealInputUnavailableError, match="activate_window"):
        client.activate_window("ChatGPT")
    with pytest.raises(RealInputUnavailableError, match="maximize_window"):
        client.maximize_window("ChatGPT")
    assert client.window_rect("ChatGPT") is None


class _FakeWinInput:
    """Minimal injected backend: window management delegates to it."""

    def __init__(self):
        self.window = WindowInfo(hwnd=1, left=0, top=0, width=100, height=50)
        self.status = WindowStatus(
            app="ChatGPT",
            candidates=(WindowCandidate(info=self.window, title="ChatGPT"),),
            main=WindowCandidate(info=self.window, title="ChatGPT"),
            ambiguous=False,
        )
        self.calls: list[str] = []

    def find_main_window(self, app):
        self.calls.append("find_main_window")
        return self.window

    def window_status(self, app):
        self.calls.append("window_status")
        return self.status

    def activate_window(self, app, title=None):
        self.calls.append(f"activate:{title}")
        return self.window

    def maximize_window(self, app, title=None):
        self.calls.append(f"maximize:{title}")
        return self.window


def test_window_management_delegates_to_win_input(monkeypatch):
    fake = _FakeWinInput()
    client = CliUpstreamClient(binary="unused", win_input=fake)
    assert client.window_status("ChatGPT") is fake.status
    client.activate_window("ChatGPT", title="Main")
    client.maximize_window("ChatGPT")
    assert fake.calls == ["window_status", "activate:Main", "maximize:None"]


def test_window_rect_maps_window_info_to_screen_rect(monkeypatch):
    fake = _FakeWinInput()
    client = CliUpstreamClient(binary="unused", win_input=fake)
    assert client.window_rect("ChatGPT") == (0, 0, 100, 50)


def test_window_rect_degrades_to_none_on_backend_error(monkeypatch):
    class FailingWinInput(_FakeWinInput):
        def find_main_window(self, app):
            raise RuntimeError("boom")

    client = CliUpstreamClient(binary="unused", win_input=FailingWinInput())
    assert client.window_rect("ChatGPT") is None
