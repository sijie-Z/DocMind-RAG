"""
知识库服务 - 管理知识库的构建、检索和问答
"""
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.elasticsearch import get_elasticsearch
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services.file_service import FileUploadService

logger = logging.getLogger(__name__)


class KnowledgeService:
    """知识库服务 - 企业级RAG核心实现"""

    def __init__(self):
        self.index_name = settings.ELASTICSEARCH_INDEX_NAME
        self.file_upload_service = FileUploadService()

    async def build_knowledge_base(self, document_id: str) -> bool:
        """Build knowledge base through the unified document processor."""
        from app.worker.doc_processor import processor

        return await processor.process(document_id)

    async def search_knowledge(
        self,
        query: str,
        organization_id: str | None = None,
        top_k: int = 5,
        search_mode: str = "hybrid"
    ) -> list[dict[str, Any]]:
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
                top_k=top_k,
            )

            # 安全修复：移除检索结果全文日志（原 DEBUG 日志把文档内容写入日志文件，
            # 且 warning 级别刷屏）；如需调试请显式开启 DEBUG 级别。
            logger.debug(f"search_knowledge: got {len(results) if isinstance(results, list) else 'n/a'} results")

            # 格式化为 KnowledgeService 期望的输出格式以保持兼容性
            formatted_results = []
            for res in results:
                if not isinstance(res, dict):
                    logger.warning(f"search_knowledge: skipping non-dict result: {type(res).__name__}")
                    continue
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

    async def get_search_suggestions(self, query: str, organization_id: str, limit: int = 5) -> list[str]:
        """获取搜索建议"""
        # 简单实现：使用关键词搜索的前几个标题
        results = await self.search_knowledge(query, organization_id, top_k=limit, search_mode="keyword")
        return [r["text"][:50] + "..." for r in results]

    async def get_knowledge_stats(self, organization_id: str) -> dict[str, Any]:
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

    async def delete_knowledge(self, document_id: str, organization_id: int | None = None) -> bool:
        """兼容旧接口的删除方法"""
        return await self.delete_knowledge_thoroughly(document_id, organization_id)

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

    async def delete_knowledge_thoroughly(
        self, document_id: str, organization_id: int | None = None
    ) -> bool:
        """彻底删除文档"""
        try:
            logger.info(f"🧹 正在彻底清理文档: {document_id}")
            async with AsyncSessionLocal() as session:
                conditions = [Document.id == document_id]
                if organization_id is not None:
                    conditions.append(Document.organization_id == organization_id)
                result = await session.execute(select(Document).where(*conditions))
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

                # 删除文档后按组织清空 RAG 缓存，避免旧缓存仍被命中
                from app.rag.cache import RetrievalCache, SemanticCache
                await RetrievalCache().clear_for_org(doc.organization_id)
                await SemanticCache().clear_for_org(doc.organization_id)
                return True
        except Exception as e:
            logger.error(f"彻底删除文档失败: {str(e)}")
            return False

    async def batch_delete_knowledge(self, document_ids: list[str], organization_id: int | None = None) -> dict[str, Any]:
        """批量删除文档"""
        success_count = 0
        fail_count = 0
        for doc_id in document_ids:
            if await self.delete_knowledge_thoroughly(doc_id, organization_id):
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
