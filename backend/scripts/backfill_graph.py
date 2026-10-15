"""回填：为已 INDEXED 的文档补做知识图谱抽取（幂等，可重复执行）。

背景（GraphRAG 写链 a8a3d155）：worker 侧接线只覆盖"此后新索引"的文档；
存量已索引文档需要本脚本补出图数据。图数据与幂等标记都存 Redis
（`rag:graph:{org}` / `rag:graph:docs:{org}`）——回填能否对运行中的
后端可见，取决于脚本与后端是否连同一个 Redis（同一份 .env）。

幂等：复用 `GraphRAGService.ingest_document` 的文档标记——已摄入的文档
重复执行直接跳过，不会二次叠加实体计数/关系条目。标记与图数据同 TTL，
过期后重跑 = 干净重建。

用法（在 backend 目录下运行，与后端同一份 .env / venv）：
    python scripts/backfill_graph.py                 # 全部已索引文档
    python scripts/backfill_graph.py --org 3         # 只补某个组织
    python scripts/backfill_graph.py --document-id <uuid> --dry-run

退出码：0 全部成功；1 存在单文档失败（其余照常）；2 致命（Redis/DB 不可用等）。
"""
import argparse
import asyncio
import os
import sys

# 确保 backend 在 path 中（与 scripts/check_login.py 同款）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.core.redis import get_redis, init_redis, redis_client  # noqa: E402
from app.models.document import Document, DocumentChunk, DocumentStatus  # noqa: E402
from app.services.graph_rag_service import graph_rag_service  # noqa: E402

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_FATAL = 2


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="回填已索引文档的知识图谱数据（幂等）")
    parser.add_argument("--org", type=int, default=None, help="只处理该组织 id（默认全部）")
    parser.add_argument(
        "--document-id", default=None, help="只处理该文档 id（默认全部已索引文档）"
    )
    parser.add_argument("--dry-run", action="store_true", help="只列出计划，不写任何数据")
    return parser


async def fetch_indexed_documents(
    session, *, organization_id: int | None = None, document_id: str | None = None
) -> list[tuple[str, int]]:
    """返回 [(document_id, organization_id)]，按创建时间排序。"""
    stmt = select(Document.id, Document.organization_id).where(
        Document.status == DocumentStatus.INDEXED
    )
    if organization_id is not None:
        stmt = stmt.where(Document.organization_id == organization_id)
    if document_id:
        stmt = stmt.where(Document.id == document_id)
    stmt = stmt.order_by(Document.created_at)
    return [(str(row[0]), row[1]) for row in (await session.execute(stmt)).all()]


async def fetch_document_text(session, document_id: str) -> str:
    """按 chunk_index 顺序拼接文档全文（图谱抽取的输入）。"""
    stmt = (
        select(DocumentChunk.chunk_text)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
    )
    texts = (await session.execute(stmt)).scalars().all()
    return "\n".join(t for t in texts if t)


async def backfill(
    documents: list[tuple[str, int]],
    text_loader,
    *,
    service=None,
    llm_client=None,
    dry_run: bool = False,
) -> dict:
    """核心回填循环：单文档失败不中断整批。

    返回分类报告：extracted/skipped/empty/failed/planned（均为 doc_id 列表，
    failed 为 (doc_id, 错误str) 二元组）。
    """
    service = service or graph_rag_service
    report: dict = {
        "extracted": [],
        "skipped": [],
        "empty": [],
        "failed": [],
        "planned": [],
    }
    for doc_id, org_id in documents:
        if dry_run:
            already = await service.is_document_extracted(doc_id, org_id)
            (report["skipped"] if already is True else report["planned"]).append(doc_id)
            continue
        try:
            text = await text_loader(doc_id)
            result = await service.ingest_document(
                document_id=doc_id,
                organization_id=org_id,
                text=text,
                llm_client=llm_client,
            )
            if result["status"] == "skipped":
                report["skipped"].append(doc_id)
            elif result["entities"] == 0:
                report["empty"].append(doc_id)
            else:
                report["extracted"].append(doc_id)
        except Exception as e:
            report["failed"].append((doc_id, f"{type(e).__name__}: {e}"))
    return report


async def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_arg_parser().parse_args(argv)

    # Redis 是图数据与幂等标记的唯一存放介质：不可达时直接失败，
    # 绝不允许"抽取跑完却无处可存"的假成功。
    await init_redis()
    try:
        await (await get_redis()).ping()
    except Exception as e:
        print(f"[FATAL] Redis 不可达（图数据的唯一存放介质）：{e}")
        return EXIT_FATAL

    from app.services.rag_service import rag_service

    llm_client = rag_service.openai_client
    if llm_client is None:
        print("[WARN] 未取到 LLM 客户端（key 未配？）——将回退规则抽取，实体质量会明显下降")

    async with AsyncSessionLocal() as session:
        docs = await fetch_indexed_documents(
            session, organization_id=args.org, document_id=args.document_id
        )
    run_mode = "（dry-run，不写数据）" if args.dry_run else ""
    print(f"已索引文档：{len(docs)} 篇{run_mode}")

    async def _text_loader(doc_id: str) -> str:
        async with AsyncSessionLocal() as session:
            return await fetch_document_text(session, doc_id)

    report = await backfill(
        docs, _text_loader, llm_client=llm_client, dry_run=args.dry_run
    )

    # 写后读回核验：有文档被真抽取过，对应组织的图键必须真实存在
    if report["extracted"]:
        extracted = set(report["extracted"])
        for org in {org for doc_id, org in docs if doc_id in extracted}:
            if await redis_client.get(f"rag:graph:{org}") is None:
                print(f"[FATAL] 组织 {org} 图键写后读回失败——数据未持久化")
                return EXIT_FATAL

    for doc_id, err in report["failed"]:
        print(f"  [FAILED] {doc_id}: {err}")
    for doc_id in report["planned"]:
        print(f"  [PLANNED] {doc_id}")
    summary = (
        f"extracted={len(report['extracted'])} skipped={len(report['skipped'])} "
        f"empty={len(report['empty'])} failed={len(report['failed'])}"
    )
    if args.dry_run:
        summary += f" planned={len(report['planned'])}"
    print(summary)
    return EXIT_PARTIAL if report["failed"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
