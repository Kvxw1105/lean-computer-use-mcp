"""Local web panel for managing vision API endpoints.

Serves a single-page Chinese UI on 127.0.0.1 (random port) with a
per-session token, backed by the same JSON store as the ``config`` CLI.
Users who never touch a terminal can add/remove/reorder/test multiple
OpenAI-compatible endpoints (base URL / key / model) and enable automatic
failover. Keys are never echoed in full: the API returns masked views and
``POST /api/config`` treats an empty key as "keep the stored value".
"""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from lean_computer_use_mcp.config_store import (
    EndpointTestResult,
    load_config,
    public_provider_view,
    providers_from_config,
    save_config,
    ping_endpoint,
)

_HTML_PATH = Path(__file__).parent / "config_ui.html"


def _read_html() -> str:
    try:
        return _HTML_PATH.read_text(encoding="utf-8")
    except OSError:
        return "<html><body>config_ui.html missing</body></html>"


class _ConfigUIHandler(BaseHTTPRequestHandler):
    token = ""
    config_path: Path | None = None
    transport: Any | None = None  # injectable httpx transport for tests

    # -- plumbing -----------------------------------------------------------

    def log_message(self, _format: str, *args: Any) -> None:  # keep the console quiet
        pass

    def _send(self, status: int, body: bytes | str, content_type: str = "application/json") -> None:
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict[str, Any]) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False))

    def _read_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return {}

    def _authorized(self) -> bool:
        query = parse_qs(urlparse(self.path).query)
        supplied = (query.get("t") or [""])[0]
        return secrets.compare_digest(supplied, self.token)

    # -- routing ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, _read_html(), "text/html; charset=utf-8")
            return
        if path == "/api/config":
            if not self._authorized():
                self._json(401, {"error": "invalid token"})
                return
            config = load_config(self.config_path)
            providers = providers_from_config(config)
            self._json(
                200,
                {
                    "engine": config.get("engine", "llm"),
                    "model": config.get("model", ""),
                    "providers": public_provider_view(providers),
                },
            )
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        path = urlparse(self.path).path
        if not self._authorized():
            self._json(401, {"error": "invalid token"})
            return
        body = self._read_body()
        if path == "/api/config":
            self._save(body)
            return
        if path == "/api/test":
            self._test(body)
            return
        self._json(404, {"error": "not found"})

    def _save(self, body: dict[str, Any]) -> None:
        config = load_config(self.config_path)
        engine = str(body.get("engine") or config.get("engine", "llm"))
        model = str(body.get("model") or "")
        raw_providers: list[dict[str, Any]] = []
        for entry in body.get("providers", []):
            if not isinstance(entry, dict):
                continue
            api_base = str(entry.get("api_base") or "").strip()
            if not api_base:
                continue
            api_key = str(entry.get("api_key") or "").strip()
            if not api_key:
                # empty key means keep the stored secret for this api_base
                stored = next(
                    (
                        old.get("api_key", "")
                        for old in config.get("providers", [])
                        if isinstance(old, dict) and str(old.get("api_base", "")).rstrip("/")
                        == api_base.rstrip("/")
                    ),
                    "",
                )
                if not stored:
                    self._json(400, {"error": "new endpoint needs an api_key"})
                    return
                api_key = stored
            raw_providers.append(
                {
                    "api_base": api_base,
                    "api_key": api_key,
                    "model": str(entry.get("model") or "").strip(),
                }
            )
        if not raw_providers:
            self._json(400, {"error": "at least one endpoint with api_key is required"})
            return
        save_config(
            {"engine": engine, "model": model, "providers": raw_providers},
            self.config_path,
        )
        self._json(200, {"ok": True, "saved": len(raw_providers)})

    def _test(self, body: dict[str, Any]) -> None:
        api_base = str(body.get("api_base") or "").strip()
        model = str(body.get("model") or "").strip()
        api_key = str(body.get("api_key") or "").strip()
        if not api_key:
            # fall back to the stored key for the same base
            config = load_config(self.config_path)
            stored = next(
                (
                    str(old.get("api_key", ""))
                    for old in config.get("providers", [])
                    if isinstance(old, dict) and str(old.get("api_base", "")).rstrip("/")
                    == api_base.rstrip("/")
                ),
                "",
            )
            api_key = stored
        if not api_base or not api_key or not model:
            self._json(400, {"error": "api_base, api_key and model are required for a test"})
            return
        result: EndpointTestResult = ping_endpoint(
            api_base, api_key, model, transport=self.transport
        )
        self._json(
            200,
            {
                "ok": result.ok,
                "status": result.status,
                "latency_ms": round(result.latency_ms, 1),
                "error": result.error,
            },
        )


class ConfigUI:
    """Owns the HTTP server and the per-session token."""

    def __init__(self, config_path: Path | None = None, host: str = "127.0.0.1", port: int = 0) -> None:
        self.config_path = config_path
        _ConfigUIHandler.token = secrets.token_urlsafe(16)
        _ConfigUIHandler.config_path = config_path
        _ConfigUIHandler.transport = None
        self._server = ThreadingHTTPServer((host, port), _ConfigUIHandler)
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/?t={_ConfigUIHandler.token}"

    def serve_forever(self) -> None:
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self._server.server_close()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="lean-cu-config-ui", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
