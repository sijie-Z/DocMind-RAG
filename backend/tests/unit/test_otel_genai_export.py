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
    SEMCONV_VERSION,
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
    """生产默认必须关闭；monkeypatch 掉全局 tracer 确保 no-op 不触碰 OTel。"""
    from app.core.config.base import BaseAppSettings

    assert BaseAppSettings.model_fields["OTEL_GEN_AI_EXPORT_ENABLED"].default is False

    def _boom():  # pragma: no cover - 一旦被调用就证明默认开关失效
        raise AssertionError("disabled export must not touch tracer")

    import opentelemetry.trace as otel_trace

    monkeypatch.setattr(otel_trace, "get_tracer", _boom)
    assert export_gen_ai_span(_record()) is False


def test_mapping_is_pure_and_independent_of_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """纯函数映射不受开关影响（便于审计），但导出受开关控制。"""
    monkeypatch.setattr(settings, "OTEL_GEN_AI_EXPORT_ENABLED", False)
    attrs = build_gen_ai_attributes(_record())
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.semconv.version"] == SEMCONV_VERSION


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
        ("claude-3-5-sonnet", "anthropic"),
        ("gemini-2.5-pro", "google"),
        ("qwen3-8b", "alibaba"),
        ("llama3", "meta"),
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

    record = _record(prompt_id="42", prompt_version="7", experiment_id="exp-A")
    assert export_gen_ai_span(record) is True

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "gen_ai.chat"
    assert dict(spans[0].attributes) == build_gen_ai_attributes(record)


def test_exporter_failure_never_breaks_llm_call_path(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tracer 抛错必须吞掉；JSONL 仍成功写入且键集不变。"""
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
