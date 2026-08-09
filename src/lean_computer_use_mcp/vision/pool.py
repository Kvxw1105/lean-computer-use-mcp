"""Failover pool for OpenAI-compatible vision endpoints.

Pure Python, platform-neutral, unit-testable. A pool holds an ordered list of
providers (each with its own base URL, key and model). A provider that fails
enters a cooldown window; requests are routed to the next healthy provider,
and the preferred one is retried once its cooldown expires, keeping a channel
outage from interrupting long sessions.

Failure classes:
- auth failures (HTTP 401/403): the key/base pair is likely broken; longer
  cooldown so we do not hammer a dead channel.
- transient failures (timeouts, 429, 5xx, connection errors): short cooldown,
  the channel may recover within seconds.

Never logs or exposes API keys; log lines only carry the endpoint host.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from lean_computer_use_mcp.vision.base import VisionProvider

logger = logging.getLogger(__name__)

# Default cooldowns (seconds). Auth errors cool down for 10 minutes so a
# revoked or rotated key is not retried every call; transient errors cool
# down for 30 seconds because channels often recover quickly.
DEFAULT_AUTH_COOLDOWN_SECONDS = 600.0
DEFAULT_TRANSIENT_COOLDOWN_SECONDS = 30.0


def _host(api_base: str) -> str:
    return urlparse(api_base).hostname or api_base


@dataclass
class _FailureState:
    until: float  # monotonic timestamp when the provider becomes available again
    reason: str


class ProviderPool:
    """Ordered failover routing over :class:`VisionProvider` entries.

    The first provider is preferred. A provider is skipped while it is inside
    its cooldown window; among exhausted providers the one whose cooldown
    expires soonest is tried first, so the pool self-heals without an active
    probe. Successful calls clear the failure state (immediate recovery when
    a channel comes back).
    """

    def __init__(
        self,
        providers: list[VisionProvider],
        auth_cooldown_seconds: float = DEFAULT_AUTH_COOLDOWN_SECONDS,
        transient_cooldown_seconds: float = DEFAULT_TRANSIENT_COOLDOWN_SECONDS,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        if not providers:
            raise ValueError("ProviderPool requires at least one provider")
        self._providers = list(providers)
        self._auth_cooldown = max(1.0, auth_cooldown_seconds)
        self._transient_cooldown = max(1.0, transient_cooldown_seconds)
        self._now = now_fn or time.monotonic
        self._failed: dict[int, _FailureState] = {}
        self._current = 0

    @property
    def providers(self) -> list[VisionProvider]:
        """Read-only ordered provider list (safe to inspect; keys stay private)."""
        return list(self._providers)

    def _available_at(self, index: int) -> float:
        state = self._failed.get(index)
        if state is None:
            return 0.0
        return max(0.0, state.until - float(self._now()))

    def candidates(self) -> list[tuple[int, VisionProvider]]:
        """Ordered list of ``(index, provider)`` to try for the next request.

        Healthy providers come first (preferred order), then failing ones
        sorted by earliest cooldown expiry. All providers are included, so a
        fully exhausted pool still produces one last attempt instead of an
        empty result.
        """
        now = float(self._now())
        healthy: list[tuple[int, VisionProvider]] = []
        cooling: list[tuple[int, VisionProvider, float]] = []
        for index, provider in enumerate(self._providers):
            state = self._failed.get(index)
            if state is None or state.until <= now:
                healthy.append((index, provider))
            else:
                cooling.append((index, provider, state.until))
        cooling.sort(key=lambda item: item[2])
        return healthy + [(index, provider) for index, provider, _until in cooling]

    def mark_success(self, index: int) -> None:
        """Clear failure state after a successful call (channel recovered)."""
        self._failed.pop(index, None)
        self._current = index

    def mark_failure(self, index: int, status: int | None = None) -> None:
        """Record a failure for ``index`` and advance rotation.

        ``status`` is the HTTP status when available; 401/403 get the long
        auth cooldown, everything else (including connection errors) the
        short transient cooldown.
        """
        reason = f"http {status}" if status is not None else "transport error"
        if status in (401, 403):
            until = float(self._now()) + self._auth_cooldown
        else:
            until = float(self._now()) + self._transient_cooldown
        self._failed[index] = _FailureState(until=until, reason=reason)
        provider = self._providers[index]
        logger.warning(
            "vision provider failover: host=%s reason=%s cooldown=%.0fs",
            _host(provider.api_base),
            reason,
            until - float(self._now()),
        )
        # Advance rotation so the next request naturally lands on the next
        # healthy provider instead of re-trying the failing one.
        self._current = (index + 1) % len(self._providers)

    def is_healthy(self, index: int) -> bool:
        return self._available_at(index) == 0.0
