"""Unified document processor — the single pipeline for parse, chunk, embed, and index.

All document processing entry points (Kafka worker, in-process fallback, ``/files/upload``,
knowledge-base rebuild) should go through ``processor.process()`` so chunk/ES/job state
stays consistent.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.elasticsearch import (
    ElasticsearchTools,
    create_index_if_not_exists,
    get_elasticsearch,
)
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.knowledge_job import KnowledgeJobStatus, KnowledgeProcessingJob
from app.services.document_parser import document_service
from app.services.embedding_service import embedding_service

logger = logging.getLogger(__name__)

# 文档处理并发锁：Redis SETNX + TTL 兜底，防止同一文档被并发重复处理
_LOCK_TTL_SECONDS = 600


def _noop() -> None:
    """默认进度回调（不关心进度的调用方）。"""


async def _verify_indexed(
    client, document_id: str, expected: int, *, organization_id: str
) -> int:
    """确认该文档的 chunk **真的**能被检索到；不能就抛错。

    三条不变式（issue #181 / #224）——「状态 = INDEXED」必须意味着文档可被检索：

    1. 该文档至少有一条 chunk 落在索引里（#181）；
    2. 其中至少一条带 **embedding 字段**（#224；`exists` 判据只证"存在"，
       不证"非退化" —— 零向量由写入路径保证不产生）：空值在 ES 里 `exists=false`，
       而向量通道硬过滤 `exists: embedding` —— 一条都没有 = 混合检索的向量臂
       永远查不到它；
    3. chunk 的 `organization_id` 与该文档一致（#224）：检索通道硬过滤组织 ——
       org 写错 = 谁都搜不到。

    为什么必须校验（issue #181）：**`_mark_success` 是无条件把状态置成 `INDEXED` 的** ——
    它不看 ES 里到底有没有东西。而「已索引」这个状态是运维、前端与监控判断
    「知识库到底能不能用」的唯一依据。

    **不要用「库会抛错」当判据。** 我最初在这里写的是"bulk 不抛错 ≠ 数据进去了"，
    但**实测不成立**：`async_bulk` 默认 `raise_on_error=True`，遇到 mapping 冲突会抛
    `BulkIndexError`（对抗评审 F1 指出，已实测复现）。那条因果故事当时没验证过。

    真正的理由是**不变式要在它被断言的地方强制**：
    「状态 = INDEXED ⟹ 这篇文档在 ES 里查得到」这条不变式，此前唯一的执行者是
    「bulk 库大概会抛错」这个假设。回读校验把它变成一条可执行的断言 ——
    库的行为变了、mapping 改了、将来有人把 bulk 换成别的写法，都不再能让它悄悄失效。

    返回实际索引到的条数（便于调用方记录）。
    """
    index_name = settings.ELASTICSEARCH_INDEX_NAME
    await client.indices.refresh(index=index_name)

    res = await client.count(
        index=index_name, query={"term": {"document_id": document_id}}
    )
    indexed = int(res.get("count", 0))

    if indexed == 0:
        raise RuntimeError(
            f"索引校验失败：文档 {document_id} 的 {expected} 个 chunk 已提交给 Elasticsearch，"
            f"但刷新后在索引 {index_name} 里查不到任何一条。"
            f"拒绝把该文档标记为已索引 —— 否则会出现「状态已索引、检索却查不到」。"
        )

    res_emb = await client.count(
        index=index_name,
        query={
            "bool": {
                "filter": [
                    {"term": {"document_id": document_id}},
                    {"exists": {"field": "embedding"}},
                ]
            }
        },
    )
    with_embedding = int(res_emb.get("count", 0))
    if with_embedding == 0:
        raise RuntimeError(
            f"索引校验失败：文档 {document_id} 在索引 {index_name} 里有 {indexed} 条 chunk，"
            f"但**没有任何一条带 embedding 字段** —— 向量通道硬过滤 exists:embedding，"
            f"这些内容对混合检索的向量臂永远不可见（#224）。拒绝把该文档标记为已索引。"
        )

    res_org = await client.count(
        index=index_name,
        query={
            "bool": {
                "filter": [
                    {"term": {"document_id": document_id}},
                    {"term": {"organization_id": str(organization_id)}},
                ]
            }
        },
    )
    with_org = int(res_org.get("count", 0))
    if with_org == 0:
        raise RuntimeError(
            f"索引校验失败：文档 {document_id} 的 chunk 的 organization_id 与文档不一致"
            f"（期望 {organization_id!r}）—— 检索通道硬过滤组织，谁都搜不到它（#224）。"
            f"拒绝把该文档标记为已索引。"
        )

    if indexed != expected:
        # 部分落库：文档确实可被检索到，但内容不完整。不阻断，但必须留痕。
        logger.warning(
            "[DOC_PROC] 索引条数与预期不符 document_id=%s 预期=%d 实际=%d",
            document_id,
            expected,
            indexed,
        )
    if with_embedding < indexed or with_org < indexed:
        # 部分 chunk 对某条检索引擎通道不可见：文档整体可检索（有带向量的、
        # 组织匹配的 chunk），但不完整。不阻断，必须留痕。
        logger.warning(
            "[DOC_PROC] 索引可检索性不完整 document_id=%s 总数=%d 带embedding=%d 组织匹配=%d",
            document_id,
            indexed,
            with_embedding,
            with_org,
        )
    return indexed




class DocumentProcessor:
    """Parse, embed, and index one document with idempotent replacement."""

    def __init__(self):
        self.parser = document_service

    async def _acquire_processing_lock(self, document_id: str) -> bool | None:
        """Redis SETNX 并发锁。

        Returns:
            True  — 成功获取锁（处理完成后需释放）；
            False — 同一文档已有任务在处理，本次应跳过；
            None  — Redis 不可用，降级为不加锁直接处理（与现有容错风格一致）。
        """
        try:
            from app.core.redis import get_redis
            client = await get_redis()
            acquired = await client.set(
                f"doc:{document_id}:processing", "1", nx=True, ex=_LOCK_TTL_SECONDS
            )
            return bool(acquired)
        except Exception as e:
            logger.warning(
                "[DOC_PROC] Redis unavailable, processing without lock: %s", e
            )
            return None

    async def _release_processing_lock(self, document_id: str) -> None:
        """释放并发锁；释放失败时由 TTL 兜底过期。"""
        try:
            from app.core.redis import get_redis
            client = await get_redis()
            await client.delete(f"doc:{document_id}:processing")
        except Exception as e:
            logger.warning(
                "[DOC_PROC] Redis lock release failed (TTL will expire): %s", e
            )

    async def process(
        self,
        document_id: str,
        job_id: int | None = None,
        on_progress: Callable[[], None] | None = None,
    ) -> bool:
        """Run the full document pipeline once.

        Old chunks and ES entries are replaced only after a successful parse, so a
        retry never leaves partial duplicates behind.

        ``on_progress``（可选）在每个阶段边界各调用一次：worker 用它刷新心跳，
        避免单篇大文档的长耗时解析/向量化被健康检查误判为"卡死"（issue #156）。

        返回值契约（issue #301 批 D / #30 起对 consumer 生效，此前 consumer 忽略返回值）：

        - ``True``——该消息**已了结**：本进程完成索引，或经判断无需（也不应）重投的
          跳过（另一持锁者正在处理 / 对象已不存在——两处都有响亮日志）。
          consumer 可以安全提交偏移；
        - ``False``——处理**失败**：文档与任务已尽力如实落 ``FAILED``（含原因），
          consumer 必须按"未提交偏移"处理（走受限重投，见 ``worker/consumer.py``
          的 ``MAX_DELIVERY_ATTEMPTS``），绝不允许当成功提交。
        """
        notify = on_progress or _noop
        logger.info("[DOC_PROC] start document_id=%s job_id=%s", document_id, job_id)
        lock_acquired = await self._acquire_processing_lock(document_id)
        if lock_acquired is False:
            # 重复投递/并发处理中：完成责任在持锁者手中，本消息可以安全提交
            # （持锁者自己的投递在成功前不会提交偏移，崩溃后其消息会重投——
            # 至少一次语义由那条投递维系）。
            logger.info(
                "[DOC_PROC] skip document_id=%s job_id=%s: already being processed",
                document_id,
                job_id,
            )
            return True
        notify()
        try:
            doc = await self._load_and_mark_parsing(document_id, job_id)
            if doc is None:
                # 对象已不存在：重投永远不会成功——响亮记录后视同"已了结"，
                # 避免 consumer 对毒消息做无意义的重投/重启循环。
                logger.error(
                    "[DOC_PROC] document not found, message is terminal: %s", document_id
                )
                return True

            parse_result = await self.parser.parse_document(
                doc.file_path, str(doc.organization_id)
            )
            notify()
            chunks_data = parse_result.get("chunks", [])
            # issue #301 批 D（#28）：先剔除全空白 chunk 再判空。修前只看"列表非空"，
            # 而扫描件/OCR 缺失时解析链会产出一个**空文本 chunk**（content="" →
            # semantic_chunker 返回 [{"text": ""}]）——空内容因此绕过本护栏，
            # 一路向量化并写 INDEXED（"已完成"的文档永远搜不到）。空内容 = 处理失败。
            non_empty = [c for c in chunks_data if (c.get("chunk_text") or "").strip()]
            if len(non_empty) != len(chunks_data):
                logger.warning(
                    "[DOC_PROC] dropped %d blank chunk(s) document_id=%s",
                    len(chunks_data) - len(non_empty),
                    document_id,
                )
            chunks_data = non_empty
            if not chunks_data:
                raise ValueError(
                    "文件解析后无有效文本内容：可能为空文件、扫描件（OCR 依赖不可用）或解析失败。"
                )

            embeddings = await self._get_embeddings(
                [c["chunk_text"] for c in chunks_data]
            )
            notify()
            if len(embeddings) != len(chunks_data):
                raise ValueError(
                    f"向量生成数量与文本块数量不匹配: {len(embeddings)} vs {len(chunks_data)}"
                )

            await self._replace_document_state(doc, chunks_data, embeddings)
            notify()
            await self._mark_success(document_id, chunks_data, job_id)
            notify()
            # 图谱抽取（GraphRAG 写链 a8a3d155）：放在 INDEXED 落库之后、独立
            # try/except 内（见 _extract_graph_safely）——抽取失败/LLM 不可用
            # 不得把已成功的文档拖成 FAILED。
            await self._extract_graph_safely(doc, chunks_data)
            logger.info(
                "[DOC_PROC] done document_id=%s chunks=%d", document_id, len(chunks_data)
            )
            return True
        except Exception as e:
            logger.error(
                "[DOC_PROC] failed document_id=%s: %s", document_id, e, exc_info=True
            )
            await self._mark_failed(document_id, str(e), job_id)
            return False
        finally:
            if lock_acquired is True:
                await self._release_processing_lock(document_id)

    async def _load_and_mark_parsing(
        self, document_id: str, job_id: int | None
    ) -> Document | None:
        async with AsyncSessionLocal() as session:
            doc = (
                await session.execute(
                    select(Document).where(Document.id == document_id)
                )
            ).scalar_one_or_none()
            if not doc:
                logger.error("[DOC_PROC] document not found: %s", document_id)
                return None

            doc.status = DocumentStatus.PARSING
            doc.parse_error = None
            if job_id:
                job = await session.get(KnowledgeProcessingJob, job_id)
                if job:
                    job.status = KnowledgeJobStatus.PROCESSING
                    job.started_at = datetime.now()
            await session.commit()
            return doc

    async def _replace_document_state(
        self, doc: Document, chunks_data: list[dict], embeddings: list[list[float]]
    ) -> None:
        chunk_rows: list[DocumentChunk] = []
        es_docs: list[dict] = []

        for i, (chunk_data, embedding) in enumerate(
            zip(chunks_data, embeddings, strict=False)
        ):
            chunk_id = str(uuid.uuid4())
            metadata = chunk_data.get("metadata", {}) or {}
            chunk_rows.append(
                DocumentChunk(
                    id=chunk_id,
                    document_id=doc.id,
                    chunk_index=chunk_data["chunk_index"],
                    chunk_text=chunk_data["chunk_text"],
                    chunk_length=chunk_data["chunk_length"],
                    start_pos=chunk_data.get("start_pos"),
                    end_pos=chunk_data.get("end_pos"),
                    page_number=metadata.get("page_number"),
                    section_title=metadata.get("section_title"),
                    meta_data=metadata,
                )
            )
            es_docs.append(
                {
                    "_index": settings.ELASTICSEARCH_INDEX_NAME,
                    "_id": f"{doc.id}_{chunk_id}",
                    "_source": {
                        "content": chunk_data["chunk_text"],
                        "chunk_text": chunk_data["chunk_text"],
                        "embedding": embedding,
                        "organization_id": str(doc.organization_id),
                        "document_id": doc.id,
                        "filename": doc.filename,
                        "file_type": (
                            doc.file_type.value
                            if hasattr(doc.file_type, "value")
                            else str(doc.file_type)
                        ),
                        "file_size": doc.file_size or 0,
                        "upload_time": (
                            doc.created_at.isoformat() if doc.created_at else None
                        ),
                        "user_id": str(doc.uploaded_by),
                        "metadata": {
                            "chunk_index": i,
                            "chunk_id": chunk_id,
                            "page_number": metadata.get("page_number"),
                            "section_title": metadata.get("section_title"),
                        },
                    },
                }
            )

        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == doc.id)
            )
            session.add_all(chunk_rows)
            await session.commit()

        await create_index_if_not_exists()
        client = await get_elasticsearch()
        await client.delete_by_query(
            index=settings.ELASTICSEARCH_INDEX_NAME,
            query={"term": {"document_id": doc.id}},
            refresh=True,
        )
        await ElasticsearchTools.bulk_index_documents(es_docs)
        # bulk 不抛错不代表写进去了 —— 必须回读确认（issue #181；
        # 校验范围在 #224 加严：embedding 有效性与 organization_id 一致性）
        await _verify_indexed(
            client, doc.id, len(es_docs), organization_id=str(doc.organization_id)
        )

        # 替换成功后按组织清空 RAG 缓存，避免旧内容仍命中缓存
        from app.rag.cache import RetrievalCache, SemanticCache
        await RetrievalCache().clear_for_org(doc.organization_id)
        await SemanticCache().clear_for_org(doc.organization_id)

    async def _mark_success(
        self, document_id: str, chunks_data: list[dict], job_id: int | None
    ) -> None:
        async with AsyncSessionLocal() as session:
            doc = await session.get(Document, document_id)
            if doc:
                doc.status = DocumentStatus.INDEXED
                doc.chunk_count = len(chunks_data)
                doc.parsed_at = datetime.now()
                doc.indexed_at = datetime.now()
                doc.content_length = sum(len(c["chunk_text"]) for c in chunks_data)
                doc.parse_error = None
            if job_id:
                job = await session.get(KnowledgeProcessingJob, job_id)
                if job:
                    job.status = KnowledgeJobStatus.SUCCESS
                    job.error_message = None
                    job.finished_at = datetime.now()
            await session.commit()

    async def _mark_failed(
        self, document_id: str, error: str, job_id: int | None
    ) -> None:
        async with AsyncSessionLocal() as session:
            doc = await session.get(Document, document_id)
            if doc:
                doc.status = DocumentStatus.FAILED
                doc.parse_error = error
            await session.commit()

        if job_id:
            async with AsyncSessionLocal() as session:
                job = await session.get(KnowledgeProcessingJob, job_id)
                if job:
                    job.status = KnowledgeJobStatus.FAILED
                    job.error_message = error
                    job.finished_at = datetime.now()
                    await session.commit()

    async def _extract_graph_safely(self, doc: Document, chunks_data: list[dict]) -> None:
        """索引成功后的知识图谱抽取（非阻塞契约）。

        - 任何失败（LLM 不可用/超时/Redis 异常）只记 warning，文档保持 INDEXED，
          绝不进入 ``_mark_failed``；
        - llm_client 取自 RAG 管线（与 document_parser 摘要生成同口径）；
        - 幂等由 ``graph_rag_service.ingest_document`` 的文档标记保证——
          同一文档重复处理不会二次叠加图谱数据。
        """
        try:
            from app.services.graph_rag_service import graph_rag_service
            from app.services.rag_service import rag_service

            text = "\n".join((c.get("chunk_text") or "") for c in chunks_data)
            result = await graph_rag_service.ingest_document(
                document_id=doc.id,
                organization_id=doc.organization_id,
                text=text,
                llm_client=rag_service.openai_client,
            )
            if result["status"] == "skipped":
                logger.info(
                    "[DOC_PROC] graph skipped (already ingested) document_id=%s", doc.id
                )
            else:
                logger.info(
                    "[DOC_PROC] graph extracted document_id=%s entities=%d",
                    doc.id,
                    result["entities"],
                )
        except Exception as e:
            logger.warning(
                "[DOC_PROC] graph extraction failed (non-blocking) document_id=%s: %s",
                doc.id,
                e,
            )

    async def _get_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Batch embedding with stable length; empty text becomes a placeholder."""
        cleaned = [
            t.replace("\n", " ") if t and t.strip() else " " for t in texts
        ]
        return await embedding_service.get_embeddings(cleaned)


# Global instance used by the worker, API fallback, and rebuild endpoints.
processor = DocumentProcessor()
