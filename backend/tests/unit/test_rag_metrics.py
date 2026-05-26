"""Tests for RAG metrics."""
from app.rag.metrics import RAGMetrics


class TestRAGMetrics:
    def test_inc_counters(self):
        m = RAGMetrics()
        m.inc("retrieval_total")
        m.inc("retrieval_total")
        m.inc("retrieval_hit")
        snap = m.get_snapshot()
        assert snap["retrieval_total"] == 2
        assert snap["retrieval_hit"] == 1

    def test_inc_with_value(self):
        m = RAGMetrics()
        m.inc("latency_count", 10)
        snap = m.get_snapshot()
        # latency_count is used internally for avg_latency_ms calculation
        assert snap["retrieval_total"] == 0

    def test_record_event_window(self):
        m = RAGMetrics()
        m.record_event("latency", 10.0)
        m.record_event("latency", 20.0)
        # window_sum and window_count work on events
        total = m.window_sum("latency", window_seconds=60)
        count = m.window_count("latency", window_seconds=60)
        assert abs(total - 30.0) < 0.01
        assert count == 2

    def test_percentile(self):
        m = RAGMetrics()
        for i in range(100):
            m.record_event("latency", float(i))
        p50 = m.percentile("latency", 0.5, window_seconds=3600)
        p99 = m.percentile("latency", 0.99, window_seconds=3600)
        assert p50 is not None
        assert p99 is not None
        assert 45 <= p50 <= 55
        assert 95 <= p99 <= 100

    def test_percentile_empty(self):
        m = RAGMetrics()
        result = m.percentile("nonexistent", 0.5, window_seconds=60)
        assert result is None

    def test_reset(self):
        m = RAGMetrics()
        m.inc("retrieval_total", 10)
        m.record_event("latency", 5.0)
        m.reset()
        snap = m.get_snapshot()
        assert snap["retrieval_total"] == 0

    def test_get_snapshot_returns_dict(self):
        m = RAGMetrics()
        m.inc("retrieval_total", 5)
        m.inc("retrieval_hit", 3)
        m.inc("cache_hit", 1)
        snap = m.get_snapshot()
        assert isinstance(snap, dict)
        assert "retrieval_total" in snap
        assert "hit_rate" in snap
        assert "avg_latency_ms" in snap

    def test_hit_rate_calculation(self):
        m = RAGMetrics()
        m.inc("retrieval_total", 10)
        m.inc("retrieval_hit", 5)
        snap = m.get_snapshot()
        assert abs(snap["hit_rate"] - 0.5) < 0.01

    def test_groundedness_calculation(self):
        m = RAGMetrics()
        m.inc("grounded_total", 10)
        m.inc("grounded_hit", 8)
        snap = m.get_snapshot()
        assert abs(snap["groundedness"] - 0.8) < 0.01
