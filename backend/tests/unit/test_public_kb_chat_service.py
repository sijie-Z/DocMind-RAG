"""公开聊天纯函数（批次十七）：配置默认值、预算口径、输入校验。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.core.config.base import BaseAppSettings
from app.exceptions import PublicKbChatBudgetError, ValidationError
from app.services import public_kb_service as service


def test_public_chat_config_defaults_are_closed_and_bounded():
    fields = BaseAppSettings.model_fields
    assert fields["PUBLIC_KB_CHAT_ENABLED"].default is False
    assert fields["PUBLIC_KB_CHAT_DAILY_TOKENS_DEFAULT"].default == 50_000
    assert fields["PUBLIC_KB_CHAT_RATE_LIMIT_PER_MINUTE"].default == 10
    assert fields["PUBLIC_KB_CHAT_MAX_HISTORY"].default == 6
    assert fields["PUBLIC_KB_CHAT_MAX_QUERY_CHARS"].default == 200


def test_resolve_chat_budget_null_uses_default_and_zero_means_deny(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_KB_CHAT_DAILY_TOKENS_DEFAULT", 1234)
    assert service.resolve_chat_budget(SimpleNamespace(daily_token_budget=None)) == 1234
    assert service.resolve_chat_budget(SimpleNamespace(daily_token_budget=0)) == 0
    assert service.resolve_chat_budget(SimpleNamespace(daily_token_budget=99)) == 99
    # 运行期值漂移不得意外放开：bool/负数/非整数按 0（禁止）。
    assert service.resolve_chat_budget(SimpleNamespace(daily_token_budget=True)) == 0
    assert service.resolve_chat_budget(SimpleNamespace(daily_token_budget=-5)) == 0
    assert service.resolve_chat_budget(SimpleNamespace(daily_token_budget="500")) == 0


def test_chat_available_requires_global_and_publication_switches(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_KB_CHAT_DAILY_TOKENS_DEFAULT", 100)
    publication = SimpleNamespace(chat_enabled=True, daily_token_budget=None)
    monkeypatch.setattr(settings, "PUBLIC_KB_CHAT_ENABLED", False)
    assert service.chat_available(publication) is False
    monkeypatch.setattr(settings, "PUBLIC_KB_CHAT_ENABLED", True)
    assert service.chat_available(publication) is True
    assert (
        service.chat_available(SimpleNamespace(chat_enabled=False, daily_token_budget=None))
        is False
    )
    # 预算 0 等价禁止聊天：不能投影为可用（否则 UI 每次提问都 429）。
    assert service.chat_available(SimpleNamespace(chat_enabled=True, daily_token_budget=0)) is False
    monkeypatch.setattr(settings, "PUBLIC_KB_CHAT_DAILY_TOKENS_DEFAULT", 0)
    assert (
        service.chat_available(SimpleNamespace(chat_enabled=True, daily_token_budget=None)) is False
    )


def test_validate_chat_message_bounds(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_KB_CHAT_MAX_QUERY_CHARS", 5)
    assert service.validate_chat_message("  制度  ") == "制度"
    for bad in ("", "   ", "x" * 6):
        with pytest.raises(ValidationError):
            service.validate_chat_message(bad)
    with pytest.raises(ValidationError):
        service.validate_chat_message(123)  # type: ignore[arg-type]


def test_normalize_chat_history_bounds(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_KB_CHAT_MAX_HISTORY", 2)
    assert service.normalize_chat_history(None) == []
    assert service.normalize_chat_history(
        [{"role": "user", "content": " 你好 "}, {"role": "assistant", "content": "好的"}]
    ) == [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "好的"}]
    for bad in (
        [{"role": "user", "content": "a"}] * 3,
        [{"role": "system", "content": "x"}],
        [{"role": "user", "content": "x" * (service.CHAT_HISTORY_CONTENT_MAX_CHARS + 1)}],
        [{"role": "user", "content": "   "}],
        [{"role": "user"}],
        "not-a-list",
    ):
        with pytest.raises(ValidationError):
            service.normalize_chat_history(bad)


def test_utc_budget_window_helpers_are_midnight_utc():
    now = datetime(2026, 11, 28, 23, 59, 59, tzinfo=UTC)
    assert service._utc_day_start(now) == datetime(2026, 11, 28, tzinfo=UTC)
    assert service._utc_next_midnight(now) == datetime(2026, 11, 29, tzinfo=UTC)


def test_public_metadata_projects_only_effective_chat_switch(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_KB_CHAT_ENABLED", True)
    publication = SimpleNamespace(
        title="标题",
        description=None,
        expires_at=None,
        chat_enabled=True,
        daily_token_budget=123,
        token_hash="secret",
        organization_id=7,
        knowledge_base_id="kb",
    )
    data = service.public_metadata(publication)
    assert set(data) == {"title", "description", "expires_at", "chat_enabled"}
    assert data["chat_enabled"] is True
    assert "daily_token_budget" not in data
    assert "organization_id" not in data


def test_budget_error_carries_used_budget_reset():
    exc = PublicKbChatBudgetError(detail={"used": 5, "budget": 5, "reset_at": "x"})
    assert exc.status_code == 429
    assert exc.error_code == "PUBLIC_KB_CHAT_BUDGET_EXCEEDED"
    assert exc.detail["used"] == 5


def test_mask_chat_history_removes_pii_with_shared_mapping(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_PII_MASKING", True)
    history = [
        {"role": "user", "content": "我的手机号是 13800138000"},
        {"role": "assistant", "content": "邮箱 test@example.com 已记录"},
    ]
    masked, mapping = service.mask_chat_history(history)
    rendered = " ".join(item["content"] for item in masked)
    assert "13800138000" not in rendered
    assert "test@example.com" not in rendered
    assert mapping
    assert any(placeholder.startswith("[PHONE_") for placeholder in mapping)
    # 占位符编号从历史专属偏移开始，避开 pipeline 的 context/query 编号。
    assert all(
        int(placeholder.rsplit("_", 1)[1].rstrip("]")) >= service.CHAT_HISTORY_MASK_OFFSET
        for placeholder in mapping
    )
    # 原始 history 不被就地改写。
    assert history[0]["content"].endswith("13800138000")


def test_mask_chat_history_is_noop_when_masking_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_PII_MASKING", False)
    history = [{"role": "user", "content": "13800138000"}]
    masked, mapping = service.mask_chat_history(history)
    assert masked == history
    assert mapping == {}


def test_public_chat_feature_key_fits_token_usage_column():
    """feature='public_kb_chat:<uuid>' 必须能完整写入 VARCHAR 列（51<=64）。"""
    from app.models.token_usage import TokenUsageRecord

    feature = f"{service.CHAT_FEATURE_PREFIX}{'0' * 36}"
    length = TokenUsageRecord.__table__.c.feature.type.length
    assert length is not None
    assert len(feature) <= length, (len(feature), length)
    assert len(feature) == 51


class _SinkRag:
    """模拟真实 pipeline：流开始时先 clear() 再写入 context/query 掩码。"""

    openai_client = object()

    async def chat_stream(
        self,
        query,
        context,
        history=None,
        system_prompt_override=None,
        enable_masking=True,
        mask_mapping_sink=None,
        model_override=None,
        client_override=None,
        raise_user_errors=False,
    ):
        if mask_mapping_sink is not None:
            mask_mapping_sink.clear()
            mask_mapping_sink["[EMAIL_1]"] = "ctx@example.com"
        yield "[EMAIL_1] 与 [PHONE_5001]"

    def get_last_token_usage(self):
        return (1, 1)


async def test_extra_history_mask_mapping_survives_pipeline_sink_reset(monkeypatch):
    """pipeline 的 clear() 不得把调用方 history 的掩码映射一起清掉。"""
    from app.services import openai_compat_service as compat

    monkeypatch.setattr(compat, "rag_service", _SinkRag())
    extra = {"[PHONE_5001]": "13800138000"}

    chunks = [
        chunk
        async for chunk in compat.iter_completion_chunks(
            query="q",
            context_results=[],
            history=[],
            system_prompt=None,
            model="m",
            enable_masking=True,
            extra_mask_mapping=extra,
        )
    ]
    assert "".join(chunks) == "ctx@example.com 与 13800138000"

    answer = await compat.collect_completion(
        query="q",
        context_results=[],
        history=[],
        system_prompt=None,
        model="m",
        enable_masking=True,
        extra_mask_mapping=extra,
    )
    assert answer == "ctx@example.com 与 13800138000"
