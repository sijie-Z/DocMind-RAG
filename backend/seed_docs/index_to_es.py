#!/usr/bin/env python3
"""Index any unindexed document chunks into Elasticsearch."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DOCMIND_DATABASE_URL"] = "sqlite+aiosqlite:///./docmind_dev.db"

from app.core.database import AsyncSessionLocal
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.core.elasticsearch import es_client, init_elasticsearch
from sqlalchemy import select
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

INDEX_NAME = "docmind_knowledge"


async def index_chunks():
    await init_elasticsearch()

    # Index is created by init_elasticsearch() with proper cjk analyzer (see elasticsearch.py)

    async with AsyncSessionLocal() as db:
        # Find documents with INDEXED status that may not be fully in ES
        result = await db.execute(
            select(Document).where(Document.status == DocumentStatus.INDEXED)
        )
        documents = result.scalars().all()

        total_indexed = 0
        for doc in documents:
            chunks_result = await db.execute(
                select(DocumentChunk).where(
                    DocumentChunk.document_id == doc.id
                ).order_by(DocumentChunk.chunk_index)
            )
            chunks = chunks_result.scalars().all()

            for chunk in chunks:
                # Check if already in ES
                try:
                    existing = await es_client.get(
                        index=INDEX_NAME, id=chunk.id, ignore=[404]
                    )
                    if existing.get("found"):
                        logger.info(f"  Already in ES: chunk {chunk.id[:8]}...")
                        continue
                except Exception:
                    pass

                # Index into ES
                doc_body = {
                    "chunk_id": chunk.id,
                    "document_id": doc.id,
                    "filename": doc.filename,
                    "chunk_text": chunk.chunk_text,
                    "chunk_index": chunk.chunk_index,
                    "section_title": chunk.section_title or "",
                    "organization_id": str(doc.organization_id),
                    "content_length": chunk.chunk_length,
                }

                await es_client.index(
                    index=INDEX_NAME,
                    id=chunk.id,
                    body=doc_body,
                    refresh="wait_for",
                )
                total_indexed += 1
                logger.info(f"  Indexed chunk {chunk.id[:8]}... ({chunk.section_title})")

        logger.info(f"\nDone! Indexed {total_indexed} new chunks into {INDEX_NAME}")

    # Close ES
    await es_client.close()


if __name__ == "__main__":
    asyncio.run(index_chunks())
