"""Tests for RAG metrics."""

import pytest

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

    def test_window_zero_events_returns_zero(self):
        """零事件窗口必须返回 0（旧实现 max(1) 把「没有查询」伪装成 1 次）。"""
        m = RAGMetrics()
        snap = m.get_snapshot(window_seconds=3600)
        assert snap["retrieval_total"] == 0
        assert snap["retrieval_hit"] == 0
        assert snap["hit_rate"] == 0


class TestAvgDocsPerQuery:
    def test_cumulative_average(self):
        m = RAGMetrics()
        m.inc("docs_retrieved_total", 3)
        m.inc("docs_retrieved_queries", 1)
        m.inc("docs_retrieved_total", 1)
        m.inc("docs_retrieved_queries", 1)
        snap = m.get_snapshot()
        assert snap["avg_docs_per_query"] == 2.0

    def test_windowed_average(self):
        m = RAGMetrics()
        m.record_event("docs_retrieved", 4.0)
        m.record_event("docs_retrieved", 0.0)
        snap = m.get_snapshot(window_seconds=60)
        assert snap["avg_docs_per_query"] == 2.0

    def test_no_queries_is_zero(self):
        m = RAGMetrics()
        assert m.get_snapshot()["avg_docs_per_query"] == 0
        assert m.get_snapshot(window_seconds=60)["avg_docs_per_query"] == 0


class TestPipelineDocsRecording:
    @pytest.mark.asyncio
    async def test_search_records_docs_and_failures_as_zero(self, monkeypatch):
        """检索入口统一记录：成功按返回条数、失败按 0（与 retrieval_total 同人群）。"""
        from app.rag.metrics import RAGMetrics
        from app.rag.pipeline import RAGPipeline

        pipeline = object.__new__(RAGPipeline)
        pipeline.metrics = RAGMetrics()

        async def _ok(*args, **kwargs):
            return [{"id": 1}, {"id": 2}], None

        monkeypatch.setattr(RAGPipeline, "_search_knowledge_base_impl", _ok)
        results, _ = await pipeline.search_knowledge_base("q", 1, 5)
        assert len(results) == 2
        assert pipeline.metrics.get_snapshot()["avg_docs_per_query"] == 2.0

        async def _fail(*args, **kwargs):
            raise RuntimeError("retrieval down")

        monkeypatch.setattr(RAGPipeline, "_search_knowledge_base_impl", _fail)
        with pytest.raises(RuntimeError):
            await pipeline.search_knowledge_base("q2", 1, 5)
        # (2 + 0) / 2 次检索 = 1.0：失败也进入分母，不会伪造偏高均值
        assert pipeline.metrics.get_snapshot()["avg_docs_per_query"] == 1.0
