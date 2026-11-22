"""检索评测测试集服务（竞品对标 P2）：CRUD + 运行评测（文档级 recall@k / MRR）。

与 ``backend/evals`` 的分工：
- 条款级评测（``evals.metrics``：ClauseHit@k / NearMiss / MRR）衡量「找对条款」，
  数据来自入库的 golden set 与固定语料，CI 门禁比对基线；
- 本模块是产品化的**文档级**评测：用例存在 DB（``retrieval_eval_case``），
  只认 ``document_id`` 金标。**运行循环直接调用 ``evals.harness.run_eval``**，
  不另写一套「调用重试 / 单条失败记 error」逻辑；只是把每条 DB 用例适配成
  ``GoldenItem``、把检索适配成 ``RetrieveFn``，再在 harness 的逐条 ``ItemRun``
  上做文档级判定（条款级 ``evaluate_item`` 判不了 document_id，故只复用其
  golden 数据结构与 harness 编排，不重复实现错误语义）。

运行默认只读用例、结果不落库；判定口径（纯函数，可无 ES/无网络单测）：
- 单条 recall@k = 期望文档 id 中出现在 top-k 的比例（文档级召回，不要求序位）；
- 单条 reciprocal rank = 第一个命中期望文档的名次的倒数（未命中记 0）；
- 汇总 = 对 ``status=ok`` 的用例取宏平均（错误用例单独计数）。

指标命名：``document_hit_at_k`` 是**文档级** hit（top-k 里是否出现任一期望
``document_id``），与条款级 ``clause_hit@k``（文件名 + 条号定义位）不同口径；
``hit_at_k`` 保留为同一文档级值的历史别名。运行 top_k 默认为 5，是 rank-5
产品评测的固定口径，与组织设置 ``retrieval_top_k``（线上检索返回条数）无关，
也不读取/覆盖它。

金标时效性：创建/更新只接受本组织、索引完成（``status=indexed``）、启用且
viewer 可见的文档；运行前再校验一次，失效金标记为 ``status=error`` +
机器码（``STALE_GOLDEN_DOCUMENTS``），绝不伪装成 recall=0。

日志/错误纪律：query 与文档正文只以「条数/长度」参与日志；错误响应只回显
异常类型名或固定机器码，不回显可能包含 query 内容的异常字符串。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError, ValidationError
from app.models.document import Document, DocumentStatus
from app.models.retrieval_eval import RetrievalEvalCase
from app.services.document_acl import get_blocked_document_ids
from evals.golden import GoldenItem, Locator
from evals.harness import RetrieveFn
from evals.harness import run_eval as harness_run_eval
from evals.metrics import ItemOutcome, aggregate

# 运行结果的稳定机器码：stale 与一般检索失败都提供 error_code，
# error 字段继续保留兼容值（机器码或异常类型名），调用方无需分支猜键。
ERROR_CODE_STALE_GOLDEN = "STALE_GOLDEN_DOCUMENTS"
ERROR_CODE_RETRIEVAL = "RETRIEVAL_ERROR"


# 一次运行中所有用例共享的可检索性快照（组织状态 + viewer ACL 各查一次）。
@dataclass(frozen=True)
class _SearchableDocs:
    """本组织内当前可被检索命中的文档 id 集合，以及 viewer 的 ACL 屏蔽集合。"""

    searchable_ids: frozenset[str]
    blocked_ids: frozenset[str]


async def load_searchable_docs(
    session: AsyncSession, *, organization_id: int, viewer_user_id: int | None
) -> _SearchableDocs:
    """按正常检索口径读取可搜索文档（索引完成 + 启用），不改变 ACL fail-closed 语义。

    与 ``app.rag.retriever.build_retrieval_filters``（``is_enabled=False`` 排除）
    及 ``knowledge_service.get_knowledge_stats``（``status=INDEXED`` 才算已索引）
    保持一致。ACL 完全委托既有 ``get_blocked_document_ids``，不在评测里另写一套。
    """
    ids = list(
        (
            await session.execute(
                select(Document.id).where(
                    Document.organization_id == organization_id,
                    Document.status == DocumentStatus.INDEXED,
                    Document.is_enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    blocked = set(await get_blocked_document_ids(session, organization_id, viewer_user_id))
    return _SearchableDocs(frozenset(ids), frozenset(blocked))


def _is_searchable(snapshot: _SearchableDocs, doc_id: str) -> bool:
    return doc_id in snapshot.searchable_ids and doc_id not in snapshot.blocked_ids


def case_to_dict(case: RetrievalEvalCase) -> dict[str, Any]:
    """序列化一条用例（响应与日志共用同一形状）。"""
    return {
        "id": case.id,
        "organization_id": case.organization_id,
        "name": case.name,
        "query": case.query,
        "expected_document_ids": list(case.expected_document_ids or []),
        "tags": list(case.tags or []),
        "created_by": case.created_by,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
    }


async def _validate_expected_docs(
    session: AsyncSession,
    *,
    organization_id: int,
    ids: list[str],
    viewer_user_id: int | None,
    snapshot: _SearchableDocs | None = None,
) -> list[str]:
    """去重 + 属于本组织 + 可检索（已索引/启用）+ viewer 可见（创建与更新共用）。

    跨组织、停用/未索引、私有不可见的金标永远不可能被该用户的检索合法命中，
    留着只会把评测变成结构性 miss，甚至把私有文档 id 固化成可读元数据。
    fail-closed 的 ACL 语义完全沿用 ``get_blocked_document_ids``。
    """
    unique = list(dict.fromkeys(ids))
    if snapshot is None:
        snapshot = await load_searchable_docs(
            session, organization_id=organization_id, viewer_user_id=viewer_user_id
        )
    # ACL 命中先判且不带计数：否则 found=1/0 可区分「无权看的私有文档」与
    # 「不存在的文档」，等于把私有文档存在性泄漏给同组织其他成员。
    if snapshot.blocked_ids.intersection(unique):
        raise ValidationError(
            "expected_document_ids 含不属于当前组织或不可检索的文档 id",
            field="expected_document_ids",
        )
    found = sum(1 for doc_id in unique if doc_id in snapshot.searchable_ids)
    if found != len(unique):
        raise ValidationError(
            "expected_document_ids 含不属于当前组织或不可检索的文档 id",
            field="expected_document_ids",
            expected=len(unique),
            found=int(found),
        )
    return unique


async def create_case(
    session: AsyncSession,
    *,
    organization_id: int,
    created_by: int,
    name: str,
    query: str,
    expected_document_ids: list[str],
    tags: list[str] | None = None,
    viewer_user_id: int | None = None,
) -> RetrievalEvalCase:
    """创建用例；期望文档必须属于调用者组织、可检索且对调用者可见。

    跨组织、停用/未索引、私有不可见的金标永远不可能被该用户的检索合法命中，
    留着只会把评测变成结构性 miss，甚至把私有文档 id 固化成可读元数据——必须拒绝。
    """
    ids = await _validate_expected_docs(
        session,
        organization_id=organization_id,
        ids=expected_document_ids,
        viewer_user_id=viewer_user_id,
    )
    case = RetrievalEvalCase(
        id=str(uuid.uuid4()),
        organization_id=organization_id,
        name=name,
        query=query,
        expected_document_ids=ids,
        tags=list(tags or []),
        created_by=created_by,
    )
    session.add(case)
    await session.commit()
    await session.refresh(case)
    return case


async def list_cases(
    session: AsyncSession,
    *,
    organization_id: int,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """按组织列出用例（创建时间倒序；id 兜底保证同秒排序稳定）。"""
    total = (
        await session.execute(
            select(func.count(RetrievalEvalCase.id)).where(
                RetrievalEvalCase.organization_id == organization_id
            )
        )
    ).scalar() or 0
    rows = (
        (
            await session.execute(
                select(RetrievalEvalCase)
                .where(RetrievalEvalCase.organization_id == organization_id)
                .order_by(RetrievalEvalCase.created_at.desc(), RetrievalEvalCase.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {"data": [case_to_dict(row) for row in rows], "total": int(total)}


async def get_case(
    session: AsyncSession, *, organization_id: int, case_id: str
) -> RetrievalEvalCase:
    """读取本组织单条用例；不存在或属于其他组织一律 404（不回显存在性）。"""
    case = await session.get(RetrievalEvalCase, case_id)
    if case is None or case.organization_id != organization_id:
        raise NotFoundError("评测用例不存在")
    return case


async def update_case(
    session: AsyncSession,
    *,
    organization_id: int,
    case_id: str,
    viewer_user_id: int | None,
    changes: dict[str, Any],
) -> RetrievalEvalCase:
    """更新本组织用例；跨组织 404，金标变更复用创建时的组织/ACL/可检索校验。"""
    case = await get_case(session, organization_id=organization_id, case_id=case_id)
    if "expected_document_ids" in changes:
        changes = dict(changes)
        changes["expected_document_ids"] = await _validate_expected_docs(
            session,
            organization_id=organization_id,
            ids=changes["expected_document_ids"],
            viewer_user_id=viewer_user_id,
        )
    if changes:
        await session.execute(
            update(RetrievalEvalCase)
            .where(
                RetrievalEvalCase.id == case_id,
                RetrievalEvalCase.organization_id == organization_id,
            )
            .values(**changes)
        )
        await session.commit()
        await session.refresh(case)
    return case


async def delete_case(session: AsyncSession, *, organization_id: int, case_id: str) -> None:
    """删除本组织用例；不存在或属于其他组织一律 404（不回显存在性）。"""
    result = await session.execute(
        delete(RetrievalEvalCase).where(
            RetrievalEvalCase.id == case_id,
            RetrievalEvalCase.organization_id == organization_id,
        )
    )
    await session.commit()
    if not result.rowcount:
        raise NotFoundError("评测用例不存在")


MAX_CASES_PER_RUN = 100
MAX_CASE_IDS_PER_RUN = 50  # 与 RetrievalEvalRunRequest.case_ids 的 schema 上限一致


async def load_cases(
    session: AsyncSession,
    *,
    organization_id: int,
    case_ids: list[str] | None = None,
) -> list[RetrievalEvalCase]:
    """读取本组织用例（可按 id 子集过滤）；外组织 id 不会出现在结果里。

    ``case_ids`` 为显式子集时最多 50 条（schema 已限）；``None`` = 全量运行，
    最多 100 条，超出直接 ValidationError —— 每次运行都要逐条真实检索，
    不能让一个组织无限量地把评测端点变成检索放大/DoS 入口。
    """
    stmt = select(RetrievalEvalCase).where(RetrievalEvalCase.organization_id == organization_id)
    # None = 全部用例；显式 [] = 空子集（不能回落成全量运行，否则用户以为只跑
    # 零条却把整个测试集都执行了）。
    if case_ids is not None:
        if len(case_ids) > MAX_CASE_IDS_PER_RUN:
            raise ValidationError(
                f"单次评测最多指定 {MAX_CASE_IDS_PER_RUN} 条用例",
                field="case_ids",
                expected=MAX_CASE_IDS_PER_RUN,
                found=len(case_ids),
            )
        stmt = stmt.where(RetrievalEvalCase.id.in_(case_ids))
    else:
        stmt = stmt.limit(MAX_CASES_PER_RUN + 1)
    stmt = stmt.order_by(RetrievalEvalCase.created_at.asc(), RetrievalEvalCase.id.asc())
    rows = list((await session.execute(stmt)).scalars().all())
    if case_ids is None and len(rows) > MAX_CASES_PER_RUN:
        raise ValidationError(
            f"全量评测最多 {MAX_CASES_PER_RUN} 条用例，请通过 case_ids 分批运行",
            field="case_ids",
            expected=MAX_CASES_PER_RUN,
            found=len(rows),
        )
    return rows


def retrieved_document_ids(results: list[dict]) -> list[str]:
    """从检索结果提取文档 id（兼容知识库服务的 ``id``/``source`` 与 pipeline 的 ``document_id``）。"""
    ids: list[str] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        source = result.get("source")
        candidates = (
            result.get("document_id"),
            result.get("id"),
            source.get("document_id") if isinstance(source, dict) else None,
        )
        for doc_id in candidates:
            if isinstance(doc_id, str) and doc_id and doc_id not in ids:
                ids.append(doc_id)
                break
    return ids


def score_case(expected_ids: list[str], retrieved_ids: list[str], top_k: int) -> dict[str, Any]:
    """单条判定（纯函数）：文档级 recall@k 与 reciprocal rank。"""
    expected = [doc_id for doc_id in expected_ids if doc_id]
    top = retrieved_ids[:top_k]
    matched = [doc_id for doc_id in expected if doc_id in top]
    first_rank: int | None = None
    for rank, doc_id in enumerate(top, start=1):
        if doc_id in expected:
            first_rank = rank
            break
    return {
        "expected_count": len(expected),
        "matched_count": len(matched),
        # 期望集合为空是数据错误（schema 已拒绝）；这里按 None 处理，不进宏平均分母。
        "recall_at_k": (len(matched) / len(expected)) if expected else None,
        "first_hit_rank": first_rank,
        "reciprocal_rank": (1.0 / first_rank) if first_rank else 0.0,
    }


def _case_to_golden(case: RetrievalEvalCase) -> GoldenItem:
    """DB 用例 → evals.harness 的 golden 结构（id/query/expect 沿用既有契约）。

    ``gold`` 用 document_id 作 Locator.file、条号留空（本评测只看文档级命中），
    避免对 golden.py 的严格条号校验产生假依赖；``expect='hit'`` 使 harness
    的 error 计数与重试语义直接适用于本产品化评测。
    """
    return GoldenItem(
        id=case.id,
        type="retrieval_eval_case",
        query=case.query,
        expect="hit",
        gold=tuple(Locator(file=str(doc_id), clause="") for doc_id in case.expected_document_ids),
    )


async def run_eval(
    cases: list[RetrievalEvalCase],
    retrieve: RetrieveFn,
    top_k: int,
    *,
    searchable: _SearchableDocs | None = None,
) -> dict[str, Any]:
    """对用例集跑检索并汇总（不写库）；逐条执行直接复用 evals.harness.run_eval。

    ``searchable`` 为运行开始时的文档可检索性快照（含 viewer ACL）：失效金标
    直接记 ``status=error`` + 机器码，不调用检索、不进 recall 分母。
    """
    by_id = {case.id: case for case in cases}
    stale_ids = (
        {
            case.id
            for case in cases
            if any(not _is_searchable(searchable, doc_id) for doc_id in case.expected_document_ids)
        }
        if searchable is not None
        else set()
    )
    runnable = [case for case in cases if case.id not in stale_ids]
    runs = await harness_run_eval([_case_to_golden(case) for case in runnable], retrieve, top_k)

    results_by_id: dict[str, dict[str, Any]] = {}
    # 失效金标（机器码，不回显具体 id 或异常文本），不调用检索、不进分母。
    for case in cases:
        if case.id not in stale_ids:
            continue
        results_by_id[case.id] = {
            "case_id": case.id,
            "name": case.name,
            "query": case.query,
            "status": "error",
            "error": ERROR_CODE_STALE_GOLDEN,
            "error_code": ERROR_CODE_STALE_GOLDEN,
            "expected_document_ids": list(case.expected_document_ids or []),
            "retrieved_document_ids": [],
            "recall_at_k": None,
            "first_hit_rank": None,
            "reciprocal_rank": None,
        }
    for run in runs:
        case = by_id[run.item.id]
        expected = list(case.expected_document_ids or [])
        retrieved = retrieved_document_ids(run.results)
        if run.outcome.status == "error":
            results_by_id[case.id] = {
                "case_id": case.id,
                "name": case.name,
                "query": case.query,
                "status": "error",
                # 只回显异常类型：harness 的 error 字符串含原始异常消息
                # （可能带 query 原文），禁止进入响应/日志。
                "error": (run.outcome.error or "retrieval_error").split(":", 1)[0],
                "error_code": ERROR_CODE_RETRIEVAL,
                "expected_document_ids": expected,
                "retrieved_document_ids": [],
                "recall_at_k": None,
                "first_hit_rank": None,
                "reciprocal_rank": None,
            }
            continue
        score = score_case(expected, retrieved, top_k)
        results_by_id[case.id] = {
            "case_id": case.id,
            "name": case.name,
            "query": case.query,
            "status": "ok",
            "error": None,
            "error_code": None,
            "expected_document_ids": expected,
            "retrieved_document_ids": retrieved[:top_k],
            **score,
        }
    # 维持输入用例顺序：stale 不是新用例，不能因预填而跑到结果列表最前面。
    results = [results_by_id[case.id] for case in cases]

    # MRR / hit@k 由 evals.metrics.aggregate 计算（与条款级评测同一口径实现，
    # 错误项自动剔除分母）；recall@k 是文档级特有指标（期望集合的召回比例），
    # 只在以上结果行上做宏平均。outcomes 以**结果行**为准（含失效金标），
    # 不能与 harness runs 按位置 zip：stale 行没有对应 run。
    result_counts = {run.item.id: len(run.results) for run in runs}
    outcomes = [
        ItemOutcome(
            item_id=row["case_id"],
            item_type="retrieval_eval_case",
            expect="hit",
            status=row["status"],
            error=row["error"],
            gold_rank=row["first_hit_rank"],
            n_results=result_counts.get(row["case_id"], 0),
        )
        for row in results
    ]
    agg = aggregate(outcomes, cutoffs=(top_k,))
    recalls = [
        row["recall_at_k"]
        for row in results
        if row["status"] == "ok" and row["recall_at_k"] is not None
    ]
    return {
        "top_k": top_k,
        "counts": {
            "cases": agg["counts"]["total"],
            "evaluated": agg["counts"]["evaluated"],
            "errors": agg["counts"]["errors"],
        },
        "error_case_ids": agg["error_items"],
        "metrics": {
            "recall_at_k": round(sum(recalls) / len(recalls), 4) if recalls else None,
            "mrr": agg["mrr"],
            # 原名 hit_at_k 沿用（历史前端/CI 依赖），但口径其实是文档级 hit，
            # 与条款级 clause_hit@k（文件名 + 条号定义位）不同，故补明确别名。
            "document_hit_at_k": agg[f"clause_hit@{top_k}"],
            "hit_at_k": agg[f"clause_hit@{top_k}"],
        },
        "results": results,
        "persisted": False,
    }
