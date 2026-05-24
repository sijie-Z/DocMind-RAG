# -*- coding: utf-8 -*-
"""Prometheus metrics registry tests."""
import pytest
from app.core.prometheus import (
    RAG_RETRIEVAL_TOTAL,
    RAG_RETRIEVAL_HITS,
    RAG_RETRIEVAL_ERRORS,
    RAG_RETRIEVAL_LATENCY,
    RAG_CACHE_HITS,
    RAG_CACHE_MISSES,
    RAG_GROUNDED_TOTAL,
    RAG_GROUNDED_HITS,
    LLM_REQUEST_TOTAL,
    LLM_REQUEST_ERRORS,
    LLM_TOKENS,
    LLM_LATENCY,
    RAG_PIPELINE_IN_FLIGHT,
    RAG_QUERY_INTENT,
    AGENT_PLANNING_TOTAL,
    AGENT_PLANNING_LATENCY,
    AGENT_EXECUTION_STEPS,
    AGENT_TOOL_CALLS,
    AGENT_TOOL_LATENCY,
    AGENT_REFLECTION_DECISIONS,
    AGENT_MEMORY_RECALLS,
    get_prometheus_metrics,
    get_content_type,
)


class TestPrometheusMetrics:
    def test_retrieval_total_inc(self):
        """Counter increments correctly."""
        before = RAG_RETRIEVAL_TOTAL._value.get()
        RAG_RETRIEVAL_TOTAL.inc()
        after = RAG_RETRIEVAL_TOTAL._value.get()
        assert after == before + 1

    def test_retrieval_hits_inc(self):
        before = RAG_RETRIEVAL_HITS._value.get()
        RAG_RETRIEVAL_HITS.inc()
        assert RAG_RETRIEVAL_HITS._value.get() == before + 1

    def test_cache_hits_labels(self):
        """Labeled counter tracks exact and semantic separately."""
        RAG_CACHE_HITS.labels(cache_type="exact").inc()
        RAG_CACHE_HITS.labels(cache_type="semantic").inc()
        RAG_CACHE_HITS.labels(cache_type="semantic").inc()
        # Both should be independently trackable
        assert RAG_CACHE_HITS.labels(cache_type="semantic")._value.get() >= 2

    def test_latency_histogram_observe(self):
        """Histogram accepts observations."""
        RAG_RETRIEVAL_LATENCY.observe(0.05)
        RAG_RETRIEVAL_LATENCY.observe(0.1)
        # Should not raise
        sample_count = RAG_RETRIEVAL_LATENCY._sum.get()
        assert sample_count > 0

    def test_llm_tokens_labels(self):
        """Token counter tracks input/output separately."""
        LLM_TOKENS.labels(direction="input").inc(100)
        LLM_TOKENS.labels(direction="output").inc(50)
        assert LLM_TOKENS.labels(direction="input")._value.get() >= 100
        assert LLM_TOKENS.labels(direction="output")._value.get() >= 50

    def test_pipeline_in_flight_gauge(self):
        """Gauge can increment and decrement."""
        RAG_PIPELINE_IN_FLIGHT.inc()
        RAG_PIPELINE_IN_FLIGHT.inc()
        val = RAG_PIPELINE_IN_FLIGHT._value.get()
        RAG_PIPELINE_IN_FLIGHT.dec()
        assert RAG_PIPELINE_IN_FLIGHT._value.get() == val - 1

    def test_query_intent_labels(self):
        """Query intent counter tracks by intent type."""
        RAG_QUERY_INTENT.labels(intent="factual").inc()
        RAG_QUERY_INTENT.labels(intent="definition").inc()
        assert RAG_QUERY_INTENT.labels(intent="factual")._value.get() >= 1

    def test_get_prometheus_metrics_returns_bytes(self):
        """generate_latest returns bytes."""
        data = get_prometheus_metrics()
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_prometheus_metrics_contain_rag_prefix(self):
        """Exported metrics include rag_ prefixed metrics."""
        data = get_prometheus_metrics().decode("utf-8")
        assert "rag_retrieval_total" in data
        assert "rag_retrieval_hits_total" in data
        assert "rag_cache_hits_total" in data
        assert "rag_llm_tokens_total" in data
        assert "rag_grounded_total" in data
        assert "rag_pipeline_in_flight" in data

    def test_content_type(self):
        ct = get_content_type()
        assert "text/plain" in ct
        assert "charset=utf-8" in ct


class TestAgentPrometheusMetrics:
    """Agent-specific Prometheus metrics tests."""

    def test_planning_total_inc(self):
        before = AGENT_PLANNING_TOTAL._value.get()
        AGENT_PLANNING_TOTAL.inc()
        assert AGENT_PLANNING_TOTAL._value.get() == before + 1

    def test_planning_latency_observe(self):
        AGENT_PLANNING_LATENCY.observe(0.5)
        assert AGENT_PLANNING_LATENCY._sum.get() > 0

    def test_execution_steps_inc(self):
        AGENT_EXECUTION_STEPS.inc(3)
        assert AGENT_EXECUTION_STEPS._value.get() >= 3

    def test_tool_calls_labels(self):
        AGENT_TOOL_CALLS.labels(tool="search_knowledge_base", result="success").inc()
        AGENT_TOOL_CALLS.labels(tool="search_knowledge_base", result="error").inc()
        assert AGENT_TOOL_CALLS.labels(tool="search_knowledge_base", result="success")._value.get() >= 1
        assert AGENT_TOOL_CALLS.labels(tool="search_knowledge_base", result="error")._value.get() >= 1

    def test_tool_latency_observe(self):
        AGENT_TOOL_LATENCY.observe(0.1)
        assert AGENT_TOOL_LATENCY._sum.get() > 0

    def test_reflection_decisions_labels(self):
        AGENT_REFLECTION_DECISIONS.labels(decision="pass").inc()
        AGENT_REFLECTION_DECISIONS.labels(decision="retry").inc()
        assert AGENT_REFLECTION_DECISIONS.labels(decision="pass")._value.get() >= 1

    def test_memory_recalls_labels(self):
        AGENT_MEMORY_RECALLS.labels(result="hit").inc()
        AGENT_MEMORY_RECALLS.labels(result="miss").inc()
        assert AGENT_MEMORY_RECALLS.labels(result="hit")._value.get() >= 1

    def test_agent_metrics_in_export(self):
        """Agent metrics should appear in the exported Prometheus output."""
        AGENT_PLANNING_TOTAL.inc()
        data = get_prometheus_metrics().decode("utf-8")
        assert "agent_planning_total" in data
        assert "agent_execution_steps_total" in data
        assert "agent_tool_calls_total" in data
