"""Unified document processor — the single pipeline for parse, chunk, embed, and index.

All document processing entry points (Kafka worker, in-process fallback, ``/files/upload``,
knowledge-base rebuild) should go through ``processor.process()`` so chunk/ES/job state
stays consistent.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime

from sqlalchemy import delete, select, update

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
from app.services.document_status import verify_indexed as _verify_indexed
from app.services.embedding_service import embedding_service

logger = logging.getLogger(__name__)

# 文档处理并发锁：Redis SETNX + TTL 兜底，防止同一文档被并发重复处理
_LOCK_TTL_SECONDS = 600
# 持锁期间每 1/3 TTL 续期一次：大文档解析/向量化/批量索引可能远超 600s，
# 不续期则锁中途过期、第二个 worker 并发处理同一文档（A3-P0）。
_LOCK_HEARTBEAT_INTERVAL_SECONDS = max(30, _LOCK_TTL_SECONDS // 3)

# 只续/只删**自己**的锁：值必须等于本 run 的 token。
# 否则 TTL 过期后新持有者刚拿到的锁会被旧 run 的释放/续期踩掉。
_LOCK_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""
_LOCK_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class DeferredOutcome:
    """第三种终局：消息**未了结**，必须延迟重投（不提交偏移、不记失败）。"""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return "DEFERRED"


DEFERRED = DeferredOutcome()


class StaleRunOutcome:
    """内部哨兵：该消息对应的 job 已完成（SUCCESS）——陈旧重投，不得再触碰文档。"""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return "STALE_RUN"


STALE_RUN = StaleRunOutcome()


class LockLostError(Exception):
    """持锁 run 在阶段边界发现锁已被接管：立即中止，不再写终态/索引。"""


def _noop() -> None:
    """默认进度回调（不关心进度的调用方）。"""


class DocumentProcessor:
    """Parse, embed, and index one document with idempotent replacement."""

    def __init__(self):
        self.parser = document_service
        # document_id -> 本次处理 run 的锁 token（续期/释放时校验归属）
        self._lock_tokens: dict[str, str] = {}
        # 心跳续期发现锁已被接管的 document_id 集合：阶段边界据此中止本 run
        self._lock_lost: set[str] = set()

    async def _acquire_processing_lock(self, document_id: str) -> bool | None:
        """Redis SETNX 并发锁（值为本次 run 的 token）。

        Returns:
            True  — 成功获取锁（处理完成后需释放）；
            False — 同一文档已有任务在处理，本次**不得**当成功提交；
            None  — Redis 不可用，降级为不加锁直接处理（与现有容错风格一致）。
        """
        try:
            from app.core.redis import get_redis
            client = await get_redis()
            token = uuid.uuid4().hex
            acquired = await client.set(
                f"doc:{document_id}:processing",
                token,
                nx=True,
                ex=_LOCK_TTL_SECONDS,
            )
            if acquired:
                self._lock_tokens[document_id] = token
            return bool(acquired)
        except Exception as e:
            logger.warning(
                "[DOC_PROC] Redis unavailable, processing without lock: %s", e
            )
            return None

    async def _renew_processing_lock(self, document_id: str) -> bool:
        """按 TTL 心跳续期；token 不匹配（锁已被他人接管）返回 False 并留痕。"""
        token = self._lock_tokens.get(document_id)
        if not token:
            return False
        try:
            from app.core.redis import get_redis
            client = await get_redis()
            renewed = await client.eval(
                _LOCK_RENEW_SCRIPT,
                1,
                f"doc:{document_id}:processing",
                token,
                str(_LOCK_TTL_SECONDS),
            )
            if not renewed:
                # 锁已过期且被他人接管：本 run 必须尽快中止（阶段边界检查），
                # 否则两个 run 会交替重写 chunk/ES（最后写入者胜）。
                self._lock_lost.add(document_id)
                logger.error(
                    "[DOC_PROC] lock lost during processing document_id=%s: "
                    "token no longer owns the lock; aborting at next checkpoint",
                    document_id,
                )
            return bool(renewed)
        except Exception as e:
            logger.warning(
                "[DOC_PROC] Redis lock renewal failed (TTL will expire): %s", e
            )
            return False

    async def _lock_heartbeat_loop(self, document_id: str) -> None:
        """持锁期间周期续期，直到被 ``process`` 取消。"""
        while True:
            await asyncio.sleep(_LOCK_HEARTBEAT_INTERVAL_SECONDS)
            await self._renew_processing_lock(document_id)

    def _checkpoint(self, document_id: str, notify: Callable[[], None]) -> None:
        """阶段边界：先刷新进度/心跳，再检查锁归属；丢失则中止本 run。"""
        notify()
        if document_id in self._lock_lost:
            raise LockLostError(document_id)

    async def _release_processing_lock(self, document_id: str) -> None:
        """释放并发锁（token 匹配才删）；释放失败时由 TTL 兜底过期。"""
        token = self._lock_tokens.pop(document_id, None)
        try:
            from app.core.redis import get_redis
            client = await get_redis()
            if token:
                await client.eval(
                    _LOCK_RELEASE_SCRIPT, 1, f"doc:{document_id}:processing", token
                )
            else:  # 兼容旧路径/测试替身：无 token 时按原语义删除键
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
    ) -> bool | DeferredOutcome:
        """Run the full document pipeline once.

        Old chunks and ES entries are replaced only after a successful parse, so a
        retry never leaves partial duplicates behind.

        ``on_progress``（可选）在每个阶段边界各调用一次：worker 用它刷新心跳，
        避免单篇大文档的长耗时解析/向量化被健康检查误判为"卡死"（issue #156）。

        返回值契约（issue #301 批 D / #30 起对 consumer 生效；A3-P0 增加第三种）：

        - ``True``——该消息**已了结**：本进程完成索引，或经判断无需（也不应）重投的
          跳过（对象已不存在——响亮日志）。consumer 可以安全提交偏移；
        - ``False``——处理**失败**：文档与任务已尽力如实落 ``FAILED``（含原因），
          consumer 必须按"未提交偏移"处理（走受限重投，见 ``worker/consumer.py``
          的 ``MAX_DELIVERY_ATTEMPTS``），绝不允许当成功提交；
        - ``DEFERRED``——**未了结**（锁被另一处理者持有）：既不是成功也不是失败。
          consumer 必须不提交偏移、不计失败预算，把消息回卷做延迟重投——持锁者
          崩溃时这条投递是文档唯一的恢复路径（A3-P0：修前这里返回 True，
          偏移被提交后文档永久卡 PARSING）。
        """
        notify = on_progress or _noop
        logger.info("[DOC_PROC] start document_id=%s job_id=%s", document_id, job_id)
        self._lock_lost.discard(document_id)
        lock_acquired = await self._acquire_processing_lock(document_id)
        if lock_acquired is False:
            # A3-P0：锁竞争不得当成功提交。持锁者自己的投递在成功前不会提交偏移，
            # 本消息保持未提交 → 崩溃/超时后 Kafka 仍能重投，不会留下永久 PARSING。
            logger.warning(
                "[DOC_PROC] defer document_id=%s job_id=%s: another worker holds the "
                "lock; message stays uncommitted for delayed redelivery",
                document_id,
                job_id,
            )
            return DEFERRED
        notify()
        heartbeat_task: asyncio.Task | None = None
        if lock_acquired is True:
            heartbeat_task = asyncio.create_task(
                self._lock_heartbeat_loop(document_id)
            )
        try:
            doc = await self._load_and_mark_parsing(document_id, job_id)
            if doc is STALE_RUN:
                # A3-P0：该 job 已 SUCCESS（INDEXED 与 job 成功同事务提交）。
                # 这是「成功提交后、偏移提交前」崩溃造成的重投：绝不能再进入
                # PARSING/FAILED，否则一次重投失败就能把 INDEXED 终态打回 FAILED。
                logger.info(
                    "[DOC_PROC] stale redelivery of completed job, keeping terminal "
                    "state document_id=%s job_id=%s",
                    document_id,
                    job_id,
                )
                return True
            if doc is None:
                # 对象已不存在：重投永远不会成功——响亮记录后视同"已了结"，
                # 避免 consumer 对毒消息做无意义的重投/重启循环。同时把 job
                # 收敛为 FAILED，避免启动对账每次重启都重新调度这个孤儿任务。
                logger.error(
                    "[DOC_PROC] document not found, message is terminal: %s", document_id
                )
                await self._mark_job_failed(
                    job_id, "文档不存在，任务无法继续（终局）"
                )
                return True

            parse_result = await self.parser.parse_document(
                doc.file_path, str(doc.organization_id)
            )
            self._checkpoint(document_id, notify)
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
            self._checkpoint(document_id, notify)
            if len(embeddings) != len(chunks_data):
                raise ValueError(
                    f"向量生成数量与文本块数量不匹配: {len(embeddings)} vs {len(chunks_data)}"
                )

            await self._replace_document_state(doc, chunks_data, embeddings)
            self._checkpoint(document_id, notify)
            await self._mark_success(document_id, chunks_data, job_id)
            self._checkpoint(document_id, notify)
            # 图谱抽取（GraphRAG 写链 a8a3d155）：放在 INDEXED 落库之后、独立
            # try/except 内（见 _extract_graph_safely）——抽取失败/LLM 不可用
            # 不得把已成功的文档拖成 FAILED。
            await self._extract_graph_safely(doc, chunks_data)
            logger.info(
                "[DOC_PROC] done document_id=%s chunks=%d", document_id, len(chunks_data)
            )
            return True
        except LockLostError:
            # 锁被接管：不写 FAILED（文档可能由新持有者正常推进），按未了结处理，
            # 让 consumer 重投；若新持有者已完成，重投会走 STALE_RUN 跳过。
            logger.warning(
                "[DOC_PROC] aborting run on lost lock document_id=%s job_id=%s "
                "(no terminal state written; message must be redelivered)",
                document_id,
                job_id,
            )
            return DEFERRED
        except Exception as e:
            logger.error(
                "[DOC_PROC] failed document_id=%s: %s", document_id, e, exc_info=True
            )
            await self._mark_failed(document_id, str(e), job_id)
            return False
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
            if lock_acquired is True:
                await self._release_processing_lock(document_id)

    async def _load_and_mark_parsing(
        self, document_id: str, job_id: int | None
    ) -> Document | None | StaleRunOutcome:
        """加载文档并推进到 PARSING；陈旧重投返回 ``STALE_RUN``（不触碰状态）。

        陈旧判定（A3-P0，独立对抗评审 B2）：该消息携带的 job 已 ``SUCCESS``
        ——意味着上一 run 已把文档写成 INDEXED（两者同事务提交），本次是
        「成功提交后、偏移提交前」崩溃造成的重投；或消息没有 job 且文档已
        INDEXED（无 job 的重复投递）。这两种情况一律跳过，否则重投 run 一旦
        解析失败，就会把 INDEXED 终态打回 FAILED。

        有意重处理（知识库重建）会创建**新的 QUEUED job**，不命中判定。
        """
        async with AsyncSessionLocal() as session:
            doc = (
                await session.execute(
                    select(Document).where(Document.id == document_id)
                )
            ).scalar_one_or_none()
            if not doc:
                logger.error("[DOC_PROC] document not found: %s", document_id)
                return None

            job = None
            if job_id:
                job = await session.get(KnowledgeProcessingJob, job_id)
            if (job is not None and job.status == KnowledgeJobStatus.SUCCESS) or (
                job is None and doc.status == DocumentStatus.INDEXED
            ):
                logger.info(
                    "[DOC_PROC] skip stale run (job SUCCESS or duplicate delivery of "
                    "INDEXED document) document_id=%s job_id=%s",
                    document_id,
                    job_id,
                )
                return STALE_RUN

            doc.status = DocumentStatus.PARSING
            doc.parse_error = None
            if job:
                job.status = KnowledgeJobStatus.PROCESSING
                job.started_at = datetime.now()
            await session.commit()
            return doc

    async def _mark_job_failed(self, job_id: int | None, error: str) -> None:
        """把孤儿 job 收敛为 FAILED（不覆盖已 SUCCESS 的任务）。"""
        if not job_id:
            return
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(KnowledgeProcessingJob)
                .where(
                    KnowledgeProcessingJob.id == job_id,
                    KnowledgeProcessingJob.status != KnowledgeJobStatus.SUCCESS,
                )
                .values(
                    status=KnowledgeJobStatus.FAILED,
                    error_message=error,
                    finished_at=datetime.now(),
                )
                .execution_options(synchronize_session=False)
            )
            await session.commit()

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
        # 回读确认把「INDEXED ⟹ 可检索」变成可执行断言（issue #181；
        # bulk 的 raise_on_error 只防部分失败，不能代替这条断言——
        # 完整说明见 document_status.verify_indexed 的 docstring；
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
        """条件写入 INDEXED：已 INDEXED 的终态不被过期 run 的元数据覆盖（A3-P0）。

        doc 条件更新与 job 终态在**同一事务**提交：避免「文档已 INDEXED、job 仍
        QUEUED」的中间态（单次 commit 保证两者要么都写、要么都不写）。
        """
        now = datetime.now()
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                update(Document)
                .where(
                    Document.id == document_id,
                    Document.status != DocumentStatus.INDEXED,
                )
                .values(
                    status=DocumentStatus.INDEXED,
                    chunk_count=len(chunks_data),
                    parsed_at=now,
                    indexed_at=now,
                    content_length=sum(len(c["chunk_text"]) for c in chunks_data),
                    parse_error=None,
                )
                .execution_options(synchronize_session=False)
            )
            if job_id:
                job = await session.get(KnowledgeProcessingJob, job_id)
                if job:
                    job.status = KnowledgeJobStatus.SUCCESS
                    job.error_message = None
                    job.finished_at = datetime.now()
            await session.commit()
            if not getattr(result, "rowcount", 0):
                # SQLAlchemy 的 MySQL 方言启用 CLIENT.FOUND_ROWS，rowcount 为
                # 「匹配行数」；0 即没有任何行满足 id + status != INDEXED
                # （文档不存在或已是 INDEXED 终态）。
                logger.warning(
                    "[DOC_PROC] INDEXED 写入未生效（文档不存在或已是 INDEXED） "
                    "document_id=%s",
                    document_id,
                )

    async def _mark_failed(
        self, document_id: str, error: str, job_id: int | None
    ) -> None:
        """条件写入 FAILED：**绝不覆盖已 INDEXED 的终态**（A3-P0 终态回退防线）。"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                update(Document)
                .where(
                    Document.id == document_id,
                    Document.status != DocumentStatus.INDEXED,
                )
                .values(status=DocumentStatus.FAILED, parse_error=error)
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            if not getattr(result, "rowcount", 0):
                logger.warning(
                    "[DOC_PROC] FAILED 写入未生效（文档不存在或已是 INDEXED） "
                    "document_id=%s error=%s",
                    document_id,
                    error,
                )

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
