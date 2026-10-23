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

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)


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
# issue #301：构建失败必须显形——记录失败原因并保证 ERROR 日志只打一次；
# /metrics 导出据此输出「不可用」自曝行（区别于「未启用」的声明性空输出）。
_build_failure: Exception | None = None
_build_failure_logged = False


class _NoopMetric:
    """Silent no-op — accepts all method calls, does nothing."""

    __slots__ = ()

    def inc(self, amount=1, **labels):
        pass

    def dec(self, amount=1, **labels):
        pass

    def observe(self, amount, **labels):
        pass

    def labels(self, **label_values):
        return self

    def set(self, value):
        pass

    def __repr__(self):
        return "<NoopMetric>"


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
    """Load real prometheus_client metrics on first call (if enabled).

    构建失败不再静默（issue #301）：记 ERROR 日志（每进程一次）并记录失败原因，
    所有埋点维持 no-op；`get_prometheus_metrics()` 会输出显式「不可用」自曝行，
    /metrics 不再无声地少掉整族指标。
    """
    global _real_metrics, _build_failure, _build_failure_logged
    if _real_metrics is not None:
        return _real_metrics
    if not _METRICS_ENABLED:
        return None
    with _lock:
        if _real_metrics is not None:
            return _real_metrics
        try:
            _real_metrics = _build_metrics()
            _build_failure = None
            return _real_metrics
        except Exception as exc:
            _build_failure = exc
            if not _build_failure_logged:
                _build_failure_logged = True
                logger.error(
                    "Prometheus 指标构建失败（%s: %s）：全部埋点降级为 no-op，"
                    "/metrics 将输出不可用自曝行",
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
            return None


def _build_metrics() -> dict[str, Any]:
    """Create all prometheus_client metric objects."""
    from prometheus_client import Counter, Gauge, Histogram

    m: dict[str, Any] = {}
    m["RAG_RETRIEVAL_TOTAL"] = Counter("rag_retrieval_total", "Total RAG retrieval attempts")
    m["RAG_RETRIEVAL_HITS"] = Counter(
        "rag_retrieval_hits", "Retrievals that returned at least one document"
    )
    m["RAG_RETRIEVAL_ERRORS"] = Counter(
        "rag_retrieval_errors", "Retrieval attempts that raised an exception"
    )
    # issue #292：检索故障/超时的终态口径（state: failed / timeout / degraded）。
    # 与 RAG_RETRIEVAL_ERRORS（按次重试计数）不同，这里按「最终结论」计数：
    # failed/timeout = 未得出结论的查询；degraded = 有结果但检索臂降级。
    m["RAG_RETRIEVAL_UNAVAILABLE"] = Counter(
        "rag_retrieval_unavailable_total",
        "Retrievals that could not conclude (backend failure/timeout) or ran degraded",
        ["state"],
    )
    m["RAG_RETRIEVAL_LATENCY"] = Histogram(
        "rag_retrieval_latency_seconds",
        "RAG retrieval latency",
        buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    )
    m["RAG_CACHE_HITS"] = Counter("rag_cache_hits_total", "Cache hits", ["cache_type"])
    m["RAG_CACHE_MISSES"] = Counter("rag_cache_misses_total", "Cache misses")
    m["RAG_CACHE_EVICTIONS"] = Counter(
        "rag_cache_evictions_total", "Cache evictions", ["cache_type"]
    )
    m["LLM_REQUEST_TOTAL"] = Counter("rag_llm_requests_total", "Total LLM API requests")
    m["LLM_REQUEST_ERRORS"] = Counter(
        "rag_llm_request_errors_total", "LLM API requests that failed"
    )
    m["LLM_TOKENS"] = Counter("rag_llm_tokens_total", "LLM tokens consumed", ["direction"])
    # agent（PER 环 + 工具内调用）独立口径（issue #239 minor-9）：此前与 RAG 侧
    # 共用 rag_llm_tokens_total，观测上无法拆分两条链路。model 标签由 usage.py
    # 从 response.model 提取（取不到回退 "unknown"）。
    m["AGENT_TOKENS"] = Counter(
        "agent_tokens_total",
        "Agent-side LLM tokens consumed (PER loop + tool-internal calls)",
        ["model", "direction"],
    )
    m["LLM_LATENCY"] = Histogram(
        "rag_llm_latency_seconds",
        "LLM streaming response latency",
        buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
    )
    m["RAG_GROUNDED_TOTAL"] = Counter("rag_grounded_total", "Total groundedness checks")
    m["RAG_GROUNDED_HITS"] = Counter("rag_grounded_hits", "Responses with source citations")
    m["RAG_RERANK_LATENCY"] = Histogram(
        "rag_rerank_latency_seconds",
        "Reranker latency",
        buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
    )
    # issue #189：path/outcome 标签让「走了哪条重排路径 / 是否降级」可直接聚合查询
    # （修前只有裸计数，降级与正常无从区分）。
    m["RAG_RERANK_TOTAL"] = Counter(
        "rag_rerank_total", "Total rerank operations", ["path", "outcome"]
    )
    # issue #209：检索相关度闸门的终态口径（outcome: pass / refused /
    # strict_refused / fail_open_no_scores）。fail_open 表示阈值已配置但重排器
    # 没有产出绝对分（降级/关闭）——按 fail-open 放行，但绝不静默。
    m["RAG_RELEVANCE_GATE_TOTAL"] = Counter(
        "rag_relevance_gate_total", "RAG relevance gate decisions", ["outcome"]
    )
    m["RAG_QUERY_INTENT"] = Counter(
        "rag_query_intent_total", "Query intent classifications", ["intent"]
    )
    m["RAG_QUERY_REWRITE_TOTAL"] = Counter("rag_query_rewrite_total", "Query rewrite operations")
    m["RAG_ADAPTIVE_STRATEGY"] = Counter(
        "rag_adaptive_strategy_total", "Adaptive RAG strategy", ["strategy"]
    )
    m["RAG_PIPELINE_IN_FLIGHT"] = Gauge(
        "rag_pipeline_in_flight", "Currently executing RAG pipeline operations"
    )
    m["RAG_EVAL_TOTAL"] = Counter("rag_eval_total", "Total RAG evaluation runs")
    m["RAG_EVAL_FAITHFULNESS"] = Histogram(
        "rag_eval_faithfulness_score",
        "Faithfulness scores",
        buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    )
    m["RAG_EVAL_RELEVANCY"] = Histogram(
        "rag_eval_relevancy_score",
        "Relevancy scores",
        buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    )
    m["RAG_EVAL_CONTEXT_PRECISION"] = Histogram(
        "rag_eval_context_precision_score",
        "Context precision",
        buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    )
    m["AGENT_PLANNING_TOTAL"] = Counter("agent_planning_total", "Total planning attempts")
    m["AGENT_PLANNING_LATENCY"] = Histogram(
        "agent_planning_latency_seconds",
        "Planning latency",
        buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
    )
    m["AGENT_EXECUTION_STEPS"] = Counter(
        "agent_execution_steps_total", "Total execution steps performed"
    )
    m["AGENT_TOOL_CALLS"] = Counter("agent_tool_calls_total", "Tool calls", ["tool", "result"])
    m["AGENT_TOOL_LATENCY"] = Histogram(
        "agent_tool_latency_seconds",
        "Per-tool execution latency",
        buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
    )
    m["AGENT_REFLECTION_DECISIONS"] = Counter(
        "agent_reflection_decisions_total", "Reflection decisions", ["decision"]
    )
    m["AGENT_MEMORY_RECALLS"] = Counter(
        "agent_memory_recalls_total", "Memory recall attempts", ["result"]
    )
    m["AGENT_FEEDBACK_TOTAL"] = Counter("agent_feedback_total", "User feedback", ["feedback_type"])
    # User Commands（issue #475）：命令执行审计面——按命令名与结果
    # （success / 具体错误码）计数；失败可查（issue #475 任务 5）。
    m["AGENT_COMMAND_EXECUTIONS"] = Counter(
        "agent_command_executions_total", "User command executions", ["command", "result"]
    )
    # Hooks（issue #474）：扩展点执行审计面——每次 hook 执行/失败都计数与计时。
    # outcome ∈ allow/deny/modify/ask/error/timeout/invalid_decision；失败不得静默。
    m["HOOK_EXECUTIONS"] = Counter(
        "docmind_hook_executions_total", "Hook executions", ["event", "hook", "outcome"]
    )
    m["HOOK_DURATION"] = Histogram(
        "docmind_hook_duration_seconds",
        "Hook execution duration",
        ["event", "hook"],
        buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
    )
    # Webhook runtime（issue #478）：投递终态、poll 循环异常、幂等降级、限流。
    m["WEBHOOK_DELIVERY_TOTAL"] = Counter(
        "webhook_delivery_total", "Webhook delivery processing outcomes", ["status"]
    )
    m["WEBHOOK_POLL_ERRORS_TOTAL"] = Counter(
        "webhook_poll_errors_total", "Webhook outbox poll loop errors"
    )
    m["WEBHOOK_IDEMPOTENCY_REDIS_FALLBACK_TOTAL"] = Counter(
        "webhook_idempotency_redis_fallback_total",
        "Webhook idempotency checks degraded to DB-only (Redis unavailable)",
    )
    m["WEBHOOK_RATE_LIMITED_TOTAL"] = Counter(
        "webhook_rate_limited_total", "Webhook deliveries rejected by rate limit"
    )
    return m


# ── Module-level convenience accessors (backward compat) ──
# These are real module objects that existing importers reference.
# They delegate to real or no-op metrics at runtime.

RAG_RETRIEVAL_TOTAL = _LazyMetric("RAG_RETRIEVAL_TOTAL")
RAG_RETRIEVAL_HITS = _LazyMetric("RAG_RETRIEVAL_HITS")
RAG_RETRIEVAL_ERRORS = _LazyMetric("RAG_RETRIEVAL_ERRORS")
RAG_RETRIEVAL_UNAVAILABLE = _LazyMetric("RAG_RETRIEVAL_UNAVAILABLE")
RAG_RETRIEVAL_LATENCY = _LazyMetric("RAG_RETRIEVAL_LATENCY")
RAG_CACHE_HITS = _LazyMetric("RAG_CACHE_HITS")
RAG_CACHE_MISSES = _LazyMetric("RAG_CACHE_MISSES")
RAG_CACHE_EVICTIONS = _LazyMetric("RAG_CACHE_EVICTIONS")
LLM_REQUEST_TOTAL = _LazyMetric("LLM_REQUEST_TOTAL")
LLM_REQUEST_ERRORS = _LazyMetric("LLM_REQUEST_ERRORS")
LLM_TOKENS = _LazyMetric("LLM_TOKENS")
AGENT_TOKENS = _LazyMetric("AGENT_TOKENS")
LLM_LATENCY = _LazyMetric("LLM_LATENCY")
RAG_GROUNDED_TOTAL = _LazyMetric("RAG_GROUNDED_TOTAL")
RAG_GROUNDED_HITS = _LazyMetric("RAG_GROUNDED_HITS")
RAG_RERANK_LATENCY = _LazyMetric("RAG_RERANK_LATENCY")
RAG_RERANK_TOTAL = _LazyMetric("RAG_RERANK_TOTAL")
RAG_RELEVANCE_GATE_TOTAL = _LazyMetric("RAG_RELEVANCE_GATE_TOTAL")
RAG_QUERY_INTENT = _LazyMetric("RAG_QUERY_INTENT")
RAG_QUERY_REWRITE_TOTAL = _LazyMetric("RAG_QUERY_REWRITE_TOTAL")
RAG_ADAPTIVE_STRATEGY = _LazyMetric("RAG_ADAPTIVE_STRATEGY")
RAG_PIPELINE_IN_FLIGHT = _LazyMetric("RAG_PIPELINE_IN_FLIGHT")
RAG_EVAL_TOTAL = _LazyMetric("RAG_EVAL_TOTAL")
RAG_EVAL_FAITHFULNESS = _LazyMetric("RAG_EVAL_FAITHFULNESS")
RAG_EVAL_RELEVANCY = _LazyMetric("RAG_EVAL_RELEVANCY")
RAG_EVAL_CONTEXT_PRECISION = _LazyMetric("RAG_EVAL_CONTEXT_PRECISION")
AGENT_PLANNING_TOTAL = _LazyMetric("AGENT_PLANNING_TOTAL")
AGENT_PLANNING_LATENCY = _LazyMetric("AGENT_PLANNING_LATENCY")
AGENT_EXECUTION_STEPS = _LazyMetric("AGENT_EXECUTION_STEPS")
AGENT_TOOL_CALLS = _LazyMetric("AGENT_TOOL_CALLS")
AGENT_TOOL_LATENCY = _LazyMetric("AGENT_TOOL_LATENCY")
AGENT_REFLECTION_DECISIONS = _LazyMetric("AGENT_REFLECTION_DECISIONS")
AGENT_MEMORY_RECALLS = _LazyMetric("AGENT_MEMORY_RECALLS")
AGENT_FEEDBACK_TOTAL = _LazyMetric("AGENT_FEEDBACK_TOTAL")
AGENT_COMMAND_EXECUTIONS = _LazyMetric("AGENT_COMMAND_EXECUTIONS")
HOOK_EXECUTIONS = _LazyMetric("HOOK_EXECUTIONS")
HOOK_DURATION = _LazyMetric("HOOK_DURATION")
WEBHOOK_DELIVERY_TOTAL = _LazyMetric("WEBHOOK_DELIVERY_TOTAL")
WEBHOOK_POLL_ERRORS_TOTAL = _LazyMetric("WEBHOOK_POLL_ERRORS_TOTAL")
WEBHOOK_IDEMPOTENCY_REDIS_FALLBACK_TOTAL = _LazyMetric("WEBHOOK_IDEMPOTENCY_REDIS_FALLBACK_TOTAL")
WEBHOOK_RATE_LIMITED_TOTAL = _LazyMetric("WEBHOOK_RATE_LIMITED_TOTAL")


# issue #301：启用但构建失败时的自曝输出（注释行 + 一个 gauge 样本）。
# 选「自曝行」而非 fail-fast/500：/metrics 语义是抓取端点，http/ws 等既有
# 指标族仍可用，整端 500 会把可用指标一起淹没；exposition 格式对注释行与
# 未注册样本都合法（untyped），抓取端安全，告警端可直接对
# docmind_metrics_unavailable 建规则。
_METRICS_UNAVAILABLE_TEXT = (
    b"# docmind prometheus metric build failed - see server logs\n"
    b"# HELP docmind_metrics_unavailable Metrics pipeline unavailable (1 = prometheus metric build failed)\n"
    b"# TYPE docmind_metrics_unavailable gauge\n"
    b"docmind_metrics_unavailable 1\n"
)


def get_prometheus_metrics() -> bytes:
    """Generate latest metrics in Prometheus exposition format (if enabled).

    启用但构建失败时返回「不可用」自曝行（issue #301）；未启用（no-op 模式）
    仍返回空——那是声明过的关闭态，不是故障（#207）。
    """
    r = _get_real()
    if r is None:
        if _build_failure is not None:
            return _METRICS_UNAVAILABLE_TEXT
        return b""
    from prometheus_client import REGISTRY, generate_latest

    return generate_latest(REGISTRY)


def read_counter_samples(registry_key: str) -> list[tuple[dict[str, str], float]]:
    """读回指定计数器的全部标签样本（issue #286：消费端读回已埋指标）。

    registry_key 为 `_build_metrics()` 的注册键（如 `"AGENT_TOKENS"`），
    返回 `[(labels, value), ...]`（已过滤 `_created` 时间戳样本）。
    真实指标未启用（no-op 模式）或键不存在时返回 `[]` —— 调用方按 0/空处理，
    与写入侧 no-op 语义**对称**（写入即静默丢弃，读取即静默为空）。
    """
    r = _get_real()
    if r is None:
        return []
    metric = r.get(registry_key)
    if metric is None:
        return []
    out: list[tuple[dict[str, str], float]] = []
    for family in metric.collect():
        for sample in family.samples:
            if sample.name.endswith("_created"):
                continue
            out.append((dict(sample.labels), float(sample.value)))
    return out


def get_content_type() -> str:
    from prometheus_client import CONTENT_TYPE_LATEST

    return CONTENT_TYPE_LATEST
