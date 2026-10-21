"""LLM 未配置 → 错误通道回归：不落库、不写记忆、SSE 收 error 事件。

背景：`RAGPipeline.chat_stream` 在无 LLM client 时曾 `yield "LLM未配置"`，
上层（`run_rag_pipeline`）把它当作正文 chunk 逐字累加：
- 用户消息与「LLM未配置」ASSISTANT 消息双双落库（会话历史垃圾消息）；
- `memory_system.store_interaction` 把这段配置诊断写进长期记忆；
- SSE 客户端只看到一条"正常"的 message 事件，而非 error。

修复：聊天管线入口预检无 LLM client → 发 error 事件并返回（先于任何落库/
语义缓存读写/检索/记忆）；`chat_stream` 改为抛 `LLMNotConfiguredError`
（配置诊断绝不作为内容）；RAG 评估的单条/批量路径同样预检为 503 明确文案。

变异点（回退判别）：
- 删掉 `run_rag_pipeline` 入口的 `rag_service.openai_client is None` 预检
  → 用例 1/2/3 红（发生落库/记忆写入/无 error 事件/缓存被触碰）；
- `chat_stream` 恢复 `yield "LLM未配置"` → 用例 0 红；
- 删掉 monitoring 两处 `settings.DEEPSEEK_API_KEY` 预检 → 用例 5 红（500 ≠ 503）。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.chat import ChatMessage, MessageType


class _ExplodingSemanticCache:
    """触碰即炸的语义缓存：无 LLM 预览必须连缓存都不碰。"""

    async def get(self, *args, **kwargs):
        raise AssertionError("semantic-cache-touched")

    async def set(self, *args, **kwargs):
        raise AssertionError("semantic-cache-touched")


class _AsyncCtx:
    """支持 ``async with`` 的 mock 上下文管理器。"""

    def __init__(self, obj):
        self._obj = obj

    async def __aenter__(self):
        return self._obj

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


class _RecordingMemory:
    """记录 store_interaction 的记忆系统假对象（用于断言「不写记忆」）。"""

    def __init__(self):
        self._embedding_provider = None
        self.store_calls: list[tuple[str, str]] = []

    def set_embedding_provider(self, provider):
        self._embedding_provider = provider

    async def get_context(self, query: str) -> str:
        return ""

    async def store_interaction(self, user_input: str, assistant_response: str) -> None:
        self.store_calls.append((user_input, assistant_response))


def _mock_session():
    """足以走完 run_rag_pipeline 的假会话；返回 (session, added)。"""
    session = MagicMock()
    added: list = []
    session.add = MagicMock(side_effect=added.append)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    hist = MagicMock()
    hist.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=hist)
    return session, added


def _make_no_llm_rag_service():
    """openai_client=None 的假门面；行为模拟修前管线（供红态走完整流程）。"""
    calls = {"chat_stream": 0, "embedding": 0, "search": 0}

    async def _chat_stream(*args, **kwargs):
        calls["chat_stream"] += 1
        yield "LLM未配置"

    async def _get_embedding(text):
        calls["embedding"] += 1

    async def _search(*args, **kwargs):
        calls["search"] += 1
        return [], None

    fake = SimpleNamespace(
        openai_client=None,
        get_embedding=_get_embedding,
        search_knowledge_base=_search,
        chat_stream=_chat_stream,
        report_grounded=MagicMock(),
        get_last_token_usage=lambda: None,
    )
    return fake, calls


def _drain(queue: asyncio.Queue) -> list:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


def _override_user(user):
    """覆盖 get_current_user；返回清理函数。"""
    from app.main import app

    async def _user():
        return user

    app.dependency_overrides[get_current_user] = _user

    def _cleanup():
        app.dependency_overrides.pop(get_current_user, None)

    return _cleanup


# ══════════════════════════════════════════════════════════════════════════
# 0 · pipeline 层：配置诊断不得伪装成内容 chunk
# ══════════════════════════════════════════════════════════════════════════
class TestPipelineLLMNotConfigured:
    @pytest.mark.asyncio
    async def test_chat_stream_raises_instead_of_yielding_config_diagnostic(self):
        """无 client 时 chat_stream 抛 LLMNotConfiguredError，绝不 yield 内容。

        变异验证：恢复 `yield "LLM未配置"` → 本用例红（不抛异常）。
        """
        from app.rag.errors import LLMNotConfiguredError
        from app.rag.pipeline import RAGPipeline

        pipeline = RAGPipeline(openai_client=None)
        chunks = []
        with pytest.raises(LLMNotConfiguredError):
            async for chunk in pipeline.chat_stream("你好", []):
                chunks.append(chunk)
        assert chunks == [], "配置诊断绝不能被当作回答内容下发"


# ══════════════════════════════════════════════════════════════════════════
# 1 · 管线层：入口预检先于落库 / 记忆 / 内容累加
# ══════════════════════════════════════════════════════════════════════════
class TestRunRagPipelineNoLLM:
    @pytest.mark.asyncio
    async def test_no_llm_client_refuses_before_persistence_and_memory(self):
        """无 LLM client → 只发 error 事件；会话/消息零落库、记忆零写入。

        变异验证：删掉入口预检 → 修前行为回归（chunk/message 事件、用户与
        ASSISTANT 消息落库、store_interaction 被调用）→ 本用例红。
        """
        from app.services.chat_service import LLM_NOT_CONFIGURED_MESSAGE, run_rag_pipeline

        session, added = _mock_session()
        queue: asyncio.Queue = asyncio.Queue()
        fake_rag, calls = _make_no_llm_rag_service()
        memory = _RecordingMemory()

        with (
            patch("app.services.chat_service.rag_service", new=fake_rag),
            patch(
                "app.services.chat_service.get_memory_system",
                new=MagicMock(return_value=memory),
            ),
            patch(
                "app.services.chat_service._semantic_cache",
                new=_ExplodingSemanticCache(),
            ),
        ):
            await run_rag_pipeline(
                session=session,
                user_content="你好",
                user_id=1,
                search_org_id=5,
                organization_id=5,
                conversation_id=None,
                file_ids=[],
                strict_mode=True,
                privacy_mode=True,
                event_queue=queue,
            )

        events = _drain(queue)
        assert [e.type for e in events] == ["error"], "无 LLM 时必须只发 error 事件"
        assert events[0].content == LLM_NOT_CONFIGURED_MESSAGE
        assert added == [], "预检必须先于一切落库：会话/用户消息/ASSISTANT 都不产生"
        assert session.commit.call_count == 0
        assert memory.store_calls == [], "不得把配置诊断写入长期记忆"
        assert calls == {"chat_stream": 0, "embedding": 0, "search": 0}

    @pytest.mark.asyncio
    async def test_no_llm_client_intercepts_cache_path_before_any_touch(self):
        """语义缓存/检索路径同样拦截：预检发生在读缓存与 embedding 之前。

        变异验证：把入口预检移到缓存/检索之后 → embedding 断言炸 → 本用例红。
        """
        from app.services.chat_service import LLM_NOT_CONFIGURED_MESSAGE, run_rag_pipeline

        session, added = _mock_session()
        queue: asyncio.Queue = asyncio.Queue()

        async def _embedding_touched(text):
            raise AssertionError("embedding-touched")

        async def _search_touched(*args, **kwargs):
            raise AssertionError("kb-search-touched")

        async def _chat_stream_touched(*args, **kwargs):
            raise AssertionError("llm-stream-touched")
            yield  # pragma: no cover

        fake_rag = SimpleNamespace(
            openai_client=None,
            get_embedding=_embedding_touched,
            search_knowledge_base=_search_touched,
            chat_stream=_chat_stream_touched,
            report_grounded=MagicMock(),
            get_last_token_usage=lambda: None,
        )

        with (
            patch("app.services.chat_service.rag_service", new=fake_rag),
            patch(
                "app.services.chat_service.get_memory_system",
                new=MagicMock(side_effect=AssertionError("memory-touched")),
            ),
            patch(
                "app.services.chat_service._semantic_cache",
                new=_ExplodingSemanticCache(),
            ),
        ):
            await run_rag_pipeline(
                session=session,
                user_content="你好",
                user_id=1,
                search_org_id=5,
                organization_id=5,
                conversation_id=None,
                file_ids=[],
                strict_mode=False,
                privacy_mode=True,
                event_queue=queue,
            )

        events = _drain(queue)
        assert [e.type for e in events] == ["error"]
        assert events[0].content == LLM_NOT_CONFIGURED_MESSAGE
        assert added == [], "缓存/检索路径同样不得落库"

    def test_chat_sse_no_llm_client_returns_error_event_without_message(self, client: TestClient):
        """SSE 端点：无 LLM client → event: error（明确文案），无 message/落库。

        变异验证：删掉入口预检 → 修前行为：event: chunk「LLM未配置」+ message，
        且 ASSISTANT 消息被落库、记忆被写入 → 本用例红。
        """
        from app.main import app

        db_user = SimpleNamespace(organization_id=5)
        session, added = _mock_session()
        session.get = AsyncMock(return_value=db_user)

        async def _override_db():
            yield session

        user = SimpleNamespace(
            id=1, organization_id=5, is_superuser=False, role="user", is_active=True
        )
        _cleanup = _override_user(user)
        app.dependency_overrides[get_db] = _override_db
        fake_rag, calls = _make_no_llm_rag_service()
        memory = _RecordingMemory()
        try:
            with (
                patch(
                    "app.api.v1.endpoints.chat.AsyncSessionLocal",
                    new=lambda: _AsyncCtx(session),
                ),
                patch("app.services.chat_service.rag_service", new=fake_rag),
                patch(
                    "app.services.chat_service.get_memory_system",
                    new=MagicMock(return_value=memory),
                ),
                patch(
                    "app.services.chat_service._semantic_cache",
                    new=_ExplodingSemanticCache(),
                ),
            ):
                r = client.post("/api/v1/chat/stream", json={"content": "你好"})
        finally:
            app.dependency_overrides.pop(get_db, None)
            _cleanup()

        assert r.status_code == 200, r.text
        assert "event: error" in r.text, f"必须收到 error 事件，实际流: {r.text[:400]}"
        assert "LLM" in r.text and "DEEPSEEK_API_KEY" in r.text, (
            f"错误文案必须明确指向 LLM 配置: {r.text[:400]}"
        )
        assert "event: message" not in r.text, "错误不得以 message 事件呈现"
        assert not [
            o
            for o in added
            if isinstance(o, ChatMessage) and o.message_type == MessageType.ASSISTANT
        ], "不得产生 ASSISTANT 消息（会话历史无垃圾消息）"
        assert memory.store_calls == [], "不得写入长期记忆"
        assert calls["chat_stream"] == 0


# ══════════════════════════════════════════════════════════════════════════
# 2 · agent 通道对齐：既有的 type="error" 事件语义（char 化，防回归）
# ══════════════════════════════════════════════════════════════════════════
class TestAgentChannelAlignment:
    def test_agent_chat_stream_llm_not_configured_emits_error_event(self, client: TestClient):
        """agent 模式无 LLM client：SSE 收到 event: error（与 RAG 通道同语义）。"""
        from app.agent.service import agent_service
        from app.main import app

        user = SimpleNamespace(
            id=1, organization_id=5, is_superuser=False, role="user", is_active=True
        )

        async def _override_db():
            yield MagicMock()

        _cleanup = _override_user(user)
        app.dependency_overrides[get_db] = _override_db
        original_client = agent_service._client
        agent_service._client = None
        try:
            with patch(
                "app.dependencies.get_rag_pipeline",
                new=MagicMock(return_value=SimpleNamespace(openai_client=None)),
            ):
                r = client.post(
                    "/api/v1/chat/stream",
                    json={"content": "你好", "useAgent": True},
                )
        finally:
            agent_service._client = original_client
            app.dependency_overrides.pop(get_db, None)
            _cleanup()

        assert r.status_code == 200, r.text
        assert "event: error" in r.text, f"agent 通道必须转发 error 事件: {r.text[:400]}"
        assert "LLM 未配置" in r.text, f"agent 错误文案缺失: {r.text[:400]}"


# ══════════════════════════════════════════════════════════════════════════
# 3 · 批量/评估路径：LLM 未配置 → 503 明确文案（不落到构造异常 500）
# ══════════════════════════════════════════════════════════════════════════
class TestRagEvalNoLLMKey:
    @pytest.mark.parametrize(
        ("endpoint", "payload"),
        [
            (
                "/api/v1/monitoring/rag-eval",
                {"question": "q", "answer": "a", "context": []},
            ),
            (
                "/api/v1/monitoring/rag-eval-batch",
                {"items": [{"question": "q", "answer": "a", "context": []}]},
            ),
        ],
    )
    def test_rag_eval_without_llm_key_returns_503_with_copy(
        self, client: TestClient, monkeypatch, endpoint, payload
    ):
        """批量/评估路径同样拦截：无 DEEPSEEK_API_KEY → 503 + 明确文案。

        变异验证：删掉两处 `settings.DEEPSEEK_API_KEY` 预检 → 构造即抛
        （模拟 AsyncOpenAI(api_key=None) 的实际行为）→ 500 ≠ 503 → 本用例红。
        """
        from app.api.v1.endpoints import monitoring

        user = SimpleNamespace(
            id=1, organization_id=5, is_superuser=True, role="admin", is_active=True
        )
        _cleanup = _override_user(user)
        monkeypatch.setattr(monitoring.settings, "DEEPSEEK_API_KEY", "")
        try:
            with patch(
                "openai.AsyncOpenAI",
                side_effect=RuntimeError("api_key client option must be set"),
            ):
                r = client.post(endpoint, json=payload)
        finally:
            _cleanup()

        assert r.status_code == 503, (
            f"LLM 未配置必须如实 503（而非构造异常 500/全零 200）: {r.status_code} {r.text[:300]}"
        )
        assert "LLM" in r.text and "DEEPSEEK_API_KEY" in r.text, (
            f"503 文案必须明确指向 LLM 配置: {r.text[:300]}"
        )
