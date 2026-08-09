"""Local vision configuration store (JSON file under ~/.lean-cu/).

The GUI and the ``config`` CLI both read/write this file so users who never
touch a terminal can manage vision endpoints (base URL / key / model) in a
browser. The MCP server prefers this file when it exists and falls back to
the legacy environment variables otherwise, so existing setups keep working.

Keys are stored plaintext in the user profile directory (the same trust
boundary as environment variables); the web UI only ever echoes masked
keys and requires a per-session token. No key ever appears in logs.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from lean_computer_use_mcp.vision.base import VisionProvider

CONFIG_DIR_NAME = ".lean-cu"
CONFIG_FILE_NAME = "config.json"


def default_config_dir() -> Path:
    override = os.getenv("LEAN_CU_CONFIG_DIR")
    if override:
        return Path(override)
    return Path.home() / CONFIG_DIR_NAME


def default_config_path() -> Path:
    return default_config_dir() / CONFIG_FILE_NAME


def _empty_config() -> dict[str, Any]:
    return {"engine": "llm", "model": "", "providers": []}


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Read the config file; returns the empty shape when absent/corrupt."""
    config_path = path or default_config_path()
    if not config_path.exists():
        return _empty_config()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_config()
    if not isinstance(data, dict):
        return _empty_config()
    data.setdefault("engine", "llm")
    data.setdefault("model", "")
    data.setdefault("providers", [])
    return data


def save_config(config: dict[str, Any], path: Path | None = None) -> None:
    """Atomically write the config file (temp file + replace)."""
    config_path = path or default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    handle, tmp_name = tempfile.mkstemp(
        prefix="config-", suffix=".json", dir=str(config_path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as tmp:
            tmp.write(payload)
        os.replace(tmp_name, config_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def mask_key(api_key: str) -> str:
    """``sk-abc123def456`` -> ``sk-***456``; short keys collapse to ``***``."""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "***"
    return api_key[:3] + "***" + api_key[-4:]


def host_of(api_base: str) -> str:
    return urlparse(api_base).hostname or api_base


def providers_from_config(config: dict[str, Any]) -> tuple[VisionProvider, ...]:
    """Validated providers from raw config entries (bad entries skipped)."""
    providers: list[VisionProvider] = []
    for entry in config.get("providers", []):
        if not isinstance(entry, dict):
            continue
        api_base = entry.get("api_base")
        api_key = entry.get("api_key")
        if not api_base or not api_key:
            continue
        providers.append(
            VisionProvider(
                api_base=str(api_base),
                api_key=str(api_key),
                model=str(entry.get("model", "")),
            )
        )
    return tuple(providers)


def public_provider_view(
    providers: tuple[VisionProvider, ...],
) -> list[dict[str, Any]]:
    """API/CLI-safe view: masked keys, never the full secret."""
    return [
        {
            "index": index,
            "host": host_of(provider.api_base),
            "api_base": provider.api_base,
            "model": provider.model,
            "key_masked": mask_key(provider.api_key),
        }
        for index, provider in enumerate(providers)
    ]


@dataclass
class EndpointTestResult:
    ok: bool
    status: int | None = None
    latency_ms: float = 0.0
    error: str = ""


def ping_endpoint(
    api_base: str,
    api_key: str,
    model: str,
    timeout_seconds: float = 15.0,
    transport: Any | None = None,
) -> EndpointTestResult:
    """Minimal chat/completions ping; never sends a screenshot."""
    import httpx

    started = time.perf_counter()
    url = api_base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        if transport is not None:
            with httpx.Client(transport=transport, timeout=timeout_seconds) as client:
                response = client.post(url, headers=headers, json=payload)
        else:
            with httpx.Client(timeout=timeout_seconds) as client:
                response = client.post(url, headers=headers, json=payload)
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code >= 400:
            return EndpointTestResult(
                ok=False,
                status=response.status_code,
                latency_ms=latency_ms,
                error=f"HTTP {response.status_code}",
            )
        return EndpointTestResult(ok=True, status=response.status_code, latency_ms=latency_ms)
    except httpx.HTTPError as exc:
        return EndpointTestResult(
            ok=False, latency_ms=(time.perf_counter() - started) * 1000, error=str(exc)
        )
