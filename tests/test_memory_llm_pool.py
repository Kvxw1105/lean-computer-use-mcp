"""ProviderPool failover for the text-LLM memory curation clients.

The three memory clients (enrich / refine / recall --llm) reuse the vision
tier's ProviderPool: auth failures (401/403) cool a channel down for 10
minutes, transient failures for 30 seconds, and requests rotate to the next
healthy provider. API keys must never reach logs or error messages.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from lean_computer_use_mcp.memory.enrich import LlmEnricher
from lean_computer_use_mcp.memory.llm_client import TextLlmClient
from lean_computer_use_mcp.memory.llm_recall import LlmRecallMapper
from lean_computer_use_mcp.memory.model import MemoryLibrary
from lean_computer_use_mcp.memory.refine import LlmRefiner
from lean_computer_use_mcp.record.model import Recording, RecordedStep
from lean_computer_use_mcp.vision.base import VisionProvider
from lean_computer_use_mcp.vision.pool import ProviderPool

PROVIDER_A = VisionProvider("https://a.example/v1", "key-a", "model-x")
PROVIDER_B = VisionProvider("https://b.example/v1", "key-b", "model-x")


def _ok_response() -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"content": "ok"}}]}
    )


def _recording() -> Recording:
    return Recording(
        name="demo",
        app="JianyingPro",
        description="",
        started_at=0.0,
        steps=[RecordedStep(action="click", window_title="JianyingPro", x=10, y=20)],
    )


# --- rotation / cooldown -----------------------------------------------------


def test_text_client_rotates_to_next_provider_after_auth_failure():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.example":
            return httpx.Response(401, json={"error": "unauthorized"})
        calls.append(request.url.host)
        return _ok_response()

    client = TextLlmClient(
        None,
        None,
        None,
        purpose="test",
        transport=httpx.MockTransport(handler),
        providers=(PROVIDER_A, PROVIDER_B),
    )
    assert client.complete("sys", "user") == "ok"
    assert calls == ["b.example"]


def test_text_client_transient_cooldown_expires():
    clock = {"t": 1000.0}
    calls: list[str] = []
    fail_a = {"value": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.example" and fail_a["value"]:
            return httpx.Response(500, text="boom")
        calls.append(request.url.host)
        return _ok_response()

    pool = ProviderPool((PROVIDER_A, PROVIDER_B), now_fn=lambda: clock["t"])
    client = TextLlmClient(
        None,
        None,
        None,
        purpose="test",
        transport=httpx.MockTransport(handler),
        providers=(PROVIDER_A, PROVIDER_B),
        pool=pool,
    )
    assert client.complete("sys", "user") == "ok"  # a 500 -> b
    assert client.complete("sys", "user") == "ok"  # a still cooling -> b
    assert calls == ["b.example", "b.example"]
    clock["t"] += 31.0  # transient cooldown (30 s) expired
    fail_a["value"] = False
    assert client.complete("sys", "user") == "ok"  # a preferred again
    assert calls == ["b.example", "b.example", "a.example"]


def test_text_client_auth_cooldown_lasts_ten_minutes():
    clock = {"t": 1000.0}
    calls: list[str] = []
    fail_a = {"value": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.example" and fail_a["value"]:
            return httpx.Response(403, json={"error": "forbidden"})
        calls.append(request.url.host)
        return _ok_response()

    pool = ProviderPool((PROVIDER_A, PROVIDER_B), now_fn=lambda: clock["t"])
    client = TextLlmClient(
        None,
        None,
        None,
        purpose="test",
        transport=httpx.MockTransport(handler),
        providers=(PROVIDER_A, PROVIDER_B),
        pool=pool,
    )
    assert client.complete("sys", "user") == "ok"
    clock["t"] += 31.0  # inside the 10-minute auth cooldown
    assert client.complete("sys", "user") == "ok"
    assert calls == ["b.example", "b.example"]
    clock["t"] += 600.0
    fail_a["value"] = False
    assert client.complete("sys", "user") == "ok"
    assert calls == ["b.example", "b.example", "a.example"]


def test_all_providers_failing_raises_structured_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = TextLlmClient(
        None,
        None,
        None,
        purpose="memory refinement",
        transport=httpx.MockTransport(handler),
        providers=(PROVIDER_A, PROVIDER_B),
    )
    with pytest.raises(ValueError) as exc_info:
        client.complete("sys", "user")
    message = str(exc_info.value)
    assert "memory refinement failed for all 2 provider(s)" in message
    assert "host=a.example http 500" in message
    assert "host=b.example http 500" in message
    assert "key-a" not in message and "key-b" not in message


# --- keys never logged -------------------------------------------------------


def test_keys_never_reach_logs_or_errors(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "nope"})

    providers = (VisionProvider("https://secret-a.example/v1", "sk-secret-key-zz", "m"),)
    pool = ProviderPool(providers, now_fn=lambda: 1000.0)
    client = TextLlmClient(
        None,
        None,
        None,
        purpose="semantic enrichment",
        transport=httpx.MockTransport(handler),
        providers=providers,
        pool=pool,
    )
    with pytest.raises(ValueError) as exc_info:
        client.complete("sys", "user")
    assert "sk-secret-key-zz" not in str(exc_info.value)
    assert "secret-a.example" in str(exc_info.value)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ValueError):
            client.complete("sys", "user")
    assert "sk-secret-key-zz" not in caplog.text
    assert "host=secret-a.example" in caplog.text


# --- wrappers accept providers ----------------------------------------------


def test_enricher_fails_over_across_providers():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.example":
            return httpx.Response(401, json={})
        calls.append(request.url.host)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps({"steps": []})}}]}
        )

    enricher = LlmEnricher(
        None,
        None,
        None,
        transport=httpx.MockTransport(handler),
        providers=(PROVIDER_A, PROVIDER_B),
    )
    result = enricher.enrich(_recording())
    assert calls == ["b.example"]
    assert result.named == 0


def test_refiner_and_mapper_accept_provider_pool():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.example":
            return httpx.Response(401, json={})
        calls.append(request.url.host)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "{}"}}]}
        )

    refiner = LlmRefiner(
        None,
        None,
        None,
        transport=httpx.MockTransport(handler),
        providers=(PROVIDER_A, PROVIDER_B),
    )
    assert refiner.refine(MemoryLibrary()).empty is True

    def recall_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.example":
            return httpx.Response(401, json={})
        calls.append(request.url.host)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"components": []})}}]},
        )

    mapper = LlmRecallMapper(
        None,
        None,
        None,
        transport=httpx.MockTransport(recall_handler),
        providers=(PROVIDER_A, PROVIDER_B),
    )
    plan = mapper.compose(MemoryLibrary(), "anything")
    assert plan.kind == "components"
    assert plan.steps == []
    assert calls == ["b.example", "b.example"]
