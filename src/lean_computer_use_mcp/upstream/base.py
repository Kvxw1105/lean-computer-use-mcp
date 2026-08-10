from __future__ import annotations

from abc import ABC, abstractmethod

from lean_computer_use_mcp.errors import RealInputUnavailableError
from lean_computer_use_mcp.models import AppInfo
from lean_computer_use_mcp.upstream.win_input import WindowInfo, WindowStatus


class UpstreamClient(ABC):
    @abstractmethod
    def list_apps(self) -> list[AppInfo]:
        """Return apps, including running state when available."""

    @abstractmethod
    def get_app_state(
        self,
        app: str,
        max_tree_nodes: int,
        max_tree_depth: int,
        text_limit: int | str,
    ) -> tuple[str, bytes | None]:
        """Return (accessibility text, optional PNG bytes)."""

    @abstractmethod
    def act_with_refresh(
        self,
        app: str,
        tool: str,
        args: dict,
        max_tree_nodes: int,
        max_tree_depth: int,
        text_limit: int | str,
    ) -> tuple[str, bytes | None, dict]:
        """Execute one action with an in-process snapshot so element_index resolves.

        Returns the post-action state text captured at the same budget, optional
        PNG bytes, and the raw upstream payload.
        """

    def real_input_click(
        self,
        app: str,
        x: int,
        y: int,
        mouse_button: str = "left",
        click_count: int = 1,
    ) -> None:
        """Inject a real mouse click at screenshot-pixel (x, y).

        Default: unavailable. Windows clients override with an
        input-injection implementation (see upstream.win_input).
        """
        raise RealInputUnavailableError(
            "real-input click is not available on this client"
        )

    def focus_window(self, app: str) -> None:
        """Bring the app's main window to the foreground (window-level action).

        Default: unavailable. Windows clients override with an
        input-injection implementation (see upstream.win_input).
        """
        raise RealInputUnavailableError("focus_window is not available on this client")

    def window_status(self, app: str) -> WindowStatus:
        """All matching windows with occlusion/ambiguity state.

        Default: unavailable. Windows clients override with an
        input-injection implementation (see upstream.win_input).
        """
        raise RealInputUnavailableError("window_status is not available on this client")

    def activate_window(self, app: str, title: str | None = None) -> WindowInfo:
        """Restore + foreground one window; never guess among ambiguous matches.

        Default: unavailable. Windows clients override with an
        input-injection implementation (see upstream.win_input).
        """
        raise RealInputUnavailableError("activate_window is not available on this client")

    def maximize_window(self, app: str, title: str | None = None) -> WindowInfo:
        """Restore + maximize + foreground one window; never guess among matches.

        Default: unavailable. Windows clients override with an
        input-injection implementation (see upstream.win_input).
        """
        raise RealInputUnavailableError("maximize_window is not available on this client")
