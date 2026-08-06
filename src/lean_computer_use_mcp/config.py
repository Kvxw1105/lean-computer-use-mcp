from __future__ import annotations

import os
from dataclasses import dataclass


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
            vision_upgrade_engine=os.getenv("LEAN_CU_VISION_UPGRADE_ENGINE", "none"),
            vision_upgrade_min_elements=int(os.getenv("LEAN_CU_VISION_UPGRADE_MIN_ELEMENTS", "3")),
            vision_upgrade_cooldown_seconds=float(os.getenv("LEAN_CU_VISION_UPGRADE_COOLDOWN_SECONDS", "60")),
            act_overlay_enabled=os.getenv("LEAN_CU_ACT_OVERLAY", "0").lower()
            in {"1", "true", "yes", "on"},
        )
