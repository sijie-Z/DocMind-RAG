"""Unified document processor — the single pipeline for parse, chunk, embed, and index.

All document processing entry points (Kafka worker, in-process fallback, ``/files/upload``,
knowledge-base rebuild) should go through ``processor.process()`` so chunk/ES/job state
stays consistent.
"""

import logging
import uuid
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

    async def process(self, document_id: str, job_id: int | None = None) -> bool:
        """Run the full document pipeline once.

        Old chunks and ES entries are replaced only after a successful parse, so a
        retry never leaves partial duplicates behind.
        """
        logger.info("[DOC_PROC] start document_id=%s job_id=%s", document_id, job_id)
        lock_acquired = await self._acquire_processing_lock(document_id)
        if lock_acquired is False:
            logger.info(
                "[DOC_PROC] skip document_id=%s job_id=%s: already being processed",
                document_id,
                job_id,
            )
            return False
        try:
            doc = await self._load_and_mark_parsing(document_id, job_id)
            if doc is None:
                return False

            parse_result = await self.parser.parse_document(
                doc.file_path, str(doc.organization_id)
            )
            chunks_data = parse_result.get("chunks", [])
            if not chunks_data:
                raise ValueError("文件解析后无内容，可能为空文件或解析失败。")

            embeddings = await self._get_embeddings(
                [c["chunk_text"] for c in chunks_data]
            )
            if len(embeddings) != len(chunks_data):
                raise ValueError(
                    f"向量生成数量与文本块数量不匹配: {len(embeddings)} vs {len(chunks_data)}"
                )

            await self._replace_document_state(doc, chunks_data, embeddings)
            await self._mark_success(document_id, chunks_data, job_id)
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

    async def _get_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Batch embedding with stable length; empty text becomes a placeholder."""
        cleaned = [
            t.replace("\n", " ") if t and t.strip() else " " for t in texts
        ]
        return await embedding_service.get_embeddings(cleaned)


# Global instance used by the worker, API fallback, and rebuild endpoints.
processor = DocumentProcessor()
