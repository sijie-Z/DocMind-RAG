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

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event, select
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

    state = SimpleNamespace(user=_make_user(), audit=AsyncMock())

    async def _override_db():
        async with factory() as session:
            yield session

    def _override_user():
        return state.user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    # 审计写独立 MySQL session（测试环境无该库）：patch 掉真实写入并记录调用，
    # 以便断言 action/组织/user，同时不把日志/DB 故障混进评测断言。
    from app.services.audit_service import audit_service

    with patch.object(audit_service, "log_activity", state.audit):
        async with httpx.AsyncClient(transport=transport, base_url="http://eval") as client:
            yield SimpleNamespace(client=client, state=state, factory=factory, engine=engine)
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
    payload = r.json()["data"]
    assert payload["total"] == 0
    # 只断言 total 不够：rows 的组织过滤若被删掉，total=0 仍可能绿而数据已泄露。
    assert payload["data"] == []


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
    assert report["metrics"] == {
        "recall_at_k": None,
        "mrr": None,
        "document_hit_at_k": None,
        "hit_at_k": None,
    }
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
    # document_hit_at_k 是文档级口径；hit_at_k 是历史别名，二者必须一致，
    # 且与条款级 clause_hit@k 不是同一判定（见服务 docstring）。
    assert report["metrics"]["document_hit_at_k"] == pytest.approx(1.0)
    assert report["metrics"]["hit_at_k"] == report["metrics"]["document_hit_at_k"]

    by_name = {row["name"]: row for row in report["results"]}
    # error_code 在所有结果行形状一致：ok=None，stale/generic 为稳定机器码。
    assert all(row["error_code"] is None for row in report["results"])
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
    assert report["metrics"] == {
        "recall_at_k": None,
        "mrr": None,
        "document_hit_at_k": None,
        "hit_at_k": None,
    }
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
    assert boom["error_code"] == "RETRIEVAL_ERROR"


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


async def test_run_eval_reuses_harness_loop():
    """行为 spy：运行评测必须真的调用 evals.harness.run_eval（非源码文本断言）。

    变异点：service 自建循环（或只写诱饵注释）时 spy 不被调用，本测试失败。
    """
    from app.services import retrieval_eval_service as svc

    class _Case:
        id = "c1"
        name = "x"
        query = "q"
        expected_document_ids = [DOC_A1]

    async def _retrieve(query, top_k):
        return SimpleNamespace(results=[])

    spy = AsyncMock(side_effect=AssertionError("harness loop was bypassed"))
    with patch.object(svc, "harness_run_eval", spy):
        with pytest.raises(AssertionError, match="harness loop was bypassed"):
            await svc.run_eval([_Case()], _retrieve, 7)
    assert spy.await_count == 1
    args = spy.await_args.args
    assert len(args[0]) == 1 and args[2] == 7


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


# ── P2 独立评审修复：金标可检索性 / strict 故障语义 / 审计 / 泄漏 / 上限 ──


async def test_create_and_update_reject_non_searchable_golden_docs(env):
    """停用/未索引的金标必须在写入时拒绝（正常检索侧这两类文档都不召回）。

    变异点：把 _validate_expected_docs 的 status/is_enabled 条件删掉，本测试
    的两组 400 断言立即失败。
    """
    _as(env)
    disabled = str(uuid4())
    unindexed = str(uuid4())
    async with env.factory() as s:
        d1 = _doc(disabled, ORG_A, USER_A)
        d1.is_enabled = False
        d2 = _doc(unindexed, ORG_A, USER_A)
        d2.status = DocumentStatus.PARSING
        s.add_all([d1, d2])
        await s.commit()

    for bad_id in (disabled, unindexed, str(uuid4())):
        r = await env.client.post(
            CASES, json={"name": "bad", "query": "q", "expected_document_ids": [bad_id]}
        )
        assert r.status_code == 400, r.text

    case = await _create(env)
    r = await env.client.put(f"{CASES}/{case['id']}", json={"expected_document_ids": [disabled]})
    assert r.status_code == 400, r.text
    async with env.factory() as s:
        persisted = await s.get(RetrievalEvalCase, case["id"])
        assert persisted.expected_document_ids == [DOC_A1]


async def test_run_reports_stale_golden_as_error_not_zero_recall(env):
    """运行时金标失效（文档停用）→ error + 机器码，不进分母。

    变异点：删掉 run_eval 的 searchable 快照检查，失效文档会被当成正常零召回，
    recall=0.0 且 status=ok，本测试失败。
    """
    _as(env)
    stale = await _create(env, name="stale", query="q1", ids=[DOC_A2])
    healthy = await _create(env, name="healthy", query="q2", ids=[DOC_A1])
    async with env.factory() as s:
        doc = await s.get(Document, DOC_A2)
        doc.is_enabled = False
        await s.commit()

    fake = _FakeKnowledge({"q1": [DOC_A2], "q2": [DOC_A1]})
    with patch("app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta", fake):
        r = await env.client.post(RUN, json={})
    assert r.status_code == 200, r.text
    report = r.json()["data"]
    assert report["counts"] == {"cases": 2, "evaluated": 1, "errors": 1}
    rows = {row["case_id"]: row for row in report["results"]}
    assert rows[stale["id"]]["status"] == "error"
    assert rows[stale["id"]]["error"] == "STALE_GOLDEN_DOCUMENTS"
    assert rows[stale["id"]]["error_code"] == "STALE_GOLDEN_DOCUMENTS"
    assert rows[stale["id"]]["recall_at_k"] is None
    assert rows[stale["id"]]["retrieved_document_ids"] == []
    assert rows[healthy["id"]]["status"] == "ok"
    assert rows[healthy["id"]]["recall_at_k"] == 1.0
    assert report["metrics"]["recall_at_k"] == pytest.approx(1.0)
    assert stale["id"] in report["error_case_ids"]
    assert [call["query"] for call in fake.calls] == ["q2"]  # stale 不触发检索


async def test_run_reports_revoked_private_golden_as_stale_error(env):
    """运行者私有权限被撤销 → ACL 屏蔽，金标失效报 error（不泄露文档存在性）。"""
    owner = await _create(env, name="private", query="q", ids=[DOC_PRIVATE])
    _as(env, USER_C, ORG_A)
    fake = _FakeKnowledge({"q": [DOC_PRIVATE]})
    with patch("app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta", fake):
        r = await env.client.post(RUN, json={"case_ids": [owner["id"]]})
    assert r.status_code == 200, r.text
    row = r.json()["data"]["results"][0]
    assert row["status"] == "error"
    assert row["error"] == "STALE_GOLDEN_DOCUMENTS"
    assert row["recall_at_k"] is None
    assert fake.calls == []


async def test_strict_search_option_controls_generic_exception_semantics(caplog):
    """strict 默认 False 吞异常；仅 strict=True 上抛（默认端点行为不变）。

    变异点：把 knowledge_service 兜底日志改回 ``{e}`` 时 caplog 必含 sentinel。
    """
    from app.services.knowledge_service import KnowledgeService

    sentinel = "leak_" + "c71d2b"
    mock = AsyncMock(side_effect=RuntimeError("boom " + sentinel))
    with (
        caplog.at_level(logging.ERROR),
        patch("app.services.rag_service.rag_service.search_knowledge_base", mock),
    ):
        results, debug = await KnowledgeService().search_knowledge_with_meta(
            "query", str(ORG_A), top_k=5
        )
        assert (results, debug) == ([], None)
        with pytest.raises(RuntimeError):
            await KnowledgeService().search_knowledge_with_meta(
                "query", str(ORG_A), top_k=5, strict=True
            )
    assert sentinel not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_downstream_exception_is_error_not_zero_recall_and_no_log_leak(env, caplog):
    """真实 knowledge_service 下游抛异常 → 逐条 error、响应/日志都不含异常正文。

    变异点：把 _make_retrieve_fn 的 strict=True 去掉，异常被吞成 []，本用例
    变 status=ok + recall=0.0，断言失败；把 knowledge_service 的日志恢复成
    回显异常字符串，caplog 中会出现 sentinel，断言失败。sentinel 运行时拼接。
    """
    _as(env)
    await _create(env, name="boom", query="q1", ids=[DOC_A1])
    sentinel = "secret_query_" + "b47f0c"
    mock = AsyncMock(side_effect=RuntimeError("failure with " + sentinel))
    with (
        caplog.at_level(logging.ERROR),
        patch("app.services.rag_service.rag_service.search_knowledge_base", mock),
    ):
        r = await env.client.post(RUN, json={})
    assert r.status_code == 200, r.text
    report = r.json()["data"]
    row = report["results"][0]
    assert row["status"] == "error"
    assert row["error"] == "RuntimeError"
    assert row["recall_at_k"] is None
    assert report["counts"]["errors"] == 1
    assert report["metrics"]["recall_at_k"] is None
    assert sentinel not in r.text
    assert sentinel not in caplog.text


async def test_endpoint_exception_logs_class_name_only(env, caplog):
    """端点兜底日志只记异常类型，不记消息（消息可能含 query 原文）。"""
    _as(env)
    sentinel = "raw_message_" + "d3a91f"
    with (
        caplog.at_level(logging.ERROR),
        patch(
            "app.api.v1.endpoints.knowledge.retrieval_eval_service.create_case",
            new=AsyncMock(side_effect=RuntimeError(sentinel)),
        ),
    ):
        r = await env.client.post(
            CASES, json={"name": "x", "query": sentinel, "expected_document_ids": [DOC_A1]}
        )
    assert r.status_code == 500, r.text
    assert sentinel not in caplog.text
    assert sentinel not in r.text
    assert "RuntimeError" in caplog.text


async def test_full_run_cap_and_case_ids_cap(env):
    """全量运行最多 100 条；case_ids 最多 50 条（schema + service 双层）。"""
    from pydantic import ValidationError

    from app.schemas.retrieval_eval import RetrievalEvalRunRequest

    _as(env)
    with pytest.raises(ValidationError):
        RetrievalEvalRunRequest(case_ids=[str(uuid4()) for _ in range(51)])

    async with env.factory() as s:
        s.add_all(
            [
                RetrievalEvalCase(
                    id=str(uuid4()),
                    organization_id=ORG_A,
                    name=f"bulk-{i}",
                    query="q",
                    expected_document_ids=[DOC_A1],
                    tags=[],
                    created_by=USER_A,
                )
                for i in range(101)
            ]
        )
        await s.commit()
    fake = _FakeKnowledge({})
    with patch("app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta", fake):
        r = await env.client.post(RUN, json={})
    assert r.status_code == 400, r.text
    assert "100" in r.text
    assert fake.calls == []
    # 显式子集仍可运行（50 条以内），不受全量上限影响。
    with patch("app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta", fake):
        r2 = await env.client.post(RUN, json={"case_ids": [str(uuid4())]})
    assert r2.status_code == 200, r2.text


async def test_update_case_keeps_org_predicate_even_with_stale_loaded_case(env):
    """update 的 SQL 组织条件是独立于 get_case 的第二道租户门。

    变异点：删掉 update_case 中 WHERE 的 `RetrievalEvalCase.organization_id ==
    organization_id`，本用例中 ORG_B 的调用就会把 ORG_A 的用例改名，断言红。
    """
    from app.services import retrieval_eval_service

    async with env.factory() as s:
        case = await _create(env, name="original", query="q", ids=[DOC_A1])
        # 绕过上游 get_case，模拟 TOCTOU / 缓存了错误租户对象的防御场景。
        loaded = await s.get(RetrievalEvalCase, case["id"])
        with patch.object(retrieval_eval_service, "get_case", new=AsyncMock(return_value=loaded)):
            await retrieval_eval_service.update_case(
                s,
                organization_id=ORG_B,
                case_id=case["id"],
                viewer_user_id=USER_A,
                changes={"name": "cross-tenant-write"},
            )
        await s.refresh(loaded)
        assert loaded.name == "original"


async def test_full_run_cap_prefetch_is_bounded(env):
    """全量上限必须在 SQL 层 LIMIT 101，不能先取无限行再在内存里拒绝。

    变异点：删掉 load_cases 的 `.limit(MAX_CASES_PER_RUN + 1)` 后，监听器记录的
    SQL 不再含 LIMIT，本测试变红（即使响应仍为 400 也要抓“先无界取行”）。
    """

    # 变异点：删掉上限后 SQL 会变无界，即使响应仍 400 也必须抓到无 LIMIT 的取行。
    from app.services import retrieval_eval_service

    def _see_sql(conn, cursor, statement, parameters, context, executemany):
        if "retrieval_eval_case" in statement and "SELECT" in statement.upper():
            seen["selects"].append((statement, tuple(parameters or ())))

    seen: dict[str, list[tuple[str, tuple]]] = {"selects": []}
    event.listen(env.engine.sync_engine, "before_cursor_execute", _see_sql)
    try:
        async with env.factory() as s:
            cases = await retrieval_eval_service.load_cases(s, organization_id=ORG_A, case_ids=None)
        assert cases == []
    finally:
        event.remove(env.engine.sync_engine, "before_cursor_execute", _see_sql)
    assert seen["selects"], "load_cases 必须实际执行 SELECT"
    statements = [sql for sql, _ in seen["selects"]]
    parameters = [params for _, params in seen["selects"]]
    assert any("LIMIT" in sql.upper() for sql in statements), statements
    # SQLAlchemy 把 limit(101) 编成绑定参数；断言参数而非 SQL 字面量。
    assert any(101 in params for params in parameters), parameters


async def test_service_case_ids_cap_is_enforced_before_db_read(env):
    """service 层 case_ids 上限是 schema 之外的第二道门，必须直接测到。

    变异点：删除 load_cases 中 `len(case_ids) > MAX_CASE_IDS_PER_RUN` 分支后，
    本测试不再抛 ValidationError，断言变红。
    """
    from app.exceptions import ValidationError
    from app.services import retrieval_eval_service

    async with env.factory() as s:
        with pytest.raises(ValidationError):
            await retrieval_eval_service.load_cases(
                s,
                organization_id=ORG_A,
                case_ids=[str(uuid4()) for _ in range(51)],
            )


async def test_document_hit_at_k_is_document_level_not_clause_level(env):
    """文档级命中只看 document_id：没有 filename/条号也命中（与 clause_hit@k 不同）。

    变异点：若把 document_hit_at_k 接回 clause_hit@k 的判定，本测试失败。
    """
    _as(env)
    await _create(env, name="doc-level", query="q1", ids=[DOC_A1])
    fake = _FakeKnowledge({})
    with (
        patch("app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta", fake),
        patch(
            "app.services.retrieval_eval_service.retrieved_document_ids",
            new=lambda results: [DOC_A1],
        ),
    ):
        r = await env.client.post(RUN, json={})
    assert r.status_code == 200, r.text
    metrics = r.json()["data"]["metrics"]
    assert metrics["document_hit_at_k"] == 1.0
    assert metrics["hit_at_k"] == 1.0


async def test_eval_crud_and_run_are_audited_without_query(env):
    """create/update/delete/run 均有审计记录，detail 只含 org/user 与计数。

    变异点：删掉任一 audit_service.log_activity 调用，对应 action 断言失败；
    把 query 写入 detail，sentinel 断言失败。
    """
    _as(env)
    sentinel = "audit_secret_" + "f82c1e"
    case = await _create(env, name="audit", query=sentinel, ids=[DOC_A1])
    r = await env.client.put(f"{CASES}/{case['id']}", json={"name": "renamed"})
    assert r.status_code == 200, r.text
    with patch(
        "app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge_with_meta",
        _FakeKnowledge({}),
    ):
        rr = await env.client.post(RUN, json={"case_ids": [case["id"]]})
    assert rr.status_code == 200, rr.text
    assert (await env.client.delete(f"{CASES}/{case['id']}")).status_code == 200

    calls = env.state.audit.await_args_list
    actions = [call.kwargs["action"] for call in calls]
    assert actions == [
        "create_retrieval_eval_case",
        "update_retrieval_eval_case",
        "run_retrieval_eval",
        "delete_retrieval_eval_case",
    ]
    for call in calls:
        detail = json.loads(call.kwargs["detail"])
        assert detail["organization_id"] == ORG_A
        assert detail["user_id"] == USER_A
        assert call.kwargs["user_id"] == USER_A
        assert sentinel not in call.kwargs["detail"]
    assert "cases" in json.loads(calls[2].kwargs["detail"])


def test_top_k_default_is_documented_rank5_contract():
    """默认 top_k=5 是 rank-5 产品口径，且显式区别于组织 retrieval_top_k。"""
    from app.schemas.retrieval_eval import RetrievalEvalRunRequest

    field = RetrievalEvalRunRequest.model_fields["top_k"]
    assert field.default == 5
    assert "rank-5" in (field.description or "")
    assert "retrieval_top_k" in (field.description or "")


async def test_hit_at_k_uses_k_not_k_plus_one():
    """命中在 rank k+1 且检索返回 k+1 条时，hit@k / MRR 必须按 k 截断。

    变异点：aggregate(outcomes, cutoffs=(top_k+1,)) 会让 hit@2=1.0、mrr=0.5，
    本测试断言 0.0/0.0，立即变红。
    """
    from app.services import retrieval_eval_service as svc

    class _Case:
        id = "c-bound"
        name = "boundary"
        query = "q"
        expected_document_ids = [DOC_A1]

    from evals.harness import RetrievalObservation

    async def _retrieve(query, top_k):
        # 返回 k+1 条，但期望文档只在第 3 名（k=2 时位于 k+1）。
        return RetrievalObservation(
            results=[{"document_id": DOC_B1}, {"document_id": DOC_A2}, {"document_id": DOC_A1}][
                : top_k + 1
            ]
        )

    real_aggregate = svc.aggregate
    spy = MagicMock(side_effect=real_aggregate)
    with patch.object(svc, "aggregate", spy):
        report = await svc.run_eval([_Case()], _retrieve, 2)
    # 变异点：cutoffs=(top_k+1,) 时此断言失败，无需依赖假结果恰好命中 k+1。
    assert spy.call_args.kwargs["cutoffs"] == (2,)
    row = report["results"][0]
    assert row["first_hit_rank"] is None  # rank 2 是错文档，rank 3 命中
    assert row["reciprocal_rank"] == 0.0
    assert report["metrics"]["document_hit_at_k"] == 0.0
    assert report["metrics"]["mrr"] == 0.0


_BACKEND = Path(__file__).resolve().parents[2]


def test_models_registry_contains_retrieval_eval_case():
    """独立进程只 import app.models，确认表注册、FK 与索引来自 __init__ 导出。

    变异点：从 app/models/__init__.py 移除 RetrievalEvalCase 导入/导出后，
    子进程中 Base.metadata.tables 不再有该表，本测试失败。直接在本测试模块
    `from app.models.retrieval_eval import RetrievalEvalCase` 会自证式污染注册表，
    因此这里必须在干净进程里验证。
    """
    script = r"""
import json
import app.models
from app.core.database import Base
t = Base.metadata.tables.get("retrieval_eval_case")
print("RESULT_JSON=" + json.dumps({
    "exists": t is not None,
    "columns": sorted(c.name for c in t.columns) if t is not None else [],
    "fks": sorted(fk.target_fullname for fk in t.foreign_keys) if t is not None else [],
    "indexes": [
        {"name": idx.name, "columns": [c.name for c in idx.columns]}
        for idx in t.indexes
    ] if t is not None else [],
}))
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_BACKEND),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [line for line in proc.stdout.splitlines() if line.startswith("RESULT_JSON=")]
    assert lines, proc.stdout
    payload = json.loads(lines[-1].split("RESULT_JSON=", 1)[1])
    assert payload["exists"] is True, payload
    assert set(payload["columns"]) >= {
        "id",
        "organization_id",
        "name",
        "query",
        "expected_document_ids",
        "tags",
        "created_by",
        "created_at",
        "updated_at",
    }
    assert set(payload["fks"]) >= {"organizations.id", "users.id"}
    assert any(
        idx["name"] == "ix_retrieval_eval_case_org_created"
        and idx["columns"] == ["organization_id", "created_at"]
        for idx in payload["indexes"]
    )


def test_retrieval_eval_migration_creates_fks_and_index(tmp_path, monkeypatch):
    """034 迁移升级后的真实 schema 必须有双 FK 与组织+创建时间索引。

    变异点：迁移里删掉 organization_id/created_by FK 或复合索引，本测试失败。
    临时 sqlite 库放在 pytest 的 basetemp（工作树 _scratch 下）。
    """
    import sqlite3
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from alembic import command
    from app.core.config import settings

    db_path = Path(str(tmp_path)) / "eval_migration.db"
    backend_root = Path(__file__).resolve().parents[2]

    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))

    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    command.upgrade(cfg, "head")

    with sqlite3.connect(str(db_path)) as conn:
        cols = conn.execute("PRAGMA table_info(retrieval_eval_case)").fetchall()
        fks = conn.execute("PRAGMA foreign_key_list(retrieval_eval_case)").fetchall()
        indexes = conn.execute("PRAGMA index_list(retrieval_eval_case)").fetchall()
        index_cols = {
            row[1]: tuple(col[2] for col in conn.execute(f"PRAGMA index_info({row[1]})"))
            for row in indexes
        }
    assert {row[1] for row in cols} >= {
        "id",
        "organization_id",
        "expected_document_ids",
        "created_by",
        "created_at",
    }
    assert {row[2] for row in fks} >= {"organizations", "users"}
    assert {row[3] for row in fks} >= {"organization_id", "created_by"}
    assert ("organization_id", "created_at") in index_cols.values(), index_cols
