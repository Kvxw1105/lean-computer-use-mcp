"""Unit tests for the M4 success-rate matrix (fake mode, no desktop)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from lean_computer_use_mcp.config import Settings
from lean_computer_use_mcp.server import LeanComputerUse

BENCHMARKS = Path(__file__).parent.parent / "benchmarks"


def _load_matrix() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "success_matrix", BENCHMARKS / "success_matrix.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_matrix_scenarios_pass_in_fake_mode(tmp_path) -> None:
    matrix = _load_matrix()
    settings = Settings(
        metrics_path=str(tmp_path / "metrics.jsonl"),
        image_cache_root=str(tmp_path / "media"),
    )
    upstream = matrix.CountingFake()
    engine = LeanComputerUse(upstream, settings)
    ctx = {
        "app": "ChatGPT",
        "real": False,
        "metrics_path": settings.metrics_path,
        "reads_before": 0,
        "acts_before": 0,
    }
    for scenario_id, name, fn in matrix.SCENARIOS:
        ctx["reads_before"] = upstream.state_reads
        ctx["acts_before"] = upstream.action_calls
        outcome = fn(engine, upstream, ctx)
        assert outcome["ok"], f"{scenario_id} {name}: {outcome.get('error')}"

    rows = [
        json.loads(line)
        for line in Path(settings.metrics_path)
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    # Fake mode: no screenshot bytes ever reach model-visible metrics.
    assert all(row.get("image_bytes", 0) == 0 for row in rows)
    assert any(row.get("error") == "STALE_STATE" for row in rows)
    assert sum(row.get("text_chars", 0) for row in rows) > 0
