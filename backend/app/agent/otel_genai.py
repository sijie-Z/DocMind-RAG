"""Existing LLM call facts → OpenTelemetry GenAI semantic attributes.

固定映射口径：**OTel GenAI semconv draft 2026-01-19**（``gen_ai.*`` 属性仍属
Development/Experimental，不随 opentelemetry-api 包版本自动漂移）。本模块只做
"已有记录 → 属性字典"的投影与可选 span 创建，不重写 JSONL / hierarchy reader /
prompt-version trace，也不构造 OTLP transport。

设计约束：

- 输入只接受既有 JSONL 记录中的**元数据**字段；任何其它键（尤其 prompt /
  completion / query / 原始异常文本）都不参与映射；
- 输出属性键取自静态白名单，值只用窄化的标量（字符串 / int / float）；
- ``error.type`` 是异常类名或固定错误类别，不是 ``record["error"]`` 原文；
- 只有 ``OTEL_GEN_AI_EXPORT_ENABLED=true`` 时才创建 span；默认完全 no-op；
- exporter / tracer 抛错一律吞掉，绝不阻断 LLM 调用主流程。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.core.log_safety import log_safe

logger = logging.getLogger(__name__)

# 固定 semconv 映射版本：属性名/语义以该快照为准，升级需显式改这里 + 文档 + 测试。
SEMCONV_VERSION = "genai-semconv-draft-2026-01-19"

# 允许进入 span 的属性全集。没有 prompt/completion/query/error.message 的容身之处。
ALLOWED_ATTRIBUTE_KEYS: frozenset[str] = frozenset(
    {
        "gen_ai.operation.name",
        "gen_ai.provider.name",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.response.tool_calls",
        "gen_ai.response.duration_ms",
        "error.type",
        "gen_ai.conversation.id",
        "gen_ai.request.id",
        "gen_ai.prompt.id",
        "gen_ai.prompt.version",
        "gen_ai.prompt.label",
        "gen_ai.experiment.id",
        "gen_ai.trace.span_id",
        "gen_ai.trace.parent_span_id",
        "gen_ai.trace.span_type",
        "gen_ai.trace.start_time",
        "gen_ai.trace.end_time",
        "gen_ai.semconv.version",
    }
)

# JSONL 元数据字段 → semconv 属性名的全量映射表（静态、可审计）。
# 注意 ``error`` 只用于判定失败/类名，绝不把原文映射为 error.message。
FIELD_TO_ATTRIBUTE: dict[str, str] = {
    "model": "gen_ai.response.model",
    "tokens_in": "gen_ai.usage.input_tokens",
    "tokens_out": "gen_ai.usage.output_tokens",
    "tool_calls": "gen_ai.response.tool_calls",
    "latency_ms": "gen_ai.response.duration_ms",
    "task_id": "gen_ai.conversation.id",
    "request_id": "gen_ai.request.id",
    "prompt_id": "gen_ai.prompt.id",
    "prompt_version": "gen_ai.prompt.version",
    "prompt_label": "gen_ai.prompt.label",
    "experiment_id": "gen_ai.experiment.id",
    "span_id": "gen_ai.trace.span_id",
    "parent_span_id": "gen_ai.trace.parent_span_id",
    "span_type": "gen_ai.trace.span_type",
    "start_time": "gen_ai.trace.start_time",
    "end_time": "gen_ai.trace.end_time",
}

# 只有能明确判定 provider 的模型名前缀才映射；其余一律 "unknown"，绝不把
# 配置中的模型名当身份声明（也不猜测 provider）。
_PROVIDER_PREFIXES: tuple[tuple[str, str], ...] = (
    ("deepseek", "deepseek"),
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("claude", "anthropic"),
    ("gemini", "google"),
    ("qwen", "alibaba"),
    ("llama", "meta"),
)
# 错误类别闭集：从错误文本中只取这些类别词，绝不回显自由文本。
_ERROR_CATEGORIES: tuple[str, ...] = (
    "timeout",
    "rate_limit",
    "auth",
    "connection",
    "validation",
)
_ERROR_CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "timeout": ("timeout", "timedout", "deadline"),
    "rate_limit": ("ratelimit", "rate limit", "429"),
    "auth": ("unauthorized", "authentication", "401", "403", "forbidden"),
    "connection": ("connectionerror", "connection error", "connectionrefused", "connecterror"),
    "validation": ("validationerror", "validation error", "invalidrequest"),
}
_ID_FIELDS = (
    "task_id",
    "request_id",
    "prompt_id",
    "prompt_version",
    "prompt_label",
    "experiment_id",
    "span_id",
    "parent_span_id",
    "span_type",
)
# 错误类名/类别/标识字段的字符集闸门：不符合即拒绝，不给自由文本留通道。
_SAFE_TOKEN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")


def _safe_text(value: Any, max_len: int = 64) -> str | None:
    """标识/版本字段的保守投影：拒绝容器、类型漂移和控制字符。"""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    text = log_safe(str(value), max_len=max_len)
    if not text or not _SAFE_TOKEN.fullmatch(text):
        return None
    return text


def _safe_number(value: Any) -> int | float | None:
    """token/latency 字段：只接受非 bool 的有限数；负数按 0 处理。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return max(0, value) if value < 0 else value


def _provider_for_model(model: str | None) -> str:
    """仅从已知前缀推断 provider；"unknown" 是显式缺省，不是身份声明。"""
    if not model:
        return "unknown"
    lowered = model.lower()
    for prefix, provider in _PROVIDER_PREFIXES:
        if lowered.startswith(prefix):
            return provider
    return "unknown"


def _error_type(error: Any) -> str | None:
    """从异常对象取类名 + 有限类别词；绝不返回异常原文。"""
    if error is None or error == "":
        return None
    if isinstance(error, str):
        name = None
        lowered = error.lower()
    else:
        name = _safe_text(type(error).__name__, max_len=64)
        lowered = ""
    for category in _ERROR_CATEGORIES:
        if any(hint in lowered for hint in _ERROR_CATEGORY_HINTS[category]):
            return category
    if isinstance(error, BaseException):
        mapped = {
            "TimeoutError": "timeout",
            "ConnectionError": "connection",
            "ValueError": "validation",
            "TypeError": "validation",
        }.get(type(error).__name__)
        if mapped:
            return mapped
    return name or "error"


def build_gen_ai_attributes(record: Mapping[str, Any]) -> dict[str, Any]:
    """把一条既有 LLM 调用记录投影到固定的 GenAI 属性白名单。

    纯函数：不修改输入，不读配置，不做 I/O。输入含正文键（如
    ``prompt`` / ``completion`` / ``query``）时直接忽略——白名单之外的键
    没有任何路径进入输出。时间戳字段只接受可解析的 ISO-8601 字符串。
    """
    if not isinstance(record, Mapping):
        return {}
    model = _safe_text(record.get("model"))
    out: dict[str, Any] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": _provider_for_model(model),
        "gen_ai.semconv.version": SEMCONV_VERSION,
    }

    if model is not None:
        out["gen_ai.request.model"] = model
        out["gen_ai.response.model"] = model

    for field in ("tokens_in", "tokens_out", "tool_calls", "latency_ms"):
        value = _safe_number(record.get(field))
        if value is not None:
            out[FIELD_TO_ATTRIBUTE[field]] = value

    for field in _ID_FIELDS:
        value = _safe_text(record.get(field))
        if value is not None:
            out[FIELD_TO_ATTRIBUTE[field]] = value

    for field in ("start_time", "end_time"):
        value = record.get(field)
        if isinstance(value, str) and value:
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                continue
            out[FIELD_TO_ATTRIBUTE[field]] = parsed.astimezone(UTC).isoformat(
                timespec="milliseconds"
            )

    error_type = _error_type(record.get("error"))
    if error_type is not None:
        out["error.type"] = error_type

    # 结构性自检：输出键必须恰为静态白名单的子集；异常即拒绝导出。
    if not set(out).issubset(ALLOWED_ATTRIBUTE_KEYS):
        logger.warning("OTel GenAI attribute allowlist violation; skipping export")
        return {}
    return out


def export_gen_ai_span(record: Mapping[str, Any]) -> bool:
    """可选导出一条 GenAI span。成功 True；关闭/缺失/失败一律 False。

    使用当前全局 ``TracerProvider``（由既有 ``app.core.tracing`` 配置）——本
    模块不创建 provider / exporter。任何异常都在内部吞掉，调用方（LLM 主链）
    永远不受影响。
    """
    from app.core.config import settings

    if not bool(getattr(settings, "OTEL_GEN_AI_EXPORT_ENABLED", False)):
        return False
    try:
        from opentelemetry import trace

        attributes = build_gen_ai_attributes(record)
        if not attributes:
            return False
        with trace.get_tracer("docmind.genai", "0.1.0").start_as_current_span(
            "gen_ai.chat", attributes=attributes
        ):
            pass
        return True
    except Exception as exc:  # noqa: BLE001 — 观测导出绝不影响 LLM 调用
        logger.debug("OTel GenAI span export skipped (%s)", type(exc).__name__)
        return False
