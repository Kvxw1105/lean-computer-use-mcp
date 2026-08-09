"""Tests for the local vision config web panel (config_ui.py)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from lean_computer_use_mcp.config_ui import ConfigUI
from lean_computer_use_mcp.config_store import save_config


@pytest.fixture()
def ui(tmp_path):
    panel = ConfigUI(config_path=tmp_path / "config.json")
    panel.start()
    try:
        yield panel, tmp_path / "config.json"
    finally:
        panel.stop()


def _token(url: str) -> str:
    return parse_qs(urlparse(url).query)["t"][0]


def test_panel_serves_page(ui) -> None:
    panel, _path = ui
    response = httpx.get(panel.url)
    assert response.status_code == 200
    assert "text/html" in response.headers["Content-Type"]


def test_api_requires_token(ui) -> None:
    panel, _path = ui
    base = panel.url.split("?")[0]
    response = httpx.get(base + "/api/config")
    assert response.status_code == 401


def test_config_get_and_save_roundtrip(ui) -> None:
    panel, config_path = ui
    token = _token(panel.url)
    base = panel.url.split("?")[0]

    # empty store
    response = httpx.get(base + "/api/config", params={"t": token})
    assert response.status_code == 200
    assert response.json()["providers"] == []

    # save two endpoints
    payload = {
        "engine": "llm",
        "model": "gpt-5.6-luna",
        "providers": [
            {"api_base": "https://a.test/v1", "api_key": "key-a", "model": "m-a"},
            {"api_base": "https://b.test/v1", "api_key": "key-b", "model": ""},
        ],
    }
    response = httpx.post(
        base + "/api/config", params={"t": token}, json=payload
    )
    assert response.status_code == 200
    assert response.json()["saved"] == 2

    # keys are masked on read-back
    response = httpx.get(base + "/api/config", params={"t": token})
    data = response.json()
    assert len(data["providers"]) == 2
    assert data["providers"][0]["key_masked"] == "***"
    assert "api_key" not in data["providers"][0]


def test_save_with_empty_key_keeps_stored_secret(ui) -> None:
    panel, config_path = ui
    token = _token(panel.url)
    base = panel.url.split("?")[0]
    save_config(
        {
            "engine": "llm",
            "providers": [
                {"api_base": "https://a.test/v1", "api_key": "secret-123", "model": "m"}
            ],
        },
        config_path,
    )
    payload = {
        "engine": "llm",
        "model": "",
        "providers": [{"api_base": "https://a.test/v1", "api_key": "", "model": ""}],
    }
    response = httpx.post(base + "/api/config", params={"t": token}, json=payload)
    assert response.status_code == 200
    data = httpx.get(base + "/api/config", params={"t": token}).json()
    assert data["providers"][0]["key_masked"] == "sec***-123"


def test_save_new_endpoint_without_key_rejected(ui) -> None:
    panel, _path = ui
    token = _token(panel.url)
    base = panel.url.split("?")[0]
    payload = {
        "engine": "llm",
        "model": "",
        "providers": [{"api_base": "https://new.test/v1", "api_key": "", "model": ""}],
    }
    response = httpx.post(base + "/api/config", params={"t": token}, json=payload)
    assert response.status_code == 400


def test_ping_endpoint_via_panel(ui) -> None:
    panel, _path = ui
    token = _token(panel.url)
    base = panel.url.split("?")[0]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})

    from lean_computer_use_mcp.config_ui import _ConfigUIHandler

    _ConfigUIHandler.transport = httpx.MockTransport(handler)
    try:
        response = httpx.post(
            base + "/api/test",
            params={"t": token},
            json={"api_base": "https://a.test/v1", "api_key": "k1", "model": "m1"},
        )
    finally:
        _ConfigUIHandler.transport = None
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["status"] == 200
    assert data["latency_ms"] >= 0
