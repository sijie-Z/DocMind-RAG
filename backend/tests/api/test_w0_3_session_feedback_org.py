"""W0-3 回归 —— feedback 端点与 RAG 会话组织的有效组织口径（#416 / T4 补漏）。

背景
====
T4（#438）把按 id 的会话详情/更新/删除/清空/解绑统一到
``session_access_conditions`` / ``load_accessible_session``，并给
``save_session_interaction`` / ``get_or_create_session`` 补了跨组织写入拒绝。
但**消息反馈**两条写路径（``/chat/messages/{id}/feedback`` 与
``/agent/feedback``）仍只按 ``ChatSession.user_id`` 过滤：多组织用户切到 B
组织后，仍可改写自己在 A 组织会话里消息的反馈。另外聊天页 RAG 分支的
``/chat/stream`` 仍用裸 ``current_user.organization_id`` 解析会话组织；
对 token 组织回退用户（库中主组织为空）该值为 None，叠加 ``search_org_id``
同样为 None，请求会在 ``run_rag_pipeline`` 入口被显式拒绝——本次将其与
``/agent/chat`` / ``create_conversation`` 的有效组织口径对齐（能力放行，
不是修 NULL 落库；放行前提是 token 组织已回源成员表校验）。

本文件用**真实 SQLite**（sqlite+aiosqlite 内存库）驱动 ASGI app，断言端点
真实生成的查询/写入语义，而不是 mock 的调用顺序。

口径（与 T4 一致）
==================
- 非超管有组织：本人 + 本组织 + 历史 NULL 组织会话；
- 非超管无组织：只能触达本人历史 NULL 组织会话（fail-closed）；
- 超管：保留列表既有旁路（不按组织过滤，仍限本人）；
- feedback 是写路径：跨组织消息视同不存在（404），反馈值/备注不得被改写；
- WS 逐消息路径**有意**只认库中主组织、不启用 token 组织回退（见
  ``TestWebSocketOrgSemantics``）。

变异点（回退判别）
==================
- feedback 查询去掉 org 条件 → ``TestChatFeedbackOrgScope`` /
  ``TestAgentFeedbackOrgScope`` 的 denial 用例红；
- 用 raw ``current_user.organization_id`` 解析有效组织（忽略 token 回退）→
  ``test_token_fallback_*`` 的 allowed 断言红（B 组织消息被误拒）；
- RAG 分支把裸 ``organization_id`` 传给 run_rag_pipeline →
  ``test_rag_stream_uses_effective_token_org`` 红；
- 超管不再旁路 → ``TestSuperuserFeedbackBypass`` 红；
- WS 被改成 token 组织回退 → ``TestWebSocketOrgSemantics`` 红（锁定严格口径）。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.security import get_current_user
from app.exceptions import AuthorizationError
from app.models import Base
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType
from app.models.organization import Organization
from app.services.chat_service import get_or_create_session

ORG_A = 7
ORG_B = 8

SESSION_A = "w03-sess-org-a"
SESSION_B = "w03-sess-org-b"
SESSION_LEGACY = "w03-sess-legacy-null"
SESSION_FOREIGN = "w03-sess-other-user"

MSG_A = "w03-msg-a"
MSG_B = "w03-msg-b"
MSG_LEGACY = "w03-msg-legacy"
MSG_FOREIGN = "w03-msg-foreign"

CHAT_FEEDBACK = "/api/v1/chat/messages/{mid}/feedback"
AGENT_FEEDBACK = "/api/v1/agent/feedback"


# ── 夹具：真实 SQLite + ASGI 客户端 ─────────────────────────────────────────


async def _seeded_factory():
    """内存库：同一用户（id=1）在 A/B 两个组织的会话/消息矩阵 + 他人 + 历史 NULL。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as s:
        s.add_all(
            [
                Organization(id=ORG_A, name="W03 Org A", owner_id=0),
                Organization(id=ORG_B, name="W03 Org B", owner_id=0),
            ]
        )
        s.add_all(
            [
                ChatSession(
                    id=SESSION_A,
                    user_id=1,
                    organization_id=ORG_A,
                    title="A session",
                    status=ChatSessionStatus.ACTIVE,
                ),
                ChatSession(
                    id=SESSION_B,
                    user_id=1,
                    organization_id=ORG_B,
                    title="B session",
                    status=ChatSessionStatus.ACTIVE,
                ),
                ChatSession(
                    id=SESSION_LEGACY,
                    user_id=1,
                    organization_id=None,
                    title="Legacy session",
                    status=ChatSessionStatus.ACTIVE,
                ),
                ChatSession(
                    id=SESSION_FOREIGN,
                    user_id=2,
                    organization_id=ORG_A,
                    title="Other user's session",
                    status=ChatSessionStatus.ACTIVE,
                ),
            ]
        )
        s.add_all(
            [
                ChatMessage(
                    id=MSG_A,
                    session_id=SESSION_A,
                    content="org-a answer",
                    message_type=MessageType.ASSISTANT,
                ),
                ChatMessage(
                    id=MSG_B,
                    session_id=SESSION_B,
                    content="org-b answer",
                    message_type=MessageType.ASSISTANT,
                ),
                ChatMessage(
                    id=MSG_LEGACY,
                    session_id=SESSION_LEGACY,
                    content="legacy answer",
                    message_type=MessageType.ASSISTANT,
                ),
                ChatMessage(
                    id=MSG_FOREIGN,
                    session_id=SESSION_FOREIGN,
                    content="foreign answer",
                    message_type=MessageType.ASSISTANT,
                ),
            ]
        )
        await s.commit()

    return engine, factory


def _user(org_id, *, user_id=1, superuser=False, token_org=None):
    return SimpleNamespace(
        id=user_id,
        organization_id=org_id,
        token_organization_id=token_org,
        is_superuser=superuser,
        role="user",
        is_active=True,
        username=f"user{user_id}",
    )


class _AsyncClientCtx:
    """安装 dependency_overrides → 返回同一事件循环内的 ASGI 客户端。"""

    def __init__(self, user, factory):
        self._user = user
        self._factory = factory

    async def __aenter__(self):
        from app.main import app

        async def _current_user():
            return self._user

        async def _db():
            async with self._factory() as session:
                yield session

        app.dependency_overrides[get_current_user] = _current_user
        app.dependency_overrides[get_db] = _db
        self._app = app
        self._client = AsyncClient(transport=ASGITransport(app=app), base_url="http://w03")
        return self._client

    async def __aexit__(self, exc_type, exc, tb):
        await self._client.aclose()
        self._app.dependency_overrides.pop(get_current_user, None)
        self._app.dependency_overrides.pop(get_db, None)
        return False


async def _feedback_state(factory, message_id):
    async with factory() as s:
        row = await s.get(ChatMessage, message_id)
        return None if row is None else (row.feedback, row.feedback_note)


# 拒绝类用例统一用「请求前后状态对照」：ChatMessage.feedback 的列默认值是 0，
# 不能把「未写入」硬编码成 None（否则判别的是列默认值而不是端点行为）。


async def _get_session_row(factory, session_id):
    async with factory() as s:
        return await s.get(ChatSession, session_id)


async def _count_messages(factory, session_id):
    async with factory() as s:
        stmt = (
            select(func.count())
            .select_from(ChatMessage)
            .where(ChatMessage.session_id == session_id)
        )
        return (await s.execute(stmt)).scalar() or 0


# ── 1. chat 消息反馈：跨组织写路径必须明确拒绝 ───────────────────────────────


class TestChatFeedbackOrgScope:
    @pytest.mark.asyncio
    async def test_cross_org_feedback_denied_and_value_unchanged(self):
        """切到 B 后给 A 组织会话里的消息点踩 → 404，反馈值不得被改写。"""
        engine, factory = await _seeded_factory()
        try:
            before = await _feedback_state(factory, MSG_A)
            async with _AsyncClientCtx(_user(ORG_B), factory) as ac:
                r = await ac.post(
                    CHAT_FEEDBACK.format(mid=MSG_A),
                    json={"feedback": -1, "note": "cross-org"},
                )
            assert r.status_code == 404, r.text
            assert await _feedback_state(factory, MSG_A) == before, (
                "跨组织反馈被拒绝后不得留下任何写入"
            )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_same_org_feedback_allowed(self):
        """对照：当前组织会话的消息可正常反馈。"""
        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(ORG_A), factory) as ac:
                r = await ac.post(
                    CHAT_FEEDBACK.format(mid=MSG_A),
                    json={"feedback": 1, "note": "great"},
                )
            assert r.status_code == 200, r.text
            assert r.json()["success"] is True
            assert await _feedback_state(factory, MSG_A) == (1, "great")
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_legacy_null_org_feedback_allowed_from_any_org(self):
        """历史 NULL 组织会话属本人：切到 B 后仍可反馈（不得误伤）。"""
        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(ORG_B), factory) as ac:
                r = await ac.post(
                    CHAT_FEEDBACK.format(mid=MSG_LEGACY),
                    json={"feedback": 1},
                )
            assert r.status_code == 200, r.text
            assert await _feedback_state(factory, MSG_LEGACY) == (1, None)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_orgless_non_superuser_only_reaches_legacy_null(self):
        """无组织非超管：历史 NULL 消息可反馈，带组织消息一律 404。"""
        engine, factory = await _seeded_factory()
        try:
            before = await _feedback_state(factory, MSG_A)
            async with _AsyncClientCtx(_user(None), factory) as ac:
                legacy = await ac.post(CHAT_FEEDBACK.format(mid=MSG_LEGACY), json={"feedback": 1})
                tagged = await ac.post(CHAT_FEEDBACK.format(mid=MSG_A), json={"feedback": 1})
            assert legacy.status_code == 200, legacy.text
            assert tagged.status_code == 404, tagged.text
            assert await _feedback_state(factory, MSG_A) == before
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_token_fallback_uses_effective_org(self):
        """库中 org=None、token 组织=B：按有效组织 B 判定（不是 raw None）。

        变异：改用裸 ``current_user.organization_id``（None）→ scope 退化为
        「仅历史 NULL」→ B 组织消息被误拒（allowed 断言红）；A 组织消息仍被拒。
        """
        engine, factory = await _seeded_factory()
        try:
            before = await _feedback_state(factory, MSG_A)
            async with _AsyncClientCtx(_user(None, token_org=ORG_B), factory) as ac:
                denied = await ac.post(CHAT_FEEDBACK.format(mid=MSG_A), json={"feedback": 1})
                allowed = await ac.post(CHAT_FEEDBACK.format(mid=MSG_B), json={"feedback": 1})
            assert denied.status_code == 404, denied.text
            assert allowed.status_code == 200, allowed.text
            assert await _feedback_state(factory, MSG_A) == before
            assert await _feedback_state(factory, MSG_B) == (1, None)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_other_users_message_still_hidden(self):
        """user 条件不放松：同组织其他用户的消息仍不可反馈。"""
        engine, factory = await _seeded_factory()
        try:
            before = await _feedback_state(factory, MSG_FOREIGN)
            async with _AsyncClientCtx(_user(ORG_A), factory) as ac:
                r = await ac.post(CHAT_FEEDBACK.format(mid=MSG_FOREIGN), json={"feedback": 1})
            assert r.status_code == 404, r.text
            assert await _feedback_state(factory, MSG_FOREIGN) == before
        finally:
            await engine.dispose()


# ── 2. agent 消息反馈：同一口径（含 lesson 前置校验）────────────────────────


class TestAgentFeedbackOrgScope:
    @pytest.mark.asyncio
    async def test_cross_org_feedback_denied_and_value_unchanged(self):
        """切到 B 后给 A 组织会话里的 agent 消息点踩 → 404，反馈值不得被改写。"""
        engine, factory = await _seeded_factory()
        try:
            before = await _feedback_state(factory, MSG_A)
            async with _AsyncClientCtx(_user(ORG_B), factory) as ac:
                r = await ac.post(
                    AGENT_FEEDBACK,
                    json={"message_id": MSG_A, "feedback": -1, "note": "cross-org"},
                )
            assert r.status_code == 404, r.text
            assert await _feedback_state(factory, MSG_A) == before
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_same_org_feedback_allowed(self):
        """对照：当前组织会话的消息可正常反馈（写入成功）。"""
        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(ORG_A), factory) as ac:
                r = await ac.post(
                    AGENT_FEEDBACK,
                    json={"message_id": MSG_A, "feedback": 1},
                )
            assert r.status_code == 200, r.text
            assert await _feedback_state(factory, MSG_A) == (1, None)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_legacy_null_org_feedback_allowed_from_any_org(self):
        """历史 NULL 组织会话属本人：切到 B 后仍可反馈。"""
        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(ORG_B), factory) as ac:
                r = await ac.post(
                    AGENT_FEEDBACK,
                    json={"message_id": MSG_LEGACY, "feedback": 1},
                )
            assert r.status_code == 200, r.text
            assert await _feedback_state(factory, MSG_LEGACY) == (1, None)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_other_users_message_still_hidden(self):
        """user 条件不放松：同组织其他用户的消息仍不可反馈。"""
        engine, factory = await _seeded_factory()
        try:
            before = await _feedback_state(factory, MSG_FOREIGN)
            async with _AsyncClientCtx(_user(ORG_A), factory) as ac:
                r = await ac.post(
                    AGENT_FEEDBACK,
                    json={"message_id": MSG_FOREIGN, "feedback": 1},
                )
            assert r.status_code == 404, r.text
            assert await _feedback_state(factory, MSG_FOREIGN) == before
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_cross_org_denial_does_not_record_lesson(self, monkeypatch):
        """跨组织拒绝发生在 lesson 写入之前：AgentMemoryBridge 不得被实例化。"""
        import app.agent.memory_bridge as memory_bridge

        calls: list = []

        class _SpyBridge:
            def __init__(self, **kwargs):
                calls.append(kwargs)

        monkeypatch.setattr(memory_bridge, "AgentMemoryBridge", _SpyBridge)

        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(ORG_B), factory) as ac:
                r = await ac.post(
                    AGENT_FEEDBACK,
                    json={"message_id": MSG_A, "feedback": -1, "note": "cross-org"},
                )
            assert r.status_code == 404, r.text
            assert calls == [], f"跨组织点踩不得写入任何 lesson（bridge 被实例化: {calls}）"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_orgless_non_superuser_only_reaches_legacy_null(self):
        """无组织非超管：历史 NULL 消息可反馈，带组织消息一律 404。"""
        engine, factory = await _seeded_factory()
        try:
            before = await _feedback_state(factory, MSG_A)
            async with _AsyncClientCtx(_user(None), factory) as ac:
                legacy = await ac.post(
                    AGENT_FEEDBACK,
                    json={"message_id": MSG_LEGACY, "feedback": 1},
                )
                tagged = await ac.post(
                    AGENT_FEEDBACK,
                    json={"message_id": MSG_A, "feedback": 1},
                )
            assert legacy.status_code == 200, legacy.text
            assert tagged.status_code == 404, tagged.text
            assert await _feedback_state(factory, MSG_A) == before
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_token_fallback_uses_effective_org(self):
        """库中 org=None、token 组织=B：按有效组织 B 判定（不是 raw None）。

        变异：改用裸 ``current_user.organization_id``（None）→ scope 退化为
        「仅历史 NULL」→ B 组织消息被误拒（allowed 断言红）；A 组织消息仍被拒。
        """
        engine, factory = await _seeded_factory()
        try:
            before = await _feedback_state(factory, MSG_A)
            async with _AsyncClientCtx(_user(None, token_org=ORG_B), factory) as ac:
                denied = await ac.post(
                    AGENT_FEEDBACK,
                    json={"message_id": MSG_A, "feedback": 1},
                )
                allowed = await ac.post(
                    AGENT_FEEDBACK,
                    json={"message_id": MSG_B, "feedback": 1},
                )
            assert denied.status_code == 404, denied.text
            assert allowed.status_code == 200, allowed.text
            assert await _feedback_state(factory, MSG_A) == before
            assert await _feedback_state(factory, MSG_B) == (1, None)
        finally:
            await engine.dispose()


class TestSuperuserFeedbackBypass:
    @pytest.mark.asyncio
    async def test_superuser_feedback_across_orgs_allowed_but_still_own_only(self):
        """超管保留旁路：可反馈自己在任意组织的消息；他人消息仍不可触达。"""
        engine, factory = await _seeded_factory()
        try:
            before = await _feedback_state(factory, MSG_FOREIGN)
            async with _AsyncClientCtx(_user(None, superuser=True), factory) as ac:
                chat = await ac.post(CHAT_FEEDBACK.format(mid=MSG_B), json={"feedback": 1})
                agent = await ac.post(
                    AGENT_FEEDBACK,
                    json={"message_id": MSG_LEGACY, "feedback": 1},
                )
                foreign = await ac.post(CHAT_FEEDBACK.format(mid=MSG_FOREIGN), json={"feedback": 1})
            assert chat.status_code == 200, chat.text
            assert agent.status_code == 200, agent.text
            assert foreign.status_code == 404, foreign.text
            assert await _feedback_state(factory, MSG_B) == (1, None)
            assert await _feedback_state(factory, MSG_LEGACY) == (1, None)
            assert await _feedback_state(factory, MSG_FOREIGN) == before
        finally:
            await engine.dispose()


# ── 3. 双组织验收矩阵：详情/删除/改名/清空/反馈/复用会话 ────────────────────


class TestDoubleOrgAcceptanceMatrix:
    @pytest.mark.asyncio
    async def test_all_by_id_paths_deny_after_org_switch(self):
        """切到 B 后，按 id 的六类路径全部拒绝且无副作用（T4 + W0-3 合并验收）。

        - 详情：agent/chat 均 404；
        - 删除：agent 会话行存活；
        - 改名：标题不变；
        - 清空：消息数量不变；
        - 反馈：反馈值不变（W0-3 新增）；
        - 复用它落消息：get_or_create_session 抛 AuthorizationError。
        """
        engine, factory = await _seeded_factory()
        try:
            before_feedback = await _feedback_state(factory, MSG_A)
            async with _AsyncClientCtx(_user(ORG_B), factory) as ac:
                detail_agent = await ac.get(f"/api/v1/agent/sessions/{SESSION_A}")
                detail_chat = await ac.get(f"/api/v1/chat/conversations/{SESSION_A}")
                delete_agent = await ac.delete(f"/api/v1/agent/sessions/{SESSION_A}")
                rename = await ac.put(
                    f"/api/v1/chat/conversations/{SESSION_A}",
                    json={"title": "hijacked"},
                )
                clear = await ac.delete(f"/api/v1/chat/conversations/{SESSION_A}/clear")
                feedback = await ac.post(CHAT_FEEDBACK.format(mid=MSG_A), json={"feedback": -1})
            assert detail_agent.status_code == 404, detail_agent.text
            assert detail_chat.status_code == 404, detail_chat.text
            assert delete_agent.status_code == 404, delete_agent.text
            assert rename.status_code == 404, rename.text
            assert clear.status_code == 404, clear.text
            assert feedback.status_code == 404, feedback.text

            row = await _get_session_row(factory, SESSION_A)
            assert row is not None and row.title == "A session"
            assert await _count_messages(factory, SESSION_A) == 1
            assert await _feedback_state(factory, MSG_A) == before_feedback

            async with factory() as s:
                with pytest.raises(AuthorizationError):
                    await get_or_create_session(
                        s, SESSION_A, user_id=1, title="q", organization_id=ORG_B
                    )
        finally:
            await engine.dispose()


# ── 4. /chat/stream RAG 分支：会话组织必须用有效组织 ─────────────────────────


class TestRagStreamEffectiveOrg:
    @pytest.mark.asyncio
    async def test_rag_stream_uses_effective_token_org(self, monkeypatch):
        """token 组织回退用户走 RAG 分支：传给管线的组织是有效组织 B，不是 raw None。

        变异：改回 ``getattr(current_user, "organization_id", None)`` →
        捕获到 None，本用例红。
        """
        import app.api.v1.endpoints.chat as chat_module

        engine, factory = await _seeded_factory()
        try:
            captured: dict = {}

            async def _fake_pipeline(**kwargs):
                captured.update(kwargs)

            monkeypatch.setattr(chat_module, "run_rag_pipeline", _fake_pipeline)
            monkeypatch.setattr(chat_module, "AsyncSessionLocal", factory)

            async with _AsyncClientCtx(_user(None, token_org=ORG_B), factory) as ac:
                r = await ac.post("/api/v1/chat/stream", json={"content": "你好"})
            assert r.status_code == 200, r.text
            assert captured.get("organization_id") == ORG_B, (
                f"会话组织必须用有效组织（token 回退 B），实际: {captured.get('organization_id')!r}"
            )
            assert captured.get("search_org_id") == ORG_B, (
                f"检索组织必须用有效组织（token 回退 B），实际: {captured.get('search_org_id')!r}"
            )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_rag_stream_refuses_cross_org_session_reuse(self, monkeypatch):
        """切到 B 后带 A 组织会话 id 走 RAG：管线拒绝复用，不新增任何消息。"""
        import app.api.v1.endpoints.chat as chat_module
        import app.services.chat_service as chat_service_module

        engine, factory = await _seeded_factory()
        try:
            monkeypatch.setattr(chat_module, "AsyncSessionLocal", factory)
            from app.dependencies import get_rag_pipeline

            monkeypatch.setattr(get_rag_pipeline(), "openai_client", object())

            async def _policy(_db, _org_id):
                return SimpleNamespace(retrieval_top_k=None, allow_privacy_mode=True)

            monkeypatch.setattr(
                chat_service_module.organization_settings_service,
                "get_runtime_policy",
                _policy,
            )

            async with _AsyncClientCtx(_user(ORG_B), factory) as ac:
                r = await ac.post(
                    "/api/v1/chat/stream",
                    json={"content": "hi", "conversationId": SESSION_A},
                )
            assert r.status_code == 200, r.text
            assert "event: error" in r.text, f"跨组织复用会话必须拒绝并如实告知: {r.text[:500]}"
            assert "无权访问该会话" in r.text, (
                "错误必须来自 get_or_create_session 的组织拒绝，"
                f"而不是前置预检（LLM/策略）: {r.text[:500]}"
            )
            assert await _count_messages(factory, SESSION_A) == 1, (
                "跨组织 RAG 复用被拒绝后 A 组织会话不得新增消息"
            )
        finally:
            await engine.dispose()


# ── 5. WS 逐消息口径：严格只认库中主组织（有意不启用 token 回退）────────────


class TestWebSocketOrgSemantics:
    """锁定 WS 与 SSE 的有意差异（W0-3 复核结论，防止后续误改）。

    SSE RAG 分支使用有效组织（``get_user_org_id``，含已校验的 token 组织
    回退）；WS 逐消息只复查库中用户状态，不携带 token 组织，因此无主组织
    用户在 WS 上保持 fail-closed（``search_org_id``/``organization_id`` 均为
    None，由管线显式拒绝）。这是**有意的严格口径**：不放宽到未做消息级
    成员复查的 token 组织。
    """

    def test_ws_rag_uses_db_org_not_token_fallback(self, client, monkeypatch):
        from app.main import app
        from app.services.auth_service import auth_service
        from app.services.chat_service import RAGEvent

        monkeypatch.setattr(
            "app.core.ws_auth.auth_service.is_token_blacklisted",
            AsyncMock(return_value=False),
        )
        # 库里主组织为空，token 携带已校验的组织 B（握手会回源成员表）。
        token = auth_service.create_access_token(
            {"sub": "w03ws", "user_id": 1, "role": "user", "organization_id": ORG_B}
        )

        handshake_db = MagicMock()
        handshake_result = MagicMock()
        handshake_result.first = MagicMock(
            return_value=SimpleNamespace(id=1, is_active=True, organization_id=None)
        )
        handshake_db.execute = AsyncMock(return_value=handshake_result)
        handshake_db.close = AsyncMock()

        async def _override_get_db():
            yield handshake_db

        captured: dict = {}

        async def _fake_pipeline(**kwargs):
            captured.update(kwargs)
            kwargs["event_queue"].put_nowait(
                RAGEvent(type="message", content="ok", conversation_id="")
            )

        class _Ctx:
            async def __aenter__(self):
                return MagicMock()

            async def __aexit__(self, *exc):
                return False

        app.dependency_overrides[get_db] = _override_get_db
        try:
            with (
                patch(
                    "app.api.v1.endpoints.chat.load_active_ws_user",
                    new=AsyncMock(
                        return_value=SimpleNamespace(id=1, is_active=True, organization_id=None)
                    ),
                ),
                patch(
                    "app.api.v1.endpoints.chat.AsyncSessionLocal",
                    new=lambda: _Ctx(),
                ),
                patch(
                    "app.api.v1.endpoints.chat.run_rag_pipeline",
                    new=_fake_pipeline,
                ),
                client.websocket_connect("/api/v1/chat/ws", subprotocols=[f"auth.{token}"]) as ws,
            ):
                ws.send_json({"content": "你好"})
                payload = ws.receive_json()
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert payload.get("type") == "message", payload
        assert captured.get("search_org_id") is None, (
            "WS 不启用 token 组织回退：检索组织必须是库中主组织（None），"
            f"实际 {captured.get('search_org_id')!r}"
        )
        assert captured.get("organization_id") is None, (
            "WS 不启用 token 组织回退：会话组织必须是库中主组织（None），"
            f"实际 {captured.get('organization_id')!r}"
        )
