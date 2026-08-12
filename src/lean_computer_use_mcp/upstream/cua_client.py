"""Upstream client for cua-driver (https://github.com/trycua/cua).

cua-driver is the open-source background-computer-use runtime used by Hermes.
On Windows it is "Supported"-level: UIA patterns first, targeted PostMessage
for pixel paths, and an explicit `delivery_mode: "foreground"` escalation that
is the only focus-stealing path. Structured refusals (`background_unavailable`)
are part of the contract instead of silent failures.

This client adapts cua-driver onto the existing :class:`UpstreamClient`
interface so the facade (server, parse, diff, fingerprint, vision, metrics)
keeps working unchanged. cua's structured `elements` array is rendered into
the same accessibility-text format the tree parser consumes.

Coordinate contract: x/y remain window-local screenshot pixels, matching the
rest of the facade (DPI handled by the driver).

The module stays importable on non-Windows platforms; daemon management is
Windows-only and raises a clear error elsewhere.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any

from lean_computer_use_mcp.errors import (
    AppNotFoundError,
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

_IS_WINDOWS = sys.platform == "win32"

# role -> Secondary Actions list, mirroring the open-computer-use text that
# models already consume (Invoke / SetValue / Scroll / ScrollIntoView).
_ROLE_ACTIONS: dict[str, tuple[str, ...]] = {
    "Button": ("Invoke", "ScrollIntoView"),
    "CheckBox": ("Invoke", "ScrollIntoView"),
    "RadioButton": ("Invoke", "ScrollIntoView"),
    "MenuItem": ("Invoke", "ScrollIntoView"),
    "Hyperlink": ("Invoke", "ScrollIntoView"),
    "Edit": ("SetValue", "Scroll", "ScrollIntoView"),
    "ComboBox": ("SetValue", "Scroll", "ScrollIntoView"),
    "List": ("Scroll", "ScrollIntoView"),
    "ScrollBar": ("Scroll",),
    "Pane": ("Scroll", "ScrollIntoView"),
    "Window": ("ScrollIntoView",),
}

# cua-driver tool name -> our _ACTION_TOOLS action name. Both spellings must
# resolve; anything unknown raises so the facade never guesses.
_ACTION_TO_CUA: dict[str, str] = {
    "click": "click",
    "drag": "drag",
    "set_value": "set_value",
    "scroll": "scroll",
    "type_text": "type_text",
    "press_key": "press_key",
    "secondary_action": "right_click",
    "right_click": "right_click",
    "double_click": "double_click",
}


def _match_app_name(needle: str, candidate: str) -> bool:
    """True when a cua app name matches the facade's app query.

    cua reports process names (``JianyingPro.exe``); the facade may query with
    or without the extension, case-insensitively.
    """
    return needle.lower() in candidate.lower()


def _cua_actions(role: str) -> list[str]:
    return list(_ROLE_ACTIONS.get(role, ()))


def render_elements(app: str, title: str, pid: int, payload: dict) -> str:
    """Render a cua `get_window_state` payload into parser-compatible text.

    Format mirrors the open-computer-use accessibility text:
    ``App=...`` / ``Window: "..."`` header lines, indented
    ``<index> <role> <label> Value: ... Secondary Actions: ... Frame: {...}``
    rows. Unknown or missing fields degrade to empty parts; the tree parser
    tolerates that (model-visible output is a superset of the old format).
    """
    lines = [f"App={app} (pid {pid})", f'Window: "{title}", App: {app}.']
    for element in payload.get("elements", []):
        if not isinstance(element, dict):
            continue
        index = element.get("element_index")
        role = element.get("role") or "pane"
        label = (element.get("label") or "").strip()
        row = f"{' ' * (2 * int(element.get('depth', 0)))}{index} {role}"
        if label:
            row += f" {label}"
        value = element.get("value")
        if value:
            row += f" Value: {value}"
        actions = _cua_actions(role)
        if actions:
            row += f" Secondary Actions: {', '.join(actions)}"
        frame = element.get("frame")
        if isinstance(frame, dict) and all(
            k in frame for k in ("x", "y", "w", "h")
        ):
            row += (
                f" Frame: {{x: {frame['x']}, y: {frame['y']}, "
                f"width: {frame['w']}, height: {frame['h']}}}"
            )
        lines.append(row)
    return "\n".join(lines)


class CuaUpstreamClient(UpstreamClient):
    """Talks to a local `cua-driver` daemon via ``cua-driver call``.

    A daemon is required (``cua-driver serve``); on Windows the client
    auto-starts one (hidden, ``--no-overlay``) when none is running. The
    daemon owns per-window snapshot caches, so element_index actions resolve
    against the snapshot this client just captured.
    """

    def __init__(
        self,
        binary: str = "cua-driver",
        timeout_seconds: int = 60,
        win_input: Win32Input | None = None,
        auto_start_daemon: bool = True,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.binary = self._resolve_binary(binary)
        self._win_input = win_input
        if self._win_input is None and _IS_WINDOWS:
            self._win_input = CtypesWin32Input()
        self._auto_start_daemon = auto_start_daemon
        self._daemon_checked = False

    @staticmethod
    def _resolve_binary(binary: str) -> str:
        """Resolves the cua-driver executable, including the default Windows
        install path when the current process started before the installer
        updated the user PATH."""
        resolved = shutil.which(binary)
        if resolved:
            return resolved
        if sys.platform == "win32":
            local = os.environ.get("LOCALAPPDATA", "")
            candidate = os.path.join(
                local, "Programs", "Cua", "cua-driver", "bin", binary + ".exe"
            )
            if os.path.isfile(candidate):
                return candidate
        return binary

    # ------------------------------------------------------------------ run

    def _subprocess(self, cmd: list[str], label: str) -> subprocess.CompletedProcess:
        """Runs one command; injectable in tests via monkeypatch."""
        try:
            return subprocess.run(
                cmd,
                input=None,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise UpstreamTimeoutError(f"cua-driver call timed out: {label}") from exc

    def _run_binary(self, *args: str, stdin_text: str | None = None) -> tuple[int, str]:
        """Runs ``binary <args>`` with optional stdin; returns (exit, stdout)."""
        cmd = [self.binary, *args]
        try:
            proc = subprocess.run(
                cmd,
                input=stdin_text.encode("utf-8") if stdin_text is not None else None,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise UpstreamTimeoutError(
                f"cua-driver call timed out: {' '.join(args)}"
            ) from exc
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise UpstreamError(
                f"cua-driver failed ({' '.join(args)}): {(stderr or stdout).strip()}"
            )
        return proc.returncode, stdout

    def _call(self, tool: str, args: dict) -> dict:
        """Invokes one cua-driver tool; raises UpstreamError on refusal."""
        exit_code, stdout = self._run_binary(
            "call", tool, stdin_text=json.dumps(args, ensure_ascii=False)
        )
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            # cua-driver prints plain-text refusals on stdout with exit 0.
            raise self._refusal(tool, stdout.strip())
        if isinstance(payload, dict) and payload.get("isError"):
            raise self._refusal(tool, payload)
        return payload

    @staticmethod
    def _refusal(tool: str, message: str | dict) -> UpstreamError:
        """Builds a structured UpstreamError from a cua refusal message."""
        text = message if isinstance(message, str) else str(message)
        reason: str | None = None
        lowered = text.lower()
        if "background_unavailable" in lowered:
            reason = "background_unavailable"
        elif "background_occluded" in lowered:
            reason = "background_occluded"
        elif "no window with window_id" in lowered or "window_id_not_found" in lowered:
            reason = "window_not_found"
        elif "stale" in lowered and "snapshot" in lowered:
            reason = "stale_snapshot"
        elif "timed out" in lowered or "timeout" in lowered:
            reason = "timeout"
        return UpstreamError(
            f"cua-driver refused {tool}: {text[:300]}", reason=reason
        )

    # ------------------------------------------------------------- daemon

    def _daemon_running(self) -> bool:
        try:
            _, stdout = self._run_binary("status")
        except UpstreamError:
            return False
        return "is running" in stdout.lower()

    def _ensure_daemon(self) -> None:
        """Ensures a cua-driver daemon is up (Windows: auto-start, hidden)."""
        if self._daemon_checked and self._daemon_running():
            return
        if self._daemon_running():
            self._daemon_checked = True
            return
        if not _IS_WINDOWS:
            raise UpstreamError(
                "cua-driver daemon is not running; start it with "
                "`cua-driver serve` (auto-start is Windows-only)"
            )
        if not self._auto_start_daemon:
            raise UpstreamError(
                "cua-driver daemon is not running; start it with `cua-driver serve`"
            )
        try:
            subprocess.Popen(
                [self.binary, "serve", "--no-overlay"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise UpstreamError(
                f"failed to start cua-driver daemon: {exc}"
            ) from exc
        deadline = time.time() + 15
        while time.time() < deadline:
            if self._daemon_running():
                self._daemon_checked = True
                return
            time.sleep(0.5)
        raise UpstreamError(
            "cua-driver daemon did not become ready within 15s"
        )

    # ------------------------------------------------------ app resolution

    def _apps_payload(self) -> dict:
        return self._call("list_apps", {})

    def _windows_payload(self) -> dict:
        return self._call("list_windows", {})

    def _resolve_window(self, app: str) -> tuple[int, int, str]:
        """Returns (pid, window_id, title) for the app's main visible window.

        Selection rule mirrors ``find_main_window``: on-screen windows beat
        minimized ones, largest area wins. Raises ``AppNotFoundError`` when
        nothing matches.
        """
        apps = self._apps_payload().get("apps", [])
        pids = [
            int(a["pid"])
            for a in apps
            if a.get("running") and a.get("pid") and _match_app_name(app, a.get("name", ""))
        ]
        windows = self._windows_payload().get("_legacy_windows", [])
        if not isinstance(windows, list):
            windows = self._windows_payload().get("windows", [])
        candidates: list[tuple[int, int, str, bool, int]] = []
        for window in windows:
            if not isinstance(window, dict) or int(window.get("pid", -1)) not in pids:
                continue
            try:
                width = int(window.get("width", 0))
                height = int(window.get("height", 0))
            except (TypeError, ValueError):
                continue
            area = width * height
            on_screen = bool(window.get("is_on_screen"))
            minimized = bool(window.get("minimized"))
            if on_screen and not minimized:
                candidates.append(
                    (window["window_id"], window.get("title", ""), on_screen, minimized, area)
                )
        if not candidates:
            # Fall back to any window (minimized / off-screen) rather than
            # failing when the app is running but its window is minimized.
            for window in windows:
                if not isinstance(window, dict) or int(window.get("pid", -1)) not in pids:
                    continue
                try:
                    width = int(window.get("width", 0))
                    height = int(window.get("height", 0))
                except (TypeError, ValueError):
                    continue
                candidates.append(
                    (
                        window["window_id"],
                        window.get("title", ""),
                        bool(window.get("is_on_screen")),
                        bool(window.get("minimized")),
                        width * height,
                    )
                )
        if not candidates:
            raise AppNotFoundError(f"no cua-driver window found for app {app!r}")
        # Largest visible (or any) window wins, matching find_main_window.
        candidates.sort(key=lambda c: (c[2], not c[3], c[4]), reverse=True)
        window_id, title, _, _, _ = candidates[0]
        return pids[0], int(window_id), str(title or "")

    # ------------------------------------------------------ UpstreamClient

    def list_apps(self) -> list[AppInfo]:
        payload = self._apps_payload()
        apps: list[AppInfo] = []
        for row in payload.get("apps", []):
            if not isinstance(row, dict):
                continue
            windows = row.get("windows") or []
            apps.append(
                AppInfo(
                    name=row.get("name", ""),
                    running=bool(row.get("running")),
                    visible_windows=(
                        sum(
                            1
                            for w in windows
                            if isinstance(w, dict)
                            and (w.get("is_on_screen") or w.get("visible", True))
                        )
                        if isinstance(windows, list)
                        else 0
                    ),
                    details={
                        "pid": row.get("pid"),
                        "source": "cua-driver",
                    },
                )
            )
        if not apps:
            raise UpstreamError("cua-driver list_apps returned no apps")
        return apps

    def get_app_state(
        self,
        app: str,
        max_tree_nodes: int,
        max_tree_depth: int,
        text_limit: int | str,
    ) -> tuple[str, bytes | None]:
        self._ensure_daemon()
        pid, window_id, title = self._resolve_window(app)
        payload = self._call(
            "get_window_state",
            {
                "pid": pid,
                "window_id": window_id,
                "max_elements": max_tree_nodes,
                "max_depth": max_tree_depth,
                "include_screenshot": True,
            },
        )
        text = render_elements(app, title, pid, payload)
        image = _png_from_payload(payload)
        return text, image

    def act_with_refresh(
        self,
        app: str,
        tool: str,
        args: dict,
        max_tree_nodes: int,
        max_tree_depth: int,
        text_limit: int | str,
    ) -> tuple[str, bytes | None, dict]:
        """Executes one action, then returns a fresh snapshot of the window."""
        self._ensure_daemon()
        cua_tool = _ACTION_TO_CUA.get(tool)
        if cua_tool is None:
            raise UpstreamError(f"action {tool!r} has no cua-driver mapping")
        pid, window_id, title = self._resolve_window(app)
        call_args = self._build_call_args(cua_tool, args, pid, window_id)
        result = self._call(cua_tool, call_args)
        payload = self._call(
            "get_window_state",
            {
                "pid": pid,
                "window_id": window_id,
                "max_elements": max_tree_nodes,
                "max_depth": max_tree_depth,
                "include_screenshot": True,
            },
        )
        text = render_elements(app, title, pid, payload)
        image = _png_from_payload(payload)
        return text, image, {"cua_tool": cua_tool, "action_result": result}

    @staticmethod
    def _build_call_args(
        cua_tool: str, args: dict, pid: int, window_id: int
    ) -> dict:
        """Maps facade action args onto cua-driver tool args."""
        if cua_tool == "click":
            if args.get("element_index") is not None:
                return {
                    "pid": pid,
                    "window_id": window_id,
                    "element_index": int(args["element_index"]),
                }
            call: dict[str, Any] = {
                "pid": pid,
                "window_id": window_id,
                "x": int(args["x"]),
                "y": int(args["y"]),
                "count": int(args.get("click_count", 1) or 1),
                "button": args.get("mouse_button", "left") or "left",
            }
            # Foreground delivery is cua's only focus-stealing escalation;
            # background synthesized clicks can be ignored by self-drawn
            # apps (JianYing, Kuark), so the facade forwards it on request.
            if args.get("click_method") == "foreground":
                call["delivery_mode"] = "foreground"
            return call
        if cua_tool == "right_click":
            return {
                "pid": pid,
                "window_id": window_id,
                "x": int(args["x"]),
                "y": int(args["y"]),
            }
        if cua_tool == "double_click":
            return {
                "pid": pid,
                "window_id": window_id,
                "x": int(args["x"]),
                "y": int(args["y"]),
            }
        if cua_tool == "drag":
            return {
                "pid": pid,
                "window_id": window_id,
                "from_x": int(args["from_x"]),
                "from_y": int(args["from_y"]),
                "to_x": int(args["to_x"]),
                "to_y": int(args["to_y"]),
            }
        if cua_tool == "set_value":
            return {
                "pid": pid,
                "window_id": window_id,
                "value": args["value"],
                **(
                    {"element_index": int(args["element_index"])}
                    if args.get("element_index") is not None
                    else {}
                ),
            }
        if cua_tool == "scroll":
            return {
                "pid": pid,
                "window_id": window_id,
                "direction": args.get("direction", "down"),
                "by": args.get("by", "line"),
                "amount": int(args.get("pages", 1) or 1),
            }
        if cua_tool == "type_text":
            return {"pid": pid, "text": args["value"] or args.get("text", "")}
        if cua_tool == "press_key":
            return {"pid": pid, "key": args["key"]}
        raise UpstreamError(f"unhandled cua-driver tool: {cua_tool}")

    # -------------------------------------------------- window-level actions

    def real_input_click(
        self,
        app: str,
        x: int,
        y: int,
        mouse_button: str = "left",
        click_count: int = 1,
    ) -> None:
        """Same contract as the CLI client: real cursor click (Windows)."""
        if self._win_input is None:
            raise RealInputUnavailableError(
                "real-input click requires the Windows client (no win_input backend)"
            )
        window = self._win_input.find_main_window(app)
        self._win_input.click(window, int(x), int(y), mouse_button, click_count)

    def focus_window(self, app: str) -> None:
        if self._win_input is None:
            raise RealInputUnavailableError(
                "focus_window requires the Windows client (no win_input backend)"
            )
        self._win_input.focus_main_window(app)

    def window_status(self, app: str) -> WindowStatus:
        if self._win_input is None:
            raise RealInputUnavailableError(
                "window_status requires the Windows client (no win_input backend)"
            )
        return self._win_input.window_status(app)

    def activate_window(self, app: str, title: str | None = None) -> WindowInfo:
        if self._win_input is None:
            raise RealInputUnavailableError(
                "activate_window requires the Windows client (no win_input backend)"
            )
        return self._win_input.activate_window(app, title)

    def maximize_window(self, app: str, title: str | None = None) -> WindowInfo:
        if self._win_input is None:
            raise RealInputUnavailableError(
                "maximize_window requires the Windows client (no win_input backend)"
            )
        return self._win_input.maximize_window(app, title)

    def window_rect(self, app: str) -> tuple[int, int, int, int] | None:
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


def _png_from_payload(payload: dict) -> bytes | None:
    """Extracts PNG bytes from a cua get_window_state payload, if present."""
    encoded = payload.get("screenshot_png_b64")
    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        return base64.b64decode(encoded)
    except Exception:  # noqa: BLE001 - image bytes are best-effort
        return None
