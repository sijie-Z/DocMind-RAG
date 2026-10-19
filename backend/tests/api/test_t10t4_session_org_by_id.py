"""T4 回归 —— 按 id 的会话访问补齐 org 口径（#416 / T4）。

背景
====
B2 的「org + user 可见范围」此前只落在两个会话列表端点；按 id 的详情/更新/
删除/清空/解绑/落库仍只按 user_id 过滤。多组织用户切到 B 组织后仍能按 id 读写
自己在 A 组织的会话；Agent 落库也可能把 B 组织的消息写进 A 组织会话行。

本文件用**真实 SQLite**（sqlite+aiosqlite 内存库）驱动 ASGI app（httpx
ASGITransport，同一事件循环），断言的是端点真实生成的查询/写入语义，而不是
mock 的调用顺序。

口径
====
- 非超管有组织：本人 + 本组织 + 历史 NULL 组织会话（迁移前遗留，属本人）；
- 非超管无组织（非超管）：只能触达本人历史 NULL 组织会话（fail-closed）；
- 超管：保留列表既有旁路（不按组织过滤，仍限本人）；
- 列表的 ``status != ENDED`` 不适用于按 id 读取（历史会话仍可打开）。

变异点（回退判别）
==================
- 去掉任一按 id 查询的 org 条件 → ``test_*_denied*`` 系列红；
- 只按 raw organization_id 解析（忽略 token_organization_id 回退）→
  ``test_token_fallback_*`` 红；
- 把列表的 status != ENDED 顺带加到详情读取 → ``test_ended_session_detail_readable`` 红；
- 把历史 NULL org 会话误伤（详情/落库）→ ``test_legacy_null_*`` 红；
- 允许跨 org 落库（save_session_interaction / get_or_create_session）→
  ``TestWritePathsRefuseCrossOrg`` 红；
- 超管不再旁路 → ``TestSuperuserBypass`` 红。
"""
from types import SimpleNamespace

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
from app.services.session_service import save_session_interaction

ORG_A = 7
ORG_B = 8

SESSION_A = "sess-org-a"
SESSION_B = "sess-org-b"
SESSION_LEGACY = "sess-legacy-null"
SESSION_FOREIGN = "sess-other-user"
SESSION_ENDED = "sess-ended"

AGENT_DETAIL = "/api/v1/agent/sessions/{sid}"
CHAT_DETAIL = "/api/v1/chat/conversations/{sid}"


# ── 夹具：真实 SQLite + ASGI 客户端 ─────────────────────────────────────────


async def _seeded_factory():
    """内存库：同一用户（id=1）在 A/B 两个组织的会话矩阵 + 他人 + 历史 NULL。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as s:
        s.add_all([
            Organization(id=ORG_A, name="Org A", owner_id=0),
            Organization(id=ORG_B, name="Org B", owner_id=0),
        ])
        s.add_all([
            ChatSession(
                id=SESSION_A, user_id=1, organization_id=ORG_A,
                title="A session", status=ChatSessionStatus.ACTIVE,
                settings={"bound_document_ids": ["doc-1"]},
            ),
            ChatSession(
                id=SESSION_B, user_id=1, organization_id=ORG_B,
                title="B session", status=ChatSessionStatus.ACTIVE,
            ),
            ChatSession(
                id=SESSION_LEGACY, user_id=1, organization_id=None,
                title="Legacy session", status=ChatSessionStatus.ACTIVE,
            ),
            ChatSession(
                id=SESSION_FOREIGN, user_id=2, organization_id=ORG_A,
                title="Other user's session", status=ChatSessionStatus.ACTIVE,
            ),
            ChatSession(
                id=SESSION_ENDED, user_id=1, organization_id=ORG_A,
                title="Ended session", status=ChatSessionStatus.ENDED,
            ),
        ])
        s.add_all([
            ChatMessage(
                id="msg-a-1", session_id=SESSION_A,
                content="org-a history", message_type=MessageType.USER,
            ),
            ChatMessage(
                id="msg-b-1", session_id=SESSION_B,
                content="org-b history", message_type=MessageType.USER,
            ),
            ChatMessage(
                id="msg-legacy-1", session_id=SESSION_LEGACY,
                content="legacy history", message_type=MessageType.USER,
            ),
            ChatMessage(
                id="msg-foreign-1", session_id=SESSION_FOREIGN,
                content="foreign history", message_type=MessageType.USER,
            ),
            ChatMessage(
                id="msg-ended-1", session_id=SESSION_ENDED,
                content="ended history", message_type=MessageType.USER,
            ),
        ])
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
        self._client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t10t4"
        )
        return self._client

    async def __aexit__(self, exc_type, exc, tb):
        await self._client.aclose()
        self._app.dependency_overrides.pop(get_current_user, None)
        self._app.dependency_overrides.pop(get_db, None)
        return False


async def _count(factory, model, session_id):
    async with factory() as s:
        stmt = select(func.count()).select_from(model)
        if model is ChatMessage:
            stmt = stmt.where(ChatMessage.session_id == session_id)
        else:
            stmt = stmt.where(ChatSession.id == session_id)
        return (await s.execute(stmt)).scalar() or 0


async def _get_session_row(factory, session_id):
    async with factory() as s:
        return await s.get(ChatSession, session_id)


# ── 1. 跨组织按 id 访问必须明确拒绝 ─────────────────────────────────────────


class TestCrossOrgByIdDenied:
    @pytest.mark.asyncio
    async def test_agent_detail_denied_after_org_switch(self):
        """用户切到 B 组织后，按 id 读取 A 组织会话 → 404。"""
        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(ORG_B), factory) as ac:
                r = await ac.get(AGENT_DETAIL.format(sid=SESSION_A))
            assert r.status_code == 404, r.text
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_agent_delete_denied_and_row_survives(self):
        """切到 B 后按 id 删除 A 组织会话 → 404，会话与消息都不得被删除。"""
        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(ORG_B), factory) as ac:
                r = await ac.delete(AGENT_DETAIL.format(sid=SESSION_A))
            assert r.status_code == 404, r.text
            assert await _get_session_row(factory, SESSION_A) is not None, (
                "跨组织删除被拒绝后 A 组织会话必须仍然存在"
            )
            assert await _count(factory, ChatMessage, SESSION_A) == 1, (
                "跨组织删除被拒绝后会话消息不得被清掉"
            )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_chat_detail_denied_after_org_switch(self):
        """chat 详情：切到 B 后按 id 读取 A 组织会话 → 404（不得静默返回正文）。"""
        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(ORG_B), factory) as ac:
                r = await ac.get(CHAT_DETAIL.format(sid=SESSION_A))
            assert r.status_code == 404, r.text
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_chat_update_denied_and_title_unchanged(self):
        """切到 B 后按 id 改 A 组织会话标题 → 404，标题保持原值。"""
        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(ORG_B), factory) as ac:
                r = await ac.put(
                    CHAT_DETAIL.format(sid=SESSION_A), json={"title": "hijacked"}
                )
            assert r.status_code == 404, r.text
            row = await _get_session_row(factory, SESSION_A)
            assert row is not None and row.title == "A session", (
                "跨组织更新被拒绝后不得留下任何写入"
            )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_chat_clear_denied_and_messages_survive(self):
        """切到 B 后清空 A 组织会话 → 404，历史消息不得被清掉。"""
        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(ORG_B), factory) as ac:
                r = await ac.delete(
                    f"{CHAT_DETAIL.format(sid=SESSION_A)}/clear"
                )
            assert r.status_code == 404, r.text
            assert await _count(factory, ChatMessage, SESSION_A) == 1
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_chat_delete_denied_and_row_survives(self):
        """切到 B 后按 id 删除 A 组织会话 → 404，会话与消息都保留。"""
        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(ORG_B), factory) as ac:
                r = await ac.delete(CHAT_DETAIL.format(sid=SESSION_A))
            assert r.status_code == 404, r.text
            assert await _get_session_row(factory, SESSION_A) is not None
            assert await _count(factory, ChatMessage, SESSION_A) == 1
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_chat_unbind_docs_denied_and_settings_unchanged(self):
        """切到 B 后解绑 A 组织会话的文档 → 403，绑定设置保持原值。"""
        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(ORG_B), factory) as ac:
                r = await ac.post(
                    f"{CHAT_DETAIL.format(sid=SESSION_A)}/unbind-docs"
                )
            assert r.status_code == 403, r.text
            row = await _get_session_row(factory, SESSION_A)
            assert row.settings.get("bound_document_ids") == ["doc-1"]
        finally:
            await engine.dispose()


# ── 2. 同组织 / 历史 NULL / 已结束会话仍可访问 ─────────────────────────────


class TestAllowedAccess:
    @pytest.mark.asyncio
    async def test_switch_back_to_org_a_allows_detail_and_update(self):
        """切回 A 组织：详情/更新恢复正常（不能把 org 条件做成永久拒绝）。"""
        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(ORG_A), factory) as ac:
                r1 = await ac.get(AGENT_DETAIL.format(sid=SESSION_A))
                r2 = await ac.get(CHAT_DETAIL.format(sid=SESSION_A))
                r3 = await ac.put(
                    CHAT_DETAIL.format(sid=SESSION_A), json={"title": "renamed"}
                )
            assert r1.status_code == 200, r1.text
            assert r2.status_code == 200, r2.text
            assert r3.status_code == 200, r3.text
            row = await _get_session_row(factory, SESSION_A)
            assert row.title == "renamed"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_legacy_null_session_readable_from_any_org(self):
        """历史 NULL org 会话属于本人：切到 B 后详情仍可读（不得误伤）。"""
        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(ORG_B), factory) as ac:
                r1 = await ac.get(AGENT_DETAIL.format(sid=SESSION_LEGACY))
                r2 = await ac.get(CHAT_DETAIL.format(sid=SESSION_LEGACY))
            assert r1.status_code == 200, r1.text
            assert r2.status_code == 200, r2.text
            assert r2.json()["success"] is True
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_ended_session_detail_readable(self):
        """按 id 读取不得继承列表的 status != ENDED 条件（历史会话仍可打开）。"""
        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(ORG_A), factory) as ac:
                r1 = await ac.get(AGENT_DETAIL.format(sid=SESSION_ENDED))
                r2 = await ac.get(CHAT_DETAIL.format(sid=SESSION_ENDED))
            assert r1.status_code == 200, r1.text
            assert r2.status_code == 200, r2.text
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_other_users_session_still_hidden(self):
        """user 条件不放松：同组织其他用户的会话仍不可访问。"""
        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(ORG_A), factory) as ac:
                r1 = await ac.get(AGENT_DETAIL.format(sid=SESSION_FOREIGN))
                r2 = await ac.get(CHAT_DETAIL.format(sid=SESSION_FOREIGN))
            assert r1.status_code == 404, r1.text
            # chat 详情对“不存在/他人”保留既有软失败形状（不得泄露内容）
            assert r2.status_code == 200
            assert r2.json()["success"] is False
            assert "foreign history" not in r2.text
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_list_and_by_id_visibility_agree_after_switch(self):
        """列表与按 id 行为一致：B 组织列表可见的会话可按 id 打开，
        列表不可见（A 组织）的会话按 id 也必须拒绝。"""
        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(ORG_B), factory) as ac:
                r_agent_list = await ac.get("/api/v1/agent/sessions")
                r_chat_list = await ac.get("/api/v1/chat/conversations")
                assert r_agent_list.status_code == 200, r_agent_list.text
                assert r_chat_list.status_code == 200, r_chat_list.text
                agent_ids = {
                    s["id"] for s in r_agent_list.json()["data"]["sessions"]
                }
                chat_ids = {
                    s["id"] for s in r_chat_list.json()["data"]["data"]
                }
                assert agent_ids == chat_ids == {SESSION_B, SESSION_LEGACY}

                visible = await ac.get(AGENT_DETAIL.format(sid=SESSION_B))
                hidden = await ac.get(AGENT_DETAIL.format(sid=SESSION_A))
            assert visible.status_code == 200
            assert hidden.status_code == 404
        finally:
            await engine.dispose()


# ── 3. 有效组织解析（token 回退 / 无组织非超管 / 超管旁路）─────────────────


class TestEffectiveOrgResolution:
    @pytest.mark.asyncio
    async def test_token_fallback_org_used_for_by_id_access(self):
        """库中 org=None、token 组织=B：按有效组织 B 过滤（不是 raw None）。

        变异：改用裸 current_user.organization_id → org 条件缺失 → 本用例红。
        """
        engine, factory = await _seeded_factory()
        try:
            user = _user(None, token_org=ORG_B)
            async with _AsyncClientCtx(user, factory) as ac:
                allowed = await ac.get(AGENT_DETAIL.format(sid=SESSION_B))
                denied = await ac.get(AGENT_DETAIL.format(sid=SESSION_A))
            assert allowed.status_code == 200, allowed.text
            assert denied.status_code == 404, denied.text
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_orgless_non_superuser_only_reaches_legacy_null(self):
        """无组织非超管：只能读到本人历史 NULL 组织会话，不能触达任何带组织行。"""
        engine, factory = await _seeded_factory()
        try:
            async with _AsyncClientCtx(_user(None), factory) as ac:
                agent_legacy = await ac.get(AGENT_DETAIL.format(sid=SESSION_LEGACY))
                agent_tagged = await ac.get(AGENT_DETAIL.format(sid=SESSION_A))
                chat_legacy = await ac.get(CHAT_DETAIL.format(sid=SESSION_LEGACY))
                chat_tagged = await ac.get(CHAT_DETAIL.format(sid=SESSION_A))
            assert agent_legacy.status_code == 200, agent_legacy.text
            assert agent_tagged.status_code == 404, agent_tagged.text
            assert chat_legacy.status_code == 200, chat_legacy.text
            assert chat_tagged.status_code == 404, chat_tagged.text
        finally:
            await engine.dispose()


class TestSuperuserBypass:
    @pytest.mark.asyncio
    async def test_superuser_reads_and_updates_own_sessions_across_orgs(self):
        """超管保留既有旁路：不按组织过滤（仍限本人），读写均可。"""
        engine, factory = await _seeded_factory()
        try:
            user = _user(None, superuser=True)
            async with _AsyncClientCtx(user, factory) as ac:
                r1 = await ac.get(AGENT_DETAIL.format(sid=SESSION_A))
                r2 = await ac.get(CHAT_DETAIL.format(sid=SESSION_A))
                r3 = await ac.put(
                    CHAT_DETAIL.format(sid=SESSION_A), json={"title": "su-renamed"}
                )
                r4 = await ac.get(AGENT_DETAIL.format(sid=SESSION_FOREIGN))
            assert r1.status_code == 200, r1.text
            assert r2.status_code == 200, r2.text
            assert r3.status_code == 200, r3.text
            assert r4.status_code == 404, "超管旁路也只限本人会话，不得读他人会话"
            row = await _get_session_row(factory, SESSION_A)
            assert row.title == "su-renamed"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_superuser_save_into_visible_cross_org_session_allowed(self):
        """超管旁路同样覆盖写路径：可见即可写（仍然限本人会话）。"""
        engine, factory = await _seeded_factory()
        try:
            out = await save_session_interaction(
                SESSION_A, 1, "q", ORG_B, "answer",
                session_factory=factory, bypass_org=True,
            )
            assert out is not None, "超管旁路下不得因组织不一致拒绝落库"
            assert await _count(factory, ChatMessage, SESSION_A) == 3  # 原 1 + 新 2
        finally:
            await engine.dispose()


# ── 4. 写路径：跨 org 落库必须明确拒绝 ─────────────────────────────────────


class TestWritePathsRefuseCrossOrg:
    @pytest.mark.asyncio
    async def test_save_session_interaction_refuses_cross_org(self):
        """目标会话 org=A，本次运行 org=B → 拒绝写入（不落任何消息）。"""
        engine, factory = await _seeded_factory()
        try:
            with pytest.raises(AuthorizationError):
                await save_session_interaction(
                    SESSION_A, 1, "q", ORG_B, "answer", session_factory=factory
                )
            assert await _count(factory, ChatMessage, SESSION_A) == 1, (
                "跨组织落库被拒绝后不得新增任何消息"
            )
            row = await _get_session_row(factory, SESSION_A)
            assert row.organization_id == ORG_A
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_save_session_interaction_refuses_unresolved_org_into_tagged(self):
        """有效组织无法解析（None）而目标会话带组织标记 → 同样拒绝。"""
        engine, factory = await _seeded_factory()
        try:
            with pytest.raises(AuthorizationError):
                await save_session_interaction(
                    SESSION_A, 1, "q", None, "answer", session_factory=factory
                )
            assert await _count(factory, ChatMessage, SESSION_A) == 1
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_save_session_interaction_heals_legacy_null_org(self):
        """历史 NULL org 会话不得误伤：本次运行 org=B → 自愈并正常落库。"""
        engine, factory = await _seeded_factory()
        try:
            out = await save_session_interaction(
                SESSION_LEGACY, 1, "q", ORG_B, "answer", session_factory=factory
            )
            assert out is not None
            row = await _get_session_row(factory, SESSION_LEGACY)
            assert row.organization_id == ORG_B
            assert await _count(factory, ChatMessage, SESSION_LEGACY) == 3  # 原 1 + 新 2
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_rag_get_or_create_session_refuses_cross_org(self):
        """RAG 链路按 id 复用会话：跨组织必须拒绝，不得把消息写进 A 组织会话。"""
        engine, factory = await _seeded_factory()
        try:
            async with factory() as s:
                with pytest.raises(AuthorizationError):
                    await get_or_create_session(
                        s, SESSION_A, user_id=1, title="q", organization_id=ORG_B
                    )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_rag_get_or_create_session_allows_legacy_null(self):
        """历史 NULL org 会话：RAG 链路仍可复用（不得误伤）。"""
        engine, factory = await _seeded_factory()
        try:
            async with factory() as s:
                sid, _ = await get_or_create_session(
                    s, SESSION_LEGACY, user_id=1, title="q", organization_id=ORG_B
                )
            assert sid == SESSION_LEGACY
        finally:
            await engine.dispose()


# ── 5. Agent 历史加载：不得把其他组织的会话历史喂给本轮运行 ────────────────


class TestAgentHistoryLoadOrgScope:
    @staticmethod
    def _fake_agent(monkeypatch, captured):
        from app.agent.service import agent_service

        async def _chat(**kwargs):
            captured["history"] = kwargs.get("history")
            yield SimpleNamespace(
                type="done", content="",
                to_sse_dict=lambda: {"type": "done", "content": ""},
            )

        monkeypatch.setattr(agent_service, "chat", _chat)

    @pytest.mark.asyncio
    async def test_agent_chat_does_not_load_history_from_other_org(
        self, monkeypatch
    ):
        """切到 B 后用 A 组织会话 id 发起 /agent/chat：历史必须为空。"""
        import app.api.v1.endpoints.agent as agent_module

        engine, factory = await _seeded_factory()
        try:
            captured: dict = {}
            self._fake_agent(monkeypatch, captured)
            monkeypatch.setattr(agent_module, "AsyncSessionLocal", factory)
            async with _AsyncClientCtx(_user(ORG_B), factory) as ac:
                r = await ac.post(
                    "/api/v1/agent/chat",
                    json={"query": "hi", "session_id": SESSION_A},
                )
            assert r.status_code == 200, r.text
            assert captured["history"] == [], (
                "不得把 A 组织会话的历史加载进 B 组织的运行上下文"
            )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_agent_chat_loads_history_from_current_org(self, monkeypatch):
        """对照：当前组织会话的历史正常加载（org 过滤不得误伤正常路径）。"""
        import app.api.v1.endpoints.agent as agent_module

        engine, factory = await _seeded_factory()
        try:
            captured: dict = {}
            self._fake_agent(monkeypatch, captured)
            monkeypatch.setattr(agent_module, "AsyncSessionLocal", factory)
            async with _AsyncClientCtx(_user(ORG_A), factory) as ac:
                r = await ac.post(
                    "/api/v1/agent/chat",
                    json={"query": "hi", "session_id": SESSION_A},
                )
            assert r.status_code == 200, r.text
            assert captured["history"] == [
                {"role": "user", "content": "org-a history"}
            ], f"同组织历史应正常加载: {captured['history']}"
        finally:
            await engine.dispose()
