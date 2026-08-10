"""Tests for the local vision config store (config_store.py)."""

from __future__ import annotations

import json
import os

import httpx
import pytest

from lean_computer_use_mcp.config_store import (
    config_lock,
    host_of,
    load_config,
    mask_key,
    providers_from_config,
    public_provider_view,
    save_config,
    ping_endpoint,
    update_config,
)
from lean_computer_use_mcp.vision.base import VisionProvider


def _clear_vision_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make Settings.from_env() hermetic: the local machine may set real
    LEAN_CU_VISION_* variables that are meant to override the config file."""
    for name in list(os.environ):
        if name.startswith("LEAN_CU_VISION_"):
            monkeypatch.delenv(name, raising=False)


def test_load_config_missing_returns_empty_shape(tmp_path) -> None:
    config = load_config(tmp_path / "nope.json")
    assert config["engine"] == "llm"
    assert config["providers"] == []


def test_load_config_corrupt_returns_empty_shape(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_config(path)["providers"] == []


def test_save_and_load_roundtrip(tmp_path) -> None:
    path = tmp_path / "config.json"
    save_config(
        {
            "engine": "llm",
            "model": "gpt-5.6-luna",
            "providers": [
                {"api_base": "https://a.test/v1", "api_key": "sk-abc", "model": "m1"}
            ],
        },
        path,
    )
    config = load_config(path)
    assert config["model"] == "gpt-5.6-luna"
    assert config["providers"][0]["api_base"] == "https://a.test/v1"


def test_mask_key_hides_middle() -> None:
    assert mask_key("sk-abcdef1234567890") == "sk-***7890"
    assert mask_key("short") == "***"
    assert mask_key("") == ""


def test_providers_from_config_skips_bad_entries() -> None:
    config = {
        "providers": [
            {"api_base": "https://a.test/v1", "api_key": "k1", "model": "m1"},
            {"api_base": "https://b.test/v1", "api_key": ""},  # missing key
            "garbage",
            {"api_base": "", "api_key": "k2"},  # missing base
            {"api_base": "https://c.test/v1", "api_key": "k3"},  # model optional
        ]
    }
    providers = providers_from_config(config)
    assert [p.api_base for p in providers] == [
        "https://a.test/v1",
        "https://c.test/v1",
    ]
    assert providers[1].model == ""


def test_public_view_masks_keys() -> None:
    providers = (VisionProvider("https://a.test/v1", "sk-secret123456", "m1"),)
    view = public_provider_view(providers)
    assert view[0]["key_masked"] == "sk-***3456"
    assert "api_key" not in view[0]  # never expose the key field
    assert "sk-secret123456" not in json.dumps(view)


def test_host_of_parses() -> None:
    assert host_of("https://api.example.com/v1") == "api.example.com"
    # scheme-less strings fall back to the raw value
    assert host_of("localhost:8080") == "localhost:8080"


def test_ping_endpoint_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        assert b"ping" in body
        assert request.headers["Authorization"] == "Bearer k1"
        return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})

    result = ping_endpoint(
        "https://a.test/v1", "k1", "m1", transport=httpx.MockTransport(handler)
    )
    assert result.ok
    assert result.status == 200


def test_ping_endpoint_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad key")

    result = ping_endpoint(
        "https://a.test/v1", "k-bad", "m1", transport=httpx.MockTransport(handler)
    )
    assert not result.ok
    assert result.status == 401
    assert "HTTP 401" in result.error


def test_ping_endpoint_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    result = ping_endpoint(
        "https://a.test/v1", "k1", "m1", transport=httpx.MockTransport(handler)
    )
    assert not result.ok
    assert result.status is None
    assert result.error


def test_config_file_overrides_settings(monkeypatch, tmp_path) -> None:
    from lean_computer_use_mcp.config import Settings

    monkeypatch.setenv("LEAN_CU_CONFIG_DIR", str(tmp_path))
    _clear_vision_env(monkeypatch)
    save_config(
        {
            "engine": "llm",
            "model": "file-model",
            "providers": [
                {"api_base": "https://file.test/v1", "api_key": "file-key", "model": ""}
            ],
        },
        tmp_path / "config.json",
    )
    settings = Settings.from_env()
    assert settings.vision_engine == "llm"
    assert settings.vision_model == "file-model"
    assert len(settings.vision_providers) == 1
    assert settings.vision_providers[0].api_base == "https://file.test/v1"


def test_env_providers_override_config_file(monkeypatch, tmp_path) -> None:
    from lean_computer_use_mcp.config import Settings

    monkeypatch.setenv("LEAN_CU_CONFIG_DIR", str(tmp_path))
    _clear_vision_env(monkeypatch)
    save_config(
        {
            "engine": "llm",
            "providers": [
                {"api_base": "https://file.test/v1", "api_key": "file-key", "model": ""}
            ],
        },
        tmp_path / "config.json",
    )
    monkeypatch.setenv(
        "LEAN_CU_VISION_PROVIDERS",
        json.dumps(
            [{"api_base": "https://env.test/v1", "api_key": "env-key", "model": "m"}]
        ),
    )
    settings = Settings.from_env()
    assert settings.vision_providers[0].api_base == "https://env.test/v1"


def _bases(path) -> list[str]:
    return [
        p["api_base"]
        for p in load_config(path).get("providers", [])
        if isinstance(p, dict)
    ]


def test_update_config_reads_freshest_state_inside_lock(tmp_path) -> None:
    """A stale snapshot can never clobber a concurrent write.

    Regression for the shared-config 乌龙: two agents on one machine read the
    store, one saves, the other's stale snapshot used to overwrite the new
    endpoint. update_config re-reads under the lock, so the mutation always
    starts from the freshest on-disk state.
    """
    path = tmp_path / "config.json"
    save_config(
        {
            "engine": "llm",
            "providers": [{"api_base": "https://a.test/v1", "api_key": "ka"}],
        },
        path,
    )
    load_config(path)  # agent's stale snapshot (conceptually taken earlier)

    # another agent lands a new endpoint while the first one is mid-flight
    save_config(
        {
            "engine": "llm",
            "providers": [
                {"api_base": "https://a.test/v1", "api_key": "ka"},
                {"api_base": "https://b.test/v1", "api_key": "kb"},
            ],
        },
        path,
    )

    def _add(cfg: dict) -> None:
        assert [p["api_base"] for p in cfg["providers"]] == [
            "https://a.test/v1",
            "https://b.test/v1",
        ]  # fresh inside the lock
        cfg["providers"].append({"api_base": "https://c.test/v1", "api_key": "kc"})

    update_config(_add, path)
    assert _bases(path) == [
        "https://a.test/v1",
        "https://b.test/v1",
        "https://c.test/v1",
    ]


def test_update_config_mutator_return_value(tmp_path) -> None:
    path = tmp_path / "config.json"
    result = update_config(
        lambda cfg: len(cfg.setdefault("providers", [])), path
    )
    assert result == 0


def test_config_lock_timeout_while_held(tmp_path) -> None:
    """The lock excludes a concurrent writer and reports a busy timeout."""
    path = tmp_path / "config.json"
    with config_lock(path, timeout_seconds=5.0):
        with pytest.raises(RuntimeError, match="config lock .* still held"):
            with config_lock(path, timeout_seconds=0.2):
                pass  # pragma: no cover - must not be reached


def test_config_lock_released_after_exit(tmp_path) -> None:
    path = tmp_path / "config.json"
    with config_lock(path):
        pass
    # a second acquisition succeeds after release
    with config_lock(path, timeout_seconds=1.0):
        pass
