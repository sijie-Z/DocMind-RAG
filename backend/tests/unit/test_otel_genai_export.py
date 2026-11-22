"""OTel GenAI 语义属性映射的判别性验收（固定 semconv 映射版本）。

变异点：

1. 默认开关改成 True → `test_export_disabled_by_default` 红；
2. 属性映射放宽白名单/允许正文 → `test_allowlist_never_exposes_bodies_or_secrets` 红；
3. provider / model 映射写错 → provider/model 用例红；
4. token / error 字段漏映射或回显原文 → token/error 用例红；
5. JSONL 键集变化或导出异常穿透 → 契约/容错用例红；
6. 配置默认值/校验被改 → 配置用例红。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.agent.llm_call_log import append_llm_call_record, build_llm_call_record
from app.agent.otel_genai import (
    ALLOWED_ATTRIBUTE_KEYS,
    build_gen_ai_attributes,
    export_gen_ai_span,
)
from app.core.config import settings

SECRET_PROMPT = "PROMPT_BODY_NEVER_EXPORTED"
SECRET_COMPLETION = "COMPLETION_BODY_NEVER_EXPORTED"
SECRET_QUERY = "用户合同正文 SECRET_QUERY"
LEGACY_FIELDS = {
    "ts",
    "task_id",
    "request_id",
    "model",
    "tokens_in",
    "tokens_out",
    "latency_ms",
    "prompt_hash",
    "tool_calls",
    "error",
}


def _record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "ts": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC).isoformat(timespec="milliseconds"),
        "task_id": "task-7",
        "request_id": "req-7",
        "model": "deepseek-chat",
        "tokens_in": 120,
        "tokens_out": 30,
        "latency_ms": 842.5,
        "prompt_hash": "sha256:abc123",
        "tool_calls": 2,
        "error": None,
    }
    record.update(overrides)
    return record


# ── 1. 默认关闭 ───────────────────────────────────────────────────────────


def test_export_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """生产默认必须关闭：用计数器断言 get_tracer 从未被调用（异常会被吞掉，不能用抛错做哨兵）。"""
    import opentelemetry.trace as otel_trace

    from app.core.config.base import BaseAppSettings

    assert BaseAppSettings.model_fields["OTEL_GEN_AI_EXPORT_ENABLED"].default is False

    calls = 0

    def _count_get_tracer(*args, **kwargs):  # pragma: no cover - 一旦被调用即证明开关失效
        nonlocal calls
        calls += 1
        raise AssertionError("disabled export must not call get_tracer")

    monkeypatch.setattr(otel_trace, "get_tracer", _count_get_tracer)
    assert export_gen_ai_span(_record()) is False
    assert calls == 0, "默认关闭时不得触碰 OTel tracer"


def test_mapping_is_pure_and_independent_of_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """纯函数映射不受开关影响（便于审计），但导出受开关控制。"""
    monkeypatch.setattr(settings, "OTEL_GEN_AI_EXPORT_ENABLED", False)
    attrs = build_gen_ai_attributes(_record())
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.semconv.version"] == "genai-semconv-draft-2026-01-19"


# ── 2. 白名单 / 无正文泄漏 ───────────────────────────────────────────────


def test_allowlist_never_exposes_bodies_or_secrets() -> None:
    record = _record(
        prompt=SECRET_PROMPT,
        completion=SECRET_COMPLETION,
        query=SECRET_QUERY,
        messages=[{"role": "user", "content": SECRET_PROMPT}],
        raw_error=f"failed with {SECRET_PROMPT}",
        api_key="sk-12345678901234567890",
        error="RateLimitError: too many requests",
    )
    attrs = build_gen_ai_attributes(record)
    assert set(attrs) <= ALLOWED_ATTRIBUTE_KEYS
    serialized = repr(attrs)
    for secret in (SECRET_PROMPT, SECRET_COMPLETION, SECRET_QUERY, "sk-12345678901234567890"):
        assert secret not in serialized
    assert "error.message" not in attrs
    assert attrs["error.type"] == "rate_limit"


def test_free_text_identifier_values_are_rejected() -> None:
    """标识字段有字符集闸门：含空格/换行的自由文本不得成为属性。"""
    attrs = build_gen_ai_attributes(
        _record(
            task_id=f"run {SECRET_QUERY}", request_id="req\nwith-newline", prompt_label="生产 环境"
        )
    )
    assert "gen_ai.conversation.id" not in attrs
    assert "gen_ai.request.id" not in attrs
    assert "gen_ai.prompt.label" not in attrs
    assert SECRET_QUERY not in repr(attrs)


def test_prompt_hash_is_not_exported_as_body() -> None:
    """摘要是元数据，但映射到 prompt.id 会混淆语义；当前白名单不含原始 hash。"""
    attrs = build_gen_ai_attributes(_record(prompt_hash="sha256:abc123"))
    assert attrs.get("gen_ai.prompt.id") is None


# ── 3. provider / model 映射 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("model", "provider"),
    [
        ("deepseek-chat", "deepseek"),
        ("gpt-4o-mini", "openai"),
        ("o1", "openai"),
        ("o3-mini", "openai"),
        ("o4", "openai"),
        ("claude-3-5-sonnet", "anthropic"),
        ("gemini-2.5-pro", "google"),
        ("qwen3-8b", "alibaba"),
        ("llama3", "meta"),
        ("o1-private-router", "unknown"),
        ("o3-our-internal-alias", "unknown"),
        ("our-private-router-alias", "unknown"),
        ("", "unknown"),
    ],
)
def test_provider_mapping_is_conservative(model: str, provider: str) -> None:
    attrs = build_gen_ai_attributes(_record(model=model))
    assert attrs["gen_ai.provider.name"] == provider


def test_request_and_response_model_both_mapped_from_existing_record() -> None:
    attrs = build_gen_ai_attributes(_record(model="deepseek-chat"))
    assert attrs["gen_ai.request.model"] == "deepseek-chat"
    assert attrs["gen_ai.response.model"] == "deepseek-chat"


# ── 4. token / usage / error 字段 ────────────────────────────────────────


def test_token_usage_and_latency_map_to_semconv() -> None:
    attrs = build_gen_ai_attributes(
        _record(tokens_in=120, tokens_out=30, tool_calls=2, latency_ms=842.5)
    )
    assert attrs["gen_ai.usage.input_tokens"] == 120
    assert attrs["gen_ai.usage.output_tokens"] == 30
    assert attrs["gen_ai.response.tool_calls"] == 2
    assert attrs["gen_ai.response.duration_ms"] == 842.5


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, None),
        ("RateLimitError: 429 too many requests", "rate_limit"),
        ("ReadTimeout: deadline exceeded", "timeout"),
        ("ConnectionError: refused", "connection"),
        ("ValidationError: bad input", "validation"),
        (RuntimeError("private details in message"), "RuntimeError"),
        (TimeoutError(), "timeout"),
    ],
)
def test_error_type_is_closed_set_not_raw_text(error: Any, expected: str | None) -> None:
    attrs = build_gen_ai_attributes(_record(error=error))
    assert attrs.get("error.type") == expected
    assert "private details" not in repr(attrs)


def test_bad_number_types_do_not_pollute_attributes() -> None:
    attrs = build_gen_ai_attributes(
        _record(tokens_in="not-a-number", tokens_out=True, latency_ms=float("nan"))
    )
    assert "gen_ai.usage.input_tokens" not in attrs
    assert "gen_ai.usage.output_tokens" not in attrs
    assert "gen_ai.response.duration_ms" not in attrs


# ── 5. span 导出 + JSONL 契约 + 容错 ─────────────────────────────────────


def test_span_attributes_come_from_existing_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """用 SDK InMemorySpanExporter 验证真实 span 属性（非只测 mapping）。"""
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(otel_trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(settings, "OTEL_GEN_AI_EXPORT_ENABLED", True)

    record = _record(
        prompt_id="42",
        prompt_version="7",
        experiment_id="exp-A",
        span_id="span-7",
        parent_span_id="span-6",
        span_type="llm",
        start_time="2026-01-02T03:04:05.000+00:00",
        end_time="2026-01-02T03:04:05.842+00:00",
        error="provider returned invalid status ZX81",
    )
    assert export_gen_ai_span(record) is True

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "gen_ai.chat"
    attrs = dict(spans[0].attributes)
    # 固定断言：不得用 build_gen_ai_attributes(record) 自己验自己。
    assert attrs["gen_ai.semconv.version"] == "genai-semconv-draft-2026-01-19"
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.provider.name"] == "deepseek"
    assert attrs["gen_ai.request.model"] == "deepseek-chat"
    assert attrs["gen_ai.response.model"] == "deepseek-chat"
    assert attrs["gen_ai.usage.input_tokens"] == 120
    assert attrs["gen_ai.usage.output_tokens"] == 30
    assert attrs["gen_ai.response.tool_calls"] == 2
    assert attrs["gen_ai.response.duration_ms"] == 842.5
    assert attrs["gen_ai.conversation.id"] == "task-7"
    assert attrs["gen_ai.request.id"] == "req-7"
    assert attrs["gen_ai.prompt.id"] == "42"
    assert attrs["gen_ai.prompt.version"] == "7"
    assert attrs["gen_ai.experiment.id"] == "exp-A"
    # 层级 trace 投影（含时间归一化到 UTC ISO-8601 毫秒）。
    assert attrs["gen_ai.trace.span_id"] == "span-7"
    assert attrs["gen_ai.trace.parent_span_id"] == "span-6"
    assert attrs["gen_ai.trace.span_type"] == "llm"
    assert attrs["gen_ai.trace.start_time"] == "2026-01-02T03:04:05.000+00:00"
    assert attrs["gen_ai.trace.end_time"] == "2026-01-02T03:04:05.842+00:00"
    # 字符串错误且不含闭集类别词 → 明确契约 collapse 为 "error"，绝不回显原文。
    assert attrs["error.type"] == "error"
    assert "ZX81" not in repr(attrs)
    assert set(attrs) <= ALLOWED_ATTRIBUTE_KEYS


def test_exporter_failure_is_swallowed_by_export_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """直接调用导出函数注入 tracer 失败：必须返回 False 且不抛异常。

    变异点：删除 export_gen_ai_span 内部 try/except → 本用例红（此前只经
    append_llm_call_record 外层兜底，删掉内部保护仍绿）。
    """
    import opentelemetry.trace as otel_trace

    def _boom(*args, **kwargs):
        raise RuntimeError("exporter down")

    monkeypatch.setattr(otel_trace, "get_tracer", _boom)
    monkeypatch.setattr(settings, "OTEL_GEN_AI_EXPORT_ENABLED", True)

    assert export_gen_ai_span(_record()) is False


def test_exporter_failure_never_breaks_llm_call_path(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """导出异常不得影响 JSONL：仍成功写入且 10 字段键集不变。"""
    import opentelemetry.trace as otel_trace

    def _boom(*args, **kwargs):
        raise RuntimeError("exporter down")

    monkeypatch.setattr(otel_trace, "get_tracer", _boom)
    monkeypatch.setattr(settings, "OTEL_GEN_AI_EXPORT_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CALL_LOG_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CALL_LOG_FILE", str(tmp_path / "calls.jsonl"))
    monkeypatch.setattr(settings, "LLM_CALL_LOG_PER_RUN", False)

    record = build_llm_call_record(task_id="t", request_id="r", model="deepseek-chat")
    assert append_llm_call_record(record) is True
    line = (tmp_path / "calls.jsonl").read_text(encoding="utf-8").strip()
    import json

    assert set(json.loads(line)) == LEGACY_FIELDS


def test_export_works_when_jsonl_is_disabled(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OTel 开关独立：关 JSONL、开 GenAI 时仍导出，且不创建 JSONL 文件。"""
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(otel_trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(settings, "OTEL_GEN_AI_EXPORT_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CALL_LOG_ENABLED", False)
    monkeypatch.setattr(settings, "LLM_CALL_LOG_FILE", str(tmp_path / "calls.jsonl"))

    record = build_llm_call_record(task_id="t", request_id="r", model="deepseek-chat")
    assert append_llm_call_record(record) is False, "返回值只表示 JSONL 是否写入"
    assert not (tmp_path / "calls.jsonl").exists()
    assert len(exporter.get_finished_spans()) == 1


def test_jsonl_key_set_unchanged_when_export_enabled(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
    monkeypatch.setattr(otel_trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(settings, "OTEL_GEN_AI_EXPORT_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CALL_LOG_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CALL_LOG_FILE", str(tmp_path / "calls.jsonl"))
    monkeypatch.setattr(settings, "LLM_CALL_LOG_PER_RUN", False)

    record = build_llm_call_record(
        task_id="t", request_id="r", model="deepseek-chat", tokens_in=10, tokens_out=5
    )
    assert append_llm_call_record(record) is True
    written = json.loads((tmp_path / "calls.jsonl").read_text(encoding="utf-8").strip())
    assert set(written) == LEGACY_FIELDS, "OTel 导出不得改变既有 10 字段 JSONL 键集"
    assert written["tokens_in"] == 10
    assert written["tokens_out"] == 5


# ── 6. 配置校验 ───────────────────────────────────────────────────────────


def test_config_accepts_bool_switch_and_rejects_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    from app.core.config.base import BaseAppSettings

    monkeypatch.setenv("OTEL_GEN_AI_EXPORT_ENABLED", "true")
    assert BaseAppSettings().OTEL_GEN_AI_EXPORT_ENABLED is True
    monkeypatch.setenv("OTEL_GEN_AI_EXPORT_ENABLED", "definitely-not-a-bool")
    with pytest.raises(ValidationError):
        BaseAppSettings()
