"""Prometheus metrics registry — fully lazy with no-op fallback.

prometheus_client has known multiprocess deadlock issues on Windows + Git Bash.
To keep the import chain fast and reliable, ALL metrics are no-op by default.
To enable real metrics, set PROMETHEUS_ENABLED=true before the first import.

Module-level objects (AGENT_PLANNING_TOTAL, etc.) exist and accept .inc() / .observe()
calls without importing prometheus_client. They silently no-op unless enabled.

Usage:
    from app.core.prometheus import AGENT_TOOL_CALLS
    AGENT_TOOL_CALLS.labels(tool="feishu", result="success").inc()  # no-op unless enabled
"""
import os
import threading
from typing import Any


def _resolve_enabled() -> bool:
    """总开关判定（issue #207）：真实环境变量优先；其次读 settings ——
    让 **.env 配置也能生效**（此前只看 os.environ，.env 里的开关无效，
    与 #232 同源）。settings 不可用（极端导入顺序）时安全回退 False。
    """
    env = os.environ.get("PROMETHEUS_ENABLED", "")
    if env:
        return env.lower() in ("1", "true", "yes")
    try:
        from app.core.config import settings

        return bool(getattr(settings, "PROMETHEUS_ENABLED", False))
    except Exception:
        return False


_METRICS_ENABLED = _resolve_enabled()
_lock = threading.Lock()
_real_metrics: dict[str, Any] | None = None


class _NoopMetric:
    """Silent no-op — accepts all method calls, does nothing."""
    __slots__ = ()
    def inc(self, amount=1, **labels): pass
    def dec(self, amount=1, **labels): pass
    def observe(self, amount, **labels): pass
    def labels(self, **label_values): return self
    def set(self, value): pass
    def __repr__(self): return "<NoopMetric>"


class _LazyMetric:
    """Module-level metric that delegates to real or no-op implementation."""
    __slots__ = ("_name",)
    def __init__(self, name: str):
        self._name = name
    def _get(self):
        r = _get_real()
        return r.get(self._name) if r is not None else _NOOP
    def inc(self, amount=1, **labels):
        self._get().inc(amount, **labels)
    def dec(self, amount=1, **labels):
        self._get().dec(amount, **labels)
    def observe(self, amount, **labels):
        self._get().observe(amount, **labels)
    def labels(self, **label_values):
        return self._get().labels(**label_values)
    def set(self, value):
        self._get().set(value)
    def __repr__(self):
        return f"<LazyMetric:{self._name}>"


_NOOP = _NoopMetric()


def _get_real() -> dict[str, Any] | None:
    """Load real prometheus_client metrics on first call (if enabled)."""
    global _real_metrics
    if _real_metrics is not None:
        return _real_metrics
    if not _METRICS_ENABLED:
        return None
    with _lock:
        if _real_metrics is not None:
            return _real_metrics
        try:
            _real_metrics = _build_metrics()
            return _real_metrics
        except Exception:
            return None


def _build_metrics() -> dict[str, Any]:
    """Create all prometheus_client metric objects."""
    from prometheus_client import Counter, Gauge, Histogram
    m: dict[str, Any] = {}
    m["RAG_RETRIEVAL_TOTAL"] = Counter("rag_retrieval_total", "Total RAG retrieval attempts")
    m["RAG_RETRIEVAL_HITS"] = Counter("rag_retrieval_hits", "Retrievals that returned at least one document")
    m["RAG_RETRIEVAL_ERRORS"] = Counter("rag_retrieval_errors", "Retrieval attempts that raised an exception")
    m["RAG_RETRIEVAL_LATENCY"] = Histogram("rag_retrieval_latency_seconds", "RAG retrieval latency",
                                             buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0])
    m["RAG_CACHE_HITS"] = Counter("rag_cache_hits_total", "Cache hits", ["cache_type"])
    m["RAG_CACHE_MISSES"] = Counter("rag_cache_misses_total", "Cache misses")
    m["RAG_CACHE_EVICTIONS"] = Counter("rag_cache_evictions_total", "Cache evictions", ["cache_type"])
    m["LLM_REQUEST_TOTAL"] = Counter("rag_llm_requests_total", "Total LLM API requests")
    m["LLM_REQUEST_ERRORS"] = Counter("rag_llm_request_errors_total", "LLM API requests that failed")
    m["LLM_TOKENS"] = Counter("rag_llm_tokens_total", "LLM tokens consumed", ["direction"])
    m["LLM_LATENCY"] = Histogram("rag_llm_latency_seconds", "LLM streaming response latency",
                                  buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0])
    m["RAG_GROUNDED_TOTAL"] = Counter("rag_grounded_total", "Total groundedness checks")
    m["RAG_GROUNDED_HITS"] = Counter("rag_grounded_hits", "Responses with source citations")
    m["RAG_RERANK_LATENCY"] = Histogram("rag_rerank_latency_seconds", "Reranker latency",
                                          buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0])
    m["RAG_RERANK_TOTAL"] = Counter("rag_rerank_total", "Total rerank operations")
    m["RAG_QUERY_INTENT"] = Counter("rag_query_intent_total", "Query intent classifications", ["intent"])
    m["RAG_QUERY_REWRITE_TOTAL"] = Counter("rag_query_rewrite_total", "Query rewrite operations")
    m["RAG_ADAPTIVE_STRATEGY"] = Counter("rag_adaptive_strategy_total", "Adaptive RAG strategy", ["strategy"])
    m["RAG_PIPELINE_IN_FLIGHT"] = Gauge("rag_pipeline_in_flight", "Currently executing RAG pipeline operations")
    m["RAG_EVAL_TOTAL"] = Counter("rag_eval_total", "Total RAG evaluation runs")
    m["RAG_EVAL_FAITHFULNESS"] = Histogram("rag_eval_faithfulness_score", "Faithfulness scores",
                                             buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    m["RAG_EVAL_RELEVANCY"] = Histogram("rag_eval_relevancy_score", "Relevancy scores",
                                          buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    m["RAG_EVAL_CONTEXT_PRECISION"] = Histogram("rag_eval_context_precision_score", "Context precision",
                                                  buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    m["AGENT_PLANNING_TOTAL"] = Counter("agent_planning_total", "Total planning attempts")
    m["AGENT_PLANNING_LATENCY"] = Histogram("agent_planning_latency_seconds", "Planning latency",
                                              buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0])
    m["AGENT_EXECUTION_STEPS"] = Counter("agent_execution_steps_total", "Total execution steps performed")
    m["AGENT_TOOL_CALLS"] = Counter("agent_tool_calls_total", "Tool calls", ["tool", "result"])
    m["AGENT_TOOL_LATENCY"] = Histogram("agent_tool_latency_seconds", "Per-tool execution latency",
                                          buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0])
    m["AGENT_REFLECTION_DECISIONS"] = Counter("agent_reflection_decisions_total", "Reflection decisions", ["decision"])
    m["AGENT_MEMORY_RECALLS"] = Counter("agent_memory_recalls_total", "Memory recall attempts", ["result"])
    m["AGENT_FEEDBACK_TOTAL"] = Counter("agent_feedback_total", "User feedback", ["feedback_type"])
    return m


# ── Module-level convenience accessors (backward compat) ──
# These are real module objects that existing importers reference.
# They delegate to real or no-op metrics at runtime.

RAG_RETRIEVAL_TOTAL      = _LazyMetric("RAG_RETRIEVAL_TOTAL")
RAG_RETRIEVAL_HITS       = _LazyMetric("RAG_RETRIEVAL_HITS")
RAG_RETRIEVAL_ERRORS     = _LazyMetric("RAG_RETRIEVAL_ERRORS")
RAG_RETRIEVAL_LATENCY    = _LazyMetric("RAG_RETRIEVAL_LATENCY")
RAG_CACHE_HITS           = _LazyMetric("RAG_CACHE_HITS")
RAG_CACHE_MISSES         = _LazyMetric("RAG_CACHE_MISSES")
RAG_CACHE_EVICTIONS      = _LazyMetric("RAG_CACHE_EVICTIONS")
LLM_REQUEST_TOTAL        = _LazyMetric("LLM_REQUEST_TOTAL")
LLM_REQUEST_ERRORS       = _LazyMetric("LLM_REQUEST_ERRORS")
LLM_TOKENS               = _LazyMetric("LLM_TOKENS")
LLM_LATENCY              = _LazyMetric("LLM_LATENCY")
RAG_GROUNDED_TOTAL       = _LazyMetric("RAG_GROUNDED_TOTAL")
RAG_GROUNDED_HITS        = _LazyMetric("RAG_GROUNDED_HITS")
RAG_RERANK_LATENCY       = _LazyMetric("RAG_RERANK_LATENCY")
RAG_RERANK_TOTAL         = _LazyMetric("RAG_RERANK_TOTAL")
RAG_QUERY_INTENT         = _LazyMetric("RAG_QUERY_INTENT")
RAG_QUERY_REWRITE_TOTAL  = _LazyMetric("RAG_QUERY_REWRITE_TOTAL")
RAG_ADAPTIVE_STRATEGY    = _LazyMetric("RAG_ADAPTIVE_STRATEGY")
RAG_PIPELINE_IN_FLIGHT   = _LazyMetric("RAG_PIPELINE_IN_FLIGHT")
RAG_EVAL_TOTAL           = _LazyMetric("RAG_EVAL_TOTAL")
RAG_EVAL_FAITHFULNESS    = _LazyMetric("RAG_EVAL_FAITHFULNESS")
RAG_EVAL_RELEVANCY       = _LazyMetric("RAG_EVAL_RELEVANCY")
RAG_EVAL_CONTEXT_PRECISION = _LazyMetric("RAG_EVAL_CONTEXT_PRECISION")
AGENT_PLANNING_TOTAL     = _LazyMetric("AGENT_PLANNING_TOTAL")
AGENT_PLANNING_LATENCY   = _LazyMetric("AGENT_PLANNING_LATENCY")
AGENT_EXECUTION_STEPS    = _LazyMetric("AGENT_EXECUTION_STEPS")
AGENT_TOOL_CALLS         = _LazyMetric("AGENT_TOOL_CALLS")
AGENT_TOOL_LATENCY       = _LazyMetric("AGENT_TOOL_LATENCY")
AGENT_REFLECTION_DECISIONS = _LazyMetric("AGENT_REFLECTION_DECISIONS")
AGENT_MEMORY_RECALLS     = _LazyMetric("AGENT_MEMORY_RECALLS")
AGENT_FEEDBACK_TOTAL     = _LazyMetric("AGENT_FEEDBACK_TOTAL")


def get_prometheus_metrics() -> bytes:
    """Generate latest metrics in Prometheus exposition format (if enabled)."""
    r = _get_real()
    if r is None:
        return b""
    from prometheus_client import REGISTRY, generate_latest
    return generate_latest(REGISTRY)


def get_content_type() -> str:
    from prometheus_client import CONTENT_TYPE_LATEST
    return CONTENT_TYPE_LATEST
