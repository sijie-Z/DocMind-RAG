"""
知识库服务 - 管理知识库的构建、检索和问答
"""
import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import uuid

from app.core.database import AsyncSessionLocal
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services.document_parser import document_service  # type: ignore
from app.services.embedding_service import embedding_service
from app.core.elasticsearch import get_elasticsearch, ElasticsearchTools
from app.services.organization_service import organization_service
from app.services.file_service import FileUploadService
from sqlalchemy import select, and_, func, delete
from sqlalchemy.orm import selectinload
from app.core.config import settings

logger = logging.getLogger(__name__)


class KnowledgeService:
    """知识库服务 - 企业级RAG核心实现"""
    
    def __init__(self):
        self.index_name = settings.ELASTICSEARCH_INDEX_NAME
        self.file_upload_service = FileUploadService()
    
    async def build_knowledge_base(self, document_id: str) -> bool:
        """
        构建知识库 - 将文档转换为可搜索的知识（企业级流程）
        """
        try:
            logger.info(f"🚀 开始构建企业级知识库，文档ID: {document_id}")
            
            # 1. 获取文档信息
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Document)
                    .options(selectinload(Document.chunks))
                    .where(Document.id == document_id)
                )
                document = result.scalar_one_or_none()
                
                if not document:
                    logger.error(f"❌ 文档不存在: {document_id}")
                    return False
                
                # 更新状态为索引中
                document.status = DocumentStatus.INDEXED  # type: ignore
                document.indexed_at = datetime.now()  # type: ignore
                await session.commit()
            
            # 2. 解析文档
            chunks = await document_service.get_document_chunks(document_id)
            if not chunks:
                logger.warning(f"⚠️ 文档没有分块内容: {document_id}")
                return False
            
            # 3. 批量向量化与索引
            batch_size = 50
            for i in range(0, len(chunks), batch_size):
                batch = chunks[i:i+batch_size]
                texts = [c.chunk_text for c in batch]
                
                # 获取向量
                embeddings = await embedding_service.get_embeddings(texts)
                
                # 写入 Elasticsearch
                for j, chunk in enumerate(batch):
                    es_doc = {
                        "content": chunk.chunk_text,
                        "embedding": embeddings[j],
                        "filename": document.filename,
                        "document_id": document_id,
                        "organization_id": str(document.organization_id),
                        "user_id": str(document.uploaded_by),
                        "metadata": {
                            "document_id": document_id,
                            "chunk_id": chunk.id,
                            "page_number": chunk.page_number,
                            "section_title": chunk.section_title
                        }
                    }
                    await ElasticsearchTools.index_document(f"{document_id}_{chunk.id}", es_doc)
            
            logger.info(f"✅ 知识库构建完成，文档ID: {document_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ 构建知识库失败: {str(e)}")
            return False
    
    async def search_knowledge(
        self, 
        query: str, 
        organization_id: Optional[str] = None,
        top_k: int = 5,
        search_mode: str = "hybrid"
    ) -> List[Dict[str, Any]]:
        """
        搜索知识库 - 统一使用 RagService 以获得更强能力
        """
        from app.services.rag_service import rag_service
        try:
            # 转换 organization_id 为 int
            org_id = int(organization_id) if organization_id else 0
            
            # 使用 RagService 进行高级检索
            results = await rag_service.search_knowledge_base(
                query=query,
                organization_id=org_id,
                top_k=top_k
            )
            
            # 格式化为 KnowledgeService 期望的输出格式以保持兼容性
            formatted_results = []
            for res in results:
                formatted_results.append({
                    "id": res.get("document_id"),
                    "text": res.get("text"),
                    "score": res.get("score"),
                    "metadata": {
                        "filename": res.get("filename"),
                        "snippet": res.get("snippet"),
                        "overlap_score": res.get("overlap_score"),
                        "mmr_score": res.get("mmr_score")
                    },
                    "source": {
                        "filename": res.get("filename"),
                        "document_id": res.get("document_id")
                    }
                })
            return formatted_results
        except Exception as e:
            logger.error(f"Knowledge search failed: {e}")
            return []

    async def get_search_suggestions(self, query: str, organization_id: str, limit: int = 5) -> List[str]:
        """获取搜索建议"""
        # 简单实现：使用关键词搜索的前几个标题
        results = await self.search_knowledge(query, organization_id, top_k=limit, search_mode="keyword")
        return [r["text"][:50] + "..." for r in results]

    async def get_knowledge_stats(self, organization_id: str) -> Dict[str, Any]:
        """获取知识库统计信息"""
        async with AsyncSessionLocal() as session:
            # 文档总数
            doc_count_result = await session.execute(
                select(func.count(Document.id)).where(Document.organization_id == organization_id)
            )
            doc_count = doc_count_result.scalar() or 0
            
            # 已索引文档数
            indexed_count_result = await session.execute(
                select(func.count(Document.id))
                .where(and_(Document.organization_id == organization_id, Document.status == DocumentStatus.INDEXED))
            )
            indexed_count = indexed_count_result.scalar() or 0
            
            return {
                "total_documents": doc_count,
                "indexed_documents": indexed_count,
                "total_size": 0, # 这里可以进一步计算文件大小总和
                "last_updated": datetime.now().isoformat()
            }

    async def delete_knowledge(self, document_id: str, organization_id: Optional[int] = None) -> bool:
        """兼容旧接口的删除方法"""
        return await self.delete_knowledge_thoroughly(document_id)

    async def delete_es_by_document_id(self, document_id: str) -> bool:
        """仅从 ES 中删除该文档的所有 chunk（用于重建前清理）"""
        try:
            client = await get_elasticsearch()
            await client.delete_by_query(
                index=self.index_name,
                query={"term": {"document_id": document_id}}
            )
            logger.info(f"已从 ES 删除 document_id={document_id} 的索引")
            return True
        except Exception as e:
            logger.error(f"从 ES 按 document_id 删除失败: {str(e)}")
            return False

    async def delete_knowledge_thoroughly(self, document_id: str) -> bool:
        """彻底删除文档"""
        try:
            logger.info(f"🧹 正在彻底清理文档: {document_id}")
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(Document).where(Document.id == document_id))
                doc = result.scalar_one_or_none()
                if not doc:
                    return True
                
                if doc.file_path:
                    try:
                        await self.file_upload_service.delete_file(doc.file_path)
                    except Exception as e:
                        logger.warning(f"从MinIO删除文件失败: {str(e)}")
                
                client = await get_elasticsearch()
                try:
                    await client.delete_by_query(
                        index=self.index_name,
                        query={"term": {"document_id": document_id}}
                    )
                except Exception as e:
                    logger.warning(f"从ES删除索引失败: {str(e)}")
                
                await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
                await session.execute(delete(Document).where(Document.id == document_id))
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"彻底删除文档失败: {str(e)}")
            return False

    async def batch_delete_knowledge(self, document_ids: List[str], organization_id: Optional[int] = None) -> Dict[str, Any]:
        """批量删除文档"""
        success_count = 0
        fail_count = 0
        for doc_id in document_ids:
            if await self.delete_knowledge_thoroughly(doc_id):
                success_count += 1
            else:
                fail_count += 1
        return {
            "success": True,
            "success_count": success_count,
            "fail_count": fail_count,
            "message": f"成功删除 {success_count} 个文档，失败 {fail_count} 个"
        }


# 创建服务实例
knowledge_service = KnowledgeService()
