from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class MetricsLogger:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path) if path else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, **fields) -> None:
        if self.path is None:
            return
        record = {"ts": datetime.now(timezone.utc).isoformat(), **fields}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def summary(self) -> dict[str, int | float]:
        if self.path is None or not self.path.exists():
            return {}
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        latencies = [row["latency_ms"] for row in rows if row.get("latency_ms") is not None]
        return {
            "calls": len(rows),
            "observe_calls": sum(1 for row in rows if row.get("tool") == "cu_observe"),
            "action_calls": sum(1 for row in rows if row.get("tool") in {"cu_act", "cu_batch"}),
            "errors": sum(1 for row in rows if row.get("error")),
            "text_chars": sum(row.get("text_chars", 0) for row in rows),
            "image_bytes": sum(row.get("image_bytes", 0) for row in rows),
            "image_payloads": sum(row.get("image_payloads", 0) for row in rows),
            "nodes": sum(row.get("nodes", 0) for row in rows),
            "stale_rejections": sum(1 for row in rows if row.get("error") == "STALE_STATE"),
            "vision_calls": sum(row.get("vision_calls", 0) for row in rows),
            "vision_image_bytes": sum(row.get("vision_image_bytes", 0) for row in rows),
            "vision_latency_ms": sum(row.get("vision_latency_ms", 0) for row in rows),
            "vision_elements": sum(row.get("vision_elements", 0) for row in rows),
            "vision_upgrade_calls": sum(1 for row in rows if row.get("vision_upgrades")),
            "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        }
