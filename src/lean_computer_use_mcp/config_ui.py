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
    ping_endpoint,
    providers_from_config,
    public_provider_view,
    update_config,
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
            try:
                loaded_at = self.config_path.stat().st_mtime
            except OSError:
                loaded_at = None
            self._json(
                200,
                {
                    "engine": config.get("engine", "llm"),
                    "model": config.get("model", ""),
                    "providers": public_provider_view(providers),
                    # the on-disk state the panel saw; saves echo it back so
                    # endpoints added by another agent after this load are
                    # preserved instead of clobbered (see _save)
                    "loaded_at": loaded_at,
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
        """Save under the cross-process config lock, merging concurrent writes.

        The panel payload describes the endpoints *this session* saw. Without
        a merge, a panel that loaded the store before another agent added an
        endpoint would silently delete that endpoint on save. Merge rule: any
        file provider not present in the payload is preserved when the file
        changed after the panel loaded (``loaded_at``, the file mtime the
        panel received on GET); payloads without ``loaded_at`` (old clients)
        keep full-replace semantics. Empty keys keep the stored secret for the
        same base URL, exactly as before.
        """
        loaded_at = body.get("loaded_at")
        wanted: list[dict[str, Any]] = []
        for entry in body.get("providers", []):
            if not isinstance(entry, dict):
                continue
            api_base = str(entry.get("api_base") or "").strip()
            if not api_base:
                continue
            wanted.append(
                {
                    "api_base": api_base,
                    "api_key": str(entry.get("api_key") or "").strip(),
                    "model": str(entry.get("model") or "").strip(),
                }
            )

        def _apply(cfg: dict[str, Any]) -> dict[str, str] | None:
            file_providers = [
                p
                for p in cfg.get("providers", [])
                if isinstance(p, dict)
            ]
            stored = {
                str(p.get("api_base", "")).rstrip("/"): str(p.get("api_key", ""))
                for p in file_providers
            }
            merged: list[dict[str, Any]] = [dict(entry) for entry in wanted]
            for entry in merged:
                if not entry["api_key"]:
                    # empty key means keep the stored secret for this api_base
                    entry["api_key"] = stored.get(entry["api_base"].rstrip("/"), "")
            if loaded_at is not None:
                try:
                    loaded_at_f = float(loaded_at)
                except (TypeError, ValueError):
                    loaded_at_f = -1.0
                try:
                    file_mtime = self.config_path.stat().st_mtime
                except OSError:
                    file_mtime = 0.0
                if file_mtime > loaded_at_f:
                    seen = {entry["api_base"].rstrip("/") for entry in merged}
                    preserved = [
                        dict(p)
                        for p in file_providers
                        if str(p.get("api_base", "")).rstrip("/") not in seen
                        and str(p.get("api_key", ""))
                    ]
                    merged.extend(preserved)
            if not merged:
                return {"error": "at least one endpoint with api_key is required"}
            for entry in merged:
                if not entry["api_key"]:
                    return {"error": "new endpoint needs an api_key"}
            cfg["engine"] = str(body.get("engine") or cfg.get("engine", "llm"))
            cfg["model"] = str(body.get("model") or "")
            cfg["providers"] = merged
            return None

        error = update_config(_apply, self.config_path)
        if error:
            self._json(400, error)
            return
        self._json(200, {"ok": True, "saved": len(wanted)})

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

    def __init__(
        self,
        config_path: Path | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
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
