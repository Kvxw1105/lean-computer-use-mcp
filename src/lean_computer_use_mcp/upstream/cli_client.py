from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys

from lean_computer_use_mcp.errors import (
    RealInputUnavailableError,
    UpstreamError,
    UpstreamTimeoutError,
)
from lean_computer_use_mcp.models import AppInfo
from lean_computer_use_mcp.upstream.base import UpstreamClient
from lean_computer_use_mcp.upstream.win_input import (
    CtypesWin32Input,
    Win32Input,
    WindowInfo,
    WindowStatus,
)

_APP_LINE_RE = re.compile(
    r"^(?P<name>.+?)\s+--\s+(?P<display>.+?)\s+\[(?P<flags>[^\]]*)\]$"
)


class CliUpstreamClient(UpstreamClient):
    """Talks to the installed `open-computer-use` CLI."""

    def __init__(
        self,
        binary: str = "open-computer-use",
        timeout_seconds: int = 60,
        win_input: Win32Input | None = None,
    ) -> None:
        self.binary = binary
        self.timeout_seconds = timeout_seconds
        self._win_input = win_input
        if self._win_input is None and sys.platform == "win32":
            self._win_input = CtypesWin32Input()
        self.binary = binary
        self.timeout_seconds = timeout_seconds

    def _command(self, *extra: str) -> list[str]:
        resolved = shutil.which(self.binary) or self.binary
        cmd = [resolved, *extra]
        if resolved.lower().endswith((".cmd", ".bat")):
            cmd = ["cmd", "/c", *cmd]
        return cmd

    def _run(self, tool: str, args: dict | None = None) -> list[dict]:
        cmd = self._command("call", tool)
        if args:
            cmd += ["--args", json.dumps(args, ensure_ascii=False)]
        proc = self._subprocess(cmd, tool)
        if proc.returncode != 0:
            raise UpstreamError(
                f"upstream call failed ({tool}): {self._error_text(proc)}"
            )
        payload = self._parse(proc.stdout, tool)
        if isinstance(payload, list):
            return payload
        content = payload.get("content", [])
        if not isinstance(content, list):
            raise UpstreamError(f"unexpected upstream payload shape for {tool}")
        return content

    def _run_calls(self, calls: list[dict], label: str) -> list[dict]:
        """Run several tool calls in one upstream process via --calls.

        The array form is required for element_index actions: each process only
        resolves indices against snapshots captured in that same process.
        """
        cmd = self._command("call", "--calls", json.dumps(calls, ensure_ascii=False))
        proc = self._subprocess(cmd, label)
        if proc.returncode != 0:
            raise UpstreamError(
                f"upstream call sequence failed ({label}): {self._error_text(proc)}"
            )
        payload = self._parse(proc.stdout, label)
        if not isinstance(payload, list):
            raise UpstreamError(
                f"upstream call sequence returned a non-array for {label}"
            )
        return payload

    def _subprocess(self, cmd: list[str], label: str) -> subprocess.CompletedProcess:
        if shutil.which(self.binary) is None:
            raise UpstreamError(f"upstream binary not found on PATH: {self.binary}")
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise UpstreamTimeoutError(f"upstream call timed out: {label}") from exc

    @staticmethod
    def _parse(stdout: str, label: str) -> dict | list:
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise UpstreamError(f"upstream returned invalid JSON for {label}") from exc

    @classmethod
    def _error_text(cls, proc: subprocess.CompletedProcess) -> str:
        """Best-effort error message from a failed upstream process."""
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return (proc.stderr or proc.stdout or "").strip()
        items = payload if isinstance(payload, list) else [payload]
        for item in reversed(items):
            result = item.get("result", item) if isinstance(item, dict) else {}
            if not isinstance(result, dict) or not result.get("isError"):
                continue
            for block in (
                result.get("content", [])
                if isinstance(result.get("content"), list)
                else []
            ):
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text:
                        return text
        return (proc.stderr or proc.stdout or "").strip()

    @staticmethod
    def _extract(content: list[dict]) -> tuple[str, bytes | None]:
        text_parts: list[str] = []
        image: bytes | None = None
        for item in content:
            kind = item.get("type")
            if kind == "text":
                text_parts.append(item.get("text", ""))
            elif kind == "image":
                data = item.get("data") or item.get("image") or ""
                if isinstance(data, str):
                    try:
                        image = base64.b64decode(data)
                    except Exception:
                        image = None
                elif isinstance(data, bytes):
                    image = data
        return "\n".join(text_parts), image

    @classmethod
    def _extract_call(cls, item: dict) -> tuple[str, bytes | None]:
        """Extract text/image from one --calls result item."""
        result = item.get("result", item)
        if not isinstance(result, dict):
            raise UpstreamError("unexpected upstream call-sequence item shape")
        if result.get("isError"):
            text, _ = cls._extract(result.get("content", []))
            raise UpstreamError(text or "upstream call in sequence failed")
        return cls._extract(result.get("content", []))

    @classmethod
    def parse_apps(cls, text: str) -> list[AppInfo]:
        apps: list[AppInfo] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            match = _APP_LINE_RE.match(line)
            if not match:
                continue
            flags = match.group("flags")
            running = "running" in flags and "not running" not in flags
            window_match = re.search(r"window=(.*)$", flags)
            visible_windows = (
                1 if (running and window_match and window_match.group(1).strip()) else 0
            )
            apps.append(
                AppInfo(
                    name=match.group("name"),
                    running=running,
                    visible_windows=visible_windows,
                    details={
                        "display": match.group("display"),
                        "window": window_match.group(1).strip() if window_match else "",
                    },
                )
            )
        return apps

    def list_apps(self) -> list[AppInfo]:
        content = self._run("list_apps")
        text, _ = self._extract(content)
        apps = self.parse_apps(text)
        if not apps:
            raise UpstreamError("upstream list_apps returned no apps")
        return apps

    def get_app_state(
        self,
        app: str,
        max_tree_nodes: int,
        max_tree_depth: int,
        text_limit: int | str,
    ) -> tuple[str, bytes | None]:
        content = self._run(
            "get_app_state",
            {
                "app": app,
                "max_tree_nodes": max_tree_nodes,
                "max_tree_depth": max_tree_depth,
                "text_limit": text_limit,
            },
        )
        return self._extract(content)

    def act_with_refresh(
        self,
        app: str,
        tool: str,
        args: dict,
        max_tree_nodes: int,
        max_tree_depth: int,
        text_limit: int | str,
    ) -> tuple[str, bytes | None, dict]:
        """Run [snapshot, action, snapshot] in one process and return the final snapshot.

        The leading snapshot lets the action resolve element_index inside the same
        process; the trailing snapshot captures the post-action state at the same
        compact budget so fingerprints stay comparable.
        """
        snapshot_args = {
            "app": app,
            "max_tree_nodes": max_tree_nodes,
            "max_tree_depth": max_tree_depth,
            "text_limit": text_limit,
        }
        calls = [
            {"tool": "get_app_state", "args": snapshot_args},
            {"tool": tool, "args": args},
            {"tool": "get_app_state", "args": snapshot_args},
        ]
        results = self._run_calls(calls, tool)
        if len(results) < 3:
            raise UpstreamError(
                f"upstream call sequence aborted before completion: {tool}"
            )
        text, image = self._extract_call(results[-1])
        return text, image, {"calls": results}

    def real_input_click(
        self,
        app: str,
        x: int,
        y: int,
        mouse_button: str = "left",
        click_count: int = 1,
    ) -> None:
        """Inject a real mouse click at screenshot-pixel (x, y) on Windows.

        Requires a Win32 input backend (auto-created on Windows, or injected
        via ``win_input`` for tests). The app is resolved by process name or
        window title; coordinates are screenshot pixel offsets.
        """
        if self._win_input is None:
            raise RealInputUnavailableError(
                "real-input click requires the Windows client (no win_input backend)"
            )
        window = self._win_input.find_main_window(app)
        self._win_input.click(window, int(x), int(y), mouse_button, click_count)

    def focus_window(self, app: str) -> None:
        """Restore and foreground the app's main window (window-level action)."""
        if self._win_input is None:
            raise RealInputUnavailableError(
                "focus_window requires the Windows client (no win_input backend)"
            )
        self._win_input.focus_main_window(app)

    def window_status(self, app: str) -> WindowStatus:
        """All matching windows with occlusion/ambiguity state (Windows)."""
        if self._win_input is None:
            raise RealInputUnavailableError(
                "window_status requires the Windows client (no win_input backend)"
            )
        return self._win_input.window_status(app)

    def activate_window(self, app: str, title: str | None = None) -> WindowInfo:
        """Restore + foreground one window (Windows); ambiguous matches raise."""
        if self._win_input is None:
            raise RealInputUnavailableError(
                "activate_window requires the Windows client (no win_input backend)"
            )
        return self._win_input.activate_window(app, title)

    def maximize_window(self, app: str, title: str | None = None) -> WindowInfo:
        """Restore + maximize + foreground one window (Windows)."""
        if self._win_input is None:
            raise RealInputUnavailableError(
                "maximize_window requires the Windows client (no win_input backend)"
            )
        return self._win_input.maximize_window(app, title)

    def window_rect(self, app: str) -> tuple[int, int, int, int] | None:
        """Screen rect (left, top, right, bottom) of the main window.

        ``None`` when no Win32 backend or when window enumeration fails: the
        stale gate must never depend on the rect succeeding, so failures
        degrade to "no rect" instead of raising.
        """
        if self._win_input is None:
            return None
        try:
            window = self._win_input.find_main_window(app)
        except Exception:  # noqa: BLE001 - best-effort rect for the gate
            return None
        return (
            window.left,
            window.top,
            window.left + window.width,
            window.top + window.height,
        )
