"""Shared OpenAI-compatible text completion client with failover.

Memory curation calls (``compile --llm``, ``refine``, ``recall --llm``) are
text-only chat-completions requests. They reuse the same :class:`ProviderPool`
failover as the vision tier so a dead endpoint cannot stall a curation run:

- auth failures (HTTP 401/403): 10-minute cooldown (rotated/revoked keys);
- transient failures (timeouts, 429, 5xx, connection errors): 30-second
  cooldown, the channel may recover within seconds;
- the next healthy provider is used automatically and the preferred one is
  retried once its cooldown expires.

API keys are never logged or exposed in errors - only endpoint hosts are.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from lean_computer_use_mcp.vision.base import VisionProvider
from lean_computer_use_mcp.vision.pool import ProviderPool

logger = logging.getLogger(__name__)


def _host(api_base: str) -> str:
    return urlparse(api_base).hostname or api_base


def resolve_providers(
    api_base: str | None,
    api_key: str | None,
    model: str | None,
    providers: tuple[VisionProvider, ...] = (),
) -> list[VisionProvider]:
    """Ordered provider list; explicit ``providers`` win, else one endpoint.

    Providers with an empty model inherit the shared ``model``, mirroring the
    vision tier's ``VisionConfig`` semantics.
    """
    if providers:
        return [
            provider
            if provider.model
            else VisionProvider(provider.api_base, provider.api_key, model or "")
            for provider in providers
        ]
    if api_base and api_key and model:
        return [VisionProvider(api_base, api_key, model)]
    return []


class TextLlmClient:
    """One text-only chat-completions call routed through a ``ProviderPool``.

    ``transport`` is injectable for tests (``httpx.MockTransport``); without
    it a real HTTPS client is used. ``pool`` is injectable so several clients
    can share cooldown state or a fake clock.
    """

    def __init__(
        self,
        api_base: str | None,
        api_key: str | None,
        model: str | None,
        purpose: str = "LLM request",
        timeout_seconds: float = 60.0,
        transport: Any | None = None,
        providers: tuple[VisionProvider, ...] = (),
        pool: ProviderPool | None = None,
    ) -> None:
        self.purpose = purpose
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._providers = resolve_providers(api_base, api_key, model, providers)
        self._pool = pool

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        """POST /chat/completions and return the assistant message content.

        Raises ``ValueError`` when unconfigured or when every provider failed;
        the message carries hosts and reasons only, never API keys.
        """
        if not self._providers:
            raise ValueError(
                f"{self.purpose} requires api_base, api_key and model "
                "(set LEAN_CU_VISION_API_BASE / LEAN_CU_VISION_API_KEY / "
                "LEAN_CU_VISION_MODEL or pass --api-base/--api-key/--model)"
            )
        import httpx

        pool = self._pool or ProviderPool(self._providers)
        errors: list[str] = []
        last_exc: Exception | None = None
        for index, provider in pool.candidates():
            try:
                content = self._post(provider, system, user, max_tokens)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                pool.mark_failure(index, status)
                errors.append(f"host={_host(provider.api_base)} http {status}")
                last_exc = exc
                continue
            except httpx.HTTPError as exc:
                pool.mark_failure(index, None)
                errors.append(f"host={_host(provider.api_base)} transport error")
                last_exc = exc
                continue
            pool.mark_success(index)
            return content
        raise ValueError(
            f"{self.purpose} failed for all {len(self._providers)} provider(s): "
            + "; ".join(errors)
        ) from last_exc

    def _post(
        self, provider: VisionProvider, system: str, user: str, max_tokens: int
    ) -> str:
        """One chat-completions call; raises ``httpx.HTTPError`` on failure."""
        import httpx

        payload = {
            "model": provider.model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        url = provider.api_base.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {provider.api_key}"}
        if self.transport is not None:
            with httpx.Client(
                transport=self.transport, timeout=self.timeout_seconds
            ) as client:
                response = client.post(url, headers=headers, json=payload)
        else:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError(
                f"{self.purpose} returned an unexpected payload: {exc}"
            ) from exc
