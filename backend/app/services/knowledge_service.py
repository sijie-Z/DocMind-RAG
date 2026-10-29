"""
知识库服务 - 管理知识库的构建、检索和问答
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, func, or_, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.elasticsearch import get_elasticsearch
from app.core.log_safety import log_safe
from app.exceptions import AppError, ConflictError, ServiceUnavailableError
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentTag
from app.models.knowledge_job import KnowledgeProcessingJob
from app.models.review import ReviewCase, ReviewDocumentMeta
from app.rag.errors import RetrievalUnavailableError
from app.rag.source_provenance import SourceProvenanceUnavailableError
from app.services.document_status import (
    require_count_response,
    require_delete_by_query_response,
)
from app.services.file_service import FileUploadService

if TYPE_CHECKING:
    from app.worker.doc_processor import DeferredOutcome

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeleteOutcome:
    """彻底删除的结果。

    ``success`` 为 True 表示文档已从数据库与索引中删除（或本就不存在）。
    ``file_cleanup_pending`` 为 True 表示物理存储对象删除失败、需要重试 ——
    响应与审计必须如实透出，不得把「DB/ES 已删、文件残留」说成完全清理成功。
    对象本身仍按 truthiness 兼容旧调用方（成功即真值）。
    """

    success: bool
    file_cleanup_pending: bool = False

    def __bool__(self) -> bool:
        return self.success


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
    ReviewDocumentMeta,
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
                        # 展示用正文：parser_original 时是 O 派生 snippet；legacy 时
                        # 是检索文本 snippet。调用方必须结合 text_provenance 展示，
                        # 不得把 legacy 文本当原文证据（PR4 消费者）。
                        "content": res.get("snippet"),
                        "text_provenance": res.get("text_provenance"),
                        "score": res.get("score"),
                        # issue #185：相对排序分（score）与重排绝对分分开透传；
                        # 无重排分时保持 None，绝不拿 score 回填。
                        "relevance_score": res.get("relevance_score"),
                        # issue #197：PDF 页码必须在本层重建结果时继续搬运——
                        # 端点读的是本层输出，漏搬会让 /search 恒返回 null。
                        "page_number": res.get("page_number"),
                        # 同族缺陷：检索链路已计算的命中标记与改写计数也须搬运，
                        # 否则端点 `.get(..., False)` 恒读默认值（has_keyword 恒 false）。
                        "has_keyword": res.get("has_keyword"),
                        "has_vector": res.get("has_vector"),
                        "rewrite_hits": res.get("rewrite_hits"),
                        "fresh_factor": res.get("fresh_factor"),
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
        except SourceProvenanceUnavailableError:
            # strict 模式下全部命中都缺 parser 原文：这是「需重建」的确定性拒绝，
            # 不是「没搜到」。原样上抛，由 /search 映射为 409 结构化错误。
            logger.warning("Knowledge search refused: source original unavailable")
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
        except SourceProvenanceUnavailableError as e:
            # strict 下原文不可用：补全同样按 best-effort 降级，但留痕；
            # 主搜索端点仍必须 409，不得把拒答伪装成空结果。
            logger.warning(f"Search suggestions degraded (source original unavailable): {e}")
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

    async def delete_knowledge(
        self, document_id: str, organization_id: int | None = None
    ) -> DeleteOutcome:
        """兼容旧接口的删除方法"""
        return await self.delete_knowledge_thoroughly(document_id, organization_id)

    async def delete_es_by_document_id(self, document_id: str) -> bool:
        """仅从 ES 中删除该文档的所有 chunk；未证明删除完成时返回 False。

        契约（P1-1，与 worker 替换链共用 ``document_status`` 的严格解析）：
        - ``delete_by_query`` 响应必须严格成功：falsy/None、``failures``、
          ``_shards.failures``、``_shards.failed>0``、``timed_out``、非法/缺失
          关键元数据（``deleted``/``failures``/``timed_out``）任一出现都不得
          当作成功（空响应 != 删除成功）。``_shards`` 在 ES 的 delete_by_query
          响应中不保证出现，缺失时由随后的 refresh+count 回读兜底；
        - 删除后显式 refresh 并回读 ``count(document_id)==0``；回读失败或仍有
          chunk 时同样返回 False —— 只有「可见删除」才允许放行 DB 删除。

        返回 False 时调用方必须保留 DB 行、不得删除物理文件、不得报成功/写成功审计。
        """
        try:
            client = await get_elasticsearch()
            response = await client.delete_by_query(
                index=self.index_name,
                query={"term": {"document_id": document_id}},
                refresh=True,
            )
            deleted = require_delete_by_query_response(
                response, context=f"ES delete for document {document_id}"
            )

            # 即使响应看似成功，也必须显式 refresh 后回读确认删除可见；否则
            # 「timed_out 后台继续删」或部分 shard 删除都可能留下旧 chunk。
            await client.indices.refresh(index=self.index_name)
            count_response = await client.count(
                index=self.index_name,
                query={"term": {"document_id": document_id}},
            )
            remaining = require_count_response(
                count_response,
                context=f"ES delete verification for document {document_id}",
            )
            if remaining:
                logger.error(
                    f"从 ES 删除 document_id={log_safe(document_id)} 后回读仍有 {remaining} 条 chunk，"
                    "拒绝放行数据库删除"
                )
                return False
            logger.info(
                f"已从 ES 删除 document_id={log_safe(document_id)} 的索引"
                f"（响应 deleted={deleted}，回读 count=0）"
            )
            return True
        except Exception as e:
            logger.error(f"从 ES 按 document_id 删除失败: {str(e)}")
            return False

    async def delete_knowledge_thoroughly(
        self, document_id: str, organization_id: int | None = None
    ) -> DeleteOutcome:
        """彻底删除文档（含所有引用 documents.id 的子表行，清单见 _DOCUMENT_CHILD_MODELS）。

        成功返回 ``DeleteOutcome``（真值）；文档本就不存在也返回成功（幂等删除语义，
        与旧行为一致）。失败抛 `AppError` 并携带原始原因 —— 不再 `return False`
        把失败原因吞掉（issue #174：调用方只能报「删除失败」，真正的原因只留在
        服务端日志里）。

        不可逆顺序（P1-1 / P2-3）：
        1. 先确认 ES 已可见删除（严格响应 + refresh 回读 count==0），否则立即中止：
           DB 行保留、物理文件不动、不写成功审计；
        2. DB 子表与主表删除并提交；
        3. 提交后才 best-effort 删除 MinIO 对象；失败时不回滚删除（文档已不可检索），
           但以 ``file_cleanup_pending=True`` 如实透出，调用方不得声称全部清理完成。

        已知剩余边界：ES 删除确认后若 DB commit 失败，文档行会随事务回滚保留，
        而索引已删 —— 该文档暂时不可检索但可下载；重试删除会再次执行
        （ES 对已清零的文档是幂等成功）并收敛。本流程**不是**跨 DB/ES/MinIO 的
        分布式原子操作；文件清理失败只能以 ``file_cleanup_pending`` 显式暴露，
        调用方不得据此宣称三者一致。
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
                    # 跨 org 的 id 在这里同样落空：无副作用，不会泄漏或误删他人文档。
                    return DeleteOutcome(success=True)

                # Batch 5 生命周期保护：被核验案件引用的文档禁止物理删除 ——
                # 删除会让案件只能看到 stale 快照，且没有归档/恢复出口。
                # 该检查在任何副作用（ES/DB/对象存储）之前执行，fail closed。
                referenced_cases = (
                    (
                        await session.execute(
                            select(ReviewCase.id).where(
                                or_(
                                    ReviewCase.policy_document_id == document_id,
                                    ReviewCase.base_document_id == document_id,
                                    ReviewCase.revised_document_id == document_id,
                                )
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if referenced_cases:
                    raise ConflictError(
                        "文档被核验案件引用，不能删除；请先处理相关案件",
                        error_code="REVIEW_DOCUMENT_IN_USE",
                        detail={
                            "document_id": document_id,
                            "case_ids": sorted(referenced_cases)[:50],
                        },
                    )

                # 先判定物理对象是否该删（共享引用保护），但**推迟到 DB commit 之后**
                # 才真正删除 —— fail-closed 的中止点之前不得有不可逆副作用（P2-3）。
                file_path = doc.file_path
                should_delete_file = False
                if file_path:
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
                        should_delete_file = True
                    else:
                        logger.info(
                            f"文档 {document_id} 的存储对象仍被 {other_refs} 个其他文档"
                            "引用，跳过物理删除"
                        )

                # ES 未明确成功时不得删除 DB 记录，也不得触碰物理对象；调用方可在
                # 故障恢复后重试。helper 会把 ES 异常、异常响应形态与回读非零
                # 统一转成 False。
                if not await self.delete_es_by_document_id(document_id):
                    raise ServiceUnavailableError("Elasticsearch 索引删除失败，文档未删除，请重试")

                # 先清所有引用 documents.id 的子表，再删主表 —— 外键均无 ON DELETE CASCADE，
                # 漏任何一张都会被 MySQL 1451 挡住（issue #174）。
                for model in _DOCUMENT_CHILD_MODELS:
                    await session.execute(delete(model).where(model.document_id == document_id))
                await session.execute(delete(Document).where(Document.id == document_id))
                await session.commit()

                # DB 已提交、文档已不可见 —— 此时才 best-effort 删除物理对象。
                # 失败不回滚（否则会重新暴露已删索引的半状态），但必须如实标记
                # cleanup_pending，让响应/审计可重试、不谎报完全清理。
                file_cleanup_pending = False
                if should_delete_file and file_path:
                    try:
                        removed = await self.file_upload_service.delete_file(file_path)
                    except Exception as e:
                        removed = False
                        logger.warning(f"删除存储对象时抛出异常: {type(e).__name__}: {e}")
                    if not removed:
                        file_cleanup_pending = True
                        logger.error(
                            "[DELETE] 文档已从数据库/索引删除，但存储对象清理未完成 "
                            "file_cleanup_pending document_id=%s file_path=%s",
                            log_safe(document_id),
                            log_safe(file_path),
                        )

                # 删除文档后按组织清空 RAG 缓存，避免旧缓存仍被命中
                from app.rag.cache import RetrievalCache, SemanticCache

                await RetrievalCache().clear_for_org(doc.organization_id)
                await SemanticCache().clear_for_org(doc.organization_id)
                return DeleteOutcome(success=True, file_cleanup_pending=file_cleanup_pending)
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

        单条成功但物理对象清理失败时，该文档仍算删除成功，但 id 会进入
        `file_cleanup_pending_ids`，`message` 也如实说明 —— 不把「文件残留」
        伪装成完全清理完成。
        """
        success_count = 0
        errors: list[dict[str, str]] = []
        cleanup_pending_ids: list[str] = []
        for doc_id in document_ids:
            try:
                outcome = await self.delete_knowledge_thoroughly(doc_id, organization_id)
                success_count += 1
                if getattr(outcome, "file_cleanup_pending", False):
                    cleanup_pending_ids.append(doc_id)
            except AppError as e:
                errors.append({"document_id": doc_id, "reason": e.message})
        fail_count = len(errors)
        message = f"成功删除 {success_count} 个文档，失败 {fail_count} 个"
        if cleanup_pending_ids:
            message += f"，其中 {len(cleanup_pending_ids)} 个文档的文件清理待重试"
        result: dict[str, Any] = {
            "success": True,
            "success_count": success_count,
            "fail_count": fail_count,
            "message": message,
        }
        if errors:
            result["errors"] = errors
        if cleanup_pending_ids:
            result["file_cleanup_pending_ids"] = cleanup_pending_ids
        return result


# 创建服务实例
knowledge_service = KnowledgeService()
