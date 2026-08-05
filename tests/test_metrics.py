from lean_computer_use_mcp.metrics.logger import MetricsLogger


def test_metrics_summary(tmp_path):
    logger = MetricsLogger(str(tmp_path / "metrics.jsonl"))
    logger.record(tool="cu_observe", text_chars=100, image_bytes=0)
    logger.record(tool="cu_act", text_chars=50, error="UPSTREAM_ERROR")
    summary = logger.summary()
    assert summary["calls"] == 2
    assert summary["text_chars"] == 150
    assert summary["errors"] == 1


def test_noop_without_path():
    logger = MetricsLogger(path=None)
    logger.record(tool="cu_observe", text_chars=10)
    assert logger.summary() == {}

def test_summary_includes_nodes_and_avg_latency(tmp_path):
    logger = MetricsLogger(str(tmp_path / "metrics.jsonl"))
    logger.record(tool="cu_observe", text_chars=100, image_bytes=0, nodes=15, latency_ms=120)
    logger.record(tool="cu_act", text_chars=50, image_bytes=0, nodes=8, latency_ms=300)
    summary = logger.summary()
    assert summary["nodes"] == 23
    assert summary["avg_latency_ms"] == 210
