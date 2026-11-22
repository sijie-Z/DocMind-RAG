"""检索评测测试集（竞品对标 P2）：组织隔离 + 创建→运行→指标正确 + 删除边界。

测试策略（tests/api 惯例：真实 sqlite 全链 + dependency_overrides，不连 ES）：
- 鉴权：未认证 401；普通成员（User 系统角色含 SEARCH_KNOWLEDGE_BASE）可读写；
- 组织隔离：list/delete/run 都只看见/只影响调用者组织；跨组织 delete → 404，
  且目标行仍在（不是「静默成功」）；
- 创建校验：期望文档 id 必须属于本组织，跨组织 id → 400（结构化错误信封）；
- 运行：检索以降级注入的 fake pipeline 驱动（``knowledge_service.
  search_knowledge_with_meta``），断言 recall@k / MRR 的**数值**——假结果集里
  故意让 top-1 错、top-2/3 各中一个，区分「只数 hit」与「真按 k 截断」；
- 空测试集：安全返回（计数 0 + 指标 null），不抛错；
- 单条检索异常：记 status=error、不进分母（错误语义与 evals.harness 一致）；
- top_k 透传与默认值：请求 top_k 进入检索调用与截断。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  # 注册全部表供 create_all
from app.core.database import Base
from app.models.document import (
    Document,
    DocumentAcl,
    DocumentStatus,
    DocumentType,
    DocumentVisibility,
)
from app.models.organization import Organization, user_organization
from app.models.rbac import Permission, PermissionType, Role
from app.models.retrieval_eval import RetrievalEvalCase
from app.models.user import User

CASES = "/api/v1/knowledge/eval/cases"
RUN = "/api/v1/knowledge/eval/run"

ORG_A, ORG_B = 1, 2
USER_A, USER_B, USER_C = 11, 22, 33
DOC_A1, DOC_A2, DOC_B1, DOC_PRIVATE = str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())


def _make_user(uid=USER_A, org=ORG_A):
    return SimpleNamespace(
        id=uid,
        username=f"evaluser{uid}",
        organization_id=org,
        is_superuser=False,
        is_active=True,
        role="user",
    )


def _doc(doc_id: str, org: int, uploader: int) -> Document:
    return Document(
        id=doc_id,
        filename=f"{doc_id}.md",
        file_path=f"{org}/{doc_id}.md",
        file_size=10,
        file_type=DocumentType.TXT,
        status=DocumentStatus.INDEXED,
        organization_id=org,
        uploaded_by=uploader,
        title=doc_id,
    )


def _private_doc(doc_id: str, org: int, uploader: int) -> Document:
    doc = _doc(doc_id, org, uploader)
    doc.visibility = DocumentVisibility.PRIVATE
    return doc


@pytest.fixture
async def env():
    """两个组织 + 每组织一个普通成员（持有 SEARCH_KNOWLEDGE_BASE）+ 文档。"""
    from app.core.database import get_db
    from app.core.security import get_current_user
    from app.main import app

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
                Organization(id=ORG_A, name="eval-org-a", owner_id=USER_A),
                Organization(id=ORG_B, name="eval-org-b", owner_id=USER_B),
                User(
                    id=USER_A,
                    username="eva",
                    email="eva@example.com",
                    hashed_password="x",
                    organization_id=ORG_A,
                    is_active=True,
                ),
                User(
                    id=USER_B,
                    username="evb",
                    email="evb@example.com",
                    hashed_password="x",
                    organization_id=ORG_B,
                    is_active=True,
                ),
                User(
                    id=USER_C,
                    username="evc",
                    email="evc@example.com",
                    hashed_password="x",
                    organization_id=ORG_A,
                    is_active=True,
                ),
                _doc(DOC_A1, ORG_A, USER_A),
                _doc(DOC_A2, ORG_A, USER_A),
                _doc(DOC_B1, ORG_B, USER_B),
                _private_doc(DOC_PRIVATE, ORG_A, USER_A),
            ]
        )
        role = Role(name="User", is_system_role=True)
        for perm in (
            PermissionType.SEARCH_KNOWLEDGE_BASE,
            PermissionType.VIEW_KNOWLEDGE_BASE,
        ):
            role.permissions.append(Permission(name=perm.value))
        s.add(role)
        await s.execute(
            user_organization.insert(),
            [
                {"user_id": USER_A, "organization_id": ORG_A},
                {"user_id": USER_B, "organization_id": ORG_B},
            ],
        )
        await s.commit()

    state = SimpleNamespace(user=_make_user())

    async def _override_db():
        async with factory() as session:
            yield session

    def _override_user():
        return state.user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://eval") as client:
        yield SimpleNamespace(client=client, state=state, factory=factory)
    app.dependency_overrides.clear()
    await engine.dispose()


def _as(env, uid=USER_A, org=ORG_A):
    env.state.user = _make_user(uid, org)


async def _create(env, name="case", query="q", ids=None, tags=None) -> dict:
    payload = {
        "name": name,
        "query": query,
        "expected_document_ids": ids if ids is not None else [DOC_A1],
        "tags": tags or [],
    }
    r = await env.client.post(CASES, json=payload)
    assert r.status_code == 200, r.text
    return r.json()["data"]


class _FakeKnowledge:
    """把 knowledge_service 检索替换成按 query 返回固定结果的假件（记录调用）。"""

    def __init__(self, results_by_query: dict[str, list[dict]], error_queries=()):
        self.results_by_query = results_by_query
        self.error_queries = set(error_queries)
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        query = kwargs["query"]
        if query in self.error_queries:
            raise RuntimeError("fake retrieval boom")
        docs = self.results_by_query.get(query, [])
        results = [
            {
                "id": doc_id,
                "content": f"snippet-{doc_id}",
                "score": 1.0 - idx * 0.1,
                "source": {"document_id": doc_id, "filename": f"{doc_id}.md"},
            }
            for idx, doc_id in enumerate(docs)
        ]
        return results, None


# ── 鉴权 ────────────────────────────────────────────────────────────────


async def test_endpoints_require_auth(env):
    """无 Authorization 头 → 401（未认证不可访问，四条端点逐一钉住）。"""
    from app.core.security import get_current_user
    from app.main import app

    app.dependency_overrides.pop(get_current_user, None)
    try:
        assert (await env.client.get(CASES)).status_code == 401
        assert (await env.client.post(RUN, json={})).status_code == 401
        assert (await env.client.post(CASES, json={})).status_code == 401
        assert (await env.client.delete(f"{CASES}/{uuid4()}")).status_code == 401
    finally:
        app.dependency_overrides[get_current_user] = lambda: env.state.user


# ── 创建 / 列表 / 校验 ─────────────────────────────────────────────────


async def test_create_and_list_roundtrip(env):
    _as(env)
    created = await _create(env, name="退款时效", query="退款多久到账", tags=["退款", " 退款 "])
    assert created["organization_id"] == ORG_A
    assert created["created_by"] == USER_A
    assert created["expected_document_ids"] == [DOC_A1]
    # 空白/重复标签被 schema 归一化
    assert created["tags"] == ["退款"]

    r = await env.client.get(CASES)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["total"] == 1
    assert data["data"][0]["id"] == created["id"]


async def test_create_rejects_foreign_document_ids(env):
    """跨组织金标 → 400（不是静默保存成永远 miss 的用例）。"""
    _as(env)
    r = await env.client.post(
        CASES,
        json={"name": "x", "query": "q", "expected_document_ids": [DOC_B1]},
    )
    assert r.status_code == 400, r.text
    async with env.factory() as s:
        assert (await s.execute(select(RetrievalEvalCase))).scalars().all() == []


async def test_create_rejects_empty_query(env):
    _as(env)
    r = await env.client.post(
        CASES, json={"name": "x", "query": "   ", "expected_document_ids": [DOC_A1]}
    )
    assert r.status_code == 422, r.text


async def test_read_update_and_crud_boundaries(env):
    """CRUD 闭环 + 跨组织读/改 404 且目标行不变（不是静默成功）。"""
    _as(env)
    case = await _create(env, name="before", query="q1", ids=[DOC_A1], tags=["a"])

    got = await env.client.get(f"{CASES}/{case['id']}")
    assert got.status_code == 200, got.text
    assert got.json()["data"]["name"] == "before"

    updated = await env.client.put(
        f"{CASES}/{case['id']}",
        json={"name": "after", "query": "q2", "tags": []},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["name"] == "after"
    assert updated.json()["data"]["query"] == "q2"
    assert updated.json()["data"]["tags"] == []
    assert updated.json()["data"]["expected_document_ids"] == [DOC_A1]

    # 跨组织读/改一律 404，且不能泄露或改动目标行。
    _as(env, USER_B, ORG_B)
    assert (await env.client.get(f"{CASES}/{case['id']}")).status_code == 404
    assert (
        await env.client.put(f"{CASES}/{case['id']}", json={"name": "hijacked"})
    ).status_code == 404
    async with env.factory() as s:
        persisted = await s.get(RetrievalEvalCase, case["id"])
        assert persisted.name == "after"
        assert persisted.query == "q2"

    # 更新时替换成外组织金标同样 400（与创建共用校验）。
    _as(env)
    r = await env.client.put(f"{CASES}/{case['id']}", json={"expected_document_ids": [DOC_B1]})
    assert r.status_code == 400, r.text

    # 空更新 / 非可空字段显式 null 被 schema 拒绝（不能撞 DB 约束变 500）。
    assert (await env.client.put(f"{CASES}/{case['id']}", json={})).status_code == 422
    assert (
        await env.client.put(f"{CASES}/{case['id']}", json={"expected_document_ids": None})
    ).status_code == 422


@pytest.mark.parametrize("bad_k", [0, -1, 51, "abc", 1.5])
def test_run_rejects_invalid_top_k(bad_k):
    """k 必须是正的有界整数：0/负数/超上限/非数字/浮点都不得进入检索。"""
    from pydantic import ValidationError

    from app.schemas.retrieval_eval import RetrievalEvalRunRequest

    with pytest.raises(ValidationError):
        RetrievalEvalRunRequest(top_k=bad_k)


async def test_run_rejects_invalid_top_k_over_http(env):
    _as(env)
    r = await env.client.post(RUN, json={"top_k": 0})
    assert r.status_code == 422, r.text
    r = await env.client.post(RUN, json={"top_k": 51})
    assert r.status_code == 422, r.text


# ── 组织隔离 ───────────────────────────────────────────────────────────


async def test_list_is_org_isolated(env):
    _as(env)
    await _create(env, name="a-case")
    _as(env, USER_B, ORG_B)
    r = await env.client.get(CASES)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["total"] == 0


async def test_delete_is_org_isolated_and_404(env):
    _as(env)
    case = await _create(env, name="a-case")

    _as(env, USER_B, ORG_B)
    r = await env.client.delete(f"{CASES}/{case['id']}")
    assert r.status_code == 404, r.text
    # 目标行必须仍在：隔离失败的典型症状是「404 但其实删掉了」
    async with env.factory() as s:
        assert await s.get(RetrievalEvalCase, case["id"]) is not None

    _as(env)
    r = await env.client.delete(f"{CASES}/{case['id']}")
    assert r.status_code == 200, r.text
    async with env.factory() as s:
        assert await s.get(RetrievalEvalCase, case["id"]) is None


async def test_run_is_org_isolated(env):
    """B 组织运行评测时看不到 A 的用例（检索调用数为 0）。"""
    _as(env)
    await _create(env, name="a-case", query="qa")

    fake = _FakeKnowledge({"qa": [DOC_A1]})
    _as(env, USER_B, ORG_B)
    with patch("app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta", fake):
        r = await env.client.post(RUN, json={})
    assert r.status_code == 200, r.text
    report = r.json()["data"]
    assert report["counts"] == {"cases": 0, "evaluated": 0, "errors": 0}
    assert report["metrics"] == {"recall_at_k": None, "mrr": None, "hit_at_k": None}
    assert fake.calls == []


# ── 运行：指标正确性 ───────────────────────────────────────────────────


async def test_run_computes_recall_and_mrr_from_fake_pipeline(env):
    """两条用例：一条 top-2 命中（RR=1/2），一条 top-1 命中（RR=1）。

    期望 recall@5 宏平均 = 1.0；MRR = (0.5 + 1.0)/2 = 0.75；hit@5 = 1.0。
    变异点：把 recall 算成「命中条目数 / top_k」或忽略名次都不会得到该组值。
    """
    _as(env)
    await _create(env, name="c1", query="q1", ids=[DOC_A1])
    await _create(env, name="c2", query="q2", ids=[DOC_A2])

    fake = _FakeKnowledge(
        {
            "q1": [DOC_B1, DOC_A1, DOC_A2],  # 第 1 名是错文档；第 2 名命中
            "q2": [DOC_A2, DOC_A1],  # 第 1 名命中
        }
    )
    with patch("app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta", fake):
        r = await env.client.post(RUN, json={"top_k": 5})
    assert r.status_code == 200, r.text
    report = r.json()["data"]

    assert report["persisted"] is False
    assert report["counts"] == {"cases": 2, "evaluated": 2, "errors": 0}
    assert report["metrics"]["recall_at_k"] == pytest.approx(1.0)
    assert report["metrics"]["mrr"] == pytest.approx(0.75)
    assert report["metrics"]["hit_at_k"] == pytest.approx(1.0)

    by_name = {row["name"]: row for row in report["results"]}
    assert by_name["c1"]["first_hit_rank"] == 2
    assert by_name["c1"]["reciprocal_rank"] == pytest.approx(0.5)
    assert by_name["c2"]["first_hit_rank"] == 1
    # 检索调用透传 top_k 与组织/查看者
    assert all(call["top_k"] == 5 for call in fake.calls)
    assert all(call["organization_id"] == ORG_A for call in fake.calls)
    assert all(call["viewer_user_id"] == USER_A for call in fake.calls)

    # 默认不写库：结果集之外没有任何回写路径（用例数不变）
    async with env.factory() as s:
        assert len((await s.execute(select(RetrievalEvalCase))).scalars().all()) == 2


async def test_run_respects_top_k_cutoff(env):
    """top_k=1 时第 2 名命中的用例不得算召回（真按 k 截断）。"""
    _as(env)
    await _create(env, name="late", query="q1", ids=[DOC_A1])
    fake = _FakeKnowledge({"q1": [DOC_B1, DOC_A1]})
    with patch("app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta", fake):
        r = await env.client.post(RUN, json={"top_k": 1})
    assert r.status_code == 200, r.text
    row = r.json()["data"]["results"][0]
    assert row["recall_at_k"] == 0.0
    assert row["first_hit_rank"] is None
    assert row["reciprocal_rank"] == 0.0
    assert fake.calls[0]["top_k"] == 1


async def test_run_partial_recall_multiple_expected_docs(env):
    """期望 2 篇、top-k 里只中 1 篇 → recall=0.5（不是 1.0）。"""
    _as(env)
    await _create(env, name="multi", query="q1", ids=[DOC_A1, DOC_A2])
    fake = _FakeKnowledge({"q1": [DOC_A1]})
    with patch("app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta", fake):
        r = await env.client.post(RUN, json={})
    report = r.json()["data"]
    assert report["metrics"]["recall_at_k"] == pytest.approx(0.5)
    assert report["results"][0]["matched_count"] == 1
    assert report["results"][0]["expected_count"] == 2


async def test_run_empty_test_set_is_safe(env):
    _as(env)
    with patch(
        "app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta",
        _FakeKnowledge({}),
    ):
        r = await env.client.post(RUN, json={})
    assert r.status_code == 200, r.text
    report = r.json()["data"]
    assert report["counts"] == {"cases": 0, "evaluated": 0, "errors": 0}
    assert report["metrics"] == {"recall_at_k": None, "mrr": None, "hit_at_k": None}
    assert report["results"] == []


async def test_run_single_query_error_counted_not_in_denominator(env):
    """一条成功一条异常：错误单独计数，指标只按成功用例算（不当作 miss）。"""
    _as(env)
    await _create(env, name="ok", query="good", ids=[DOC_A1])
    await _create(env, name="boom", query="bad", ids=[DOC_A1])
    fake = _FakeKnowledge({"good": [DOC_A1]}, error_queries={"bad"})
    with patch("app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta", fake):
        r = await env.client.post(RUN, json={})
    report = r.json()["data"]
    assert report["counts"] == {"cases": 2, "evaluated": 1, "errors": 1}
    assert len(report["error_case_ids"]) == 1
    assert report["metrics"]["recall_at_k"] == pytest.approx(1.0)
    boom = next(row for row in report["results"] if row["name"] == "boom")
    assert boom["status"] == "error"
    # 只回显异常类型，绝不把可能含 query 的异常正文写进响应（反泄漏契约）。
    assert boom["error"] == "RuntimeError"


async def test_run_case_ids_subset(env):
    """case_ids 只跑指定子集；外组织的 case_id 不会被加载。"""
    _as(env)
    c1 = await _create(env, name="c1", query="q1", ids=[DOC_A1])
    await _create(env, name="c2", query="q2", ids=[DOC_A2])
    fake = _FakeKnowledge({"q1": [DOC_A1]})
    with patch("app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta", fake):
        r = await env.client.post(RUN, json={"case_ids": [c1["id"], str(uuid4())]})
    report = r.json()["data"]
    assert report["counts"]["cases"] == 1
    assert report["results"][0]["case_id"] == c1["id"]


async def test_run_empty_case_ids_is_empty_subset_not_all(env):
    """显式 case_ids=[] 必须跑零条，不能回落成「缺省 = 全量」。"""
    _as(env)
    await _create(env, name="c1", query="q1", ids=[DOC_A1])
    fake = _FakeKnowledge({"q1": [DOC_A1]})
    with patch("app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta", fake):
        r = await env.client.post(RUN, json={"case_ids": []})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["counts"] == {"cases": 0, "evaluated": 0, "errors": 0}
    assert fake.calls == []


# ── 纯函数口径（无需 HTTP/DB）─────────────────────────────────────────


def test_score_case_pure_function_edges():
    from app.services.retrieval_eval_service import score_case

    # 未命中
    assert score_case([DOC_A1], [DOC_A2], 5) == {
        "expected_count": 1,
        "matched_count": 0,
        "recall_at_k": 0.0,
        "first_hit_rank": None,
        "reciprocal_rank": 0.0,
    }
    # 多期望、命中其一的序位
    score = score_case([DOC_A1, DOC_A2], [DOC_A2, DOC_A1], 5)
    assert score["matched_count"] == 2
    assert score["recall_at_k"] == 1.0
    assert score["first_hit_rank"] == 1


async def test_create_rejects_private_document_not_visible_to_viewer(env):
    """同组织私有文档：上传者可用作金标，其他成员不可（ACL 校验不是只看组织）。"""
    _as(env)
    r = await env.client.post(
        CASES,
        json={"name": "private", "query": "q", "expected_document_ids": [DOC_PRIVATE]},
    )
    assert r.status_code == 200, r.text  # USER_A 是上传者

    # USER_C 同组织但无授权 → 400，且不回显具体 document id。
    _as(env, USER_C, ORG_A)
    r = await env.client.post(
        CASES,
        json={"name": "private", "query": "q", "expected_document_ids": [DOC_PRIVATE]},
    )
    assert r.status_code == 400, r.text
    assert DOC_PRIVATE not in r.text

    # 显式授权后可用作金标（ACL 来自 DB，不是写死的拒绝）。
    async with env.factory() as s:
        s.add(
            DocumentAcl(
                id=str(uuid4()),
                document_id=DOC_PRIVATE,
                subject_type="user",
                subject_id=USER_C,
            )
        )
        await s.commit()
    r = await env.client.post(
        CASES,
        json={"name": "granted", "query": "q", "expected_document_ids": [DOC_PRIVATE]},
    )
    assert r.status_code == 200, r.text


async def test_run_uses_viewer_acl_retrieval_path_and_does_not_count_blocked_doc(env):
    """评测运行走正常检索链路（含 pipeline ACL）：私有文档不得被算作可召回。

    变异点：若 _make_retrieve_fn 漏传 viewer_user_id（或改用内部系统检索），
    pipeline 的 acl_enabled=False → blocked_document_ids 为空 → 私有文档会被
    算进 top-k，本测试的 recall/MRR 断言失败。
    """
    _as(env, USER_C, ORG_A)
    # USER_C 同组织但无授权 → 金标层即拒绝私有文档（不回显具体 id）。
    r = await env.client.post(
        CASES,
        json={"name": "blocked", "query": "q", "expected_document_ids": [DOC_PRIVATE]},
    )
    assert r.status_code == 400, r.text
    assert DOC_PRIVATE not in r.text

    # 合法用例：两张组织可见文档，金标为 DOC_A1。
    await _create(env, name="safe", query="q1", ids=[DOC_A1])

    # 用真实 RAGPipeline（含 ACL 解析）+ 假 retriever：假 retriever 严格尊重
    # blocked_document_ids，模拟 ES 的 must_not；这样测试检验的是 eval 端点
    # 是否真的走 viewer-aware 检索路径，而不是另一个绕过 ACL 的捷径。
    from app.rag.pipeline import RAGPipeline

    pipeline = RAGPipeline()

    async def _fake_retrieve(query, organization_id, top_k, document_ids, **kwargs):
        blocked = set(kwargs.get("blocked_document_ids") or [])
        hits = [
            {"document_id": doc_id, "filename": f"{doc_id}.md", "text": f"t-{doc_id}"}
            for doc_id in [DOC_PRIVATE, DOC_A1, DOC_A2]
            if doc_id not in blocked
        ]
        return hits[:top_k], None, {}, []

    pipeline.retriever.retrieve = AsyncMock(side_effect=_fake_retrieve)
    from unittest.mock import patch as _patch

    async def _noop_rerank(query, hits, rerank_client=None, **kwargs):
        return hits, {"status": "noop", "reason": "test_stub"}

    # rerank 会加载真实 cross-encoder 并重排，测试中固定为不排序，
    # 使「ACL 过滤 → 名次变化」这一因果关系可判定（且避免下载模型）。
    with (
        _patch("app.rag.pipeline.rerank", new=_noop_rerank),
        _patch("app.services.rag_service.get_rag_pipeline", return_value=pipeline),
        _patch("app.core.database.AsyncSessionLocal", lambda: env.factory()),
    ):
        result = await env.client.post(RUN, json={"top_k": 5})

    assert result.status_code == 200, result.text
    report = result.json()["data"]
    row = report["results"][0]
    # USER_C 无权看 DOC_PRIVATE：pipeline 必须传 blocked id，假检索才能排除它；
    # 于是命中名次是 1（若 ACL 被绕过，DOC_PRIVATE 占第 1 名，命中名次变 2）。
    assert DOC_PRIVATE not in row["retrieved_document_ids"]
    assert row["first_hit_rank"] == 1
    assert row["reciprocal_rank"] == pytest.approx(1.0)

    # 变异点：viewer 改为上传者本人（有权限）时 DOC_PRIVATE 变成可召回，
    # 证明上面的差异确实由 ACL 造成，而非假件写死排除。
    _as(env, USER_A, ORG_A)
    with (
        _patch("app.rag.pipeline.rerank", new=_noop_rerank),
        _patch("app.services.rag_service.get_rag_pipeline", return_value=pipeline),
        _patch("app.core.database.AsyncSessionLocal", lambda: env.factory()),
    ):
        result2 = await env.client.post(RUN, json={"top_k": 5})
    assert result2.json()["data"]["results"][0]["retrieved_document_ids"][0] == DOC_PRIVATE


def test_run_eval_reuses_harness_loop():
    """变异点：若 service 自建循环而不再调用 evals.harness.run_eval，本测试失败。"""
    import inspect

    from app.services import retrieval_eval_service as svc
    from evals import harness

    source = inspect.getsource(svc.run_eval)
    assert "harness_run_eval" in source, "运行评测必须直接调用 evals.harness.run_eval"
    assert harness.run_eval is not None


def test_error_response_does_not_leak_query_text():
    """变异点：若把 harness 的原始异常字符串（含 query）写进响应，本测试失败。"""

    from app.services.retrieval_eval_service import run_eval

    sentinel = "secret_query_" + "8f3a91"

    class _Case:
        id = "c1"
        name = "x"
        query = sentinel
        expected_document_ids = ["d1"]

    async def _boom(query: str, top_k: int):
        raise RuntimeError(f"retrieval failed for {query}")

    async def _go():
        return await run_eval([_Case()], _boom, 5)

    import asyncio

    report = asyncio.run(_go())
    row = report["results"][0]
    assert row["status"] == "error"
    # 错误字段只保留异常类型；原始异常消息（含 query 原文）不得进入响应/日志。
    assert row["error"] == "RuntimeError"
    assert sentinel not in row["error"]
    # query 原文本身是调用者自己保存的用例内容，允许在响应里回显给本人；
    # 本测试针对的是「异常消息把 query 带进错误通道」这一泄漏面。


def test_retrieved_document_ids_accepts_both_shapes():
    from app.services.retrieval_eval_service import retrieved_document_ids

    assert retrieved_document_ids(
        [
            {"id": "a"},
            {"document_id": "b"},
            {"source": {"document_id": "c"}},
            {"id": "a"},
        ]
    ) == ["a", "b", "c"]
