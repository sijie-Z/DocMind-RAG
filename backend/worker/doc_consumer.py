# backend/worker/doc_consumer.py
import sys
import os
import asyncio
import json
import logging
import tempfile
import shutil
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiokafka import AIOKafkaConsumer
from sqlalchemy import select
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.minio_client import minio_client
from app.core.elasticsearch import get_elasticsearch
from app.models.document import Document, DocumentStatus
from app.services.doc_parser import doc_parser
from app.services.embedding_service import embedding_service

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("RAG-Worker")

async def process_document(task_data: dict):
    doc_id = task_data.get("document_id")
    file_key = task_data.get("file_path")
    org_id = task_data.get("organization_id")
    filename = task_data.get("filename") or "unknown"

    if not isinstance(file_key, str) or not file_key:
        logger.error(f"💥 Worker Error: invalid file_path={file_key!r} for document {doc_id}")
        return
    
    logger.info(f"🚀 Processing: {filename} (ID: {doc_id})")
    
    tmp_dir = tempfile.mkdtemp()
    tmp_file_path = os.path.join(tmp_dir, os.path.basename(file_key))
    session = AsyncSessionLocal()
    es_client = await get_elasticsearch()
    
    try:
        await _update_status(session, doc_id, DocumentStatus.PARSING)
        
        # 下载并解析
        await asyncio.to_thread(minio_client.client.fget_object, settings.MINIO_BUCKET_NAME, file_key, tmp_file_path)
        chunks = await asyncio.to_thread(doc_parser.parse_and_chunk, tmp_file_path)

        if not chunks:
            ext = os.path.splitext(filename or "")[1].lower()
            if ext == ".doc":
                err_msg = "DOC 解析失败：未提取到文本。建议用 Word/LibreOffice 另存为 .docx 后重试。"
            elif ext == ".docx":
                err_msg = "DOCX 解析失败：未提取到文本。可能是受保护或扫描件，请另存为可编辑 .docx 后重试。"
            elif ext == ".pdf":
                err_msg = "PDF 解析失败：未提取到文本。可能为扫描件（图片PDF），请启用OCR或转换为可检索PDF后重试。"
            else:
                err_msg = "文档解析失败：未提取到文本内容，请检查文件格式或内容是否为空。"
            await _update_status(session, doc_id, DocumentStatus.FAILED, error=err_msg)
            return

        # 向量化（失败时降级为关键词索引，保证文档可检索）
        texts = [c.page_content for c in chunks]
        vectors = []
        embedding_failed = False
        embedding_error_msg = ""
        try:
            vectors = await embedding_service.get_embeddings(texts)
        except Exception as emb_err:
            embedding_failed = True
            embedding_error_msg = str(emb_err)
            logger.warning(f"Embedding failed for {doc_id}, fallback to keyword-only index: {emb_err}")

        if vectors and len(vectors) != len(chunks):
            logger.warning(f"Embedding size mismatch, chunks={len(chunks)} vectors={len(vectors)} for {doc_id}")

        # 写入 ES
        bulk_data = []
        max_len = len(chunks) if (embedding_failed or not vectors) else min(len(chunks), len(vectors))
        for i in range(max_len):
            chunk = chunks[i]
            doc_body = {
                "document_id": doc_id,
                "content": chunk.page_content,
                "filename": filename,
                "organization_id": str(org_id),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            # 仅在有向量时写 embedding 字段
            if not embedding_failed and vectors and i < len(vectors) and vectors[i]:
                doc_body["embedding"] = vectors[i]

            bulk_data.append({"index": {"_index": settings.ELASTICSEARCH_INDEX_NAME, "_id": f"{doc_id}_{i}"}})
            bulk_data.append(doc_body)
            
        if bulk_data:
            # ✅ 增加错误检查
            response = await es_client.bulk(operations=bulk_data, refresh=True)
            if response.get("errors"):
                first_error = response['items'][0]['index'].get('error', 'Unknown ES error')
                logger.error(f"❌ ES Bulk Indexing Failed! Reason: {first_error}")
                raise Exception(f"ES Bulk Error: {first_error}")
            else:
                logger.info(f"✅ Successfully indexed {len(bulk_data)//2} chunks to ES.")

        if embedding_failed:
            await _update_status(
                session,
                doc_id,
                DocumentStatus.INDEXED,
                error=f"向量接口异常，已降级为关键词索引，可检索但语义召回受限。详情: {embedding_error_msg[:200]}"
            )
        else:
            await _update_status(session, doc_id, DocumentStatus.INDEXED)

    except Exception as e:
        logger.error(f"💥 Worker Error: {e}")
        await _update_status(session, doc_id, DocumentStatus.FAILED, error=str(e))
    finally:
        if os.path.exists(tmp_dir): shutil.rmtree(tmp_dir)
        await session.close()

async def _update_status(session, doc_id, status, error=None):
    try:
        stmt = select(Document).where(Document.id == doc_id)
        result = await session.execute(stmt)
        doc = result.scalar_one_or_none()
        if doc:
            doc.status = status
            if error: doc.parse_error = error
            await session.commit()
    except Exception as e:
        logger.error(f"DB Update Error: {e}")

async def consume():
    consumer = AIOKafkaConsumer(
        settings.KAFKA_FILE_PROCESSING_TOPIC,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id="rag_worker_group",
        value_deserializer=lambda m: json.loads(m.decode('utf-8'))
    )
    await consumer.start()
    logger.info("📡 Worker started and listening to Kafka...")
    try:
        async for msg in consumer:
            await process_document(msg.value)
    finally:
        await consumer.stop()

if __name__ == "__main__":
    if sys.platform == 'win32': asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(consume())
    