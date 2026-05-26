import asyncio
import logging
import uuid
from datetime import datetime

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.elasticsearch import ElasticsearchTools, create_index_if_not_exists
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services.document_parser import document_service
from app.services.embedding_service import embedding_service

logger = logging.getLogger(__name__)

class DocumentProcessor:
    def __init__(self):
        # 使用统一的文档解析服务
        self.parser = document_service

    async def process(self, document_id: str):
        """
        处理文档的主流程：
        1. 更新状态为解析中
        2. 解析文件内容与切片 (统一调用 document_service)
        3. 保存切片到数据库
        4. 生成向量 Embedding
        5. 存入 Elasticsearch
        6. 更新状态为索引完成
        """
        logger.info(f"[DOC_PROC] 任务开始：处理文档 {document_id}")

        async with AsyncSessionLocal() as session:
            try:
                # 1. 获取文档信息并更新状态
                stmt = select(Document).where(Document.id == document_id)
                doc = (await session.execute(stmt)).scalar_one_or_none()

                if not doc:
                    logger.error(f"[DOC_PROC] ❌ 文档未找到: {document_id}")
                    return

                doc.status = DocumentStatus.PARSING
                await session.commit()
                logger.info(f"[DOC_PROC] 1/5 - 状态更新为 PARSING: {doc.filename}")

                # 2. 解析与切块 (现在统一使用 document_service，它内部处理了下载)
                logger.info("[DOC_PROC] 2/5 - 开始解析与切块...")
                start_parse_time = asyncio.get_event_loop().time()

                # document_service.parse_document 会自动处理 MinIO URL 并返回 chunks
                parse_result = await self.parser.parse_document(doc.file_path, str(doc.organization_id))
                chunks_data = parse_result.get("chunks", [])

                parse_duration = (asyncio.get_event_loop().time() - start_parse_time) * 1000
                if not chunks_data:
                    raise ValueError("文件解析后无内容，可能为空文件或解析失败。")
                logger.info(f"[DOC_PROC] 2/5 - ✅ 解析成功，获得 {len(chunks_data)} 个块，耗时 {parse_duration:.2f} ms")

                # 3. 保存分块到数据库
                logger.info("[DOC_PROC] 3/5 - 保存分块到数据库...")
                for chunk_data in chunks_data:
                    chunk = DocumentChunk(
                        id=str(uuid.uuid4()),
                        document_id=document_id,
                        chunk_index=chunk_data["chunk_index"],
                        chunk_text=chunk_data["chunk_text"],
                        chunk_length=chunk_data["chunk_length"],
                        start_pos=chunk_data.get("start_pos"),
                        end_pos=chunk_data.get("end_pos"),
                        metadata=chunk_data.get("metadata", {})
                    )
                    session.add(chunk)
                await session.commit()

                # 4. 生成向量
                texts_to_embed = [chunk["chunk_text"] for chunk in chunks_data]
                logger.info(f"[DOC_PROC] 4/5 - 开始为 {len(texts_to_embed)} 个块生成向量...")
                start_embed_time = asyncio.get_event_loop().time()
                embeddings = await self._get_embeddings(texts_to_embed)
                embed_duration = (asyncio.get_event_loop().time() - start_embed_time) * 1000
                if len(embeddings) != len(chunks_data):
                    raise ValueError(f"向量生成数量与文本块数量不匹配: {len(embeddings)} vs {len(chunks_data)}")
                logger.info(f"[DOC_PROC] 4/5 - ✅ 向量生成成功，耗时 {embed_duration:.2f} ms")

                # 5. 索引到 Elasticsearch
                logger.info(f"[DOC_PROC] 5/5 - 开始索引 {len(chunks_data)} 个文档到 Elasticsearch...")
                await create_index_if_not_exists()
                es_docs = []
                for i, chunk in enumerate(chunks_data):
                    es_docs.append({
                        "_index": settings.ELASTICSEARCH_INDEX_NAME,
                        "_id": f"{document_id}_{i}",
                        "_source": {
                            "content": chunk["chunk_text"],
                            "embedding": embeddings[i],
                            "organization_id": str(doc.organization_id),
                            "document_id": document_id,
                            "filename": doc.filename,
                            "file_type": doc.file_type.value if hasattr(doc.file_type, "value") else str(doc.file_type),
                            "file_size": doc.file_size or 0,
                            "upload_time": doc.created_at.isoformat(),
                            "metadata": {
                                "chunk_index": i,
                                "page": chunk.get("metadata", {}).get("page", 0) + 1
                            }
                        }
                    })

                start_index_time = asyncio.get_event_loop().time()
                await ElasticsearchTools.bulk_index_documents(es_docs)
                index_duration = (asyncio.get_event_loop().time() - start_index_time) * 1000
                logger.info(f"[DOC_PROC] 5/5 - ✅ 批量索引成功，耗时 {index_duration:.2f} ms")

                # 更新最终状态
                doc.status = DocumentStatus.INDEXED
                doc.chunk_count = len(chunks_data)
                doc.parsed_at = datetime.now()
                doc.indexed_at = datetime.now()
                doc.content_length = sum(len(c["chunk_text"]) for c in chunks_data)
                await session.commit()
                logger.info(f"[DOC_PROC] 🎉 任务完成: 文档 {document_id} 已成功处理并索引。")

            except Exception as e:
                logger.error(f"[DOC_PROC] ❌ 处理文档 {document_id} 时发生致命错误: {e}", exc_info=True)
                await session.rollback()
                async with AsyncSessionLocal() as error_session:
                    stmt = select(Document).where(Document.id == document_id)
                    doc_err = (await error_session.execute(stmt)).scalar_one_or_none()
                    if doc_err:
                        doc_err.status = DocumentStatus.FAILED
                        doc_err.parse_error = str(e)
                        await error_session.commit()

    async def _get_embeddings(self, texts: list[str]) -> list[list[float]]:
        """统一走 embedding_service，避免模型/API 配置错位。"""
        cleaned = [t.replace("\n", " ") for t in texts if t and t.strip()]
        return await embedding_service.get_embeddings(cleaned)

# 全局实例
processor = DocumentProcessor()
