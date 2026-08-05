from __future__ import annotations

import json
from pathlib import Path

from lean_computer_use_mcp.models import AppInfo
from lean_computer_use_mcp.upstream.base import UpstreamClient

_REPO_FIXTURES = Path(__file__).resolve().parents[3] / "examples" / "fixtures"


class FakeUpstreamClient(UpstreamClient):
    """Deterministic upstream used for demos and unit tests."""

    def __init__(self, fixture_dir: Path | None = None) -> None:
        self.fixture_dir = fixture_dir or _REPO_FIXTURES

    def _read_text(self, name: str) -> str:
        path = self.fixture_dir / name
        return path.read_text(encoding="utf-8")

    def list_apps(self) -> list[AppInfo]:
        rows = json.loads(self._read_text("list_apps.json"))
        return [AppInfo(**row) for row in rows]

    def get_app_state(
        self,
        app: str,
        max_tree_nodes: int,
        max_tree_depth: int,
        text_limit: int | str,
    ) -> tuple[str, bytes | None]:
        return self._read_text("state_chatgpt_control.txt"), None

    def focus_window(self, app: str) -> None:
        """No-op for the fake client (window-level action is Windows-only)."""

    def act_with_refresh(
        self,
        app: str,
        tool: str,
        args: dict,
        max_tree_nodes: int,
        max_tree_depth: int,
        text_limit: int | str,
    ) -> tuple[str, bytes | None, dict]:
        return self._read_text("state_chatgpt_after_modal.txt"), None, {"fake": True}
