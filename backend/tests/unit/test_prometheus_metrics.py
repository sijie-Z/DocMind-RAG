"""Prometheus metrics registry tests."""
import os

# Enable prometheus BEFORE importing the module
os.environ["PROMETHEUS_ENABLED"] = "true"


from app.core.prometheus import (  # noqa: E402
    AGENT_EXECUTION_STEPS,
    AGENT_MEMORY_RECALLS,
    AGENT_PLANNING_LATENCY,
    AGENT_PLANNING_TOTAL,
    AGENT_REFLECTION_DECISIONS,
    AGENT_TOKENS,
    AGENT_TOOL_CALLS,
    AGENT_TOOL_LATENCY,
    LLM_TOKENS,
    RAG_CACHE_HITS,
    RAG_PIPELINE_IN_FLIGHT,
    RAG_QUERY_INTENT,
    RAG_RETRIEVAL_HITS,
    RAG_RETRIEVAL_LATENCY,
    RAG_RETRIEVAL_TOTAL,
    _get_real,
    _LazyMetric,
    get_content_type,
    get_prometheus_metrics,
)


def _counter_value(metric: _LazyMetric, *labels: dict) -> float:
    """Helper: get current counter value from the real metric."""
    real = _get_real()
    if real is None:
        return 0.0
    m = real.get(metric._name)
    if m is None:
        return 0.0
    if labels:
        return m.labels(**labels[0])._value.get()
    return m._value.get()


def _counter_value_labelled(metric: _LazyMetric, label_values: dict) -> float:
    """Helper: get counter value for a labelled metric."""
    real = _get_real()
    if real is None:
        return 0.0
    m = real.get(metric._name)
    if m is None:
        return 0.0
    return m.labels(**label_values)._value.get()


class TestPrometheusMetrics:
    def test_retrieval_total_inc(self):
        """Counter increments correctly."""
        before = _counter_value(RAG_RETRIEVAL_TOTAL)
        RAG_RETRIEVAL_TOTAL.inc()
        assert _counter_value(RAG_RETRIEVAL_TOTAL) == before + 1

    def test_retrieval_hits_inc(self):
        before = _counter_value(RAG_RETRIEVAL_HITS)
        RAG_RETRIEVAL_HITS.inc()
        assert _counter_value(RAG_RETRIEVAL_HITS) == before + 1

    def test_cache_hits_labels(self):
        """Labeled counter tracks exact and semantic separately."""
        RAG_CACHE_HITS.labels(cache_type="exact").inc()
        RAG_CACHE_HITS.labels(cache_type="semantic").inc()
        RAG_CACHE_HITS.labels(cache_type="semantic").inc()
        assert _counter_value_labelled(RAG_CACHE_HITS, {"cache_type": "semantic"}) >= 2

    def test_latency_histogram_observe(self):
        """Histogram accepts observations."""
        RAG_RETRIEVAL_LATENCY.observe(0.05)
        RAG_RETRIEVAL_LATENCY.observe(0.1)
        # Verify via export
        data = get_prometheus_metrics().decode("utf-8")
        assert "rag_retrieval_latency" in data

    def test_llm_tokens_labels(self):
        """Token counter tracks input/output separately."""
        LLM_TOKENS.labels(direction="input").inc(100)
        LLM_TOKENS.labels(direction="output").inc(50)
        assert _counter_value_labelled(LLM_TOKENS, {"direction": "input"}) >= 100
        assert _counter_value_labelled(LLM_TOKENS, {"direction": "output"}) >= 50

    def test_agent_tokens_labels(self):
        """agent 侧独立口径（#239 minor-9）：model + direction 双标签。"""
        AGENT_TOKENS.labels(model="deepseek-chat", direction="input").inc(200)
        AGENT_TOKENS.labels(model="deepseek-chat", direction="output").inc(80)
        assert _counter_value_labelled(
            AGENT_TOKENS, {"model": "deepseek-chat", "direction": "input"}
        ) >= 200
        assert _counter_value_labelled(
            AGENT_TOKENS, {"model": "deepseek-chat", "direction": "output"}
        ) >= 80
        data = get_prometheus_metrics().decode("utf-8")
        assert "agent_tokens_total" in data

    def test_pipeline_in_flight_gauge(self):
        """Gauge can increment and decrement."""
        RAG_PIPELINE_IN_FLIGHT.inc()
        RAG_PIPELINE_IN_FLIGHT.inc()
        val = _counter_value(RAG_PIPELINE_IN_FLIGHT)
        RAG_PIPELINE_IN_FLIGHT.dec()
        assert _counter_value(RAG_PIPELINE_IN_FLIGHT) == val - 1

    def test_query_intent_labels(self):
        """Query intent counter tracks by intent type."""
        RAG_QUERY_INTENT.labels(intent="factual").inc()
        RAG_QUERY_INTENT.labels(intent="definition").inc()
        assert _counter_value_labelled(RAG_QUERY_INTENT, {"intent": "factual"}) >= 1

    def test_get_prometheus_metrics_returns_bytes(self):
        """generate_latest returns bytes."""
        data = get_prometheus_metrics()
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_prometheus_metrics_contain_rag_prefix(self):
        """Exported metrics include rag_ prefixed metrics."""
        data = get_prometheus_metrics().decode("utf-8")
        assert "rag_retrieval_total" in data

    def test_content_type(self):
        ct = get_content_type()
        assert "text/plain" in ct
        assert "charset=utf-8" in ct


class TestAgentPrometheusMetrics:
    """Agent-specific Prometheus metrics tests."""

    def test_planning_total_inc(self):
        before = _counter_value(AGENT_PLANNING_TOTAL)
        AGENT_PLANNING_TOTAL.inc()
        assert _counter_value(AGENT_PLANNING_TOTAL) == before + 1

    def test_planning_latency_observe(self):
        AGENT_PLANNING_LATENCY.observe(0.5)
        data = get_prometheus_metrics().decode("utf-8")
        assert "agent_planning_latency" in data

    def test_execution_steps_inc(self):
        AGENT_EXECUTION_STEPS.inc(3)
        assert _counter_value(AGENT_EXECUTION_STEPS) >= 3

    def test_tool_calls_labels(self):
        AGENT_TOOL_CALLS.labels(tool="search_knowledge_base", result="success").inc()
        AGENT_TOOL_CALLS.labels(tool="search_knowledge_base", result="error").inc()
        assert _counter_value_labelled(
            AGENT_TOOL_CALLS, {"tool": "search_knowledge_base", "result": "success"}
        ) >= 1
        assert _counter_value_labelled(
            AGENT_TOOL_CALLS, {"tool": "search_knowledge_base", "result": "error"}
        ) >= 1

    def test_tool_latency_observe(self):
        AGENT_TOOL_LATENCY.observe(0.1)
        data = get_prometheus_metrics().decode("utf-8")
        assert "agent_tool_latency" in data

    def test_reflection_decisions_labels(self):
        AGENT_REFLECTION_DECISIONS.labels(decision="pass").inc()
        AGENT_REFLECTION_DECISIONS.labels(decision="retry").inc()
        assert _counter_value_labelled(
            AGENT_REFLECTION_DECISIONS, {"decision": "pass"}
        ) >= 1

    def test_memory_recalls_labels(self):
        AGENT_MEMORY_RECALLS.labels(result="hit").inc()
        AGENT_MEMORY_RECALLS.labels(result="miss").inc()
        assert _counter_value_labelled(
            AGENT_MEMORY_RECALLS, {"result": "hit"}
        ) >= 1

    def test_agent_metrics_in_export(self):
        """Agent metrics should appear in the exported Prometheus output."""
        AGENT_PLANNING_TOTAL.inc()
        data = get_prometheus_metrics().decode("utf-8")
        assert "agent_planning_total" in data
        assert "agent_execution_steps_total" in data
        assert "agent_tool_calls_total" in data
