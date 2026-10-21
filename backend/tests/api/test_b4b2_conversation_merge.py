"""B2 · 会话入口合并（决策 D3）—— 先红后绿判别测试。

覆盖两件事：

1. **可见范围一致**：`/chat/conversations` 与 `/agent/sessions` 是同一个
   ChatSession 表的两个列表入口，必须使用同一套「org + user」过滤：
   - 非超管（有组织）：本人 + 本组织 + 历史 NULL 组织会话（迁移前遗留）；
   - 非超管（无组织）：A2 语义 fail-closed → 403；
   - 超管：保留旁路（不按组织过滤，仍限本人）——与 A2 `_resolve_prompt_org`
     同口径。
   修前现状：agent 侧只按 user 过滤，org B 的会话会出现在 org A 用户的列表里，
   同一会话在两入口的可见集合不一致（本文件先红）。

   判别方式不是字符串比对：把两个端点真实生成的 SELECT 语句原样放到一个
   预置了跨组织数据的 SQLite 库上执行，比较两入口实际命中的会话 id 集合。

2. **聊天 Agent 模式持久化**：`/chat/stream` 的 useAgent 分支此前只把回答流给
   前端、从不落库，刷新后本轮问答凭空消失。修复后走完 PER 必须调用统一会话
   落库服务（与 /agent/chat 同一实现），且未携带 conversationId 时要生成并
   回传会话 id（否则刷新后无法定位本轮会话）。

变异点（回退判别）：
- 删掉 agent 列表的 org 过滤 → `test_same_session_visible_in_both_entries` 红；
- 把 chat 列表改回 raw organization_id（不做 A2 resolved）→ 超管用例红；
- 删掉 chat.py 的 save_session_interaction 调用 → 持久化用例红；
- 去掉「无 conversationId 时生成 uuid 并回传」→ 新会话用例红。
"""

import contextlib
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus

SSE_URL = "/api/v1/chat/stream"
CHAT_LIST_URL = "/api/v1/chat/conversations"
AGENT_LIST_URL = "/api/v1/agent/sessions"


# ── 公共夹具 ────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


@pytest.fixture
def app_overrides():
    from app.main import app

    def _install(user, db):
        async def _user():
            return user

        async def _db():
            yield db

        app.dependency_overrides[get_current_user] = _user
        app.dependency_overrides[get_db] = _db

    yield _install
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


class _CaptureDB:
    """记录端点生成的每条语句；查询结果给空集（我们只关心语句本身）。"""

    def __init__(self):
        self.statements = []

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        result = MagicMock()
        result.scalar.return_value = 0
        result.scalar_one_or_none.return_value = None
        scalars = MagicMock()
        scalars.all.return_value = []
        result.scalars.return_value = scalars
        result.all.return_value = []
        return result

    async def close(self):
        return None


def _user(user_id=1, org_id=7, is_superuser=False):
    return SimpleNamespace(
        id=user_id,
        organization_id=org_id,
        is_superuser=is_superuser,
        role="user",
        is_active=True,
        username=f"user{user_id}",
    )


def _seeded_sqlite():
    """ChatSession 表的真实 SQLite 副本：跨组织 + 跨用户 + 历史 NULL + 已结束。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ChatSession.__table__.create(engine)
    ChatMessage.__table__.create(engine)
    rows = [
        # 当前用户（id=1）：org 7 / org 8 / 历史 NULL
        {
            "id": "own-org7",
            "user_id": 1,
            "organization_id": 7,
            "title": "own org7",
            "status": ChatSessionStatus.ACTIVE,
        },
        {
            "id": "own-org8",
            "user_id": 1,
            "organization_id": 8,
            "title": "own org8",
            "status": ChatSessionStatus.ACTIVE,
        },
        {
            "id": "own-null",
            "user_id": 1,
            "organization_id": None,
            "title": "own legacy",
            "status": ChatSessionStatus.ACTIVE,
        },
        # 同组织其他用户：必须被 user 条件排除
        {
            "id": "other-org7",
            "user_id": 2,
            "organization_id": 7,
            "title": "other org7",
            "status": ChatSessionStatus.ACTIVE,
        },
        # 本人已结束会话：两入口都以 status != ENDED 排除
        {
            "id": "own-ended",
            "user_id": 1,
            "organization_id": 7,
            "title": "ended",
            "status": ChatSessionStatus.ENDED,
        },
    ]
    with engine.begin() as conn:
        conn.execute(ChatSession.__table__.insert(), rows)
    return engine


def _captured_list_statement(statements):
    """从一批语句里挑出会话列表 SELECT（select(ChatSession) + LIMIT）。"""
    candidates = []
    for stmt in statements:
        text = str(stmt)
        if "chat_sessions" not in text:
            continue
        if getattr(stmt, "_limit_clause", None) is None:
            continue
        # count/subquery 没有 LIMIT；带 join 的消息查询出现在列表之后。
        if "JOIN" in text.upper():
            continue
        candidates.append(stmt)
    assert len(candidates) == 1, f"应恰好捕获一条列表语句，实际 {len(candidates)}"
    return candidates[0]


def _visible_ids(engine, stmt):
    with Session(engine) as session:
        return {row.id for row in session.execute(stmt).scalars().all()}


def _collect_visible_ids(client, app_overrides, user):
    db = _CaptureDB()
    app_overrides(user, db)
    chat_resp = client.get(CHAT_LIST_URL)
    chat_stmt = _captured_list_statement(db.statements) if chat_resp.status_code == 200 else None

    db.statements.clear()
    agent_resp = client.get(AGENT_LIST_URL)
    agent_stmt = _captured_list_statement(db.statements) if agent_resp.status_code == 200 else None
    return chat_resp, chat_stmt, agent_resp, agent_stmt


# ── 1. 可见范围一致 ────────────────────────────────────────────────────────


class TestSessionVisibilityConsistency:
    def test_same_session_visible_in_both_entries(self, client, app_overrides):
        """有组织用户：两入口实际可见集合一致，且 = 本人 org7 + 历史 NULL。"""
        engine = _seeded_sqlite()
        chat_resp, chat_stmt, agent_resp, agent_stmt = _collect_visible_ids(
            client, app_overrides, _user(org_id=7)
        )
        assert chat_resp.status_code == 200, chat_resp.text
        assert agent_resp.status_code == 200, agent_resp.text

        chat_ids = _visible_ids(engine, chat_stmt)
        agent_ids = _visible_ids(engine, agent_stmt)

        expected = {"own-org7", "own-null"}
        assert chat_ids == expected, f"chat 列表可见范围错误: {chat_ids}"
        assert agent_ids == expected, (
            f"agent 列表可见范围错误: {agent_ids}（修复前只按 user 过滤，"
            "跨 org 会话 own-org8 会泄漏）"
        )
        assert chat_ids == agent_ids, "同一会话必须在两个入口可见性一致"

    def test_user_without_org_is_rejected_by_both_entries(self, client, app_overrides):
        """A2 语义：非超管无组织 → 两入口都 fail-closed 403（不得一边 200）。"""
        db = _CaptureDB()
        app_overrides(_user(org_id=None), db)
        chat_resp = client.get(CHAT_LIST_URL)
        agent_resp = client.get(AGENT_LIST_URL)
        assert chat_resp.status_code == 403, chat_resp.text
        assert agent_resp.status_code == 403, agent_resp.text

    def test_superuser_bypass_keeps_all_own_sessions(self, client, app_overrides):
        """超管旁路：不按组织过滤，但 user 条件仍在；两入口集合仍一致。"""
        engine = _seeded_sqlite()
        chat_resp, chat_stmt, agent_resp, agent_stmt = _collect_visible_ids(
            client, app_overrides, _user(org_id=None, is_superuser=True)
        )
        assert chat_resp.status_code == 200, chat_resp.text
        assert agent_resp.status_code == 200, agent_resp.text

        chat_ids = _visible_ids(engine, chat_stmt)
        agent_ids = _visible_ids(engine, agent_stmt)
        expected = {"own-org7", "own-org8", "own-null"}
        assert chat_ids == expected, f"超管 chat 可见集合: {chat_ids}"
        assert agent_ids == expected, f"超管 agent 可见集合: {agent_ids}"


# ── 2. 聊天 Agent 模式持久化 ────────────────────────────────────────────────


def _sse_events(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        with contextlib.suppress(json.JSONDecodeError):
            events.append(json.loads(line[len("data: ") :]))
    return events


def _fake_agent_chat(chunks):
    async def _chat(**kwargs):
        for piece in chunks:
            yield SimpleNamespace(type="chunk", content=piece)
        yield SimpleNamespace(type="done", content="")

    return _chat


def _install_fake_agent(monkeypatch, chunks):
    from app.agent.service import agent_service
    from app.api.v1.endpoints import agent as agent_module

    async def _no_history(*args, **kwargs):
        return []

    # 测试不许触碰真实 DB：聊天 Agent 分支的多轮历史加载同样走桩。
    monkeypatch.setattr(agent_module, "_load_session_messages", _no_history)
    monkeypatch.setattr(agent_service, "chat", _fake_agent_chat(chunks))


class TestChatAgentModePersistence:
    def test_agent_answer_is_persisted_for_reload(self, client, app_overrides, monkeypatch):
        """useAgent 分支走完 PER 后必须调用统一落库服务（先红：从未调用）。"""
        import app.api.v1.endpoints.chat as chat_module

        _install_fake_agent(monkeypatch, ["你好", "，世界"])
        calls = []

        async def _fake_save(
            session_id, user_id, query, organization_id, assistant_response="", **kwargs
        ):
            calls.append(
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "query": query,
                    "organization_id": organization_id,
                    "assistant_response": assistant_response,
                }
            )
            return "assistant-msg-id"

        monkeypatch.setattr(chat_module, "save_session_interaction", _fake_save, raising=False)
        app_overrides(_user(org_id=7), _CaptureDB())

        resp = client.post(
            SSE_URL,
            json={"content": "帮我分析", "conversationId": "conv-b4b2", "useAgent": True},
        )
        assert resp.status_code == 200, resp.text
        assert "event: message" in resp.text
        assert calls == [
            {
                "session_id": "conv-b4b2",
                "user_id": 1,
                "query": "帮我分析",
                "organization_id": 7,
                "assistant_response": "你好，世界",
            }
        ], "聊天 Agent 模式没有把本轮问答交给统一落库服务"

    def test_new_conversation_id_is_generated_and_echoed(self, client, app_overrides, monkeypatch):
        """未带 conversationId：生成 uuid、随流回传、并用同一 id 落库。"""
        import app.api.v1.endpoints.chat as chat_module

        _install_fake_agent(monkeypatch, ["答"])
        calls = []

        async def _fake_save(
            session_id, user_id, query, organization_id, assistant_response="", **kwargs
        ):
            calls.append(session_id)
            return

        monkeypatch.setattr(chat_module, "save_session_interaction", _fake_save, raising=False)
        app_overrides(_user(org_id=7), _CaptureDB())

        resp = client.post(SSE_URL, json={"content": "新问题", "useAgent": True})
        assert resp.status_code == 200, resp.text
        events = _sse_events(resp.text)
        echoed = {e.get("conversationId") for e in events if e.get("conversationId")}
        assert echoed, f"新会话必须把生成的 conversationId 回传给前端: {events}"
        assert len(echoed) == 1
        generated = next(iter(echoed))
        uuid.UUID(str(generated))  # 必须是合法 uuid，刷新后可直接按 id 取会话
        assert calls == [generated], "落库使用的会话 id 必须与回传前端的一致"

    def test_save_failure_is_reported_but_stream_completes(
        self, client, app_overrides, monkeypatch
    ):
        """落库失败不得吞掉：回答照常完成 + warning 事件如实告知（与 /agent 同口径）。"""
        import app.api.v1.endpoints.chat as chat_module

        _install_fake_agent(monkeypatch, ["答"])

        async def _boom(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(chat_module, "save_session_interaction", _boom, raising=False)
        app_overrides(_user(org_id=7), _CaptureDB())

        resp = client.post(
            SSE_URL,
            json={"content": "问题", "conversationId": "conv-fail", "useAgent": True},
        )
        assert resp.status_code == 200
        events = _sse_events(resp.text)
        assert "event: warning" in resp.text, f"落库失败必须有 warning 事件: {events}"
        assert any("保存失败" in str(e.get("content", "")) for e in events), (
            f"warning 事件必须如实说明落库失败: {events}"
        )
        assert "event: message" in resp.text, "回答本身必须照常完成"

    @pytest.mark.asyncio
    async def test_persisted_messages_survive_reload_through_service(
        self, client, app_overrides, monkeypatch
    ):
        """端到端语义：把端点落库参数交给真实统一服务写库，随后可读回两条消息。

        用假 AsyncSession 复现真实服务对 chat_sessions/chat_messages 的读写，
        证明「刷新后本轮问答仍在」——不是只断言函数被调用。
        """
        import app.api.v1.endpoints.chat as chat_module
        from app.services.session_service import save_session_interaction

        _install_fake_agent(monkeypatch, ["回答正文"])
        captured = {}

        async def _fake_save(
            session_id, user_id, query, organization_id, assistant_response="", **kwargs
        ):
            captured.update(
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "query": query,
                    "organization_id": organization_id,
                    "assistant_response": assistant_response,
                }
            )
            return "mid"

        monkeypatch.setattr(chat_module, "save_session_interaction", _fake_save, raising=False)
        app_overrides(_user(org_id=7), _CaptureDB())
        resp = client.post(
            SSE_URL,
            json={"content": "持久化问题", "conversationId": "conv-persist", "useAgent": True},
        )
        assert resp.status_code == 200, resp.text
        assert captured, "端点没有调用落库服务"

        # 用真实服务实现 + 假会话（复用现有 S6 口径：按 id+user 校验归属）
        fake_session = MagicMock()
        existing = MagicMock()
        existing.user_id = 1
        existing.title = "Existing"
        existing.organization_id = 7
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=existing)
        fake_session.execute = AsyncMock(return_value=result)
        fake_session.add = MagicMock()
        fake_session.commit = AsyncMock()

        class _Ctx:
            async def __aenter__(self):
                return fake_session

            async def __aexit__(self, *exc):
                return False

        await save_session_interaction(
            captured["session_id"],
            captured["user_id"],
            captured["query"],
            captured["organization_id"],
            captured["assistant_response"],
            session_factory=lambda: _Ctx(),
        )

        added = [call.args[0] for call in fake_session.add.call_args_list]
        messages = [obj for obj in added if isinstance(obj, ChatMessage)]
        assert [m.message_type.value for m in messages] == ["user", "assistant"]
        assert [m.content for m in messages] == ["持久化问题", "回答正文"]
        assert all(m.session_id == "conv-persist" for m in messages)
        fake_session.commit.assert_awaited_once()
