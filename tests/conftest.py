from __future__ import annotations

from pathlib import Path

import pytest

from lean_computer_use_mcp.config import Settings
from lean_computer_use_mcp.upstream.fake_client import FakeUpstreamClient

FIXTURES = Path(__file__).parent.parent / "examples" / "fixtures"


@pytest.fixture
def fake_upstream() -> FakeUpstreamClient:
    return FakeUpstreamClient(FIXTURES)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        metrics_path=str(tmp_path / "metrics.jsonl"),
        image_cache_root=str(tmp_path / "media"),
    )


@pytest.fixture
def control_state_text() -> str:
    return (FIXTURES / "state_chatgpt_control.txt").read_text(encoding="utf-8")


@pytest.fixture
def after_modal_state_text() -> str:
    return (FIXTURES / "state_chatgpt_after_modal.txt").read_text(encoding="utf-8")