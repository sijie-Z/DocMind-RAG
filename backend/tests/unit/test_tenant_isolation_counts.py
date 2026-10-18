"""issue #257 回归：多租户隔离 —— 计数类输出不得跨租户（真跑 SQL + 假 ES）。

三条同族链路（均已在生产代码修复，本文件钉住行为）：

1. **agent 工具 `get_system_status`**（主犯）：此前签名 `**_: Any` 让注册表按签名
   过滤掉 org 上下文，工具输出全集群 ES 计数（`cat.indices` 求和）与全库
   `documents`/`users` 行数。本文件通过**注册表路径**
   （`tool_registry.execute_detailed`）驱动 —— 不只测 helper，测的是接线：
   执行器真的会传 org，注册表真的会把 org 送进 handler 并强制按 org 查询。
   判别面：org B（空库，实测场景）输出 0；org A 的数字绝不出现；缺 org fail-closed。
2. **`_diagnose_empty_results`**（knowledge 搜索空结果诊断）：此前全索引 count，
   任何租户搜出空结果都会看到全库 chunk 总量。现在按调用者 org 过滤。
3. **`or 1` 兜底去伪造**：chat agent 流与 `delegate_subtask` 此前把无组织用户
   静默放进 1 号组织的上下文（1 号组织甚至未必存在）。现在 chat 如实传 None
   （休闲对话仍可用，需要 org 的工具由注册表 auth 钩子 fail-closed），
   delegation 缺 org 直接拒绝。

DB 侧用内存 SQLite **真建表、真插入、真执行查询**（风格照抄
`test_issue95_org_tree_visibility.py`）；ES 侧用按 org term 真实过滤的假 client。
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.agent.service  # noqa: F401 — 触发全部工具注册（含 get_system_status / delegate_subtask）
from app.agent.registry import tool_registry
from app.core.database import get_db
from app.core.security import get_current_user
from app.models import Base, Organization, User
from app.models.document import Document, DocumentType

ORG_A = 41  # 有数据的租户
ORG_B = 42  # 空租户 —— #257 实测场景：空组织看到别人的计数

A_CHUNKS, A_DOCS, A_USERS = 97, 3, 5


class _FakeOrgAwareES:
    """假 ES count：**真的按 query 里的 org term 过滤**，无过滤时返回全库总和。

    记录每次调用 —— 测试要断言查询确实带了 org 硬过滤，而不是依赖假对象的善意：
    把 query 过滤去掉（bug 形态）时，这里返回的是跨租户的全库数。
    """

    def __init__(self, chunks_by_org: dict[int, int]):
        self._chunks = dict(chunks_by_org)
        self.calls: list[dict] = []

    async def count(self, index, query):
        self.calls.append({"index": index, "query": query})
        try:
            org = int(query["bool"]["filter"][0]["term"]["organization_id"])
        except (KeyError, IndexError, TypeError, ValueError):
            org = None  # 无 org 过滤 == 全局口径（bug 形态）
        if org is None:
            return {"count": sum(self._chunks.values())}
        return {"count": self._chunks.get(org, 0)}


class _ExplodingES:
    """触碰即炸的 ES：断言 fail-closed 路径根本不触达 ES。"""

    async def count(self, *args, **kwargs):
        raise AssertionError("fail-closed 路径不得触达 ES")


class _ExplodingDB:
    """触碰即炸的会话工厂：断言 fail-closed 路径根本不触达 DB。"""

    def __call__(self, *args, **kwargs):
        raise AssertionError("fail-closed 路径不得触达 DB")


class _RecordingES:
    """记录式 ES：只统计调用次数、不炸（issue #269 P2⑤b 的判别器）。

    为什么必须记录式：handler 的 `except Exception` 会把爆炸吞进 status dict
    （review_iso257 MA7 实测：时序变异下爆炸式断言 8/8 全绿）——只有"事后断言
    计数为 0"才钉得住「guard 在任何 await 之前」这一属性。
    """

    def __init__(self):
        self.count_calls = 0

    async def count(self, *args, **kwargs):
        self.count_calls += 1
        return {"count": 0}


class _RecordingRedis:
    """记录式 Redis：同上，断言 fail-closed 路径不触碰。"""

    def __init__(self):
        self.info_calls = 0

    async def info(self, *args, **kwargs):
        self.info_calls += 1
        return {}


class _RecordingDBFactory:
    """记录式会话工厂：被实例化即记账（不炸，同上）。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return MagicMock()


async def _make_tenant_db():
    """内存 SQLite 真建表 + 真写入；StaticPool 保证跨会话共享同一内存库。

    org A：5 users + 3 documents；org B：全空。返回 (engine, session_factory)。
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
            Organization(id=ORG_A, name="TenantA", owner_id=0),
            Organization(id=ORG_B, name="TenantB", owner_id=0),
        ])
        users = [
            User(
                username=f"user-a{i}",
                email=f"user-a{i}@example.com",
                hashed_password="x",
                organization_id=ORG_A,
            )
            for i in range(A_USERS)
        ]
        s.add_all(users)
        await s.flush()
        s.add_all([
            Document(
                id=f"doc-a{i}",
                filename=f"a{i}.pdf",
                file_path=f"/tmp/a{i}.pdf",
                file_size=1024,
                file_type=DocumentType.PDF,
                organization_id=ORG_A,
                uploaded_by=users[0].id,
            )
            for i in range(A_DOCS)
        ])
        await s.commit()
    return engine, factory


# ══════════════════════════════════════════════════════════════════════
# 1 · get_system_status —— 经注册表路径，按调用者 org 计数
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_system_status_es_count_scoped_to_org():
    """★ 主犯判别：org B 经注册表调用 → ES 段输出 0，org A 的 chunk 数绝不出现。

    变异验证：把 count 的 query 改成 `{}`（= 无 org 过滤的全库口径）→ 本用例红。
    """
    engine, factory = await _make_tenant_db()
    fake_es = _FakeOrgAwareES({ORG_A: A_CHUNKS})
    try:
        with (
            patch("app.core.database.AsyncSessionLocal", new=factory),
            patch("app.core.elasticsearch.es_client", fake_es),
        ):
            result = await tool_registry.execute_detailed(
                "get_system_status", {}, organization_id=ORG_B
            )

        assert result.success, f"工具执行失败: {result.error}"
        data = json.loads(result.data)
        assert data["elasticsearch"]["chunks"] == 0, (
            "org B 空库必须输出 0（bug 形态下这里是跨租户的全库计数）"
        )
        assert str(A_CHUNKS) not in result.data, "org A 的数字出现在响应里 = 跨租户泄漏"
        # 查询本身必须带 org 硬过滤（term），不能依赖假 ES 的善意
        term = fake_es.calls[0]["query"]["bool"]["filter"][0]["term"]
        assert term == {"organization_id": str(ORG_B)}, f"ES 查询必须按调用者 org 过滤: {term}"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_system_status_db_counts_scoped_to_org():
    """★ 主犯判别：org B 经注册表调用 → documents/users 都是 0；org A 的行数绝不出现。

    变异验证：文档 count 去掉 `.where(Document.organization_id ...)`、用户 count
    去掉 `.where(User.organization_id ...)` → 各使本用例红一次。
    """
    engine, factory = await _make_tenant_db()
    fake_es = _FakeOrgAwareES({ORG_A: A_CHUNKS})
    try:
        with (
            patch("app.core.database.AsyncSessionLocal", new=factory),
            patch("app.core.elasticsearch.es_client", fake_es),
        ):
            result = await tool_registry.execute_detailed(
                "get_system_status", {}, organization_id=ORG_B
            )

        assert result.success, f"工具执行失败: {result.error}"
        data = json.loads(result.data)
        assert data["database"]["documents"] == 0, "org B 空库必须输出 0 篇文档"
        assert data["database"]["users"] == 0, "org B 没有用户，必须输出 0"
        assert f'"documents": {A_DOCS}' not in result.data, "org A 的文档数泄漏"
        assert f'"users": {A_USERS}' not in result.data, "org A 的用户数泄漏"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_system_status_org_a_sees_own_counts():
    """防过度修复：org A 必须看到自己的完整真实计数（不是恒 0 / 也不是空结果）。"""
    engine, factory = await _make_tenant_db()
    fake_es = _FakeOrgAwareES({ORG_A: A_CHUNKS})
    try:
        with (
            patch("app.core.database.AsyncSessionLocal", new=factory),
            patch("app.core.elasticsearch.es_client", fake_es),
        ):
            result = await tool_registry.execute_detailed(
                "get_system_status", {}, organization_id=ORG_A
            )

        assert result.success, f"工具执行失败: {result.error}"
        data = json.loads(result.data)
        assert data["elasticsearch"]["chunks"] == A_CHUNKS
        assert data["database"]["documents"] == A_DOCS
        assert data["database"]["users"] == A_USERS
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_system_status_missing_org_fails_closed_via_registry():
    """缺 org 时注册表 auth 钩子在任何 handler 执行前拒绝（ES/DB 均不被触达）。

    变异验证：去掉注册装饰器的 `requires_auth=True` → 本用例红（钩子不再拦，
    字符串型 "Error: ..." 会被注册表归一为 success=True）。
    """
    with (
        patch("app.core.elasticsearch.es_client", _ExplodingES()),
        patch("app.core.database.AsyncSessionLocal", new=_ExplodingDB()),
    ):
        result = await tool_registry.execute_detailed("get_system_status", {})

    assert not result.success, "缺 org 必须拒绝执行（fail-closed）"
    assert result.error is not None and result.error.code == "auth_required", (
        f"拒绝码应为 auth_required，实际: {result.error}"
    )


@pytest.mark.asyncio
async def test_system_status_missing_org_direct_call_refuses():
    """绕过注册表的直接调用同样 fail-closed（函数体防线），不触达 ES/DB。

    变异验证：删掉函数体的 `if organization_id is None: return "Error: ..."` → 本用例红。
    """
    from app.agent.tools.utility import get_system_status

    with (
        patch("app.core.elasticsearch.es_client", _ExplodingES()),
        patch("app.core.database.AsyncSessionLocal", new=_ExplodingDB()),
    ):
        out = await get_system_status()

    assert isinstance(out, str)
    assert out.startswith("Error:"), f"缺 org 的直接调用必须拒答，实际: {out[:120]}"


@pytest.mark.asyncio
async def test_system_status_missing_org_zero_touch_recorded():
    """缺 org 直调：fail-closed 且 ES/Redis/DB **零调用**——记录式断言。

    review_iso257 MA7 的既有盲区：`_ExplodingES` 那条抓不住 guard 的时序，
    因为爆炸被 handler 的 `except Exception` 吞进 status dict；把 guard 挪到
    任何 await 之后仍 8/8 全绿。本用例把三个外部触点换成记录器：guard 一旦
    晚于任一 await → 对应计数非零 → 精确红（P2⑤b）。
    """
    from app.agent.tools.utility import get_system_status

    es, rd, db = _RecordingES(), _RecordingRedis(), _RecordingDBFactory()
    with (
        patch("app.core.elasticsearch.es_client", es),
        patch("app.core.redis.redis_client", rd),
        patch("app.core.database.AsyncSessionLocal", new=db),
    ):
        out = await get_system_status()

    assert isinstance(out, str) and out.startswith("Error:"), (
        f"缺 org 的直接调用必须拒答，实际: {out[:120]}"
    )
    assert es.count_calls == 0, "guard 必须在任何 await 之前：缺 org 不得触碰 ES"
    assert rd.info_calls == 0, "缺 org 不得触碰 Redis"
    assert db.calls == 0, "缺 org 不得创建 DB 会话"


# ══════════════════════════════════════════════════════════════════════
# 2 · _diagnose_empty_results —— 空结果诊断按 org 计数
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_diagnose_empty_results_scoped_to_caller_org():
    """全库有 A_CHUNKS 篇、org B 自己为空 → 必须报 empty_index/0，而非全库数。

    变异验证：把 count 的 query 参数去掉（= 退回全索引 count）→ 本用例红
    （org B 会拿到全库 97，被误报成 ok 且携带跨租户数字）。
    """
    from app.api.v1.endpoints.knowledge import _diagnose_empty_results

    fake_es = _FakeOrgAwareES({ORG_A: A_CHUNKS})
    with (
        patch("app.core.elasticsearch.get_index_error", return_value=None),
        # knowledge.py 在**模块级** `from app.core.elasticsearch import get_elasticsearch`，
        # 所以 patch 目标必须是消费方模块里的名字（不能只 patch 来源模块）
        patch(
            "app.api.v1.endpoints.knowledge.get_elasticsearch",
            new=AsyncMock(return_value=fake_es),
        ),
    ):
        status_b, n_b = await _diagnose_empty_results(ORG_B)
        status_a, n_a = await _diagnose_empty_results(ORG_A)

    assert (status_b, n_b) == ("empty_index", 0), (
        "org B 没有任何已索引内容 → empty_index/0（bug 形态: ok/97，误报+泄漏）"
    )
    assert (status_a, n_a) == ("ok", A_CHUNKS), "有数据的 org 口径不变"
    term = fake_es.calls[0]["query"]["bool"]["filter"][0]["term"]
    assert term == {"organization_id": str(ORG_B)}


def test_search_endpoint_diagnose_receives_caller_org(client: TestClient):
    """★ 调用点接线（issue #269 P2⑤a）：空结果时端点必须把 effective_org_id
    传进诊断——不是固定值 1。

    既有 16 条用例（本文件 helper 直调 + test_index_visibility 端点用例）全部把
    `_diagnose_empty_results` 整个 patch 掉，或只直测 helper——调用点被改成
    `(1)` 时 16/16 全绿（review_iso257 MA2 实锤）。本用例让**真实端点**驱动
    **真实诊断**：org B 调用者（空库）、假 ES 编码 org 1=13（bug 形态数字）。
    若调用点回退成 1，响应变成 ok/13（正是 #257 的泄漏形态）→ 精确红。
    """
    from app.main import app
    from app.models.rbac import PermissionType

    mock_user = MagicMock()
    mock_user.id = 7
    mock_user.organization_id = ORG_B
    mock_user.is_superuser = False
    mock_user.is_active = True
    mock_user.token_organization_id = None

    async def _user():
        return mock_user

    async def _db():
        return MagicMock()

    fake_es = _FakeOrgAwareES({ORG_A: A_CHUNKS, 1: 13, ORG_B: 0})

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user
    try:
        with (
            patch(
                "app.services.permission_service.permission_service.get_user_permissions",
                new=AsyncMock(return_value={p.value for p in PermissionType}),
            ),
            patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks,
            # knowledge.py 在模块级 `from app.core.elasticsearch import get_elasticsearch`
            patch(
                "app.api.v1.endpoints.knowledge.get_elasticsearch",
                new=AsyncMock(return_value=fake_es),
            ),
            patch("app.core.elasticsearch.get_index_error", return_value=None),
            # B4：搜索按组织设置解析 top_k；本用例的 db 是 MagicMock，
            # 打桩设置服务（None → 平台默认），保持用例焦点在组织诊断上。
            patch(
                "app.api.v1.endpoints.knowledge.organization_settings_service.resolve_retrieval_top_k",
                new=AsyncMock(return_value=None),
            ),
        ):
            mock_ks.search_knowledge = AsyncMock(return_value=[])
            r = client.post(
                "/api/v1/knowledge/search",
                # 非超管：请求体里的 organization_id 不作数，effective_org_id
                # 由 require_organization 解析成调用者自己的组织（ORG_B）
                json={"query": "revenue", "organization_id": 1},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["index_status"], body["index_doc_count"]) == ("empty_index", 0), (
        "诊断必须按调用者组织：org B 空库 → empty_index/0"
        "（调用点回退成 1 时这里是 ok/13 —— 正是 #257 的泄漏形态）"
    )
    term = fake_es.calls[0]["query"]["bool"]["filter"][0]["term"]
    assert term == {"organization_id": str(ORG_B)}, (
        f"诊断 count 必须带调用者 org 的硬过滤 term，实际 {term}"
    )


# ══════════════════════════════════════════════════════════════════════
# 3 · `or 1` 兜底去伪造 —— 无组织用户不得进入 1 号组织上下文
# ══════════════════════════════════════════════════════════════════════
def test_chat_agent_stream_no_org_user_not_forced_into_org1(client: TestClient):
    """无组织用户的 agent 流：org 如实传 None（休闲对话可用），绝不 or 1。

    变异验证：把 `organization_id=getattr(...)` 改回 `... or 1` → 本用例红。
    """
    from app.agent.events import AgentEvent
    from app.agent.service import agent_service
    from app.main import app

    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.organization_id = None  # 无组织用户
    mock_user.token_organization_id = None  # MagicMock 未设属性会泄漏成 mock 值
    mock_user.is_active = True
    mock_user.is_superuser = False

    captured: dict = {}

    async def _fake_chat(**kwargs):
        captured.update(kwargs)
        yield AgentEvent(type="chunk", content="你好")

    async def _db():
        return MagicMock()

    async def _user():
        return mock_user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user
    try:
        with patch.object(agent_service, "chat", new=_fake_chat):
            r = client.post(
                "/api/v1/chat/stream",
                json={"content": "你好", "useAgent": True},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert r.status_code == 200, r.text
    assert "event: chunk" in r.text, "休闲对话仍应可用（无组织不得被整体拒答）"
    assert captured.get("organization_id") is None, (
        "无组织用户不得被静默放进 1 号组织上下文（此前 `... or 1`）"
    )


@pytest.mark.asyncio
async def test_delegate_subtask_without_org_fails_closed():
    """缺组织上下文不得进入子代理（更不得被伪造成 org 1 的上下文）。

    变异验证：删掉函数体的 org 判空 guard（并恢复 `organization_id or 1`）→ 本用例红。
    """
    import app.agent.subagent as sa

    fake_client = MagicMock()
    with (
        patch(
            "app.dependencies.get_rag_pipeline",
            return_value=SimpleNamespace(openai_client=fake_client),
        ),
        patch.object(sa, "run_parallel_subtasks", new=AsyncMock(return_value=["x"])) as rps,
    ):
        result = await tool_registry.execute_detailed(
            "delegate_subtask", {"tasks": ["查一下甲材料"]}
        )

    assert not result.success, "缺 org 必须拒绝执行（fail-closed）"
    assert result.error is not None and result.error.code == "auth_required", (
        f"拒绝码应为 auth_required，实际: {result.error}"
    )
    assert rps.await_count == 0, "缺组织上下文不得执行子代理（bug 形态: 被伪造为 org 1）"
