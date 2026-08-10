"""M4: facade success-rate matrix over scripted scenarios.

Each scenario drives the facade exactly like a model would (observe -> act ->
window -> replay) and records pass/fail plus honest cost counters (calls,
text characters, image bytes, nodes). The matrix runs on any machine with the
deterministic fake upstream (no desktop, no API keys); ``--real`` switches the
facade to the installed open-computer-use CLI and needs a live desktop -
scenarios S2/S4 perform a real click / drag in that mode.

Usage:
    uv run python benchmarks/success_matrix.py
    uv run python benchmarks/success_matrix.py --real --app ChatGPT

Writes one JSONL row per scenario to
``benchmarks/results/success-matrix-<timestamp>.jsonl`` (counts only; no
screen text, window titles, or image bytes are stored). Exit code 0 when
every scenario passes, 1 otherwise (CI gate).
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from lean_computer_use_mcp.config import Settings
from lean_computer_use_mcp.record.model import Recording, RecordedStep
from lean_computer_use_mcp.record.replay import ReplayRunner
from lean_computer_use_mcp.server import LeanComputerUse
from lean_computer_use_mcp.upstream.fake_client import FakeUpstreamClient

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "examples" / "fixtures"
RESULTS_DIR = REPO_ROOT / "benchmarks" / "results"


class CountingFake(FakeUpstreamClient):
    """Fake upstream that counts state reads vs action executions."""

    def __init__(self) -> None:
        super().__init__(FIXTURES)
        self.state_reads = 0
        self.action_calls = 0

    def get_app_state(self, app, max_tree_nodes, max_tree_depth, text_limit):
        self.state_reads += 1
        return super().get_app_state(app, max_tree_nodes, max_tree_depth, text_limit)

    def act_with_refresh(
        self, app, tool, args, max_tree_nodes, max_tree_depth, text_limit
    ):
        self.action_calls += 1
        return super().act_with_refresh(
            app, tool, args, max_tree_nodes, max_tree_depth, text_limit
        )


def _observe(engine: LeanComputerUse, app: str) -> dict:
    return engine.observe(
        app,
        intent="interact",
        output_mode="controls",
        preset="control",
        vision="auto",
        max_results=40,
    )


def _recording() -> Recording:
    return Recording(
        name="matrix-demo",
        app="ChatGPT",
        description="",
        started_at=0.0,
        steps=[
            RecordedStep(action="focus", window_title="ChatGPT"),
            RecordedStep(
                action="click", window_title="ChatGPT", x=400, y=300, matched=False
            ),
            RecordedStep(action="type_text", window_title="ChatGPT", value="hello"),
        ],
    )


# --- scenarios ---------------------------------------------------------------


def scenario_observe(engine, upstream, ctx) -> dict:
    state = _observe(engine, ctx["app"])
    if not state.get("ok"):
        return {"ok": False, "error": state.get("error", "observe failed")}
    if not state.get("controls"):
        return {"ok": False, "error": "no controls returned"}
    return {"ok": True}


def scenario_act_click(engine, upstream, ctx) -> dict:
    state = _observe(engine, ctx["app"])
    if not state.get("ok"):
        return {"ok": False, "error": state.get("error", "observe failed")}
    result = engine.act(ctx["app"], state["state_id"], "click", element_index="12")
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "click failed")}
    if "delta" not in result:
        return {"ok": False, "error": "click response missing delta"}
    return {"ok": True}


def scenario_stale_rejected(engine, upstream, ctx) -> dict:
    first = _observe(engine, ctx["app"])
    second = _observe(engine, ctx["app"])
    if not first.get("ok") or not second.get("ok"):
        return {"ok": False, "error": "observe failed"}
    result = engine.act(ctx["app"], first["state_id"], "click", element_index="12")
    rejected = (not result.get("ok")) and result.get("error") == "STALE_STATE"
    current = result.get("current_state_id") == second["state_id"]
    if not rejected:
        return {"ok": False, "error": f"stale state was not rejected: {result.get('error')}"}
    if not current:
        return {"ok": False, "error": "current_state_id does not match the latest snapshot"}
    return {"ok": True}


def scenario_drag_coords(engine, upstream, ctx) -> dict:
    state = _observe(engine, ctx["app"])
    if not state.get("ok"):
        return {"ok": False, "error": state.get("error", "observe failed")}
    result = engine.act(
        ctx["app"],
        state["state_id"],
        "drag",
        from_x=1,
        from_y=2,
        to_x=3,
        to_y=4,
    )
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "drag failed")}
    return {"ok": True}


def scenario_window(engine, upstream, ctx) -> dict:
    listing = engine.window(ctx["app"], "list")
    if not listing.get("ok") or not listing.get("candidates"):
        return {"ok": False, "error": "window list failed or empty"}
    if listing.get("ambiguous"):
        return {"ok": False, "error": "unexpected ambiguity for one window"}
    activated = engine.window(ctx["app"], "activate")
    if not activated.get("ok"):
        return {"ok": False, "error": activated.get("error", "activate failed")}
    return {"ok": True}


def scenario_replay_dry_run(engine, upstream, ctx) -> dict:
    runner = ReplayRunner(engine, confirm=lambda index, step: True)
    result = runner.run(_recording(), dry_run=True)
    if not result.ok or result.completed != 3:
        return {"ok": False, "error": "dry-run did not complete all steps"}
    if ctx["reads_before"] != upstream.state_reads:
        return {"ok": False, "error": "dry-run touched the upstream"}
    return {"ok": True}


def scenario_replay_declined(engine, upstream, ctx) -> dict:
    runner = ReplayRunner(engine, confirm=lambda index, step: False)
    result = runner.run(_recording(), dry_run=False)
    declined = [
        outcome
        for outcome in result.outcomes
        if outcome.action != "focus" and outcome.error == "declined"
    ]
    if result.ok or len(declined) != 2:
        return {"ok": False, "error": "declined steps were not reported as failures"}
    if (
        ctx["reads_before"] != upstream.state_reads
        or ctx["acts_before"] != upstream.action_calls
    ):
        return {"ok": False, "error": "declined replay touched the upstream"}
    return {"ok": True}


def scenario_metrics_hygiene(engine, upstream, ctx) -> dict:
    rows = [
        json.loads(line)
        for line in Path(ctx["metrics_path"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        return {"ok": False, "error": "no metrics rows were recorded"}
    if sum(row.get("text_chars", 0) for row in rows) <= 0:
        return {"ok": False, "error": "no text characters were recorded"}
    if not any(row.get("error") == "STALE_STATE" for row in rows):
        return {"ok": False, "error": "stale rejection was not recorded in metrics"}
    if ctx["real"]:
        # Real snapshots legitimately capture bytes locally; the matrix only
        # requires them to be counted, never to be zero.
        return {"ok": True}
    if any(row.get("image_bytes", 0) != 0 for row in rows):
        return {"ok": False, "error": "image bytes reached model-visible metrics in fake mode"}
    return {"ok": True}


SCENARIOS = [
    ("S1", "observe known app", scenario_observe),
    ("S2", "act click by element", scenario_act_click),
    ("S3", "stale state rejected", scenario_stale_rejected),
    ("S4", "drag by coordinates", scenario_drag_coords),
    ("S5", "window list + activate", scenario_window),
    ("S6", "replay dry-run offline", scenario_replay_dry_run),
    ("S7", "replay declined offline", scenario_replay_declined),
    ("S8", "metrics hygiene", scenario_metrics_hygiene),
]


def _delta(before: dict, after: dict) -> dict:
    return {
        "calls": int(after.get("calls", 0)) - int(before.get("calls", 0)),
        "text_chars": int(after.get("text_chars", 0)) - int(before.get("text_chars", 0)),
        "image_bytes": int(after.get("image_bytes", 0)) - int(before.get("image_bytes", 0)),
        "nodes": int(after.get("nodes", 0)) - int(before.get("nodes", 0)),
        "stale_rejections": int(after.get("stale_rejections", 0))
        - int(before.get("stale_rejections", 0)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real",
        action="store_true",
        help="Use the installed open-computer-use CLI (needs a live desktop; "
        "S2/S4 perform real actions)",
    )
    parser.add_argument("--app", default="ChatGPT", help="Target app name")
    parser.add_argument(
        "--out",
        default=None,
        help="JSONL results path (default: benchmarks/results/success-matrix-<ts>.jsonl)",
    )
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="lean-cu-matrix-"))
    settings = Settings(
        metrics_path=str(tmp / "metrics.jsonl"),
        image_cache_root=str(tmp / "media"),
    )
    if args.real:
        from lean_computer_use_mcp.upstream.cli_client import CliUpstreamClient

        class CountingReal(CliUpstreamClient):
            def __init__(self) -> None:
                super().__init__(timeout_seconds=120)
                self.state_reads = 0
                self.action_calls = 0

            def get_app_state(self, app, max_tree_nodes, max_tree_depth, text_limit):
                self.state_reads += 1
                return super().get_app_state(
                    app, max_tree_nodes, max_tree_depth, text_limit
                )

            def act_with_refresh(
                self, app, tool, args, max_tree_nodes, max_tree_depth, text_limit
            ):
                self.action_calls += 1
                return super().act_with_refresh(
                    app, tool, args, max_tree_nodes, max_tree_depth, text_limit
                )

        upstream: CountingFake | CountingReal = CountingReal()
        mode = "real"
    else:
        upstream = CountingFake()
        mode = "fake"

    engine = LeanComputerUse(upstream, settings)
    ctx = {
        "app": args.app,
        "real": args.real,
        "metrics_path": settings.metrics_path,
    }

    if args.real:
        print(
            "WARNING: --real executes real actions on the desktop "
            f"(app={args.app!r}); S2 clicks and S4 drags."
        )

    out_path = Path(args.out) if args.out else RESULTS_DIR / (
        f"success-matrix-{time.strftime('%Y%m%d%H%M%S')}.jsonl"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    header = (
        f"{'scenario':<10}{'name':<28}{'ok':<6}{'error':<34}"
        f"{'calls':>6}{'text':>9}{'img':>9}{'nodes':>7}"
    )
    print(header)
    print("-" * len(header))
    for scenario_id, name, fn in SCENARIOS:
        ctx["reads_before"] = upstream.state_reads
        ctx["acts_before"] = upstream.action_calls
        before = engine.metrics_summary()
        try:
            outcome = fn(engine, upstream, ctx)
        except Exception as exc:  # noqa: BLE001 - matrix must not crash mid-run
            outcome = {"ok": False, "error": f"exception: {exc}"}
        after = engine.metrics_summary()
        delta = _delta(before, after)
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "mode": mode,
            "scenario": scenario_id,
            "name": name,
            "ok": bool(outcome.get("ok")),
            "error": outcome.get("error"),
            **delta,
        }
        rows.append(row)
        print(
            f"{scenario_id:<10}{name:<28}{str(row['ok']):<6}"
            f"{(row['error'] or ''):<34}{delta['calls']:>6}"
            f"{delta['text_chars']:>9}{delta['image_bytes']:>9}{delta['nodes']:>7}"
        )
        with out_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    passed = sum(1 for row in rows if row["ok"])
    total = len(rows)
    print("-" * len(header))
    print(f"SUCCESS {passed}/{total}  mode={mode}  results={out_path}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
