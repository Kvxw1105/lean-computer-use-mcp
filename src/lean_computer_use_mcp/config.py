from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from lean_computer_use_mcp.vision.base import VisionProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    upstream_binary: str = "open-computer-use"
    upstream_timeout_seconds: int = 60
    state_ttl_seconds: int = 30
    state_max_entries: int = 64
    metrics_path: str | None = None
    image_cache_root: str | None = None
    vision_engine: str = "none"
    vision_api_base: str | None = None
    vision_api_key: str | None = None
    vision_model: str | None = None
    # Ordered failover list: each entry has its own api_base/api_key/model.
    # Preferred provider first; a failing provider cools down and the next
    # one takes over automatically (see vision/pool.py).
    vision_providers: tuple[VisionProvider, ...] = ()
    vision_upgrade_engine: str = "none"
    vision_upgrade_min_elements: int = 3
    vision_upgrade_cooldown_seconds: float = 60.0
    act_overlay_enabled: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            upstream_binary=os.getenv("LEAN_CU_UPSTREAM_BIN", "open-computer-use"),
            upstream_timeout_seconds=int(os.getenv("LEAN_CU_UPSTREAM_TIMEOUT", "60")),
            state_ttl_seconds=int(os.getenv("LEAN_CU_STATE_TTL_SECONDS", "30")),
            state_max_entries=int(os.getenv("LEAN_CU_STATE_MAX_ENTRIES", "64")),
            metrics_path=os.getenv("LEAN_CU_METRICS_PATH"),
            image_cache_root=os.getenv("LEAN_CU_IMAGE_CACHE"),
            vision_engine=os.getenv("LEAN_CU_VISION_ENGINE", "none"),
            vision_api_base=os.getenv("LEAN_CU_VISION_API_BASE"),
            vision_api_key=os.getenv("LEAN_CU_VISION_API_KEY"),
            vision_model=os.getenv("LEAN_CU_VISION_MODEL"),
            vision_providers=cls._parse_vision_providers(),
            vision_upgrade_engine=os.getenv("LEAN_CU_VISION_UPGRADE_ENGINE", "none"),
            vision_upgrade_min_elements=int(os.getenv("LEAN_CU_VISION_UPGRADE_MIN_ELEMENTS", "3")),
            vision_upgrade_cooldown_seconds=float(os.getenv("LEAN_CU_VISION_UPGRADE_COOLDOWN_SECONDS", "60")),
            act_overlay_enabled=os.getenv("LEAN_CU_ACT_OVERLAY", "0").lower()
            in {"1", "true", "yes", "on"},
        )

    @staticmethod
    def _parse_vision_providers() -> tuple[VisionProvider, ...]:
        """Parse LEAN_CU_VISION_PROVIDERS as a JSON list of endpoints.

        Shape: [{"api_base": "https://...", "api_key": "...", "model": "..."}]
        ``model`` is optional and inherits LEAN_CU_VISION_MODEL at engine
        build time. Malformed JSON or entries without api_base/api_key are
        skipped with a warning so a bad entry cannot take the whole session
        down; the legacy single-endpoint variables still work as before.
        """
        raw = os.getenv("LEAN_CU_VISION_PROVIDERS")
        if not raw:
            return ()
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("LEAN_CU_VISION_PROVIDERS is not valid JSON; ignoring it")
            return ()
        providers: list[VisionProvider] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                logger.warning("LEAN_CU_VISION_PROVIDERS[%d] is not an object; skipping", index)
                continue
            api_base = entry.get("api_base")
            api_key = entry.get("api_key")
            if not api_base or not api_key:
                logger.warning(
                    "LEAN_CU_VISION_PROVIDERS[%d] missing api_base/api_key; skipping", index
                )
                continue
            providers.append(
                VisionProvider(
                    api_base=str(api_base),
                    api_key=str(api_key),
                    model=str(entry.get("model", "")),
                )
            )
        if not providers:
            logger.warning("LEAN_CU_VISION_PROVIDERS produced no usable entries")
        return tuple(providers)
