"""Unit tests for the E10 real record-replay benchmark helpers.

The benchmark itself drives a live desktop; only its pure parsing helpers are
unit-tested here (platform-neutral, no desktop).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

BENCHMARKS = Path(__file__).parent.parent / "benchmarks"


def _load_e10() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "e10_real_record_replay", BENCHMARKS / "e10_real_record_replay.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_completed_steps_parses_replay_tail() -> None:
    e10 = _load_e10()
    assert e10._completed_steps("  12. [coords] ok\nCompleted 12/12 steps\n") == 12
    assert e10._completed_steps("Completed 0/12 steps") == 0
    assert e10._completed_steps("no replay output yet") == 0
