"""M1: compare upstream default snapshots with facade snapshots on the real desktop.

Read-only benchmark: lists apps and takes accessibility snapshots; it never
performs a desktop action.

Usage:
    uv run python benchmarks/m1_real_compare.py [--app ChatGPT] [--out
    benchmarks/results/m1-YYYY-MM-DD.jsonl]

Output:
    One JSONL record per measurement plus a summary record, and a printed table.
    All records are counts/sizes only; no screen text or image bytes are written.
"""

from __future__ import annotations

import argparse
import base64
import json
import tempfile
import time
from datetime import date
from pathlib import Path

from lean_computer_use_mcp.config import Settings
from lean_computer_use_mcp.parse.tree_parser import parse_state
from lean_computer_use_mcp.server import LeanComputerUse
from lean_computer_use_mcp.upstream.cli_client import CliUpstreamClient

DEFAULT_BUDGET = (1200, 64, "max")
CONTROL_BUDGET = (80, 8, 160)
READ_BUDGET = (160, 10, 600)


def elapsed_ms(started: float) -> int:
    return round((time.time() - started) * 1000)


def json_chars(value) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def measure_upstream(client: CliUpstreamClient, app: str, label: str, budget) -> dict:
    started = time.time()
    raw, image = client.get_app_state(app, *budget)
    title, _focused, controls = parse_state(raw)
    image_b64 = base64.b64encode(image).decode("ascii") if image else ""
    return {
        "kind": "upstream",
        "label": label,
        "budget": list(budget),
        "text_chars": len(raw),
        "image_bytes": len(image) if image else 0,
        "image_b64_chars": len(image_b64),
        "nodes": len(controls),
        "window_title": title,
        "latency_ms": elapsed_ms(started),
    }


def measure_facade(engine: LeanComputerUse, app: str, label: str, **kwargs) -> dict:
    started = time.time()
    response = engine.observe(app, **kwargs)
    latency_ms = elapsed_ms(started)
    image_data = response.get("screenshot", {}).get("data") or ""
    image_bytes = response.get("screenshot", {}).get("bytes") or 0
    metrics_path = Path(engine.metrics.path) if engine.metrics.path else None
    nodes_parsed = None
    if metrics_path is not None and metrics_path.exists():
        rows = [
            json.loads(line)
            for line in metrics_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if rows:
            nodes_parsed = rows[-1].get("nodes")
    return {
        "kind": "facade",
        "label": label,
        "budget": None,
        "ok": response.get("ok"),
        "model_text_chars": json_chars(response),
        "model_image_chars": len(image_data),
        "image_bytes": image_bytes,
        "image_payloads": 1 if image_data else 0,
        "nodes_parsed": nodes_parsed,
        "nodes_returned": len(response.get("controls", [])),
        "window_title": response.get("window_title"),
        "latency_ms": latency_ms,
        "state_id": response.get("state_id"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", default="ChatGPT", help="App name to snapshot (must be running).")
    parser.add_argument(
        "--out",
        default=f"benchmarks/results/m1-{date.today().isoformat()}.jsonl",
        help="JSONL output path.",
    )
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="lean-cu-m1-"))
    settings = Settings(
        metrics_path=str(tmp / "metrics.jsonl"),
        image_cache_root=str(tmp / "media"),
        upstream_timeout_seconds=120,
    )
    client = CliUpstreamClient(timeout_seconds=120)
    engine = LeanComputerUse(client, settings)

    records = []
    for label, budget in [
        ("upstream-default", DEFAULT_BUDGET),
        ("upstream-control", CONTROL_BUDGET),
        ("upstream-read", READ_BUDGET),
    ]:
        records.append(measure_upstream(client, args.app, label, budget))
    records.append(
        measure_facade(
            engine,
            args.app,
            "facade-controls",
            preset="control",
            output_mode="controls",
            max_results=20,
        )
    )
    records.append(
        measure_facade(
            engine,
            args.app,
            "facade-reading",
            preset="read",
            output_mode="reading",
            max_results=20,
        )
    )
    records.append(
        measure_facade(
            engine,
            args.app,
            "facade-visual",
            preset="control",
            output_mode="controls",
            include_screenshot=True,
            max_results=20,
        )
    )

    default = records[0]
    facade_controls = records[3]
    facade_reading = records[4]
    facade_visual = records[5]

    def visible_chars(record: dict) -> int:
        if record["kind"] == "upstream":
            return record["text_chars"] + record["image_b64_chars"]
        return record["model_text_chars"] + record["model_image_chars"]

    summary = {
        "kind": "summary",
        "app": args.app,
        "date": date.today().isoformat(),
        "upstream_default": {
            "text_chars": default["text_chars"],
            "image_b64_chars": default["image_b64_chars"],
            "nodes": default["nodes"],
            "model_visible_chars": visible_chars(default),
        },
        "facade_controls": {
            "model_text_chars": facade_controls["model_text_chars"],
            "model_image_chars": facade_controls["model_image_chars"],
            "nodes_returned": facade_controls["nodes_returned"],
            "model_visible_chars": visible_chars(facade_controls),
        },
        "facade_reading": {
            "model_text_chars": facade_reading["model_text_chars"],
            "model_image_chars": facade_reading["model_image_chars"],
            "nodes_returned": facade_reading["nodes_returned"],
            "model_visible_chars": visible_chars(facade_reading),
        },
        "facade_visual": {
            "model_text_chars": facade_visual["model_text_chars"],
            "model_image_chars": facade_visual["model_image_chars"],
            "nodes_returned": facade_visual["nodes_returned"],
            "model_visible_chars": visible_chars(facade_visual),
        },
        "reductions": {
            "text_controls_pct": round(
                (1 - facade_controls["model_text_chars"] / default["text_chars"]) * 100, 1
            ),
            "text_reading_pct": round(
                (1 - facade_reading["model_text_chars"] / default["text_chars"]) * 100, 1
            ),
            "image_gate_pct": round(
                (1 - facade_controls["model_image_chars"] / default["image_b64_chars"]) * 100, 1
            ),
            "total_controls_pct": round(
                (1 - visible_chars(facade_controls) / visible_chars(default)) * 100, 1
            ),
            "total_reading_pct": round(
                (1 - visible_chars(facade_reading) / visible_chars(default)) * 100, 1
            ),
            "total_visual_pct": round(
                (1 - visible_chars(facade_visual) / visible_chars(default)) * 100, 1
            ),
            "nodes_controls_pct": round(
                (1 - facade_controls["nodes_returned"] / default["nodes"]) * 100, 1
            )
            if default["nodes"]
            else None,
        },
    }
    records.append(summary)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"app={args.app} date={summary['date']}\n")
    header = (
        f"{'snapshot':<18}{'text':>9}{'image b64':>12}{'nodes':>7}"
        f"{'model-visible':>16}{'latency':>9}"
    )
    print(header)
    for record in records:
        if record["kind"] == "summary":
            continue
        visible = visible_chars(record)
        text = record["text_chars"] if record["kind"] == "upstream" else record["model_text_chars"]
        image = (
            record["image_b64_chars"]
            if record["kind"] == "upstream"
            else record["model_image_chars"]
        )
        nodes = record["nodes"] if record["kind"] == "upstream" else record["nodes_returned"]
        print(
            f"{record['label']:<18}{text:>9}{image:>12}{nodes:>7}"
            f"{visible:>16}{record['latency_ms']:>9}"
        )
    print()
    for key, value in summary["reductions"].items():
        print(f"{key}: {value}%")
    print(f"\nrecords written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
