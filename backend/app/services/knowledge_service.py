"""
知识库服务 - 管理知识库的构建、检索和问答
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, func, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.elasticsearch import get_elasticsearch
from app.exceptions import AppError
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentTag
from app.models.knowledge_job import KnowledgeProcessingJob
from app.rag.errors import RetrievalUnavailableError
from app.services.file_service import FileUploadService

if TYPE_CHECKING:
    from app.worker.doc_processor import DeferredOutcome

logger = logging.getLogger(__name__)


def _coerce_organization_id(organization_id: str | int | None) -> int:
    """Coerce an explicit organization scope for knowledge retrieval.

    W0-6：此前 ``organization_id is None`` 被静默转成 0（``org_id = ...
    if organization_id else 0``），无组织调用会落进 0 号组织的共享检索/缓存
    命名空间。现在组织上下文必填，None / 空串 / 0 / 负数一律显式拒绝。
    """
    if organization_id is None:
        raise ValueError("search_knowledge 缺少 organization_id，拒绝在无组织上下文下检索")
    if isinstance(organization_id, bool):
        raise ValueError(f"search_knowledge 的 organization_id 非法: {organization_id!r}")
    try:
        org_id = int(organization_id)
    except (TypeError, ValueError):
        raise ValueError(f"search_knowledge 的 organization_id 非法: {organization_id!r}") from None
    if org_id <= 0:
        raise ValueError(
            f"search_knowledge 的 organization_id={organization_id!r} 非法："
            "必须是正整数组织 ID（0/-1 是历史 fail-open 哨兵，已禁用）"
        )
    return org_id


# 引用 documents.id 的子表清单 —— **这个清单是从 information_schema 查出来的，不是凭记忆**：
#
#   SELECT kcu.TABLE_NAME, kcu.COLUMN_NAME, rc.DELETE_RULE
#   FROM information_schema.KEY_COLUMN_USAGE kcu
#   JOIN information_schema.REFERENTIAL_CONSTRAINTS rc
#     ON rc.CONSTRAINT_SCHEMA = kcu.CONSTRAINT_SCHEMA
#    AND rc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
#   WHERE kcu.REFERENCED_TABLE_SCHEMA = DATABASE()
#     AND kcu.REFERENCED_TABLE_NAME = 'documents';
#
#   → document_chunks / document_tags / knowledge_processing_jobs（三张表的
#     document_id 外键均为 NO ACTION，没有 ON DELETE CASCADE，必须应用层显式清）。
#
# issue #174：原实现只清了 document_chunks 一张（凭记忆列的清单漏了两张），
# 每次上传必然写入一条 knowledge_processing_jobs 行 → 删除必然被 1451 外键错误挡住 → 500。
# 回归网：tests/integration/test_delete_document_children.py 会在真实 MySQL 上重查
# information_schema，断言本清单与库中引用 documents 的表逐表一致 —— 新增子表
# （模型 + 迁移）而忘了加进本清单时，该测试变红。
_DOCUMENT_CHILD_MODELS: tuple[type, ...] = (
    DocumentChunk,
    DocumentTag,
    KnowledgeProcessingJob,
)


class KnowledgeService:
    """知识库服务 - 企业级RAG核心实现"""

    def __init__(self):
        self.index_name = settings.ELASTICSEARCH_INDEX_NAME
        self.file_upload_service = FileUploadService()

    async def build_knowledge_base(self, document_id: str) -> "bool | DeferredOutcome":
        """Build knowledge base through the unified document processor.

        返回 ``processor.process`` 的契约值：True=已了结、False=失败、
        DEFERRED=锁竞争未了结（A3-P0 第三种终局）。
        """
        from app.worker.doc_processor import processor

        return await processor.process(document_id)

    async def search_knowledge(
        self, query: str, organization_id: str | int, top_k: int = 5, search_mode: str | None = None
    ) -> list[dict[str, Any]]:
        """
        搜索知识库 - 统一使用 RagService 以获得更强能力

        search_mode（hybrid/vector/keyword）：显式覆盖本次检索臂组合；None = 自动
        路由（issue #225 决策 7A：此前该参数收下即丢弃，三层链路都不生效）。

        W0-6：organization_id 必填，且必须是正整数组织 ID（0/None 不再兜底）。
        """
        from app.services.rag_service import rag_service

        # 组织校验必须先于 try（下方 broad except 会把业务异常吞成 []，让
        # 「没有组织」伪装成「没有检索结果」）。
        org_id = _coerce_organization_id(organization_id)

        try:
            # 使用 RagService 进行高级检索。
            # pipeline 返回 (results, debug_info) 元组，必须解包——issue #219：
            # 这里曾把元组当列表迭代，`isinstance(res, dict)` 把「hits 列表」和
            # `None` 两个元素全部跳过，本方法恒返回 []，REST 搜索端点永远搜不到东西。
            results, _ = await rag_service.search_knowledge_base(
                query=query,
                organization_id=org_id,
                top_k=top_k,
                search_mode=search_mode,
            )

            # 安全修复：移除检索结果全文日志（原 DEBUG 日志把文档内容写入日志文件，
            # 且 warning 级别刷屏）；如需调试请显式开启 DEBUG 级别。
            logger.debug(
                f"search_knowledge: got {len(results) if isinstance(results, list) else 'n/a'} results"
            )

            # 格式化为 KnowledgeService 期望的输出格式以保持兼容性
            formatted_results = []
            for res in results:
                if not isinstance(res, dict):
                    logger.warning(
                        f"search_knowledge: skipping non-dict result: {type(res).__name__}"
                    )
                    continue
                formatted_results.append(
                    {
                        "id": res.get("document_id"),
                        "text": res.get("text"),
                        "score": res.get("score"),
                        # issue #185：相对排序分（score）与重排绝对分分开透传；
                        # 无重排分时保持 None，绝不拿 score 回填。
                        "relevance_score": res.get("relevance_score"),
                        # issue #197：PDF 页码必须在本层重建结果时继续搬运——
                        # 端点读的是本层输出，漏搬会让 /search 恒返回 null。
                        "page_number": res.get("page_number"),
                        "metadata": {
                            "filename": res.get("filename"),
                            "snippet": res.get("snippet"),
                            "overlap_score": res.get("overlap_score"),
                            "mmr_score": res.get("mmr_score"),
                        },
                        "source": {
                            "filename": res.get("filename"),
                            "document_id": res.get("document_id"),
                        },
                    }
                )
            return formatted_results
        except RetrievalUnavailableError:
            # issue #292：检索后端服务故障/超时不得回落成「没有相关内容」（假空数组）。
            # 原样上抛 —— /search 端点映射为 502（ExternalServiceError），
            # suggestions 等 best-effort 调用方自行降级为空并留痕；绝不在这里吞成 []。
            logger.error("Knowledge search unavailable: retrieval backend failure", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Knowledge search failed: {e}")
            return []

    async def get_search_suggestions(
        self, query: str, organization_id: str, limit: int = 5
    ) -> list[str]:
        """获取搜索建议（best-effort：自动补全不应把检索故障扩大成 5xx）。"""
        try:
            results = await self.search_knowledge(
                query, organization_id, top_k=limit, search_mode="keyword"
            )
        except RetrievalUnavailableError as e:
            # issue #292：降级为空数组（与「没有建议」同形是此类补全接口可接受的
            # 语义边界），但必须留痕 —— 不静默。
            logger.warning(
                f"Search suggestions degraded (retrieval unavailable, kind={e.kind}): {e}"
            )
            return []
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
                select(func.count(Document.id)).where(
                    and_(
                        Document.organization_id == organization_id,
                        Document.status == DocumentStatus.INDEXED,
                    )
                )
            )
            indexed_count = indexed_count_result.scalar() or 0

            return {
                "total_documents": doc_count,
                "indexed_documents": indexed_count,
                "total_size": 0,  # 这里可以进一步计算文件大小总和
                "last_updated": datetime.now().isoformat(),
            }

    async def delete_knowledge(self, document_id: str, organization_id: int | None = None) -> bool:
        """兼容旧接口的删除方法"""
        return await self.delete_knowledge_thoroughly(document_id, organization_id)

    async def delete_es_by_document_id(self, document_id: str) -> bool:
        """仅从 ES 中删除该文档的所有 chunk（用于重建前清理）"""
        try:
            client = await get_elasticsearch()
            await client.delete_by_query(
                index=self.index_name, query={"term": {"document_id": document_id}}
            )
            logger.info(f"已从 ES 删除 document_id={document_id} 的索引")
            return True
        except Exception as e:
            logger.error(f"从 ES 按 document_id 删除失败: {str(e)}")
            return False

    async def delete_knowledge_thoroughly(
        self, document_id: str, organization_id: int | None = None
    ) -> bool:
        """彻底删除文档（含所有引用 documents.id 的子表行，清单见 _DOCUMENT_CHILD_MODELS）。

        成功返回 True；文档本就不存在也返回 True（幂等删除语义，与旧行为一致）。
        失败抛 `AppError` 并携带原始原因 —— 不再 `return False` 把失败原因吞掉
        （issue #174：调用方只能报「删除失败」，真正的原因只留在服务端日志里）。
        """
        try:
            logger.info(f"🧹 正在彻底清理文档: {document_id}")
            async with AsyncSessionLocal() as session:
                conditions = [Document.id == document_id]
                if organization_id is not None:
                    conditions.append(Document.organization_id == organization_id)
                result = await session.execute(select(Document).where(*conditions))
                doc = result.scalar_one_or_none()
                if not doc:
                    # 幂等：目标状态（无此文档）已达成。直接删除不存在的文档是成功而非失败
                    # —— 直接调用方（批量删除按原始 id 列表）不应把它计成错误。
                    return True

                if doc.file_path:
                    # md5 去重上传会跨文档共享同一 MinIO 对象（对象名
                    # `{org}/{md5}.{ext}`，见 documents.upload）——删除前检查是否
                    # 还有其他文档引用同一 file_path；有引用则只删 DB 记录，
                    # 防止删一个文档破坏其他文档的下载/重建。与
                    # file_service.delete_document 的既有保护对齐（#416：/files
                    # 删除并入本统一链路后不得丢失该保护）。
                    refs_result = await session.execute(
                        select(func.count(Document.id)).where(
                            Document.file_path == doc.file_path,
                            Document.id != document_id,
                        )
                    )
                    other_refs = refs_result.scalar() or 0
                    if other_refs == 0:
                        try:
                            await self.file_upload_service.delete_file(doc.file_path)
                        except Exception as e:
                            logger.warning(f"从MinIO删除文件失败: {str(e)}")
                    else:
                        logger.info(
                            f"文档 {document_id} 的存储对象仍被 {other_refs} 个其他文档"
                            "引用，跳过物理删除"
                        )

                # ES 清理是 best-effort：客户端获取失败（如 ES 未连接/不可用）不得挡住删除。
                # 客户端获取原先在 try 之外，ES 不可用时整个删除在碰 DB 之前就抛 RuntimeError
                # （issue #174 顺带修复）。
                try:
                    client = await get_elasticsearch()
                    await client.delete_by_query(
                        index=self.index_name, query={"term": {"document_id": document_id}}
                    )
                except Exception as e:
                    logger.warning(f"从ES删除索引失败: {str(e)}")

                # 先清所有引用 documents.id 的子表，再删主表 —— 外键均无 ON DELETE CASCADE，
                # 漏任何一张都会被 MySQL 1451 挡住（issue #174）。
                for model in _DOCUMENT_CHILD_MODELS:
                    await session.execute(delete(model).where(model.document_id == document_id))
                await session.execute(delete(Document).where(Document.id == document_id))
                await session.commit()

                # 删除文档后按组织清空 RAG 缓存，避免旧缓存仍被命中
                from app.rag.cache import RetrievalCache, SemanticCache

                await RetrievalCache().clear_for_org(doc.organization_id)
                await SemanticCache().clear_for_org(doc.organization_id)
                return True
        except AppError:
            raise
        except Exception as e:
            # 不再 return False 吞掉原因：把原始错误类型与消息带给调用方，
            # 让 500 响应体能定位到问题（issue #174）。
            logger.error(f"彻底删除文档失败: {type(e).__name__}: {e}")
            raise AppError(f"删除文档失败: {type(e).__name__}: {e}") from e

    async def batch_delete_knowledge(
        self, document_ids: list[str], organization_id: int | None = None
    ) -> dict[str, Any]:
        """批量删除文档 —— 单个失败不中断整批，原因逐条收集（issue #174）。

        此前 `delete_knowledge_thoroughly` 返回 False 时只把该条计为「失败」，
        失败原因无处可见；现在服务层抛带原因的 AppError，这里按条捕获并放进
        `errors`，其余文档继续删。
        """
        success_count = 0
        errors: list[dict[str, str]] = []
        for doc_id in document_ids:
            try:
                await self.delete_knowledge_thoroughly(doc_id, organization_id)
                success_count += 1
            except AppError as e:
                errors.append({"document_id": doc_id, "reason": e.message})
        fail_count = len(errors)
        result: dict[str, Any] = {
            "success": True,
            "success_count": success_count,
            "fail_count": fail_count,
            "message": f"成功删除 {success_count} 个文档，失败 {fail_count} 个",
        }
        if errors:
            result["errors"] = errors
        return result


# 创建服务实例
knowledge_service = KnowledgeService()
