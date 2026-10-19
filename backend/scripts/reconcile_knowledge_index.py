"""知识库索引对账与批量重索引（issue #180 收尾 · 最小运维脚本）。

## 背景

索引主链已经保证「状态 INDEXED ⟹ 文档可检索」：写入前回读校验
（``app.services.document_status.verify_indexed``），``/health`` 能发现
「有文档声称 INDEXED 但索引一篇都没有」。剩下的缺口在**部署数据态**：
没有工具逐文档对账、也没有批量重索引入口。

## 对账口径

- **基准**：``documents`` 表中 ``status = INDEXED`` 的文档及其 ``chunk_count``；
- **实际**：ES 索引里按 ``document_id`` 聚合（composite 分页，无 10k 截断）的
  chunk 数；
- 状态：``missing``（0 条）/ ``partial``（少于 chunk_count）/
  ``extra``（多于 chunk_count，可能有陈旧 chunk）/ ``ok``；
- **孤儿**：ES 中出现、但 DB 未标记 INDEXED 的 ``document_id``——只报告，
  不自动删除（删除必须由人确认）。

## 重索引（--reindex）

只处理 ``missing`` / ``partial``，并且：

1. 先落一条 ``KnowledgeProcessingJob``（``QUEUED`` / ``trigger_type=reprocess``，
   与 ``POST /knowledge/rebuild/{document_id}`` 同形态）——**不建 job 直接调
   ``process(document_id)`` 会命中「无 job 且文档已 INDEXED → STALE_RUN」，
   空转且把空转记为成功**；
2. 再调用 ``DocumentProcessor.process(document_id, job.id)``（文档锁 + 回读
   校验 + 缓存失效）。**不要**直接扫 MySQL 重写 ES——那会绕过解析/校验，
   再造一条假索引路径。

## 用法（backend 目录下运行，与后端同一份 .env / venv）

    python scripts/reconcile_knowledge_index.py               # 只对账（只读）
    python scripts/reconcile_knowledge_index.py --reindex     # 对账并修复
    python scripts/reconcile_knowledge_index.py --org 3       # 限定组织
    python scripts/reconcile_knowledge_index.py --reindex --limit 10

只读对账不需要 embedding 凭据（重型依赖在 ``run()`` 内才导入）。

## 退出码

    0  一致；或 --reindex 后全部恢复
    1  存在 missing/partial（--reindex 后仍有残留）
    2  致命错误（DB/ES 不可用、索引配置错误、配置加载失败等）
"""

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field

# 确保 backend 在 path 中（与 scripts 目录其他脚本同款）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger("reconcile_knowledge_index")

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_FATAL = 2

# composite 聚合每页桶数（按 document_id）；分页直到没有 after_key。
_COMPOSITE_PAGE_SIZE = 1000


@dataclass(frozen=True)
class DocIndexState:
    """单个 INDEXED 文档的对账结果。"""

    document_id: str
    filename: str
    organization_id: int | str
    expected_chunks: int
    indexed_chunks: int

    @property
    def status(self) -> str:
        if self.indexed_chunks == 0:
            return "missing"
        if self.expected_chunks and self.indexed_chunks < self.expected_chunks:
            return "partial"
        if self.indexed_chunks > self.expected_chunks:
            # 包含 expected_chunks == 0 却有 chunk 的异常形态（陈旧/串写）。
            return "extra"
        return "ok"

    @property
    def needs_reindex(self) -> bool:
        # extra 不重索引：多余 chunk 仍可检索，删除/收敛需人工确认。
        return self.status in ("missing", "partial")


@dataclass(frozen=True)
class ReconcileReport:
    """一次对账的完整结果。"""

    states: list[DocIndexState]
    es_chunk_total: int
    orphan_document_ids: list[str]

    @property
    def missing(self) -> list[DocIndexState]:
        return [s for s in self.states if s.status == "missing"]

    @property
    def partial(self) -> list[DocIndexState]:
        return [s for s in self.states if s.status == "partial"]

    @property
    def has_issues(self) -> bool:
        return bool(self.missing or self.partial)

    @property
    def exit_code(self) -> int:
        return EXIT_MISMATCH if self.has_issues else EXIT_OK


@dataclass
class ReindexOutcome:
    attempted: int = 0
    succeeded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)


def build_report(
    rows: list[tuple[str, str, int | str, int]], counts: dict[str, int]
) -> ReconcileReport:
    """由 DB 基准行与 ES 计数构造对账报告（纯函数，便于单测）。

    ``rows``: ``[(document_id, filename, organization_id, chunk_count)]``，
    来自 ``documents.status = INDEXED`` 的查询。
    ``counts``: ``{document_id: ES chunk 数}``，来自 ``fetch_indexed_chunk_counts``。
    """
    states: list[DocIndexState] = []
    known_ids: set[str] = set()
    for document_id, filename, organization_id, expected_chunks in rows:
        document_id = str(document_id)
        known_ids.add(document_id)
        states.append(
            DocIndexState(
                document_id=document_id,
                filename=filename or "",
                organization_id=organization_id,
                expected_chunks=int(expected_chunks or 0),
                indexed_chunks=int(counts.get(document_id, 0)),
            )
        )
    orphans = sorted(str(doc_id) for doc_id in counts if str(doc_id) not in known_ids)
    return ReconcileReport(
        states=states,
        es_chunk_total=sum(int(v) for v in counts.values()),
        orphan_document_ids=orphans,
    )


async def fetch_indexed_documents(
    session, *, organization_id: int | None = None
) -> list[tuple[str, str, int | str, int]]:
    """读取 ``status = INDEXED`` 的文档基准行（按创建时间排序）。"""
    # 延迟导入：只读工具不应在 import 期要求 DB/embedding 依赖可用。
    from sqlalchemy import select

    from app.models.document import Document, DocumentStatus

    stmt = select(
        Document.id,
        Document.filename,
        Document.organization_id,
        Document.chunk_count,
    ).where(Document.status == DocumentStatus.INDEXED)
    if organization_id is not None:
        stmt = stmt.where(Document.organization_id == organization_id)
    stmt = stmt.order_by(Document.created_at)
    rows = (await session.execute(stmt)).all()
    return [(str(row[0]), row[1] or "", row[2], int(row[3] or 0)) for row in rows]


async def fetch_indexed_chunk_counts(
    client,
    index_name: str,
    *,
    organization_id: int | None = None,
) -> dict[str, int]:
    """按 ``document_id`` 聚合 ES 中实际 chunk 数（composite 分页，无 10k 截断）。"""
    query: dict = {"match_all": {}}
    if organization_id is not None:
        query = {"term": {"organization_id": str(organization_id)}}

    counts: dict[str, int] = {}
    after_key: dict | None = None
    while True:
        composite: dict = {
            "size": _COMPOSITE_PAGE_SIZE,
            "sources": [{"document_id": {"terms": {"field": "document_id"}}}],
        }
        if after_key is not None:
            composite["after"] = after_key
        res = await client.search(
            index=index_name,
            size=0,
            query=query,
            aggs={"by_document": {"composite": composite}},
        )
        by_document = (res.get("aggregations") or {}).get("by_document") or {}
        buckets = by_document.get("buckets") or []
        for bucket in buckets:
            key = bucket.get("key") or {}
            document_id = str(key.get("document_id", ""))
            if document_id:
                counts[document_id] = int(bucket.get("doc_count") or 0)
        next_after = by_document.get("after_key")
        if not buckets or not next_after or next_after == after_key:
            break
        after_key = next_after
    return counts


async def _enqueue_reprocess_job(state: DocIndexState) -> int:
    """落一条 QUEUED/reprocess 任务并返回 job id（与 rebuild 端点同形态）。

    必须建 job：``DocumentProcessor.process(document_id, job_id=None)`` 对已
    INDEXED 文档会命中 STALE_RUN 判定并直接返回 True（无 job 的重复投递保护），
    修复动作会空转且被误记为成功。
    """
    from app.core.database import AsyncSessionLocal
    from app.models.knowledge_job import KnowledgeJobStatus, KnowledgeProcessingJob

    async with AsyncSessionLocal() as session:
        job = KnowledgeProcessingJob(
            document_id=state.document_id,
            organization_id=int(state.organization_id),
            trigger_type="reprocess",
            status=KnowledgeJobStatus.QUEUED,
            retry_count=0,
            local_fallback=True,
        )
        session.add(job)
        await session.commit()
        return int(job.id)


async def reindex_documents(
    states: list[DocIndexState], *, process=None, job_creator=None
) -> ReindexOutcome:
    """对需要修复的文档逐个「建 job → process」；单文档失败不阻断其余。

    ``process`` / ``job_creator`` 仅用于测试注入；缺省分别走
    ``DocumentProcessor.process`` 与 ``_enqueue_reprocess_job``。
    """
    from app.worker.doc_processor import DEFERRED, processor

    process_call = process or processor.process
    create_job = job_creator or _enqueue_reprocess_job
    outcome = ReindexOutcome()
    for state in states:
        outcome.attempted += 1
        logger.info(
            "[RECONCILE] 重索引 %s（%s，当前 %s/%s 条）...",
            state.document_id,
            state.filename,
            state.indexed_chunks,
            state.expected_chunks,
        )
        try:
            job_id = await create_job(state)
        except Exception as e:  # noqa: BLE001 — 单文档异常不阻断其余修复
            logger.error(
                "[RECONCILE] 创建重索引任务失败 document_id=%s: %s",
                state.document_id,
                e,
                exc_info=True,
            )
            outcome.failed.append(state.document_id)
            continue
        try:
            result = await process_call(state.document_id, job_id)
        except Exception as e:  # noqa: BLE001
            logger.error(
                "[RECONCILE] 重索引异常 document_id=%s job_id=%s: %s",
                state.document_id,
                job_id,
                e,
                exc_info=True,
            )
            outcome.failed.append(state.document_id)
            continue
        if result is DEFERRED:
            logger.warning(
                "[RECONCILE] 文档正被其他处理者锁定 document_id=%s job_id=%s"
                "（任务保持 QUEUED，稍后重跑本脚本或由启动对账回收）",
                state.document_id,
                job_id,
            )
            outcome.deferred.append(state.document_id)
        elif result:
            outcome.succeeded.append(state.document_id)
        else:
            outcome.failed.append(state.document_id)
    return outcome


def _print_report(report: ReconcileReport, *, organization_id: int | None) -> None:
    scope = f"组织 {organization_id}" if organization_id is not None else "全部组织"
    logger.info(
        "[RECONCILE] 对账范围：%s；INDEXED 文档 %s 篇；ES chunk 总数 %s",
        scope,
        len(report.states),
        report.es_chunk_total,
    )
    for state in report.states:
        if state.status == "ok":
            continue
        log = logger.warning if state.needs_reindex else logger.info
        log(
            "[RECONCILE] %-7s %s (%s) 期望 %s 条 / 实际 %s 条",
            state.status.upper(),
            state.document_id,
            state.filename,
            state.expected_chunks,
            state.indexed_chunks,
        )
    if report.orphan_document_ids:
        logger.warning(
            "[RECONCILE] 索引中存在 %s 个 DB 未标记 INDEXED 的 document_id（只报告，不删除）：%s",
            len(report.orphan_document_ids),
            ", ".join(report.orphan_document_ids[:20])
            + (" ..." if len(report.orphan_document_ids) > 20 else ""),
        )
    if report.has_issues:
        logger.warning(
            "[RECONCILE] 结论：missing=%s，partial=%s —— 需要修复（--reindex）",
            len(report.missing),
            len(report.partial),
        )
    else:
        logger.info("[RECONCILE] 结论：documents 标记与 ES 索引逐文档一致")


async def run(args: argparse.Namespace) -> int:
    # 重型依赖延迟到 run()：模块导入失败也能由 main() 统一映射为 EXIT_FATAL。
    from app.core.config import settings
    from app.core.database import AsyncSessionLocal, init_db
    from app.core.elasticsearch import (
        close_elasticsearch,
        get_elasticsearch,
        get_index_error,
        init_elasticsearch,
    )

    index_name = settings.ELASTICSEARCH_INDEX_NAME

    await init_db()
    try:
        await init_elasticsearch()
        if get_index_error():
            logger.error(
                "[RECONCILE] 索引配置错误，无法对账：%s", get_index_error()
            )
            return EXIT_FATAL

        client = await get_elasticsearch()
        counts = await fetch_indexed_chunk_counts(
            client, index_name, organization_id=args.org
        )
        async with AsyncSessionLocal() as session:
            rows = await fetch_indexed_documents(session, organization_id=args.org)

        report = build_report(rows, counts)
        _print_report(report, organization_id=args.org)

        if not args.reindex:
            return report.exit_code

        candidates = [state for state in report.states if state.needs_reindex]
        if args.limit is not None:
            candidates = candidates[: args.limit]
        if not candidates:
            logger.info("[RECONCILE] 没有需要重索引的文档")
            return report.exit_code

        outcome = await reindex_documents(candidates)
        logger.info(
            "[RECONCILE] 重索引结束：尝试 %s，成功 %s，失败 %s，暂缓 %s",
            outcome.attempted,
            len(outcome.succeeded),
            len(outcome.failed),
            len(outcome.deferred),
        )

        # 复核：重索引后重新对账（process 内部有回读校验，这里再给操作者一份终态）。
        # 重新读取 DB 基准——重索引可能改变 chunk_count（重新解析/切分）。
        counts_after = await fetch_indexed_chunk_counts(
            client, index_name, organization_id=args.org
        )
        async with AsyncSessionLocal() as session:
            rows_after = await fetch_indexed_documents(
                session, organization_id=args.org
            )
        report_after = build_report(rows_after, counts_after)
        _print_report(report_after, organization_id=args.org)

        if outcome.failed or outcome.deferred:
            return EXIT_MISMATCH
        return report_after.exit_code
    finally:
        await close_elasticsearch()


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"必须是整数：{value!r}") from e
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"必须 ≥ 1：{parsed}")
    return parsed


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="对账 documents 标记与 ES 索引，并可按需批量重索引（issue #180）"
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="对 missing/partial 文档建 reprocess job 并执行 DocumentProcessor.process()",
    )
    parser.add_argument(
        "--org", type=_positive_int, default=None, help="只处理该组织 id（默认全部）"
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="--reindex 时最多处理 N 篇（≥1，分批修复用）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    args = build_arg_parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except Exception as e:  # noqa: BLE001 — 运维脚本以退出码表达致命错误
        logger.error("[RECONCILE] 致命错误：%s", e, exc_info=True)
        return EXIT_FATAL


if __name__ == "__main__":
    sys.exit(main())
