#!/usr/bin/env python3
"""Seed knowledge base with sample documents, directly inserting pre-parsed content."""
import asyncio
import uuid
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Must be before any other imports
os.environ["DOCMIND_DATABASE_URL"] = "sqlite+aiosqlite:///./docmind_dev.db"

from app.core.database import AsyncSessionLocal, init_db
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentType
from app.models.organization import Organization
from app.models.user import User
from app.core.elasticsearch import es_client
from sqlalchemy import select
from datetime import datetime, timezone

SEED_DIR = os.path.dirname(os.path.abspath(__file__))

DOCUMENTS = [
    {
        "filename": "python_tutorial.md",
        "title": "Python 编程基础教程",
        "description": "Python 编程语言基础教程，涵盖变量、函数、面向对象和异步编程",
        "chunks": [
            ("Python 基础语法", "Python 是一种高级、解释型、面向对象的编程语言。Python 支持变量、列表、循环、函数等基础语法。变量无需声明类型，支持字符串、整数、浮点数、布尔值等基本类型。"),
            ("面向对象编程", "Python 支持面向对象编程，通过 class 关键字定义类。类可以包含属性和方法，支持继承和多态。__init__ 方法是构造函数，self 参数表示实例本身。"),
            ("异步编程", "Python 的 asyncio 库支持异步编程。通过 async/await 语法可以编写并发代码。asyncio.run() 运行主协程，await 等待异步操作完成。"),
        ],
    },
    {
        "filename": "architecture.md",
        "title": "DocMind 系统架构设计",
        "description": "DocMind 企业级 RAG 知识库系统架构设计文档",
        "chunks": [
            ("系统分层架构", "DocMind 采用五层架构：表现层(Vue3)、API网关层(FastAPI)、业务逻辑层、AI/LLM层(DeepSeek)、数据存储层(SQLite/MySQL, Redis, ES, MinIO)。"),
            ("RAG 检索管线", "RAG 管线流程：用户提问 → 向量化+关键词提取 → 混合检索 → RRF融合排序 → 语义缓存检查 → LLM生成 → 引用溯源。支持 strict_mode 精准模式和 privacy_mode 隐私模式。"),
            ("Agent 系统 PER 架构", "Agent 系统采用 PER 架构：Plan(规划)将复杂任务拆解为步骤，Execute(执行)按序调用工具，Reflect(反思)自我评估纠正。内置 25+ 工具涵盖搜索、分析、翻译、代码执行等。"),
        ],
    },
]


async def seed():
    await init_db()

    async with AsyncSessionLocal() as db:
        # Get default org and user
        org = (await db.execute(select(Organization).where(Organization.name == "Default"))).scalar_one_or_none()
        user = (await db.execute(select(User).where(User.username == "guest"))).scalar_one_or_none()
        if not org or not user:
            print("Error: Default org or guest user not found. Run the backend first.")
            return

        org_id = org.id
        user_id = user.id

        for doc_info in DOCUMENTS:
            # Check if already exists
            existing = (
                await db.execute(
                    select(Document).where(
                        Document.filename == doc_info["filename"],
                        Document.organization_id == org_id,
                    )
                )
            ).scalar_one_or_none()

            if existing:
                print(f"  Skipping {doc_info['filename']} (already exists)")
                continue

            doc_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            full_text = "\n\n".join(c[1] for c in doc_info["chunks"])
            content_length = len(full_text)
            chunk_count = len(doc_info["chunks"])

            doc = Document(
                id=doc_id,
                filename=doc_info["filename"],
                file_path=f"seed_docs/{doc_info['filename']}",
                file_size=len(full_text.encode("utf-8")),
                file_type=DocumentType.TXT,
                mime_type="text/markdown",
                title=doc_info["title"],
                description=doc_info["description"],
                status=DocumentStatus.INDEXED,
                content_length=content_length,
                chunk_count=chunk_count,
                organization_id=org_id,
                uploaded_by=user_id,
                created_at=now,
                updated_at=now,
                parsed_at=now,
                indexed_at=now,
            )
            db.add(doc)

            for idx, (section_title, text) in enumerate(doc_info["chunks"]):
                chunk = DocumentChunk(
                    id=str(uuid.uuid4()),
                    document_id=doc_id,
                    chunk_index=idx,
                    chunk_text=text,
                    chunk_length=len(text),
                    section_title=section_title,
                    meta_data={"source": "seed_docs", "section": section_title},
                )
                db.add(chunk)

            print(f"  Created: {doc_info['filename']} ({chunk_count} chunks, {content_length} chars)")

        await db.commit()

    # Also try to index into ES if available
    if es_client:
        try:
            from app.rag.indexer import indexer
            indexed = await indexer.index_all_pending()
            if indexed:
                print(f"  Indexed {indexed} chunks into Elasticsearch")
        except Exception as e:
            print(f"  ES indexing skipped: {e}")
    else:
        print("  ES not available, chunks stored in DB only")

    print("\nSeeding complete!")


if __name__ == "__main__":
    asyncio.run(seed())
