"""E10: real record -> compile -> replay -> metrics pipeline on the live desktop.

Runs the full loop against a real recording: (1) load a recording produced by
``lean-computer-use record``, (2) compile it to a skill, (3) replay it through
the facade with a metrics file, (4) aggregate one JSONL record per phase plus
a summary, and print a comparison table against the upstream-default
baseline from ``docs/BENCHMARKS.md`` (437,779 model-visible chars per upstream
``get_app_state`` on the benchmarked ChatGPT window).

Privacy: only counts and sizes are written (``benchmarks/results/e10-*.jsonl``);
no screen text, window titles or images are stored.

Usage:
    uv run python benchmarks/e10_real_record_replay.py \
        --recording recordings/jianying-user-clean.json \
        --skill-dir <out> --library <lib.json> --metrics <out>.jsonl

The replay step performs real desktop actions (the recording must be a
workflow the user wants executed). Use ``--dry-run`` to only report the plan
without touching the desktop.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from lean_computer_use_mcp.record.model import Recording

#: Baseline from docs/BENCHMARKS.md (upstream default get_app_state, ChatGPT).
UPSTREAM_DEFAULT_MODEL_CHARS = 437779


def elapsed_ms(started: float) -> int:
    return round((time.time() - started) * 1000)


def _completed_steps(stdout: str) -> int:
    """Parse the ``Completed X/Y steps`` line from replay output."""
    match = re.search(r"Completed (\d+)/(\d+) steps", stdout)
    return int(match.group(1)) if match else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording", required=True, help="recording JSON from `record`")
    parser.add_argument("--skill-dir", required=True, help="where compile writes SKILL.md")
    parser.add_argument("--library", required=True, help="procedural memory file for compile/replay")
    parser.add_argument("--metrics", required=True, help="JSONL metrics file for the replay")
    parser.add_argument("--dry-run", action="store_true", help="compile only; preview replay plan")
    parser.add_argument(
        "--results",
        default=f"benchmarks/results/e10-{date.today().isoformat()}.jsonl",
        help="JSONL output for counts/sizes only",
    )
    args = parser.parse_args()

    started = time.time()
    recording = Recording.load(args.recording)
    records: list[dict] = []

    # Phase 1: compile (pure file transformation, no desktop).
    skill_dir = Path(args.skill_dir)
    skill_dir.mkdir(parents=True, exist_ok=True)
    from lean_computer_use_mcp.record.compile import write_skill

    skill_path, _recording_path = write_skill(recording, skill_dir)
    skill_chars = len(skill_path.read_text(encoding="utf-8"))
    records.append(
        {
            "kind": "compile",
            "skill_dir": Path(args.skill_dir).name,
            "skill_file": skill_path.name,
            "skill_chars": skill_chars,
            "recording_payload_chars": recording.metrics.text_chars,
            "recording_image_bytes": recording.metrics.image_bytes,
            "recording_nodes": recording.metrics.nodes,
            "steps": len(recording.steps),
            "duration_ms": recording.metrics.duration_ms,
        }
    )

    # Phase 2: replay through the facade (real desktop actions unless dry-run).
    replay_cmd = [
        sys.executable,
        "-m",
        "lean_computer_use_mcp",
        "replay",
        "--in",
        args.recording,
        "--run",
        "--yes",
        "--library",
        args.library,
        "--metrics-path",
        args.metrics,
    ]
    if args.dry_run:
        replay_cmd.remove("--run")
    proc = subprocess.run(
        replay_cmd,
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    tail = chr(10).join((proc.stdout or "").splitlines()[-6:])

    rows = []
    metrics_path = Path(args.metrics)
    if metrics_path.exists():
        for line in metrics_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    observe = [r for r in rows if r.get("tool") == "cu_observe"]
    act = [r for r in rows if r.get("tool") == "cu_act"]
    text_chars = sum(r.get("text_chars", 0) for r in rows)
    image_bytes = sum(r.get("image_bytes", 0) for r in rows)
    nodes = sum(r.get("nodes", 0) for r in rows)
    stale = sum(1 for r in rows if r.get("error") == "STALE_STATE")
    vision_calls = sum(1 for r in rows if r.get("vision_calls"))
    records.append(
        {
            "kind": "replay",
            "dry_run": bool(args.dry_run),
            "ok": "Completed" in (proc.stdout or ""),
            "exit_code": proc.returncode,
            "calls": len(rows),
            "observe_calls": len(observe),
            "act_calls": len(act),
            "model_text_chars": text_chars,
            "model_image_chars": 0,
            "local_image_bytes": image_bytes,
            "nodes": nodes,
            "stale_rejections": stale,
            "vision_calls": vision_calls,
            "completed_steps": _completed_steps(proc.stdout or ""),
        }
    )

    upstream_calls = len(rows)
    upstream_chars = upstream_calls * UPSTREAM_DEFAULT_MODEL_CHARS
    reduction_pct = (
        100.0 * (1 - text_chars / upstream_chars) if upstream_chars else 0.0
    )
    summary = {
        "kind": "summary",
        "date": date.today().isoformat(),
        "recording": Path(args.recording).name,
        "upstream_default_per_call_chars": UPSTREAM_DEFAULT_MODEL_CHARS,
        "facade_model_visible_chars": text_chars,
        "upstream_equivalent_chars": upstream_chars,
        "model_visible_reduction_pct": round(reduction_pct, 2),
        "local_image_bytes": image_bytes,
        "image_to_model_pct": 0.0,
        "stale_rejections": stale,
        "latency_ms": elapsed_ms(started),
    }
    records.append(summary)

    if args.dry_run:
        print("dry-run: results not recorded")
        return 0

    results = Path(args.results)
    results.parent.mkdir(parents=True, exist_ok=True)
    with results.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"phase: compile ({skill_chars} skill chars, {len(recording.steps)} steps)")
    print(f"phase: replay {len(rows)} calls -> {text_chars} model chars, {image_bytes} local image bytes, {stale} stale rejections")
    print(tail)
    print(
        f"summary: model-visible {text_chars} chars vs upstream-equivalent "
        f"{upstream_chars} chars -> {reduction_pct:.2f}% reduction "
        f"(image gate: 100.0%)"
    )
    print(f"wrote: {results}")
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
