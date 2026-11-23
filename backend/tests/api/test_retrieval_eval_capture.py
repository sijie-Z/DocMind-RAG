"""bad-case → 评测用例捕获：窄端点、复用创建路径、组织内去重、ACL、审计、上限。

沿用 ``test_retrieval_eval_api`` 的两组织 sqlite 基座（真实 FastAPI app +
dependency_overrides，不连 ES）。每个判别用例的 docstring 写明变异点。

设计约束（与批次五计划一致）：
- 捕获端点**不**另写创建逻辑：它调用 ``retrieval_eval_service.create_case``，
  组织隔离/viewer ACL/去重/审计单点复用；
- 同组织「同 query + 同金标集合」返回既有用例（``created=false``），并发由
  ``(organization_id, dedup_key)`` 唯一索引兜底；
- 只接受本组织、已索引启用的文档；私有文档按 viewer ACL fail-closed；
- 审计 detail 不含 query、用例名或金标 id。
"""

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.retrieval_eval import RetrievalEvalCase
from tests.api.test_retrieval_eval_api import (  # noqa: E402
    CASES,
    DOC_A1,
    DOC_A2,
    DOC_B1,
    DOC_PRIVATE,
    ORG_A,
    ORG_B,
    USER_A,
    USER_B,
    USER_C,
    _doc,
)
from tests.api.test_retrieval_eval_api import env as _base_env_fixture  # noqa: E402

env = pytest.fixture(_base_env_fixture.__wrapped__)

CAPTURE = "/api/v1/knowledge/eval/cases/capture"


def _as(env, uid=USER_A, org=ORG_A):
    from tests.api.test_retrieval_eval_api import _make_user

    env.state.user = _make_user(uid, org)


async def _capture(env, *, query="q", ids=None, name=None, tags=None):
    payload: dict = {
        "query": query,
        "expected_document_ids": ids if ids is not None else [DOC_A1],
        "tags": tags or [],
    }
    if name is not None:
        payload["name"] = name
    return await env.client.post(CAPTURE, json=payload)


async def _all_cases(env) -> list[RetrievalEvalCase]:
    async with env.factory() as s:
        return list((await s.execute(select(RetrievalEvalCase))).scalars().all())


# ── 基本闭环与默认名 ────────────────────────────────────────────────────


async def test_capture_saves_case_and_defaults_name_from_query(env):
    """捕获保存 query + 金标；name 缺省取 query 前 100 字符，组织/创建人正确。

    变异点：去掉 capture 端点或 default_name 逻辑 → created/data.name 断言红。
    """
    _as(env)
    long_query = "退款多久到账" * 30  # 180 字符，验证截断
    r = await _capture(env, query=long_query, ids=[DOC_A1, DOC_A2])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    assert body["data"]["organization_id"] == ORG_A
    assert body["data"]["created_by"] == USER_A
    assert body["data"]["expected_document_ids"] == [DOC_A1, DOC_A2]
    assert body["data"]["name"] == long_query[:100]
    rows = await _all_cases(env)
    assert len(rows) == 1


async def test_capture_explicit_name_wins(env):
    """显式 name 不被 query 覆盖（默认名只在缺省时补）。"""
    _as(env)
    r = await _capture(env, query="退款多久到账", ids=[DOC_A1], name="  退款时效  ")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["name"] == "退款时效"


# ── 去重：幂等返回，绝不重复建行 ────────────────────────────────────────


async def test_capture_is_idempotent_for_same_query_and_golden_set(env):
    """同 query + 同金标集合（顺序无关）→ 返回既有用例，created=false，仍只有一行。

    变异点（已实测）：同时删掉 create_case 的 dedup 预查**与**模型的
    ``uq_retrieval_eval_case_org_dedup`` 唯一索引 → 第二次捕获新建第二行，
    rows==1 断言红（见本批变异记录）。只删预查时唯一索引会兜底回读赢家，
    用例仍绿——这正是双层防线的预期行为。
    """
    _as(env)
    first = await _capture(env, query="退款多久到账", ids=[DOC_A1, DOC_A2])
    assert first.status_code == 200, first.text
    # 金标顺序不同、query 首尾带空白：语义相同 → 命中同一行
    second = await _capture(env, query="  退款多久到账  ", ids=[DOC_A2, DOC_A1])
    assert second.status_code == 200, second.text
    assert second.json()["created"] is False
    assert second.json()["data"]["id"] == first.json()["data"]["id"]
    rows = await _all_cases(env)
    assert len(rows) == 1
    # 既有行的名称不被幂等命中静默覆盖（第一次缺省名保留）
    assert rows[0].name == "退款多久到账"


async def test_capture_duplicate_check_is_org_scoped(env):
    """去重只在组织内生效：B 组织可以保存与 A 完全相同内容的用例。

    变异点：dedup 查询漏 organization_id → 第二行被误判重复，created 变 false。
    """
    _as(env)
    a = await _capture(env, query="合同生效时间", ids=[DOC_A1])
    assert a.status_code == 200, a.text
    _as(env, USER_B, ORG_B)
    b = await env.client.post(
        CAPTURE, json={"query": "合同生效时间", "expected_document_ids": [DOC_B1]}
    )
    assert b.status_code == 200, b.text
    assert b.json()["created"] is True
    rows = await _all_cases(env)
    assert {row.organization_id for row in rows} == {ORG_A, ORG_B}


async def test_capture_dedup_distinguishes_query_and_golden_set(env):
    """query 不同或金标集合不同 → 新用例（去重键不能过宽）。

    变异点：dedup_key 只取 query（或只取金标）→ 对应 created==true 断言红。
    """
    _as(env)
    base = await _capture(env, query="q1", ids=[DOC_A1])
    other_query = await _capture(env, query="q2", ids=[DOC_A1])
    other_golden = await _capture(env, query="q1", ids=[DOC_A1, DOC_A2])
    assert base.json()["created"] is True
    assert other_query.json()["created"] is True
    assert other_golden.json()["created"] is True
    assert len(await _all_cases(env)) == 3


async def test_create_endpoint_shares_dedup_path(env):
    """旧创建端点同样走 create_case 去重：再次 POST 相同内容不新增行。

    变异点：把 /eval/cases 改回「直接构造模型并 commit」的第二套路径 → 第二次
    POST 新建第二行，len==1 断言红；created 字段也会缺失。
    """
    _as(env)
    payload = {"name": "x", "query": "q-shared", "expected_document_ids": [DOC_A1]}
    first = await env.client.post(CASES, json=payload)
    second = await env.client.post(CASES, json={**payload, "name": "y"})
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert second.json()["data"]["id"] == first.json()["data"]["id"]
    assert len(await _all_cases(env)) == 1
    # capture 端点与旧端点共享同一去重域：同名内容跨端点也命中。
    captured = await _capture(env, query="q-shared", ids=[DOC_A1])
    assert captured.json()["created"] is False
    assert captured.json()["data"]["id"] == first.json()["data"]["id"]
    assert len(await _all_cases(env)) == 1


async def test_unique_index_is_database_level_backstop(env):
    """绕过服务层直接插两行同 dedup_key：唯一索引必须拒绝第二行。

    变异点：从模型/迁移删除 uq_retrieval_eval_case_org_dedup → 第二次 commit
    不再抛 IntegrityError，pytest.raises 变红（并发窗口失去 DB 兜底）。
    """
    from sqlalchemy.exc import IntegrityError

    from app.services.retrieval_eval_service import case_dedup_key

    key = case_dedup_key("dup", [DOC_A1])
    async with env.factory() as s:
        s.add(
            RetrievalEvalCase(
                id=str(uuid4()),
                organization_id=ORG_A,
                name="a",
                query="dup",
                expected_document_ids=[DOC_A1],
                tags=[],
                dedup_key=key,
                created_by=USER_A,
            )
        )
        await s.commit()
    async with env.factory() as s:
        s.add(
            RetrievalEvalCase(
                id=str(uuid4()),
                organization_id=ORG_A,
                name="b",
                query="dup",
                expected_document_ids=[DOC_A1],
                tags=[],
                dedup_key=key,
                created_by=USER_A,
            )
        )
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()


async def test_create_case_concurrent_integrity_error_returns_winner(env):
    """并发窗口：另一请求已先提交同键 → IntegrityError 后回读赢家（幂等，不是 500）。

    变异点（已实测）：去掉 create_case 的 IntegrityError 分支后，本测试以
    ``sqlite3.IntegrityError: UNIQUE constraint failed: retrieval_eval_case.
    organization_id, retrieval_eval_case.dedup_key`` 失败，而不是返回 winner。
    """
    from app.services import retrieval_eval_service

    _as(env)
    # 先占位一条真实行（模拟并发赢家已提交）。
    async with env.factory() as s:
        winner = await retrieval_eval_service.create_case(
            s,
            organization_id=ORG_A,
            created_by=USER_A,
            name="winner",
            query="race",
            expected_document_ids=[DOC_A1],
            viewer_user_id=USER_A,
        )
    assert winner.created is True

    # 第二次调用：让「先查既有行」这一步被绕过（模拟两个请求都通过了 SELECT），
    # INSERT 撞唯一索引 → 服务必须回滚后回读 winner。
    real_find = retrieval_eval_service._find_case_by_dedup_key
    calls = {"n": 0}

    async def _miss_first_then_real(session, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # 模拟并发窗口：提交前查不到
        return await real_find(session, **kwargs)

    async with env.factory() as s:
        with patch.object(retrieval_eval_service, "_find_case_by_dedup_key", _miss_first_then_real):
            result = await retrieval_eval_service.create_case(
                s,
                organization_id=ORG_A,
                created_by=USER_A,
                name="loser",
                query="race",
                expected_document_ids=[DOC_A1],
                viewer_user_id=USER_A,
            )
    assert result.created is False
    assert result.case.id == winner.case.id
    assert len(await _all_cases(env)) == 1


# ── 校验：组织 / ACL / 可检索 / 大小上限 ───────────────────────────────


async def test_capture_rejects_foreign_org_document(env):
    """跨组织金标 → 400，且不建行、不回显具体 id。

    变异点：捕获端点绕过 _validate_expected_docs → 返回 200 且建出永远 miss 的用例。
    """
    _as(env)
    r = await _capture(env, query="q", ids=[DOC_B1])
    assert r.status_code == 400, r.text
    assert DOC_B1 not in r.text
    assert await _all_cases(env) == []


async def test_capture_rejects_private_document_not_visible_to_viewer(env):
    """同组织私有文档：上传者可作为金标，其他成员 400 且不回显 id。

    变异点（已实测）：把捕获端点的 viewer_user_id 传成 None（内部系统口径）→
    USER_C 也能保存，断言以 ``200 == 400`` 失败。
    """
    _as(env)
    assert (await _capture(env, query="q", ids=[DOC_PRIVATE])).status_code == 200
    _as(env, USER_C, ORG_A)
    r = await _capture(env, query="q2", ids=[DOC_PRIVATE])
    assert r.status_code == 400, r.text
    assert DOC_PRIVATE not in r.text


async def test_capture_rejects_non_searchable_documents(env):
    """未索引 / 停用的文档不能当金标（结构性 miss 不得被固化成用例）。

    变异点：去掉 load_searchable_docs 的 status/is_enabled 条件 → 200 断言红。
    """
    from app.models.document import Document, DocumentStatus

    _as(env)
    not_indexed = _doc(str(uuid4()), ORG_A, USER_A)
    not_indexed.status = DocumentStatus.PARSED
    disabled = _doc(str(uuid4()), ORG_A, USER_A)
    disabled.is_enabled = False
    async with env.factory() as s:
        s.add_all([not_indexed, disabled])
        await s.commit()
    for doc_id in (not_indexed.id, disabled.id):
        r = await _capture(env, query="q", ids=[doc_id])
        assert r.status_code == 400, (doc_id, r.text)
        assert doc_id not in r.text
    assert await _all_cases(env) == []
    assert (await env.client.get("/api/v1/knowledge/eval/cases")).json()["data"]["total"] == 0
    assert Document is not None


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "", "expected_document_ids": [DOC_A1]},
        {"query": "   ", "expected_document_ids": [DOC_A1]},
        {"query": "q", "expected_document_ids": []},
        {"query": "q", "expected_document_ids": [str(uuid4()) for _ in range(51)]},
        {"query": "q" * 2001, "expected_document_ids": [DOC_A1]},
        {"query": "q", "expected_document_ids": [DOC_A1], "name": "n" * 256},
        {"query": "q", "expected_document_ids": [DOC_A1], "tags": ["t"] * 21},
    ],
)
async def test_capture_size_bounds_are_schema_enforced(env, payload):
    """schema 上限：空 query/空金标/51 个金标/超长 query/超长名/21 个标签 → 422。

    变异点：放宽任一 Field 上限 → 对应 422 断言红（请求不会进入 service）。
    """
    _as(env)
    r = await env.client.post(CAPTURE, json=payload)
    assert r.status_code == 422, r.text
    assert await _all_cases(env) == []


# ── 更新路径：去重键重算与 409 ──────────────────────────────────────────


async def test_update_to_duplicate_content_returns_409(env):
    """把用例改成与另一条完全相同的内容 → 409 EVAL_CASE_DUPLICATE，两行都不变。

    变异点：update_case 不重算/不检查 dedup_key → 返回 200，DB 唯一索引的
    IntegrityError 被兜成 500，或者两行内容相同（去重失效）。
    """
    _as(env)
    a = (await _capture(env, query="qa", ids=[DOC_A1])).json()["data"]
    b = (await _capture(env, query="qb", ids=[DOC_A2])).json()["data"]

    # query 改成与 a 相同（金标仍自己的一组）：不同内容 → 允许
    ok = await env.client.put(f"{CASES}/{b['id']}", json={"query": "qa"})
    assert ok.status_code == 200, ok.text
    # 金标也改成与 a 相同 → 与 a 完全重复 → 409
    conflict = await env.client.put(f"{CASES}/{b['id']}", json={"expected_document_ids": [DOC_A1]})
    assert conflict.status_code == 409, conflict.text
    body = conflict.json()
    assert body["error_code"] == "EVAL_CASE_DUPLICATE"
    assert body["detail"]["existing_case_id"] == a["id"]

    async with env.factory() as s:
        stored_b = await s.get(RetrievalEvalCase, b["id"])
        stored_a = await s.get(RetrievalEvalCase, a["id"])
    assert stored_b.expected_document_ids == [DOC_A2]
    assert stored_a.expected_document_ids == [DOC_A1]


async def test_update_own_content_does_not_409(env):
    """更新自身（如只改 name，或把 query 改成自己的同义空白形式）不得触发自撞 409。

    变异点：dedup 检查漏 conflicting.id != case_id → 改 name 也 409。
    """
    _as(env)
    case = (await _capture(env, query="self", ids=[DOC_A1])).json()["data"]
    renamed = await env.client.put(f"{CASES}/{case['id']}", json={"name": "only-name"})
    assert renamed.status_code == 200, renamed.text
    same = await env.client.put(f"{CASES}/{case['id']}", json={"query": "  self  "})
    assert same.status_code == 200, same.text
    assert same.json()["data"]["query"] == "self"


# ── 审计 / RBAC / 错误语义 ──────────────────────────────────────────────


async def test_capture_audit_has_no_query_name_or_golden_ids(env):
    """捕获有审计记录，detail 只含 org/user/条数/created，不含 query/名/金标 id。

    变异点：把 query 或用例名写进 detail → sentinel 断言红；删掉 audit 调用 →
    action 断言红。
    """
    _as(env)
    sentinel = "audit_capture_" + "a41c9d"
    r = await _capture(env, query=sentinel, ids=[DOC_A1], name="secret-name-" + "b7e2")
    assert r.status_code == 200, r.text
    calls = env.state.audit.await_args_list
    assert [call.kwargs["action"] for call in calls] == ["capture_retrieval_eval_case"]
    detail = json.loads(calls[0].kwargs["detail"])
    assert detail["organization_id"] == ORG_A
    assert detail["user_id"] == USER_A
    assert detail["expected_count"] == 1
    assert detail["created"] is True
    assert sentinel not in calls[0].kwargs["detail"]
    assert "secret-name" not in calls[0].kwargs["detail"]
    assert DOC_A1 not in calls[0].kwargs["detail"]


async def test_capture_requires_auth_and_write_scope(env):
    """未认证 401；API Key 缺 knowledge:write → 403；有 scope → 通过。

    变异点：捕获端点漏挂 require_scope("knowledge:write") → 受限 key 仍能写，
    第二个断言红。
    """
    from app.core.security import get_current_user
    from app.main import app

    _as(env)
    app.dependency_overrides.pop(get_current_user, None)
    try:
        r = await env.client.post(CAPTURE, json={"query": "q", "expected_document_ids": [DOC_A1]})
        assert r.status_code == 401, r.text
    finally:
        app.dependency_overrides[get_current_user] = lambda: env.state.user

    env.state.user.api_key_scopes = ["knowledge:read"]
    try:
        r = await env.client.post(CAPTURE, json={"query": "q", "expected_document_ids": [DOC_A1]})
        assert r.status_code == 403, r.text
    finally:
        env.state.user.api_key_scopes = None
    assert await _all_cases(env) == []


async def test_capture_error_logs_and_responses_do_not_leak_query(env, caplog):
    """service 异常 → 500；响应与日志只含异常类型名，不含 query 原文。

    变异点：端点日志改用 f-string 拼异常消息 → caplog sentinel 断言红。
    """
    import logging

    _as(env)
    sentinel = "capture_raw_" + "c9d3f1"
    with (
        caplog.at_level(logging.ERROR),
        patch(
            "app.api.v1.endpoints.knowledge.retrieval_eval_service.create_case",
            new=AsyncMock(side_effect=RuntimeError(sentinel)),
        ),
    ):
        r = await env.client.post(
            CAPTURE, json={"query": sentinel, "expected_document_ids": [DOC_A1]}
        )
    assert r.status_code == 500, r.text
    assert sentinel not in r.text
    assert sentinel not in caplog.text
    assert "RuntimeError" in caplog.text


# ── 纯函数：去重键稳定性 ────────────────────────────────────────────────


def test_case_dedup_key_normalizes_whitespace_and_set_order():
    """dedup_key：query 去首尾空白、金标按集合；内容任何实质变化必变。

    变异点：不 strip / 不去重排序 / 忽略 query → 对应相等或不等断言红。
    """
    from app.services.retrieval_eval_service import case_dedup_key

    base = case_dedup_key("退款多久到账", [DOC_A1, DOC_A2])
    assert len(base) == 64
    assert case_dedup_key("  退款多久到账 ", [DOC_A2, DOC_A1]) == base  # 空白 + 顺序
    assert case_dedup_key("退款多久到账", [DOC_A1, DOC_A2, DOC_A2]) == base  # 重复金标
    assert case_dedup_key("退款多久能到", [DOC_A1, DOC_A2]) != base
    assert case_dedup_key("退款多久到账", [DOC_A1]) != base
    # 内部空白与大小写不折叠：query 决定检索行为，不能替用户判等价。
    assert case_dedup_key("refund  time", [DOC_A1]) != case_dedup_key("refund time", [DOC_A1])
    assert case_dedup_key("Refund", [DOC_A1]) != case_dedup_key("refund", [DOC_A1])
