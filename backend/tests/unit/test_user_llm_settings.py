"""个人 AI 模型设置（provider/model/api_key/base_url）真实生效链路测试。

覆盖：
- API key 加密落库（Fernet，SECRET_KEY 派生）、兼容历史明文、掩码；
- GET/PUT /user/settings/providers：掩码回传、掩码回写不覆盖真实 key、
  「使用服务端默认」清除个人配置、缺 key 校验、响应不含明文；
- resolve_for_user 优先级：有效个人配置 > 服务端默认；无效/缺 key 回退；
- 消费点接线：RAG pipeline 的 client_override/model_override、
  run_rag_pipeline 的 llm_client/llm_model 透传、AgentService 使用 per-user client。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  # 注册全部表供 create_all
from app.api.v1.endpoints import user_settings as user_settings_ep
from app.core.config import settings
from app.core.database import Base
from app.models.organization import Organization
from app.models.user_settings import UserSettings
from app.schemas.user_settings import ProviderSettingsUpdate, UserSettingsUpdate
from app.services import llm_settings_service
from app.services.llm_settings_service import (
    LLMConfigError,
    decrypt_api_key,
    encrypt_api_key,
    mask_api_key,
    read_api_key,
    resolve_for_user,
)

SECRET = "user-secret-1234567890"
USER = SimpleNamespace(id=7)


@pytest.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_client_cache():
    llm_settings_service.clear_client_cache()
    yield
    llm_settings_service.clear_client_cache()


async def _save_user_config(db, **overrides):
    config = {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "api_key_encrypted": encrypt_api_key(SECRET),
        "config_id": "cfg-test-default",
    }
    config.update(overrides)
    db.add(UserSettings(user_id=USER.id, preferences={"ai_provider": config}))
    await db.commit()


# ── 加密 / 掩码 ─────────────────────────────────────────────────────────────


class TestKeyCrypto:
    def test_encrypt_roundtrip_and_no_plaintext(self):
        token = encrypt_api_key(SECRET)
        assert token.startswith("fernet:v1:")
        assert SECRET not in token
        assert decrypt_api_key(token) == SECRET

    def test_legacy_plaintext_still_read(self):
        assert read_api_key({"api_key": SECRET}) == SECRET

    def test_encrypted_field_wins_over_legacy(self):
        token = encrypt_api_key("new-value-abcdefgh")
        assert (
            read_api_key({"api_key_encrypted": token, "api_key": "old-value"})
            == "new-value-abcdefgh"
        )

    def test_mask(self):
        assert mask_api_key("") == ""
        assert mask_api_key("short") == "********"
        assert mask_api_key(SECRET) == "user...7890"

    def test_undecryptable_ciphertext_raises_config_error(self):
        with pytest.raises(LLMConfigError):
            decrypt_api_key("fernet:v1:not-a-valid-token")


# ── 校验 ────────────────────────────────────────────────────────────────────


class TestValidation:
    def test_rejects_unknown_provider(self):
        with pytest.raises(LLMConfigError):
            llm_settings_service.validate_provider_config("nope", "m", "")

    def test_rejects_empty_model(self):
        with pytest.raises(LLMConfigError):
            llm_settings_service.validate_provider_config("deepseek", "  ", "")

    def test_rejects_bad_base_url_scheme(self):
        with pytest.raises(LLMConfigError):
            llm_settings_service.validate_provider_config("custom", "m", "ftp://example.com")

    def test_custom_requires_base_url(self):
        with pytest.raises(LLMConfigError):
            llm_settings_service.validate_provider_config("custom", "m", "")

    def test_rejects_metadata_host(self):
        with pytest.raises(LLMConfigError):
            llm_settings_service.validate_provider_config(
                "custom", "m", "http://169.254.169.254/v1"
            )

    def test_accepts_known_provider_default(self):
        provider, model, base = llm_settings_service.validate_provider_config(
            "deepseek", "deepseek-chat", ""
        )
        assert (provider, model, base) == ("deepseek", "deepseek-chat", "")


# ── resolve_for_user 优先级 ─────────────────────────────────────────────────


class TestResolve:
    async def test_no_config_falls_back_to_server(self, db):
        resolution = await resolve_for_user(db, USER)
        assert resolution.source == "server"
        assert resolution.model == llm_settings_service.server_default_model()

    async def test_user_config_wins(self, db):
        await _save_user_config(db)
        resolution = await resolve_for_user(db, USER)
        assert resolution.source == "user"
        assert resolution.provider == "deepseek"
        assert resolution.model == "deepseek-chat"
        assert resolution.client.api_key == SECRET
        assert resolution.has_api_key is True
        # 打标是 Agent 失败归因的前提：per-user 客户端必须被识别。
        from app.core.llm_failures import is_user_llm_client

        assert is_user_llm_client(resolution.client) is True

    async def test_legacy_plaintext_key_supported(self, db):
        db.add(
            UserSettings(
                user_id=USER.id,
                preferences={
                    "ai_provider": {
                        "provider": "deepseek",
                        "model": "deepseek-chat",
                        "api_key": SECRET,
                    }
                },
            )
        )
        await db.commit()
        resolution = await resolve_for_user(db, USER)
        assert resolution.source == "user"
        assert resolution.client.api_key == SECRET

    async def test_missing_key_falls_back_for_keyed_provider(self, db):
        db.add(
            UserSettings(
                user_id=USER.id,
                preferences={"ai_provider": {"provider": "deepseek", "model": "deepseek-chat"}},
            )
        )
        await db.commit()
        resolution = await resolve_for_user(db, USER)
        assert resolution.source == "server"
        from app.core.llm_failures import is_user_llm_client

        assert is_user_llm_client(resolution.client) is False, "服务端默认客户端不得被打标"

    async def test_ollama_without_key_is_valid(self, db):
        db.add(
            UserSettings(
                user_id=USER.id,
                preferences={"ai_provider": {"provider": "ollama", "model": "llama3"}},
            )
        )
        await db.commit()
        resolution = await resolve_for_user(db, USER)
        assert resolution.source == "user"
        assert resolution.client.api_key == "ollama"
        assert resolution.base_url == "http://localhost:11434/v1"

    async def test_key_change_invalidates_cached_client(self, db):
        await _save_user_config(db)
        first = await resolve_for_user(db, USER)
        first_client = first.client

        row = (
            await db.execute(select(UserSettings).where(UserSettings.user_id == USER.id))
        ).scalar_one()
        row.preferences = {
            "ai_provider": {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "api_key_encrypted": encrypt_api_key("rotated-value-0987654321"),
            }
        }
        await db.commit()

        second = await resolve_for_user(db, USER)
        assert second.source == "user"
        assert second.client is not first_client
        assert second.client.api_key == "rotated-value-0987654321"

    async def test_same_config_reuses_cached_client(self, db):
        await _save_user_config(db)
        first = await resolve_for_user(db, USER)
        second = await resolve_for_user(db, USER)
        assert first.client is second.client


# ── 端点（GET 掩码 / PUT 保存 / 掩码回写 / 服务端默认）───────────────────────


class TestProviderApi:
    async def test_get_masks_legacy_plaintext(self, db):
        db.add(
            UserSettings(
                user_id=USER.id,
                preferences={
                    "ai_provider": {
                        "provider": "deepseek",
                        "model": "deepseek-chat",
                        "api_key": SECRET,
                    }
                },
            )
        )
        await db.commit()
        payload = await user_settings_ep.get_providers(current_user=USER, db=db)
        current = payload["current"]
        assert current["source"] == "user"
        assert current["has_api_key"] is True
        assert SECRET not in str(payload)
        assert current["api_key"] == "user...7890"

    async def test_get_without_config_reports_server(self, db):
        payload = await user_settings_ep.get_providers(current_user=USER, db=db)
        assert payload["current"]["source"] == "server"
        assert payload["current"]["api_key"] == ""

    async def test_put_saves_encrypted_and_never_returns_plaintext(self, db):
        body = ProviderSettingsUpdate(provider="deepseek", model="deepseek-chat", api_key=SECRET)
        payload = await user_settings_ep.update_providers(body=body, current_user=USER, db=db)
        assert payload["current"]["source"] == "user"
        assert SECRET not in str(payload)

        row = (
            await db.execute(select(UserSettings).where(UserSettings.user_id == USER.id))
        ).scalar_one()
        stored = row.preferences["ai_provider"]
        assert "api_key" not in stored, "明文 key 不得落库"
        assert stored["api_key_encrypted"].startswith("fernet:v1:")
        assert SECRET not in str(stored)
        assert decrypt_api_key(stored["api_key_encrypted"]) == SECRET

    async def test_masked_writeback_keeps_existing_key(self, db):
        await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(provider="deepseek", model="deepseek-chat", api_key=SECRET),
            current_user=USER,
            db=db,
        )
        # 前端回读的是掩码，保存时原样回传 → 后端必须保留真实 key
        masked = mask_api_key(SECRET)
        await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(
                provider="deepseek", model="deepseek-reasoner", api_key=masked
            ),
            current_user=USER,
            db=db,
        )
        resolution = await resolve_for_user(db, USER)
        assert resolution.source == "user"
        assert resolution.model == "deepseek-reasoner"
        assert resolution.client.api_key == SECRET

    async def test_empty_key_keeps_existing_key(self, db):
        await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(provider="deepseek", model="deepseek-chat", api_key=SECRET),
            current_user=USER,
            db=db,
        )
        await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(provider="deepseek", model="deepseek-chat"),
            current_user=USER,
            db=db,
        )
        resolution = await resolve_for_user(db, USER)
        assert resolution.client.api_key == SECRET

    async def test_provider_switch_without_new_key_rejected(self, db):
        """切换 provider 时掩码/空 key 不得复用旧 provider 的 key。"""
        from fastapi import HTTPException

        await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(provider="deepseek", model="deepseek-chat", api_key=SECRET),
            current_user=USER,
            db=db,
        )
        with pytest.raises(HTTPException) as exc_info:
            await user_settings_ep.update_providers(
                body=ProviderSettingsUpdate(
                    provider="openai", model="gpt-4o", api_key=mask_api_key(SECRET)
                ),
                current_user=USER,
                db=db,
            )
        assert exc_info.value.status_code == 400
        assert "重新填写" in str(exc_info.value.detail)
        resolution = await resolve_for_user(db, USER)
        assert resolution.provider == "deepseek", "拒绝后不得改动原配置"

    async def test_provider_switch_with_new_key_succeeds(self, db):
        await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(provider="deepseek", model="deepseek-chat", api_key=SECRET),
            current_user=USER,
            db=db,
        )
        await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(
                provider="openai", model="gpt-4o", api_key="openai-value-12345678"
            ),
            current_user=USER,
            db=db,
        )
        resolution = await resolve_for_user(db, USER)
        assert resolution.source == "user"
        assert resolution.provider == "openai"
        assert resolution.model == "gpt-4o"
        assert resolution.client.api_key == "openai-value-12345678"

    async def test_client_cache_rotates_when_key_changes(self, db):
        """保存新 key 会生成新 config_id，客户端缓存必须失效（不得复用旧 client）。"""
        await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(provider="deepseek", model="deepseek-chat", api_key=SECRET),
            current_user=USER,
            db=db,
        )
        first = await resolve_for_user(db, USER)
        assert first.client.api_key == SECRET

        row = (
            await db.execute(select(UserSettings).where(UserSettings.user_id == USER.id))
        ).scalar_one()
        first_config_id = row.preferences["ai_provider"]["config_id"]
        assert first_config_id

        await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(
                provider="deepseek", model="deepseek-chat", api_key="second-value-987654"
            ),
            current_user=USER,
            db=db,
        )
        second = await resolve_for_user(db, USER)
        assert second.client is not first.client
        assert second.client.api_key == "second-value-987654"

        row = (
            await db.execute(select(UserSettings).where(UserSettings.user_id == USER.id))
        ).scalar_one()
        assert row.preferences["ai_provider"]["config_id"] != first_config_id

    async def test_switch_to_keyless_provider_clears_stored_key(self, db):
        """切到 Ollama（无需 key）时不再保留上一 provider 的密钥。"""
        await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(provider="deepseek", model="deepseek-chat", api_key=SECRET),
            current_user=USER,
            db=db,
        )
        await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(provider="ollama", model="llama3"),
            current_user=USER,
            db=db,
        )
        row = (
            await db.execute(select(UserSettings).where(UserSettings.user_id == USER.id))
        ).scalar_one()
        stored = row.preferences["ai_provider"]
        assert stored["provider"] == "ollama"
        assert "api_key_encrypted" not in stored
        assert "api_key" not in stored
        resolution = await resolve_for_user(db, USER)
        assert resolution.source == "user"
        assert resolution.provider == "ollama"

    async def test_use_server_default_clears_config(self, db):
        await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(provider="deepseek", model="deepseek-chat", api_key=SECRET),
            current_user=USER,
            db=db,
        )
        payload = await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(use_server_default=True),
            current_user=USER,
            db=db,
        )
        assert payload["current"]["source"] == "server"
        resolution = await resolve_for_user(db, USER)
        assert resolution.source == "server"

    async def test_missing_key_rejected_with_400(self, db):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await user_settings_ep.update_providers(
                body=ProviderSettingsUpdate(provider="deepseek", model="deepseek-chat"),
                current_user=USER,
                db=db,
            )
        assert exc_info.value.status_code == 400
        assert "API Key" in str(exc_info.value.detail)

    async def test_invalid_base_url_rejected(self, db):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await user_settings_ep.update_providers(
                body=ProviderSettingsUpdate(
                    provider="custom", model="m", api_key=SECRET, base_url="http://169.254.169.254"
                ),
                current_user=USER,
                db=db,
            )
        assert exc_info.value.status_code == 400

    async def test_general_get_settings_never_exposes_key_material(self, db):
        await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(provider="deepseek", model="deepseek-chat", api_key=SECRET),
            current_user=USER,
            db=db,
        )
        payload = await user_settings_ep.get_user_settings(current_user=USER, db=db)
        dumped = str(payload)
        assert SECRET not in dumped, "明文 key 不得出现在通用 settings 响应"
        assert "fernet:v1:" not in dumped, "密文也不得回传（避免离线爆破）"
        provider_view = payload["data"]["preferences"]["ai_provider"]
        assert provider_view["api_key"] == "user...7890"
        assert provider_view["source"] == "user"

    async def test_general_put_cannot_overwrite_encrypted_provider_config(self, db):
        await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(provider="deepseek", model="deepseek-chat", api_key=SECRET),
            current_user=USER,
            db=db,
        )
        payload = await user_settings_ep.update_user_settings(
            body=UserSettingsUpdate(
                preferences={
                    "note": "keep-me",
                    # 旧客户端/恶意客户端试图经通用通道写明文 key：必须被忽略
                    "ai_provider": {"provider": "openai", "api_key": "plain-injection-value"},
                }
            ),
            current_user=USER,
            db=db,
        )
        assert "plain-injection-value" not in str(payload)

        row = (
            await db.execute(select(UserSettings).where(UserSettings.user_id == USER.id))
        ).scalar_one()
        stored = row.preferences["ai_provider"]
        assert stored["provider"] == "deepseek"
        assert decrypt_api_key(stored["api_key_encrypted"]) == SECRET
        assert row.preferences["note"] == "keep-me"


# ── 消费点接线 ──────────────────────────────────────────────────────────────


class _FakeStream:
    def __aiter__(self):
        async def _gen():
            if False:  # pragma: no cover - 空异步生成器
                yield None

        return _gen()


class TestPipelineOverride:
    async def test_chat_stream_uses_client_override_and_model(self, monkeypatch):
        from app.rag.pipeline import RAGPipeline

        captured: dict = {}

        async def fake_create(client, **kwargs):
            captured["client"] = client
            captured["model"] = kwargs.get("model")
            return _FakeStream()

        monkeypatch.setattr("app.rag.pipeline.create_chat_completion", fake_create)
        override = object()
        pipeline = RAGPipeline(openai_client=object())
        chunks = [
            c
            async for c in pipeline.chat_stream(
                "q",
                [],
                [],
                system_prompt_override="sys",
                enable_masking=False,
                model_override="user-model",
                client_override=override,
            )
        ]
        assert chunks == []
        assert captured["client"] is override
        assert captured["model"] == "user-model"

    async def test_chat_stream_without_override_uses_pipeline_client(self, monkeypatch):
        from app.rag.pipeline import RAGPipeline

        captured: dict = {}

        async def fake_create(client, **kwargs):
            captured["client"] = client
            return _FakeStream()

        monkeypatch.setattr("app.rag.pipeline.create_chat_completion", fake_create)
        default_client = object()
        pipeline = RAGPipeline(openai_client=default_client)
        async for _ in pipeline.chat_stream(
            "q", [], [], system_prompt_override="sys", enable_masking=False
        ):
            pass
        assert captured["client"] is default_client

    async def test_user_key_never_written_to_logs(self, monkeypatch, caplog):
        """个人 key 在上游报错（异常文本回显 key）时不得进入日志。"""
        import logging

        from app.rag.pipeline import RAGPipeline

        class _FailingClient:
            api_key = SECRET

        async def fake_create(_client, **_kwargs):
            raise RuntimeError(f"Incorrect API key provided: {SECRET}")

        monkeypatch.setattr("app.rag.pipeline.create_chat_completion", fake_create)
        pipeline = RAGPipeline(openai_client=object())
        with caplog.at_level(logging.ERROR, logger="app.rag.pipeline"):
            chunks = [
                c
                async for c in pipeline.chat_stream(
                    "q",
                    [],
                    [],
                    system_prompt_override="sys",
                    enable_masking=False,
                    client_override=_FailingClient(),
                )
            ]
        assert chunks == [
            "个人模型调用失败。请在个人设置的 AI 模型设置中检查 API Key / Base URL，或稍后重试。"
        ]
        assert SECRET not in caplog.text, "API key 明文不得写日志"
        assert "RuntimeError" in caplog.text, "只记录异常类型以便排障"

    async def test_user_llm_error_raises_for_chat_layer(self, monkeypatch):
        """聊天层以 raise_user_errors 获取显式异常，改为 error 事件而非正文。"""
        from app.rag.pipeline import RAGPipeline, UserLLMRequestError

        class _FailingClient:
            api_key = "x"

        async def fake_create(_client, **_kwargs):
            raise RuntimeError("upstream down")

        monkeypatch.setattr("app.rag.pipeline.create_chat_completion", fake_create)
        pipeline = RAGPipeline(openai_client=object())
        with pytest.raises(UserLLMRequestError):
            async for _ in pipeline.chat_stream(
                "q",
                [],
                [],
                system_prompt_override="sys",
                enable_masking=False,
                client_override=_FailingClient(),
                raise_user_errors=True,
            ):
                pass


class _FakeRag:
    def __init__(self):
        self.openai_client = object()
        self.chat_kwargs: dict | None = None

    async def get_embedding(self, _text):
        return None

    async def search_knowledge_base(
        self, query, organization_id, top_k, document_ids=None, search_mode=None, debug=False
    ):
        return [], None

    async def chat_stream(self, *args, **kwargs):
        self.chat_kwargs = kwargs
        yield ""

    def report_grounded(self, _has_sources):
        return None

    def get_last_token_usage(self):
        return None


class _FakeMemory:
    _embedding_provider = object()

    async def get_context(self, _query):
        return ""

    async def store_interaction(self, _query, _answer):
        return None


class TestRunRagPipelineWiring:
    async def test_llm_client_and_model_passed_to_chat_stream(self, db, monkeypatch):
        from app.services import chat_service as chat_service_mod

        db.add(Organization(id=1, name="org", owner_id=1))
        await db.commit()

        fake_rag = _FakeRag()
        monkeypatch.setattr(chat_service_mod, "rag_service", fake_rag)
        monkeypatch.setattr(chat_service_mod, "get_memory_system", lambda *a, **k: _FakeMemory())

        import asyncio

        override = object()
        await chat_service_mod.run_rag_pipeline(
            session=db,
            user_content="问题",
            user_id=1,
            search_org_id=1,
            organization_id=1,
            conversation_id=None,
            file_ids=[],
            strict_mode=False,
            privacy_mode=True,
            event_queue=asyncio.Queue(),
            llm_client=override,
            llm_model="user-model",
        )
        assert fake_rag.chat_kwargs is not None
        assert fake_rag.chat_kwargs["client_override"] is override
        assert fake_rag.chat_kwargs["model_override"] == "user-model"
        assert fake_rag.chat_kwargs["raise_user_errors"] is True

    async def test_user_llm_error_emits_error_event_without_persisting(self, db, monkeypatch):
        from app.rag.pipeline import USER_LLM_REQUEST_FAILED_MESSAGE, UserLLMRequestError
        from app.services import chat_service as chat_service_mod

        db.add(Organization(id=1, name="org", owner_id=1))
        await db.commit()

        class RaisingRag(_FakeRag):
            async def chat_stream(self, *args, **kwargs):
                self.chat_kwargs = kwargs
                raise UserLLMRequestError(USER_LLM_REQUEST_FAILED_MESSAGE)
                yield ""  # pragma: no cover - 让函数保持 async generator

        fake_rag = RaisingRag()
        monkeypatch.setattr(chat_service_mod, "rag_service", fake_rag)
        monkeypatch.setattr(chat_service_mod, "get_memory_system", lambda *a, **k: _FakeMemory())
        persisted = AsyncMock()
        monkeypatch.setattr(chat_service_mod, "_persist_assistant_message", persisted)

        import asyncio

        queue: asyncio.Queue = asyncio.Queue()
        await chat_service_mod.run_rag_pipeline(
            session=db,
            user_content="问题",
            user_id=1,
            search_org_id=1,
            organization_id=1,
            conversation_id=None,
            file_ids=[],
            strict_mode=False,
            privacy_mode=False,
            event_queue=queue,
            llm_client=object(),
            llm_model="user-model",
        )
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        errors = [event for event in events if event.type == "error"]
        assert errors, "个人模型失败必须发 error 事件"
        assert errors[0].content == USER_LLM_REQUEST_FAILED_MESSAGE
        assert errors[0].debug_data == {"error_code": "USER_LLM_REQUEST_FAILED"}
        assert not any(event.type == "message" for event in events), "不得把诊断当回答"
        persisted.assert_not_awaited()

    async def test_user_llm_error_after_partial_stream_still_not_persisted(self, db, monkeypatch):
        """已流出部分正文后上游失败：半截回答同样不得落库/写记忆。"""
        from app.rag.pipeline import USER_LLM_REQUEST_FAILED_MESSAGE, UserLLMRequestError
        from app.services import chat_service as chat_service_mod

        db.add(Organization(id=1, name="org", owner_id=1))
        await db.commit()

        class PartialRag(_FakeRag):
            async def chat_stream(self, *args, **kwargs):
                self.chat_kwargs = kwargs
                yield "半截回答"
                raise UserLLMRequestError(USER_LLM_REQUEST_FAILED_MESSAGE)

        fake_rag = PartialRag()
        memory = _FakeMemory()
        memory.store_interaction = AsyncMock()
        monkeypatch.setattr(chat_service_mod, "rag_service", fake_rag)
        monkeypatch.setattr(chat_service_mod, "get_memory_system", lambda *a, **k: memory)
        persisted = AsyncMock()
        monkeypatch.setattr(chat_service_mod, "_persist_assistant_message", persisted)

        import asyncio

        queue: asyncio.Queue = asyncio.Queue()
        await chat_service_mod.run_rag_pipeline(
            session=db,
            user_content="问题",
            user_id=1,
            search_org_id=1,
            organization_id=1,
            conversation_id=None,
            file_ids=[],
            strict_mode=False,
            privacy_mode=False,
            event_queue=queue,
            llm_client=object(),
            llm_model="user-model",
        )
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        # 管线会先发一个空 chunk（建立 conversationId），按内容拼接断言。
        assert "".join(event.content for event in events if event.type == "chunk") == "半截回答"
        assert not any(event.type == "message" for event in events), "不得产生终态回答"
        assert any(event.type == "error" for event in events)
        persisted.assert_not_awaited()
        memory.store_interaction.assert_not_awaited()


class TestAgentServiceWiring:
    async def test_agent_loop_uses_per_user_client_and_model(self, monkeypatch):
        from app.agent.config import AgentConfig
        from app.agent.service import AgentService

        captured: dict = {}

        class FakeLoop:
            def __init__(self, openai_client, config, organization_id, user_id):
                captured["client"] = openai_client
                captured["model"] = config.model

            def _get_tools(self):
                return []

            async def run(self, *args, **kwargs):
                if False:  # pragma: no cover
                    yield None

        monkeypatch.setattr("app.agent.service.PERAgentLoop", FakeLoop)
        override = object()

        service = AgentService(openai_client=object())
        events = [
            e
            async for e in service.chat(
                "hi",
                organization_id=1,
                user_id=1,
                config=AgentConfig(enable_checkpoint=False),
                llm_client=override,
                llm_model="user-model",
            )
        ]
        assert events, "至少应有 thinking 元事件"
        assert captured["client"] is override
        assert captured["model"] == "user-model"

    async def test_agent_service_without_override_keeps_server_client(self, monkeypatch):
        from app.agent.config import AgentConfig
        from app.agent.service import AgentService

        captured: dict = {}

        class FakeLoop:
            def __init__(self, openai_client, config, organization_id, user_id):
                captured["client"] = openai_client

            def _get_tools(self):
                return []

            async def run(self, *args, **kwargs):
                if False:  # pragma: no cover
                    yield None

        monkeypatch.setattr("app.agent.service.PERAgentLoop", FakeLoop)
        default_client = object()
        service = AgentService(openai_client=default_client)
        async for _ in service.chat(
            "hi", organization_id=1, user_id=1, config=AgentConfig(enable_checkpoint=False)
        ):
            pass
        assert captured["client"] is default_client

    async def test_agent_user_llm_failure_emits_code_and_no_done(self, monkeypatch):
        """个人模型失败：service 以带码 error 事件收尾且不发 done（端点统一补 failed/unssaved 的终态 done）。"""
        from app.agent.config import AgentConfig
        from app.agent.service import AgentService
        from app.core.llm_failures import (
            USER_LLM_REQUEST_FAILED_CODE,
            USER_LLM_REQUEST_FAILED_MESSAGE,
            UserLLMRequestError,
        )

        class FailingLoop:
            def __init__(self, openai_client, config, organization_id, user_id):
                pass

            def _get_tools(self):
                return []

            async def run(self, *args, **kwargs):
                raise UserLLMRequestError(USER_LLM_REQUEST_FAILED_MESSAGE)
                yield None  # pragma: no cover - 保持 async generator

        monkeypatch.setattr("app.agent.service.PERAgentLoop", FailingLoop)
        service = AgentService(openai_client=object())
        events = [
            event
            async for event in service.chat(
                "hi",
                organization_id=1,
                user_id=1,
                config=AgentConfig(enable_checkpoint=False),
                llm_client=object(),
            )
        ]
        errors = [event for event in events if event.type == "error"]
        assert len(errors) == 1, "必须且只能发一个 error 事件"
        assert errors[0].code == USER_LLM_REQUEST_FAILED_CODE
        assert errors[0].content == USER_LLM_REQUEST_FAILED_MESSAGE
        assert not any(event.type == "done" for event in events), (
            "service 层不得发完成信号（端点补 failed/unssaved）"
        )


def test_server_default_model_matches_settings():
    """未启用本地模型时，服务端默认模型与全局配置一致（行为不变锚点）。"""
    with patch.object(settings, "ENABLE_LOCAL_LLM", False):
        assert llm_settings_service.server_default_model() == settings.DEEPSEEK_MODEL


class TestSecurityHardening:
    async def test_legacy_masked_key_falls_back_and_is_reported(self, db):
        """旧 UI 落库的掩码不是密钥：运行时回退，GET 也如实报 server。"""
        db.add(
            UserSettings(
                user_id=USER.id,
                preferences={
                    "ai_provider": {
                        "provider": "deepseek",
                        "model": "deepseek-chat",
                        "api_key": "masked...value",
                    }
                },
            )
        )
        await db.commit()
        resolution = await resolve_for_user(db, USER)
        assert resolution.source == "server"
        payload = await user_settings_ep.get_providers(current_user=USER, db=db)
        current = payload["current"]
        assert current["source"] == "server"
        assert current["has_saved_config"] is True
        assert current["fallback_reason"] == "missing_key"
        assert current["has_api_key"] is False

    async def test_same_provider_base_url_change_requires_new_key(self, db):
        """同 provider 改 Base URL 也必须重填 key，防止把密钥带到新目标。"""
        from fastapi import HTTPException

        await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(provider="deepseek", model="deepseek-chat", api_key=SECRET),
            current_user=USER,
            db=db,
        )
        with pytest.raises(HTTPException) as exc_info:
            await user_settings_ep.update_providers(
                body=ProviderSettingsUpdate(
                    provider="deepseek",
                    model="deepseek-chat",
                    api_key=mask_api_key(SECRET),
                    base_url="http://127.0.0.1:9999/v1",
                ),
                current_user=USER,
                db=db,
            )
        assert exc_info.value.status_code == 400
        assert "Base URL" in str(exc_info.value.detail)

        await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(
                provider="deepseek",
                model="deepseek-chat",
                api_key="new-value-12345678",
                base_url="http://127.0.0.1:9999/v1",
            ),
            current_user=USER,
            db=db,
        )
        resolution = await resolve_for_user(db, USER)
        assert resolution.base_url == "http://127.0.0.1:9999/v1"
        assert resolution.client.api_key == "new-value-12345678"

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/v1",
            "http://[::ffff:169.254.169.254]/v1",
            "http://100.100.100.200/v1",
            "http://[fd00:ec2::254]/v1",
            "http://2852039166/v1",
        ],
    )
    def test_metadata_targets_rejected(self, url):
        with pytest.raises(LLMConfigError):
            llm_settings_service._normalize_base_url(url, resolve_host=True)

    def test_localhost_base_url_allowed_for_ollama(self):
        assert (
            llm_settings_service._normalize_base_url("http://localhost:11434/v1")
            == "http://localhost:11434/v1"
        )

    def test_new_client_disables_redirects(self):
        client = llm_settings_service._new_client("deepseek", "https://api.deepseek.com/v1", "key")
        assert client._client.follow_redirects is False

    async def test_malformed_base_url_rejected_as_400(self, db):
        from fastapi import HTTPException

        for url in (
            "http://127.0.0.1:99999/v1",
            "http://127.0.0.1:abc/v1",
            "http://[::1/v1",
        ):
            with pytest.raises(HTTPException) as exc_info:
                await user_settings_ep.update_providers(
                    body=ProviderSettingsUpdate(
                        provider="custom", model="m", api_key=SECRET, base_url=url
                    ),
                    current_user=USER,
                    db=db,
                )
            assert exc_info.value.status_code == 400
            assert "Base URL" in str(exc_info.value.detail)

    async def test_custom_without_base_url_reported_fallback(self, db):
        db.add(
            UserSettings(
                user_id=USER.id,
                preferences={
                    "ai_provider": {
                        "provider": "custom",
                        "model": "m",
                        "api_key_encrypted": encrypt_api_key(SECRET),
                    }
                },
            )
        )
        await db.commit()
        payload = await user_settings_ep.get_providers(current_user=USER, db=db)
        current = payload["current"]
        assert current["source"] == "server"
        assert current["has_saved_config"] is True
        assert current["fallback_reason"] == "invalid_base_url"

    async def test_llm_status_reports_user_source(self, db):
        from app.api.v1.endpoints import monitoring

        await user_settings_ep.update_providers(
            body=ProviderSettingsUpdate(provider="ollama", model="llama3"),
            current_user=USER,
            db=db,
        )
        payload = await monitoring.get_llm_status(current_user=USER, db=db)
        assert payload["data"]["source"] == "user"
        assert payload["data"]["provider"] == "ollama"
        assert payload["data"]["model"] == "llama3"
        assert payload["data"]["configured"] is True


async def test_context_summary_uses_personal_model(monkeypatch):
    """Agent 上下文摘要必须使用个人 provider 的模型名，而非固定 DEEPSEEK_MODEL。"""
    from app.agent.context import ContextEngine

    calls: list[dict] = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="summary"))]
            )

    class ChatProxy:
        completions = FakeCompletions()

    class FakeClient:
        chat = ChatProxy()

    monkeypatch.setattr("app.agent.usage.record_llm_usage", lambda *a, **k: None)
    monkeypatch.setattr("app.agent.usage.record_llm_call_error", lambda *a, **k: None)
    engine = ContextEngine(max_context_tokens=100, tail_window=2)
    engine.set_llm_client(FakeClient(), "custom-model")
    summary = await engine._summarize_with_llm([{"role": "user", "content": "hi"}], 100)
    assert summary == "summary"
    assert calls and calls[0]["model"] == "custom-model"


def test_cache_scope_identifies_llm_identity():
    """回答缓存分域：个人配置按用户+provider+model，服务端按模型。"""
    personal = llm_settings_service.LLMResolution(
        client=object(), model="m", source="user", provider="openai"
    )
    assert llm_settings_service.cache_scope(personal, 7) == "user:7:openai:m"
    server = llm_settings_service.LLMResolution(
        client=None, model="deepseek-v4-flash", source="server"
    )
    assert llm_settings_service.cache_scope(server, 7) == "server:deepseek-v4-flash"
