"""M1: real-upstream validation of cu_act state_id rejection paths.

Read-only: every case below is rejected before any desktop action executes.
The counting client asserts that zero upstream action calls happen.

Usage:
    uv run python benchmarks/m1_real_act_validation.py [--app ChatGPT]

Prints each rejection case and the recorded metrics summary.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from lean_computer_use_mcp.config import Settings
from lean_computer_use_mcp.server import LeanComputerUse
from lean_computer_use_mcp.upstream.cli_client import CliUpstreamClient

_ACTION_TOOLS = {"click", "set_value", "scroll", "type_text", "press_key", "perform_secondary_action", "drag"}


class CountingUpstream(CliUpstreamClient):
    """Real upstream wrapper that counts action executions vs state reads."""

    def __init__(self, timeout_seconds: int = 120) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self.action_calls = 0
        self.state_reads = 0

    def _subprocess(self, cmd: list[str], label: str):
        if "--calls" in cmd:
            calls = json.loads(cmd[cmd.index("--calls") + 1])
            for call in calls:
                if call["tool"] in _ACTION_TOOLS:
                    self.action_calls += 1
                else:
                    self.state_reads += 1
        elif label in _ACTION_TOOLS:
            self.action_calls += 1
        else:
            self.state_reads += 1
        return super()._subprocess(cmd, label)


def new_engine(root: Path, ttl: int = 30) -> tuple[LeanComputerUse, CountingUpstream]:
    settings = Settings(
        metrics_path=str(root / "metrics.jsonl"),
        image_cache_root=str(root / "media"),
        upstream_timeout_seconds=120,
        state_ttl_seconds=ttl,
    )
    upstream = CountingUpstream()
    return LeanComputerUse(upstream, settings), upstream


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", default="ChatGPT", help="App name to observe (must be running).")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="lean-cu-m1-act-"))
    results: list[dict] = []

    engine, upstream = new_engine(tmp / "noncurrent")
    s1 = engine.observe(args.app, preset="control")["state_id"]
    s2 = engine.observe(args.app, preset="control")["state_id"]
    result = engine.act(args.app, s1, "click", element_index="12")
    results.append(
        {
            "case": "non-current-state-id",
            "ok": result["ok"],
            "error": result.get("error"),
            "current_state_id_matches_latest": result.get("current_state_id") == s2,
        }
    )

    engine2, upstream2 = new_engine(tmp / "bogus")
    engine2.observe(args.app, preset="control")
    result2 = engine2.act(args.app, "bogus-state", "click", element_index="12")
    results.append({"case": "bogus-state-id", "ok": result2["ok"], "error": result2.get("error")})

    engine3, upstream3 = new_engine(tmp / "expired", ttl=1)
    observed3 = engine3.observe(args.app, preset="control")
    time.sleep(1.2)
    result3 = engine3.act(args.app, observed3["state_id"], "click", element_index="12")
    results.append({"case": "expired-state-id", "ok": result3["ok"], "error": result3.get("error")})

    engine4, upstream4 = new_engine(tmp / "unsupported")
    observed4 = engine4.observe(args.app, preset="control")
    result4 = engine4.act(args.app, observed4["state_id"], "drag")
    results.append({"case": "unsupported-action", "ok": result4["ok"], "error": result4.get("error")})

    total_action_calls = (
        upstream.action_calls + upstream2.action_calls + upstream3.action_calls + upstream4.action_calls
    )
    summary = engine.metrics_summary()

    print(f"app={args.app}\n")
    for item in results:
        print(json.dumps(item, ensure_ascii=False))
    print(f"total upstream action executions: {total_action_calls}")
    print("metrics (non-current engine):")
    print(
        json.dumps(
            {
                key: summary[key]
                for key in ("calls", "observe_calls", "action_calls", "errors", "stale_rejections", "avg_latency_ms", "nodes")
            },
            ensure_ascii=False,
        )
    )
    passed = (
        all(item["error"] is not None and not item["ok"] for item in results)
        and results[0]["current_state_id_matches_latest"]
        and total_action_calls == 0
    )
    print(f"\nPASS={passed}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
