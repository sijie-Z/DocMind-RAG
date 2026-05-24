# -*- coding: utf-8 -*-
"""Prometheus metrics registry — RAG pipeline + cache + LLM metrics."""
import logging
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    CollectorRegistry,
    generate_latest,
    CONTENT_TYPE_LATEST,
    REGISTRY,
)

logger = logging.getLogger(__name__)

# ── RAG Retrieval ──────────────────────────────────────────────
RAG_RETRIEVAL_TOTAL = Counter(
    "rag_retrieval_total",
    "Total RAG retrieval attempts",
)
RAG_RETRIEVAL_HITS = Counter(
    "rag_retrieval_hits",
    "Retrievals that returned at least one document",
)
RAG_RETRIEVAL_ERRORS = Counter(
    "rag_retrieval_errors",
    "Retrieval attempts that raised an exception",
)
RAG_RETRIEVAL_LATENCY = Histogram(
    "rag_retrieval_latency_seconds",
    "RAG retrieval latency in seconds",
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# ── Cache ──────────────────────────────────────────────────────
RAG_CACHE_HITS = Counter(
    "rag_cache_hits_total",
    "Cache hits (exact + semantic)",
    ["cache_type"],  # "exact" | "semantic"
)
RAG_CACHE_MISSES = Counter(
    "rag_cache_misses_total",
    "Cache misses",
)
RAG_CACHE_EVICTIONS = Counter(
    "rag_cache_evictions_total",
    "Cache entries evicted",
    ["cache_type"],
)

# ── LLM ────────────────────────────────────────────────────────
LLM_REQUEST_TOTAL = Counter(
    "rag_llm_requests_total",
    "Total LLM API requests",
)
LLM_REQUEST_ERRORS = Counter(
    "rag_llm_request_errors_total",
    "LLM API requests that failed",
)
LLM_TOKENS = Counter(
    "rag_llm_tokens_total",
    "LLM tokens consumed",
    ["direction"],  # "input" | "output"
)
LLM_LATENCY = Histogram(
    "rag_llm_latency_seconds",
    "LLM streaming response latency (time to first token + full generation)",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
)

# ── Groundedness ───────────────────────────────────────────────
RAG_GROUNDED_TOTAL = Counter(
    "rag_grounded_total",
    "Total groundedness checks",
)
RAG_GROUNDED_HITS = Counter(
    "rag_grounded_hits",
    "Responses with source citations",
)

# ── Reranker ───────────────────────────────────────────────────
RAG_RERANK_LATENCY = Histogram(
    "rag_rerank_latency_seconds",
    "Reranker latency",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)
RAG_RERANK_TOTAL = Counter(
    "rag_rerank_total",
    "Total rerank operations",
)

# ── Query Processing ──────────────────────────────────────────
RAG_QUERY_INTENT = Counter(
    "rag_query_intent_total",
    "Query intent classifications",
    ["intent"],  # "factual" | "procedural" | "list" | "definition" | ...
)
RAG_QUERY_REWRITE_TOTAL = Counter(
    "rag_query_rewrite_total",
    "Query rewrite operations",
)
RAG_ADAPTIVE_STRATEGY = Counter(
    "rag_adaptive_strategy_total",
    "Adaptive RAG strategy selections",
    ["strategy"],  # "keyword_only" | "hybrid" | "hybrid_hyde"
)

# ── Pipeline Gauges ────────────────────────────────────────────
RAG_PIPELINE_IN_FLIGHT = Gauge(
    "rag_pipeline_in_flight",
    "Currently executing RAG pipeline operations",
)

# ── Evaluation ─────────────────────────────────────────────────
RAG_EVAL_TOTAL = Counter(
    "rag_eval_total",
    "Total RAG evaluation runs",
)
RAG_EVAL_FAITHFULNESS = Histogram(
    "rag_eval_faithfulness_score",
    "Faithfulness evaluation scores",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)
RAG_EVAL_RELEVANCY = Histogram(
    "rag_eval_relevancy_score",
    "Relevancy evaluation scores",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)
RAG_EVAL_CONTEXT_PRECISION = Histogram(
    "rag_eval_context_precision_score",
    "Context precision evaluation scores",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

# ── Agent ────────────────────────────────────────────────────
AGENT_PLANNING_TOTAL = Counter(
    "agent_planning_total",
    "Total planning attempts",
)
AGENT_PLANNING_LATENCY = Histogram(
    "agent_planning_latency_seconds",
    "Planning latency",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)
AGENT_EXECUTION_STEPS = Counter(
    "agent_execution_steps_total",
    "Total execution steps performed",
)
AGENT_TOOL_CALLS = Counter(
    "agent_tool_calls_total",
    "Tool calls by tool and result",
    ["tool", "result"],  # result: "success" | "error"
)
AGENT_TOOL_LATENCY = Histogram(
    "agent_tool_latency_seconds",
    "Per-tool execution latency",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)
AGENT_REFLECTION_DECISIONS = Counter(
    "agent_reflection_decisions_total",
    "Reflection decisions",
    ["decision"],  # "pass" | "retry" | "replan"
)
AGENT_MEMORY_RECALLS = Counter(
    "agent_memory_recalls_total",
    "Memory recall attempts",
    ["result"],  # "hit" | "miss"
)
AGENT_FEEDBACK_TOTAL = Counter(
    "agent_feedback_total",
    "User feedback on agent responses",
    ["feedback_type"],  # "thumbs_up" | "thumbs_down"
)


def get_prometheus_metrics() -> bytes:
    """Generate latest metrics in Prometheus exposition format."""
    return generate_latest(REGISTRY)


def get_content_type() -> str:
    return CONTENT_TYPE_LATEST
