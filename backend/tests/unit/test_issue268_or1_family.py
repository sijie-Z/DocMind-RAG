"""issue #268 回归：「organization_id or 1」家族残余去伪造 + monitoring 活口。

#257 合并后的对抗评审（`_orchestrate/round2/review_iso257.md` §2.6）逐处裁定出
五处**真泄漏**（chat WS/SSE RAG、workflow 端点+引擎、demo 写入、MinIO 前缀白名单）
与两处跟进（memory 命名空间标签错、feedback lesson 命名空间不一致致静默失效）。
本文件钉住修复后的行为：

1. **chat RAG 链路（WS + SSE）**：无组织用户此前被 `search_org_id … or 1` 静默
   放进 1 号组织（语义缓存 `org:1:` 命名空间 + retriever 的 org term 双重命中）。
   现在如实传 None，`run_rag_pipeline` 显式拒绝：不检索任何组织、不读写任何
   （含 org 1 的）缓存命名空间、不落组织归属为空的会话与消息。WS 与 SSE 同一语义。
2. **workflow 端点**：引擎拿到的组织必须是调用者的真实值（None-able）——仅改端点
   不够的引擎侧在 `test_rag_fix_org_scope.py` 里钉（缺组织显式失败）。
3. **demo 写路径**：示例数据归属「用户自己的组织」；无组织显式 403，绝不写进
   1 号组织命名空间。
4. **MinIO 前缀白名单**：无组织用户不再被算作 1 号组织的阅读者（`documents/1/…`、
   `1/…` 直接 403）。
5. **agent 记忆命名空间**：memory API 用真实 org/user 键；feedback lesson 与
   agent 侧读侧同一命名空间（修前写 `1:…` 读 `None:…`，lesson 静默失效）。
6. **monitoring `/admin-summary`**：补上 VIEW_SYSTEM_HEALTH 权限门；无组织的非超管
   显式 403（修前任何登录用户直接拿全库 total_users/total_documents/total_sessions）；
   作用域计数按组织过滤，全系统组织数仅超管可见。

追加（issue #277，同一评审 review_or1268 的残余收账）：

7. **/agent/chat 会话 org 对称性（R2）**：会话落库 org、运行解析 org、feedback
   lesson 命名空间 org 三处统一为 require_organization 的 resolved org（含
   token_organization_id 回退 / 无组织超管回源）；历史遗留会话（org=None）在
   下一次运行落库时自愈。判别：回退链用户的 lesson 必须能被运行侧同键加载。
8. **/llm-stats 权限门（R3）**：全局 token/成本聚合从「仅登录」升级为
   VIEW_SYSTEM_HEALTH（前端唯一消费方 dashboard/admin.vue 仅对 admin 渲染）。
9. **token 回退路径直接用例（R6）**：/files 代理对「库 org=None、令牌 org=9」
   的用户按回退 org 授权（自己的 9 放行、其它组织 403），钉住 #268 引入的
   `get_user_org_id` 回退写法（此前仅代码推导，无直接用例）。

DB 侧与既有用例同风格：能真跑就真跑（内存 SQLite 真建表），外部依赖（语义缓存、
检索、ES、MinIO、embedding）用「触碰即炸 / 记录调用」的假对象 —— 断言的是
「1 号组织数据/缓存零出现」，而不是假对象的善意。
"""
import asyncio
import io
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.security import get_current_user, optional_current_user
from app.models import Base, Organization, User
from app.models.chat import ChatMessage, ChatSession, MessageType
from app.models.document import Document, DocumentType

REFUSAL_TEXT = "当前用户未加入任何组织"


class _ExplodingSemanticCache:
    """触碰即炸的语义缓存：无组织路径必须连缓存都不碰（含 org:1: 命名空间）。"""

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


def _mock_session() -> MagicMock:
    """足以穿过 run_rag_pipeline 前置流程的假会话（真拒绝时其实一步都用不到）。"""
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    hist = MagicMock()
    hist.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=hist)
    return session


def _orgless_user(user_id: int = 1) -> SimpleNamespace:
    """无组织用户：``organization_id=None`` 且无 token 组织回退。"""
    return SimpleNamespace(
        id=user_id,
        organization_id=None,
        is_superuser=False,
        role="user",
        is_active=True,
    )


def _override_http(user=None, db=None, optional_user=False):
    """为一个 HTTP 调用挂上 get_db / get_current_user 覆盖；返回清理函数。"""
    from app.main import app

    async def _db():
        return db if db is not None else MagicMock()

    async def _user():
        return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user
    if optional_user:
        app.dependency_overrides[optional_current_user] = _user

    def _cleanup():
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        if optional_user:
            app.dependency_overrides.pop(optional_current_user, None)

    return _cleanup


# ══════════════════════════════════════════════════════════════════════════
# 1 · chat RAG 链路 —— 无组织显式拒绝，1 号组织的检索/缓存零触碰
# ══════════════════════════════════════════════════════════════════════════
class TestChatRagNoOrgSemantics:
    @pytest.mark.asyncio
    async def test_rag_pipeline_no_org_refuses_without_cache_or_retrieval_touch(self):
        """链路层：search_org_id=None → 发出显式拒绝事件；缓存/检索/落库全部零触碰。

        变异验证：把函数体顶部的 `if search_org_id is None: … return` 去掉 →
        流程继续、语义缓存被触碰 → 本用例红。
        """
        from app.services.chat_service import run_rag_pipeline

        session = _mock_session()
        queue: asyncio.Queue = asyncio.Queue()

        with (
            patch(
                "app.services.chat_service._semantic_cache",
                new=_ExplodingSemanticCache(),
            ),
            patch(
                "app.services.chat_service.rag_service.get_embedding",
                new=AsyncMock(return_value=[0.1] * 8),
            ),
            patch(
                "app.services.chat_service.rag_service.search_knowledge_base",
                new=AsyncMock(side_effect=AssertionError("kb-search-touched")),
            ),
        ):
            await run_rag_pipeline(
                session=session,
                user_content="报销流程是什么",
                user_id=1,
                search_org_id=None,
                organization_id=None,
                conversation_id=None,
                file_ids=[],
                strict_mode=False,
                privacy_mode=True,
                event_queue=queue,
            )

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        assert [e.type for e in events] == ["error"], "无组织必须发出显式拒绝事件"
        assert REFUSAL_TEXT in events[0].content
        assert session.add.call_count == 0, "拒绝先于一切落库：不得创建会话/消息"

    def test_chat_sse_rag_stream_no_org_user_refused_explicitly(self, client: TestClient):
        """SSE 端点：无组织用户走非 agent（RAG）分支 → 显式拒绝事件（非空转/非空结果）。

        变异验证：`else None` 改回 `else 1`（或恢复 `or 1`）→ 管线被放行 →
        语义缓存被触碰 → 响应里再也看不到拒绝消息 → 本用例红。
        """
        db_user = SimpleNamespace(organization_id=None)  # 无组织用户的库中状态
        session = _mock_session()
        session.get = AsyncMock(return_value=db_user)

        user = _orgless_user()
        _cleanup = _override_http(user=user)
        try:
            with (
                patch("app.api.v1.endpoints.chat.AsyncSessionLocal", new=lambda: _AsyncCtx(session)),
                patch(
                    "app.services.chat_service._semantic_cache",
                    new=_ExplodingSemanticCache(),
                ),
                patch(
                    "app.services.chat_service.rag_service.get_embedding",
                    new=AsyncMock(return_value=[0.1] * 8),
                ),
                patch(
                    "app.services.chat_service.rag_service.search_knowledge_base",
                    new=AsyncMock(side_effect=AssertionError("kb-search-touched")),
                ),
            ):
                r = client.post("/api/v1/chat/stream", json={"content": "你好"})
        finally:
            _cleanup()

        assert r.status_code == 200, r.text
        assert "event: error" in r.text, f"必须发出显式拒绝事件，实际流: {r.text[:400]}"
        assert REFUSAL_TEXT in r.text, f"拒绝文案缺失（可能被静默吞掉）: {r.text[:400]}"
        assert "服务器处理出错" not in r.text, "拒绝是预期行为，不是服务器错误"

    def test_chat_ws_rag_no_org_user_refused_explicitly(self, client: TestClient, monkeypatch):
        """WS 端点：无组织用户在 RAG 会话中收到显式拒绝（与 SSE 同一语义）。

        变异验证：`user_state.organization_id or 1` 恢复原样 → 管线被放行 →
        客户端收到的是处理出错/无拒绝文案 → 本用例红。
        """
        from app.main import app
        from app.services.auth_service import auth_service

        monkeypatch.setattr(
            "app.core.ws_auth.auth_service.is_token_blacklisted",
            AsyncMock(return_value=False),
        )
        token = auth_service.create_access_token(
            {"sub": "or1268", "user_id": 1, "role": "user"}
        )

        # 握手阶段的假会话：_load_user_state 的 db.execute(...).first() 命中一行（无组织）
        handshake_db = MagicMock()
        handshake_result = MagicMock()
        handshake_result.first = MagicMock(
            return_value=SimpleNamespace(id=1, is_active=True, organization_id=None)
        )
        handshake_db.execute = AsyncMock(return_value=handshake_result)
        handshake_db.close = AsyncMock()

        async def _override_get_db():
            yield handshake_db

        session = _mock_session()
        app.dependency_overrides[get_db] = _override_get_db
        try:
            with (
                patch(
                    "app.api.v1.endpoints.chat.load_active_ws_user",
                    new=AsyncMock(
                        return_value=SimpleNamespace(id=1, is_active=True, organization_id=None)
                    ),
                ),
                patch("app.api.v1.endpoints.chat.AsyncSessionLocal", new=lambda: _AsyncCtx(session)),
                patch(
                    "app.services.chat_service._semantic_cache",
                    new=_ExplodingSemanticCache(),
                ),
                patch(
                    "app.services.chat_service.rag_service.get_embedding",
                    new=AsyncMock(return_value=[0.1] * 8),
                ),
                patch(
                    "app.services.chat_service.rag_service.search_knowledge_base",
                    new=AsyncMock(side_effect=AssertionError("kb-search-touched")),
                ),
                client.websocket_connect(
                    "/api/v1/chat/ws", subprotocols=[f"auth.{token}"]
                ) as ws,
            ):
                ws.send_json({"content": "你好"})
                payload = ws.receive_json()
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert payload.get("type") == "error", f"必须收到显式拒绝，实际: {payload}"
        assert REFUSAL_TEXT in (payload.get("content") or ""), (
            f"拒绝文案缺失（WS 与 SSE 必须同语义）: {payload}"
        )


# ══════════════════════════════════════════════════════════════════════════
# 2 · workflow 端点 —— 引擎拿到真实组织（None-able），不是伪造的 1
# ══════════════════════════════════════════════════════════════════════════
class TestWorkflowExecuteOrgDerivation:
    def test_workflow_execute_no_org_passes_none_to_engine(self, client: TestClient):
        """无组织用户执行工作流：引擎应以 organization_id=None 创建（不伪造 1）。

        引擎侧「缺组织显式失败」在 test_rag_fix_org_scope.py 钉住（评审已证
        只改端点层无效、须同修）；本用例钉端点的推导值本身。

        变异验证：`effective_org_id = get_user_org_id(...)` 改回 `… or 1` → 本用例红。
        """
        user = _orgless_user(user_id=5)
        workflow = SimpleNamespace(
            id=7,
            name="测试工作流",
            is_active=True,
            created_by=5,
            flow_data={
                "nodes": [
                    {"id": "n1", "type": "tool_search", "data": {}, "position": {"x": 0, "y": 0}}
                ],
                "edges": [],
            },
        )
        db = MagicMock()
        lookup = MagicMock()
        lookup.scalar_one_or_none = MagicMock(return_value=workflow)
        db.execute = AsyncMock(return_value=lookup)
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        captured: dict = {}

        class _FakeEngine:
            def __init__(self, config, organization_id=None, **kwargs):
                captured["organization_id"] = organization_id

            def set_event_callback(self, callback):
                pass

            async def execute(self, input_data):
                return {"output": "ok"}

            def get_node_results(self):
                return []

        _cleanup = _override_http(user=user, db=db)
        try:
            with patch("app.api.v1.endpoints.workflow.WorkflowEngine", _FakeEngine):
                r = client.post("/api/v1/workflows/7/execute?stream=false", json={})
        finally:
            _cleanup()

        assert r.status_code == 200, r.text
        assert captured.get("organization_id") is None, (
            "无组织用户的检索范围必须如实为 None（此前被 `or 1` 伪造为 1 号组织）"
        )


# ══════════════════════════════════════════════════════════════════════════
# 3 · demo 写路径 —— 归属用户真实组织；无组织显式 403
# ══════════════════════════════════════════════════════════════════════════
class TestDemoSeedOrgAttribution:
    def test_demo_seed_orgless_user_forbidden(self, client: TestClient):
        """无组织用户不得加载示例数据（此前固定文本被写进 1 号组织命名空间）。

        变异验证：`org_id = await require_organization(...)` 改回 `… or 1` → 本用例红
        （无组织用户会走到写库，返回 200）。
        """
        user = _orgless_user()
        db = MagicMock()
        db.execute = AsyncMock(side_effect=AssertionError("db-touched"))
        _cleanup = _override_http(user=user, db=db)
        try:
            r = client.post("/api/v1/demo/seed")
        finally:
            _cleanup()

        assert r.status_code == 403, r.text
        assert db.execute.call_count == 0, "403 必须发生在任何 DB 操作之前"

    def test_demo_seed_writes_into_users_real_org_namespace(self, client: TestClient):
        """有组织用户：文档/会话/ES 索引的 organization_id 都是调用者的真实组织。

        防过度修复 + 防归属错位（既不能是 1，也不能是别的组织）。
        """
        import app.api.v1.endpoints.demo as demo_mod
        from app.models.chat import ChatSession as ChatSessionModel
        from app.models.document import Document as DocumentModel

        org_id = 42
        user = SimpleNamespace(
            id=9, organization_id=org_id, is_superuser=False, role="user", is_active=True
        )
        db = MagicMock()
        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=0)  # 未加载过示例数据
        db.execute = AsyncMock(return_value=count_result)
        added: list = []
        db.add = MagicMock(side_effect=added.append)
        db.commit = AsyncMock()

        es_docs: list[dict] = []

        async def _fake_index(doc_id, document):
            es_docs.append(document)

        _cleanup = _override_http(user=user, db=db)
        try:
            with (
                patch.object(
                    demo_mod.embedding_service,
                    "get_embeddings",
                    new=AsyncMock(side_effect=lambda chunks: [[0.1] * 4 for _ in chunks]),
                ),
                patch.object(
                    demo_mod.ElasticsearchTools,
                    "index_document",
                    new=AsyncMock(side_effect=_fake_index),
                ),
            ):
                r = client.post("/api/v1/demo/seed")
        finally:
            _cleanup()

        assert r.status_code == 200, r.text
        assert r.json()["success"] is True

        docs = [o for o in added if isinstance(o, DocumentModel)]
        assert len(docs) == 3, f"应创建 3 篇示例文档，实际 {len(docs)}"
        assert all(d.organization_id == org_id for d in docs), (
            "示例文档必须归属调用者的真实组织（bug 形态：落进 1 号组织命名空间）"
        )
        sessions = [o for o in added if isinstance(o, ChatSessionModel)]
        assert len(sessions) == 1 and sessions[0].organization_id == org_id

        assert es_docs, "仍应索引到 ES（本用例前提）"
        assert {d["organization_id"] for d in es_docs} == {str(org_id)}, (
            "ES 索引的 organization_id 必须只含调用者组织（绝不出现 1 或其它组织）"
        )


# ══════════════════════════════════════════════════════════════════════════
# 4 · MinIO 前缀白名单 —— 无组织用户不得被算作 1 号组织阅读者
# ══════════════════════════════════════════════════════════════════════════
class TestFilesProxyOrglessDenied:
    def test_files_proxy_orgless_user_denied_documents_prefix(self, client: TestClient):
        """`documents/1/…`：无组织用户此前被 `… or 1` 判为 org 1 阅读者直接放行。

        变异验证：把 main.py 的 `user_org is None or` 守卫去掉（恢复 or 1 形态）→
        本用例红（放行到 MinIO/其它错误码）。
        """
        user = _orgless_user()
        _cleanup = _override_http(user=user, optional_user=True)
        try:
            with patch("app.main.minio_client.get_object") as get_obj:
                resp = client.get("/files/documents/1/abc.pdf")
        finally:
            _cleanup()

        assert resp.status_code == 403, f"无组织用户不得读任何组织前缀的对象: {resp.status_code}"
        assert get_obj.call_count == 0, "拒绝必须发生在触达对象存储之前"

    def test_files_proxy_orgless_user_denied_bare_org_prefix(self, client: TestClient):
        """`1/…`（裸组织前缀）形态同样 403——两条前缀解析路径都必须 fail-closed。"""
        user = _orgless_user()
        _cleanup = _override_http(user=user, optional_user=True)
        try:
            with patch("app.main.minio_client.get_object") as get_obj:
                resp = client.get("/files/1/abc.pdf")
        finally:
            _cleanup()

        assert resp.status_code == 403
        assert get_obj.call_count == 0


# ══════════════════════════════════════════════════════════════════════════
# 5 · agent 记忆命名空间 —— 真实 org/user 键；lesson 写读同命名空间
# ══════════════════════════════════════════════════════════════════════════
class TestAgentMemoryNamespaces:
    @pytest.mark.asyncio
    async def test_memory_api_namespace_uses_true_org_key_not_fake_1(self):
        """memory API 的命名空间用真实 org（无组织如实为 None），不再冒充 1 号组织。

        变异验证：`get_user_org_id(...)` 改回 `organization_id or 1` → 无组织用例红。
        """
        import app.api.v1.endpoints.memory as memory_mod

        captured: list[str] = []
        fake_system = SimpleNamespace(_load_from_redis=AsyncMock())

        def _fake_get(namespace: str):
            captured.append(namespace)
            return fake_system

        with patch.object(
            memory_mod, "get_memory_system", new=MagicMock(side_effect=_fake_get)
        ):
            await memory_mod._get_user_memory_system(
                SimpleNamespace(organization_id=None, id=5), "default"
            )
            assert captured == ["None:5:default"], (
                "无组织用户的命名空间标签必须如实（bug 形态: '1:5:default'）"
            )

            captured.clear()
            await memory_mod._get_user_memory_system(
                SimpleNamespace(organization_id=2, id=7), "default"
            )
            assert captured == ["2:7:default"], "有组织用户走真实组织段，行为不变"

    def test_agent_feedback_lesson_lands_in_agent_visible_namespace(self, client: TestClient):
        """feedback lesson 必须落在 agent 侧读侧同一命名空间（否则 lesson 静默失效）。

        修前：无组织会话写 `1:{user}:session:{sid}`，agent 侧读 `None:{user}:session:{sid}`
        → 自己写的 lesson 自己学不到（功能 bug）。修后写读同键。

        变异验证：`organization_id=session.organization_id` 改回 `… or 1` → 本用例红
        （读侧在 None 命名空间看不到任何 lesson）。
        """
        from app.agent.memory_bridge import AgentMemoryBridge
        from app.services.memory_service import get_memory_system

        session_id = str(uuid.uuid4())
        user = _orgless_user(user_id=77)
        message = MagicMock()
        message.id = "msg-268"
        message.session_id = session_id
        message.content = "把汇总表算错了"
        message.feedback = None
        message.feedback_note = None
        chat_session = SimpleNamespace(id=session_id, organization_id=None)

        msg_result = MagicMock()
        msg_result.scalar_one_or_none = MagicMock(return_value=message)
        session_result = MagicMock()
        session_result.scalar_one_or_none = MagicMock(return_value=chat_session)
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[msg_result, session_result])
        db.commit = AsyncMock()

        _cleanup = _override_http(user=user, db=db)
        try:
            r = client.post(
                "/api/v1/agent/feedback",
                json={"message_id": "msg-268", "feedback": -1, "note": "回答与文档不符"},
            )
        finally:
            _cleanup()

        assert r.status_code == 200, r.text

        # 读侧 = agent 运行时的同参数桥（/chat/stream useAgent 无组织场景传 None）
        reader = AgentMemoryBridge(
            agent_id=f"session:{session_id}", organization_id=None, user_id=77
        )
        lessons = reader.system.reflective.lessons
        assert any(l.get("lesson") == "回答与文档不符" for l in lessons), (
            "feedback 写入的 lesson 必须能被 agent 侧同一命名空间加载（修前静默失效）"
        )

        # 反向：不得写进 1 号组织的命名空间（bug 形态的落点）
        bugged_ns = get_memory_system(f"1:77:session:{session_id}")
        assert not any(l.get("lesson") == "回答与文档不符" for l in bugged_ns.reflective.lessons), (
            "lesson 不得落入 1 号组织命名空间"
        )


# ══════════════════════════════════════════════════════════════════════════
# 6 · monitoring /admin-summary —— 权限门 + 无组织 fail-closed + 组织口径
# ══════════════════════════════════════════════════════════════════════════
def _allow_permissions(perms: set[str] | None = None):
    """桩掉权限解析（返回给定权限集，默认全部）。"""
    from app.models.rbac import PermissionType

    return patch(
        "app.services.permission_service.permission_service.get_user_permissions",
        new=AsyncMock(return_value=perms if perms is not None else {p.value for p in PermissionType}),
    )


class TestAdminSummaryHole:
    def test_admin_summary_requires_view_system_health_permission(self, client: TestClient):
        """普通已登录用户（无 system:health）→ 403；处理器不得执行、不得触碰 DB。

        变异验证：去掉 dependencies 里的 permission_required → 本用例红（会走到处理器）。
        """
        user = SimpleNamespace(
            id=1, organization_id=5, is_superuser=False, role="user", is_active=True
        )
        db = MagicMock()
        db.execute = AsyncMock(side_effect=AssertionError("db-touched"))
        _cleanup = _override_http(user=user, db=db)
        try:
            with _allow_permissions(perms=set()):  # 无任何权限
                r = client.get("/api/v1/monitoring/admin-summary")
        finally:
            _cleanup()

        assert r.status_code == 403, r.text
        # issue #277（评审 or1268 R5）：逗号表达式一拆为「有效断言 + 活消息」
        # （原写法 `db.execute.assert_not_called(), "…"` 的消息文本是死串）。
        assert db.execute.call_count == 0, "权限拒绝必须发生在任何统计查询之前"

    def test_admin_summary_orgless_non_superuser_forbidden_no_queries(self, client: TestClient):
        """无组织的非超管即使持有权限也被显式拒绝（修前拿全库计数）。

        变异验证：去掉 `org_id is None and not is_superuser` 守卫 → 本用例红
        （处理器执行、计数查询触达 DB）。
        """
        user = SimpleNamespace(
            id=1, organization_id=None, is_superuser=False, role="admin", is_active=True
        )
        db = MagicMock()
        db.execute = AsyncMock(side_effect=AssertionError("db-touched"))
        _cleanup = _override_http(user=user, db=db)
        try:
            with _allow_permissions():
                r = client.get("/api/v1/monitoring/admin-summary")
        finally:
            _cleanup()

        assert r.status_code == 403, r.text
        # issue #277（评审 or1268 R5）：同 §6.1，拆开逗号表达式。
        assert db.execute.call_count == 0, "拒绝必须在 try 内任何计数查询之前（且不被吞成 200）"

    @pytest.mark.asyncio
    async def test_admin_summary_org_scoped_counts_via_real_sqlite(self, client: TestClient):
        """有组织管理员：五项计数全部按自己组织口径；组织数不再是全系统数。

        真建表真插入：org 55 = 2 users/2 docs/4 sessions；org 66 = 3 users/1 doc/1 session。
        变异验证：去掉任一 `if org_id: … .where(...)` → 对应计数变大 → 本用例红；
        `total_organizations` 改回全局 → 本用例红。
        """
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
                Organization(id=55, name="Org55", owner_id=0),
                Organization(id=66, name="Org66", owner_id=0),
            ])
            users_55 = [
                User(username=f"u55-{i}", email=f"u55-{i}@e.com", hashed_password="x",
                     organization_id=55)
                for i in range(2)
            ]
            users_66 = [
                User(username=f"u66-{i}", email=f"u66-{i}@e.com", hashed_password="x",
                     organization_id=66)
                for i in range(3)
            ]
            s.add_all(users_55 + users_66)
            await s.flush()
            s.add_all([
                Document(id=f"d55-{i}", filename=f"d55-{i}.pdf", file_path=f"/tmp/d55-{i}.pdf",
                         file_size=1, file_type=DocumentType.PDF, organization_id=55,
                         uploaded_by=users_55[0].id)
                for i in range(2)
            ] + [
                Document(id="d66-0", filename="d66-0.pdf", file_path="/tmp/d66-0.pdf",
                         file_size=1, file_type=DocumentType.PDF, organization_id=66,
                         uploaded_by=users_66[0].id)
            ])
            s.add_all([
                ChatSession(id=str(uuid.uuid4()), user_id=users_55[0].id, organization_id=55,
                            title="s55")
                for _ in range(4)
            ] + [
                ChatSession(id=str(uuid.uuid4()), user_id=users_66[0].id, organization_id=66,
                            title="s66")
            ])
            await s.commit()

        user = SimpleNamespace(
            id=550, organization_id=55, is_superuser=False, role="admin", is_active=True
        )

        async def _ref():
            return user

        async def _db_gen():
            async with factory() as session:
                yield session

        from app.main import app
        app.dependency_overrides[get_db] = _db_gen
        app.dependency_overrides[get_current_user] = _ref
        try:
            with _allow_permissions():
                r = client.get("/api/v1/monitoring/admin-summary")
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            await engine.dispose()

        assert r.status_code == 200, r.text
        data = r.json()
        assert data == {
            "total_users": 2,          # org 66 的 3 个不得计入
            "total_documents": 2,      # org 66 的 1 篇不得计入
            "total_sessions": 4,       # org 66 的 1 个不得计入
            "active_users_24h": 0,
            "total_organizations": 1,  # 组织作用域视图：自己所属的 1 个（≠全局 2）
            # W2-T7 契约扩展：会话周环比真值——本 fixture 的 4 个会话均为
            # 插入时刻 NOW（server_default），无显式 created_at → 全落本周（4/0）。
            "sessions_this_week": 4,
            "sessions_last_week": 0,
        }, f"计数必须全部按调用者组织口径: {data}"

    @pytest.mark.asyncio
    async def test_admin_summary_orgless_superuser_keeps_system_view(self, client: TestClient):
        """无组织超管（系统管理员）保留全系统视图——本修复只关租户侧的口子。"""
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
                Organization(id=1, name="First", owner_id=0),
                Organization(id=3, name="Third", owner_id=0),
                User(username="a", email="a@e.com", hashed_password="x", organization_id=1),
                User(username="b", email="b@e.com", hashed_password="x", organization_id=3),
            ])
            await s.commit()

        user = SimpleNamespace(
            id=999, organization_id=None, is_superuser=True, role="admin", is_active=True
        )

        async def _ref():
            return user

        async def _db_gen():
            async with factory() as session:
                yield session

        from app.main import app
        app.dependency_overrides[get_db] = _db_gen
        app.dependency_overrides[get_current_user] = _ref
        try:
            r = client.get("/api/v1/monitoring/admin-summary")
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            await engine.dispose()

        assert r.status_code == 200, r.text
        data = r.json()
        assert data["total_users"] == 2
        assert data["total_organizations"] == 2, "无组织超管是系统管理员，保留全局组织数"


# ══════════════════════════════════════════════════════════════════════════
# 7 · /agent/chat 会话 org 对称性（issue #277 / 评审 or1268 R2）
# ══════════════════════════════════════════════════════════════════════════
def _token_org_user(user_id: int = 77, token_org: int = 42) -> SimpleNamespace:
    """org 来自 token_organization_id 回退的用户（库中 organization_id=None）。"""
    return SimpleNamespace(
        id=user_id,
        organization_id=None,
        token_organization_id=token_org,
        is_superuser=False,
        role="user",
        is_active=True,
    )


class _CaptureDbSession:
    """_save_session_interaction 的可捕获假库会话：查无会话 → 走新建分支。"""

    def __init__(self):
        self.added: list = []

    async def execute(self, stmt):
        res = MagicMock()
        res.scalar_one_or_none = MagicMock(return_value=None)
        return res

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


class TestAgentChatSessionOrgSymmetry:
    def test_create_session_persists_resolved_token_org(self, client: TestClient, monkeypatch):
        """回退链用户建会话（POST /agent/sessions）：落库 org == 运行解析 org（42）。

        变异验证：organization_id 改回 current_user.organization_id → 落库 None → 红。
        """
        user = _token_org_user()
        captured: list = []
        fake_db = MagicMock()
        fake_db.add = MagicMock(side_effect=captured.append)
        fake_db.commit = AsyncMock()
        monkeypatch.setattr(
            "app.core.database.AsyncSessionLocal", lambda: _AsyncCtx(fake_db)
        )

        _cleanup = _override_http(user=user)
        try:
            r = client.post("/api/v1/agent/sessions", json={"title": "新会话"})
        finally:
            _cleanup()

        assert r.status_code == 200, r.text
        sessions = [o for o in captured if isinstance(o, ChatSession)]
        assert len(sessions) == 1, f"应创建 1 个会话: {captured}"
        assert sessions[0].organization_id == 42, (
            "落库 org 必须是 resolved org（token 回退 42），而不是 raw None"
        )

    def test_agent_chat_run_and_persist_org_unified_then_feedback_lesson_loads(
        self, client: TestClient, monkeypatch
    ):
        """回退链用户走 /agent/chat：运行 org == 落库 org == 42，feedback lesson 可加载。

        判别链（R2 题目原话）：org 来自回退链的用户建会话 → 落库 org == 运行 org
        → feedback lesson 可加载。修前落库 raw None：lesson 写入 `None:{user}:…`，
        运行侧读 `42:{user}:…`，自己写的 lesson 自己学不到。

        变异验证：chat 落库参数改回 current_user.organization_id →
        落库 None ≠ 运行 42 → 红。
        """
        import app.api.v1.endpoints.agent as agent_mod
        from app.agent.memory_bridge import AgentMemoryBridge
        from app.services.memory_service import get_memory_system

        user = _token_org_user()
        session_id = str(uuid.uuid4())
        captured: dict = {}

        async def _fake_agent_chat(**kwargs):
            captured["run_org"] = kwargs.get("organization_id")
            captured["run_session_id"] = kwargs.get("session_id")
            yield SimpleNamespace(
                type="chunk",
                content="好的",
                to_sse_dict=lambda: {"type": "chunk", "content": "好的"},
            )

        fake_db = _CaptureDbSession()
        monkeypatch.setattr(agent_mod, "AsyncSessionLocal", lambda: _AsyncCtx(fake_db))
        monkeypatch.setattr(agent_mod.agent_service, "chat", _fake_agent_chat)

        _cleanup = _override_http(user=user)
        try:
            r = client.post(
                "/api/v1/agent/chat",
                json={"query": "你好", "session_id": session_id},
            )
        finally:
            _cleanup()

        assert r.status_code == 200, r.text
        assert captured.get("run_org") == 42, "运行侧必须用 resolved org（token 回退 42）"
        assert captured.get("run_session_id") == session_id

        sessions = [o for o in fake_db.added if isinstance(o, ChatSession)]
        assert len(sessions) == 1, f"会话应落库: {fake_db.added}"
        assert sessions[0].organization_id == captured["run_org"] == 42, (
            "落库 org 必须与运行解析 org 相同（修前 raw None ≠ 运行 42）"
        )
        assistant_msgs = [
            o
            for o in fake_db.added
            if isinstance(o, ChatMessage) and o.message_type == MessageType.ASSISTANT
        ]
        assert assistant_msgs, "助手消息应已落库（feedback 的锚点）"

        # ── feedback：lesson 必须落在运行侧同命名空间（42:77:session:<id>）──
        msg_result = MagicMock()
        msg_result.scalar_one_or_none = MagicMock(return_value=assistant_msgs[0])
        session_result = MagicMock()
        session_result.scalar_one_or_none = MagicMock(return_value=sessions[0])
        fb_db = MagicMock()
        fb_db.execute = AsyncMock(side_effect=[msg_result, session_result])
        fb_db.commit = AsyncMock()

        _cleanup = _override_http(user=user, db=fb_db)
        try:
            r = client.post(
                "/api/v1/agent/feedback",
                json={
                    "message_id": assistant_msgs[0].id,
                    "feedback": -1,
                    "note": "R2 判别：答案不对",
                },
            )
        finally:
            _cleanup()

        assert r.status_code == 200, r.text
        reader = AgentMemoryBridge(
            agent_id=f"session:{session_id}", organization_id=42, user_id=77
        )
        assert any(
            lesson.get("lesson") == "R2 判别：答案不对"
            for lesson in reader.system.reflective.lessons
        ), "运行侧（resolved org 42）必须能加载 feedback 写入的 lesson（修前静默失效）"
        bugged_ns = get_memory_system(f"None:77:session:{session_id}")
        assert not any(
            lesson.get("lesson") == "R2 判别：答案不对"
            for lesson in bugged_ns.reflective.lessons
        ), "lesson 不得落在 raw org（None）命名空间"

    def test_feedback_lesson_resolves_run_org_for_legacy_orgless_session(self, client: TestClient):
        """历史会话（落库 org=None）上的 feedback：lesson 仍落在运行侧 resolved org。

        运行侧（/agent/chat，token 回退）= 42；修前 bridge 用 session.organization_id
        （None）→ 运行读 42 找不到。历史会话不因落库值陈旧而丢 lesson。

        变异验证：lesson org 改回 session.organization_id → 本用例红。
        """
        from app.agent.memory_bridge import AgentMemoryBridge
        from app.services.memory_service import get_memory_system

        session_id = str(uuid.uuid4())
        user = _token_org_user()
        message = MagicMock()
        message.id = "msg-277"
        message.session_id = session_id
        message.content = "算错了"
        chat_session = SimpleNamespace(id=session_id, organization_id=None)

        msg_result = MagicMock()
        msg_result.scalar_one_or_none = MagicMock(return_value=message)
        session_result = MagicMock()
        session_result.scalar_one_or_none = MagicMock(return_value=chat_session)
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[msg_result, session_result])
        db.commit = AsyncMock()

        _cleanup = _override_http(user=user, db=db)
        try:
            r = client.post(
                "/api/v1/agent/feedback",
                json={"message_id": "msg-277", "feedback": -1, "note": "遗留会话判别"},
            )
        finally:
            _cleanup()

        assert r.status_code == 200, r.text
        reader = AgentMemoryBridge(
            agent_id=f"session:{session_id}", organization_id=42, user_id=77
        )
        assert any(
            lesson.get("lesson") == "遗留会话判别"
            for lesson in reader.system.reflective.lessons
        ), "历史会话的 feedback lesson 也必须落在运行侧 resolved org（42）"
        bugged_ns = get_memory_system(f"None:77:session:{session_id}")
        assert not any(
            lesson.get("lesson") == "遗留会话判别"
            for lesson in bugged_ns.reflective.lessons
        ), "不得沿用会话落库的 raw org（None）"

    @pytest.mark.asyncio
    async def test_save_session_interaction_heals_legacy_none_org(self, monkeypatch):
        """历史会话（org=None）在带 resolved org 的消息落库时自愈（只补空值）。

        变异验证：删掉 `elif existing.organization_id is None` 自愈分支 → 本用例红。
        """
        import app.api.v1.endpoints.agent as agent_mod

        legacy = MagicMock()
        legacy.user_id = 77
        legacy.title = "Existing session"
        legacy.organization_id = None
        result_mock = MagicMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=legacy)
        fake = MagicMock()
        fake.execute = AsyncMock(return_value=result_mock)
        fake.add = MagicMock()
        fake.commit = AsyncMock()
        monkeypatch.setattr(agent_mod, "AsyncSessionLocal", lambda: _AsyncCtx(fake))

        out = await agent_mod._save_session_interaction("sess-legacy", 77, "q", 42, "a")

        assert out is not None
        assert legacy.organization_id == 42, "落库 org 应与本次运行解析 org 收敛（自愈）"
        assert fake.add.call_count == 2  # user + assistant 消息


# ══════════════════════════════════════════════════════════════════════════
# 8 · monitoring /llm-stats —— 全局聚合升级为 VIEW_SYSTEM_HEALTH 门槛（R3）
# ══════════════════════════════════════════════════════════════════════════
class TestLlmStatsGate:
    def test_llm_stats_without_permission_forbidden(self, client: TestClient):
        """普通已登录用户（无 system:health）→ 403，处理器不执行、不触碰 DB。

        修前本端点仅登录门：全局 token/成本/延迟聚合任意登录用户可见。

        变异验证：去掉 dependencies 里的 permission_required → 200 → 本用例红。
        """
        user = SimpleNamespace(
            id=1, organization_id=5, is_superuser=False, role="user", is_active=True
        )
        db = MagicMock()
        db.execute = AsyncMock()
        _cleanup = _override_http(user=user, db=db)
        try:
            with _allow_permissions(perms=set()):
                r = client.get("/api/v1/monitoring/llm-stats")
        finally:
            _cleanup()

        assert r.status_code == 403, r.text
        assert db.execute.call_count == 0, "权限拒绝必须发生在任何统计查询之前"

    def test_llm_stats_with_permission_accessible(self, client: TestClient):
        """持 system:health 的用户（管理员）不被过度收窄：仍 200 且聚合字段齐全。"""
        user = SimpleNamespace(
            id=1, organization_id=5, is_superuser=False, role="admin", is_active=True
        )
        _cleanup = _override_http(user=user)
        try:
            with _allow_permissions():
                r = client.get("/api/v1/monitoring/llm-stats")
        finally:
            _cleanup()

        assert r.status_code == 200, r.text
        data = r.json()
        assert "total_tokens_today" in data and "cost_today_usd" in data


# ══════════════════════════════════════════════════════════════════════════
# 9 · token 回退路径直接用例（R6）—— /files 代理按回退 org 授权
# ══════════════════════════════════════════════════════════════════════════
class TestFilesProxyTokenOrgFallback:
    def test_files_proxy_token_org_user_reads_own_org_only(self, client: TestClient):
        """库中 org=None、令牌 org=9 的用户：documents/9/… 放行、其它组织 403。

        issue #277（评审 or1268 R6）：#268 把 /files 的 `… or 1` 换成
        get_user_org_id 回退写法后，回退路径无直接用例（仅代码推导）。此处钉：
        回退 org 恰好授予该组织的读权——既不放行他人（8 → 403），也不拒绝自己。

        变异验证：main.py 改回 raw `current_user.organization_id` → 回退用户被判
        无组织 → 第一条断言红（403）。
        """
        user = SimpleNamespace(
            id=5,
            organization_id=None,
            token_organization_id=9,
            is_superuser=False,
            role="user",
            is_active=True,
        )
        _cleanup = _override_http(user=user, optional_user=True)
        try:
            with patch(
                "app.main.minio_client.get_object",
                return_value=io.BytesIO(b"data"),
            ) as get_obj:
                ok = client.get("/files/documents/9/abc.pdf")
                denied = client.get("/files/documents/8/abc.pdf")
        finally:
            _cleanup()

        assert ok.status_code == 200, f"token 回退组织（9）本组织对象必须可读: {ok.status_code}"
        assert ok.content == b"data"
        assert get_obj.call_count == 1, "拒绝的请求不得触达对象存储"
        assert denied.status_code == 403, "其它组织（8）前缀必须 403（回退不得放大读权）"
