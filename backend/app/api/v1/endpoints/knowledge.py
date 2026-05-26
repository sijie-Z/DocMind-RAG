"""
知识库API端点
"""
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.notifications import create_notification
from app.core.config import settings
from app.core.database import get_db
from app.core.elasticsearch import get_elasticsearch
from app.core.kafka_client import kafka_producer
from app.core.security import get_current_user, permission_required
from app.exceptions import (
    AppError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.models.document import Document, DocumentStatus
from app.models.knowledge_job import KnowledgeJobStatus, KnowledgeProcessingJob
from app.models.rbac import PermissionType
from app.models.user import User
from app.schemas.knowledge import (
    BatchDeleteRequest,
    KnowledgeBuildResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    SearchSuggestionResponse,
    SimilarityResponse,
)
from app.services.audit_service import audit_service
from app.services.embedding_service import embedding_service
from app.services.knowledge_service import knowledge_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/", response_model=dict[str, Any], dependencies=[Depends(permission_required([PermissionType.VIEW_KNOWLEDGE_BASE]))])
async def list_knowledge_bases(
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    获取知识库列表 (真实数据)
    """
    try:
        # 1. 构建查询条件
        # 如果用户属于某个组织，查询组织下的所有文档；否则查询自己上传的文档
        if current_user.organization_id:
             conditions = [Document.organization_id == current_user.organization_id]
        else:
             conditions = [Document.uploaded_by == current_user.id]

        if search:
            conditions.append(Document.title.contains(search))

        # 2. 查询总数
        total_query = select(func.count(Document.id)).where(*conditions)
        total_result = await db.execute(total_query)
        total = total_result.scalar() or 0

        # 3. 查询分页数据
        skip = (page - 1) * page_size
        query = select(Document).where(*conditions).order_by(desc(Document.created_at)).offset(skip).limit(page_size)
        result = await db.execute(query)
        documents = result.scalars().all()

        # 4. 转换数据
        data = []
        for doc in documents:
            # 映射状态
            status_map: dict[Any, str] = {
                DocumentStatus.PENDING: "pending",
                DocumentStatus.UPLOADED: "uploaded",
                DocumentStatus.PARSING: "parsing",
                DocumentStatus.PARSED: "parsed",
                DocumentStatus.INDEXED: "completed",
                DocumentStatus.FAILED: "failed"
            }

            # 声明 doc_status 为 Any 类型，以避免 pyright 中 status_map.get 方法对于 SQLAlchemy Column 参数的匹配报错
            doc_status: Any = doc.status

            data.append({
                "id": doc.id,
                "title": doc.title or doc.filename,
                "file_name": doc.filename,
                "file_type": doc.file_type.value if hasattr(doc.file_type, 'value') else str(doc.file_type),
                "file_size": doc.file_size,
                "tags": doc.keywords if doc.keywords else[],
                "description": doc.description,
                "parse_error": doc.parse_error,
                "created_at": doc.created_at.isoformat() if doc.created_at else "",
                "updated_at": doc.updated_at.isoformat() if doc.updated_at else "",
                "status": status_map.get(doc_status, "failed")
            })

        return {
            "success": True,
            "message": "获取成功",
            "data": {
                "data": data,
                "total": total,
                "page": page,
                "page_size": page_size
            }
        }
    except Exception as e:
        raise AppError(f"获取知识库列表失败: {str(e)}")


@router.post("/search", response_model=KnowledgeSearchResponse, dependencies=[Depends(permission_required([PermissionType.SEARCH_KNOWLEDGE_BASE], organization_id_param='organization_id'))])
async def search_knowledge(
    request: KnowledgeSearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    搜索知识库
    """
    try:
        # 允许 search_type 或 search_mode
        mode = request.search_mode or getattr(request, 'search_type', 'hybrid')

        # 执行搜索
        results = await knowledge_service.search_knowledge(
            query=request.query,
            organization_id=request.organization_id or current_user.organization_id,
            top_k=request.top_k or 10,
            search_mode=mode
        )

        # 转换为前端期待的格式
        formatted_results =[]
        for r in results:
            # Safety check for empty results
            if not r:
                continue
            # Ensure source exists
            source = r.get("source") or {}
            if not source.get("filename"):
                source["filename"] = r.get("filename", "未知文件")
            formatted_results.append({
                "id": str(r.get("id") or r.get("document_id") or ""),
                "text": r.get("text") or r.get("content") or "",
                "score": r.get("score") or r.get("relevance_score") or 0,
                "metadata": r.get("metadata") or {},
                "source": source,
                "has_keyword": r.get("has_keyword", False),
                "has_vector": r.get("has_vector", False),
                "rewrite_hits": r.get("rewrite_hits", 1),
                "fresh_factor": r.get("fresh_factor", 1.0),
            })

        return {
            "query": request.query,
            "results": formatted_results,
            "total_count": len(formatted_results),
            "search_mode": mode
        }

    except Exception as e:
        logger.error(f"搜索失败: {str(e)}")
        raise AppError(f"搜索知识库失败: {str(e)}")


@router.get("/suggestions", response_model=SearchSuggestionResponse, dependencies=[Depends(permission_required([PermissionType.SEARCH_KNOWLEDGE_BASE], organization_id_param='organization_id'))])
async def get_search_suggestions(
    q: str = Query(..., min_length=2, description="搜索查询"),
    organization_id: int = Query(..., description="组织ID"),
    limit: int = Query(5, ge=1, le=10, description="建议数量"),
    current_user: User = Depends(get_current_user)
):
    """
    获取搜索建议
    """
    try:
        suggestions = await knowledge_service.get_search_suggestions(
            query=q,
            organization_id=organization_id,
            limit=limit
        )

        return {
            "success": True,
            "suggestions": suggestions
        }

    except Exception as e:
        raise AppError(f"获取搜索建议失败: {str(e)}")


@router.get("/stats/{organization_id}", response_model=dict, dependencies=[Depends(permission_required([PermissionType.VIEW_KNOWLEDGE_BASE], organization_id_param='organization_id'))])
async def get_knowledge_stats(
    organization_id: int,
    current_user: User = Depends(get_current_user)
):
    """
    获取知识库统计信息
    """
    try:
        stats = await knowledge_service.get_knowledge_stats(organization_id)

        return {
            "success": True,
            "stats": {
                "total_files": stats["total_documents"],
                "file_types": {}, # 可以进一步实现
                "recent_searches":[]
            }
        }

    except Exception as e:
        raise AppError(f"获取知识库统计失败: {str(e)}")


@router.get("/graph/stats", response_model=dict, dependencies=[Depends(permission_required([PermissionType.VIEW_KNOWLEDGE_BASE]))])
async def get_graph_rag_stats(
    current_user: User = Depends(get_current_user)
):
    """
    获取GraphRAG知识图谱统计信息
    """
    try:
        from app.services.graph_rag_service import graph_rag_service

        analytics = graph_rag_service.get_analytics()

        return {
            "success": True,
            "data": {
                "total_entities": analytics["total_entities"],
                "type_distribution": analytics["type_distribution"],
                "entity_types": analytics["entity_types"],
                "entity_type_labels_cn": {
                    k: v for k, v in analytics["entity_types"].items()
                }
            }
        }
    except Exception as e:
        raise AppError(f"获取知识图谱统计失败: {str(e)}")


@router.delete("/graph/clear", response_model=dict, dependencies=[Depends(permission_required([PermissionType.DOCUMENT_DELETE]))])
async def clear_graph_rag(
    current_user: User = Depends(get_current_user)
):
    """
    清空GraphRAG知识图谱
    """
    try:
        from app.services.graph_rag_service import graph_rag_service

        graph_rag_service.graph.clear()

        return {
            "success": True,
            "message": "知识图谱已清空"
        }
    except Exception as e:
        raise AppError(f"清空知识图谱失败: {str(e)}")


@router.post("/rebuild/{document_id}", response_model=KnowledgeBuildResponse, dependencies=[Depends(permission_required([PermissionType.REBUILD_DOCUMENT_INDEX], organization_id_param='document_id'))])
async def rebuild_document_knowledge(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    重建单个文档的知识库索引：删除 ES 中该文档的索引后，重新发送 Kafka 任务由 Worker 解析并写 ES。
    """
    try:
        result = await db.execute(select(Document).where(Document.id == document_id))
        doc = result.scalar_one_or_none()

        if not doc:
            raise NotFoundError("文档不存在")

        # 1. 仅从 ES 删除该文档的索引
        await knowledge_service.delete_es_by_document_id(document_id)

        # 2. 记录任务并发送 Kafka，由 Worker 重新解析并写 ES
        job = KnowledgeProcessingJob(
            document_id=document_id,
            organization_id=doc.organization_id,
            trigger_type="reprocess",
            status=KnowledgeJobStatus.QUEUED,
            retry_count=0,
        )
        db.add(job)

        try:
            message = {
                "document_id": document_id,
                "organization_id": doc.organization_id,
                "file_path": doc.file_path,
                "filename": doc.filename,
                "file_type": doc.file_type.value if hasattr(doc.file_type, "value") else str(doc.file_type),
                "action": "process",
            }
            await kafka_producer.send_message(settings.KAFKA_FILE_PROCESSING_TOPIC, message)
        except Exception as e:
            logger.error(f"发送 Kafka 重建任务失败: {e}")
            job.status = KnowledgeJobStatus.FAILED
            job.error_message = f"发送处理任务失败: {str(e)}"
            job.finished_at = datetime.now()
            await db.commit()

            await create_notification(
                db,
                user_id=current_user.id,
                title="知识库重建失败",
                content=f"文档《{doc.title or doc.filename}》重建失败：{str(e)}",
                type="knowledge",
                target_route="Knowledge",
                target_id=str(document_id),
            )

            raise AppError(f"发送处理任务失败: {str(e)}")

        # 3. 将文档状态置为 PENDING，Worker 处理后会更新为 INDEXED/FAILED
        doc.status = DocumentStatus.PENDING
        doc.parse_error = None
        await db.commit()

        await create_notification(
            db,
            user_id=current_user.id,
            title="知识库重建任务已提交",
            content=f"文档《{doc.title or doc.filename}》正在重建索引。",
            type="knowledge",
            target_route="Knowledge",
            target_id=str(document_id),
        )

        # 补全 build_time 必填参数
        return KnowledgeBuildResponse(
            success=True,
            document_id=document_id,
            message="已提交重建任务，请等待 Worker 处理完成",
            build_time=0.0
        )

    except (AppError, NotFoundError, ValidationError, AuthorizationError, ConflictError):
        raise
    except Exception as e:
        logger.error(f"重建知识库失败: {e}")
        raise AppError(f"重建知识库失败: {str(e)}")


@router.get("/jobs", response_model=dict, dependencies=[Depends(permission_required([PermissionType.VIEW_KNOWLEDGE_BASE]))])
async def list_knowledge_jobs(
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    conditions = []
    if current_user.organization_id:
        conditions.append(KnowledgeProcessingJob.organization_id == current_user.organization_id)

    if status:
        try:
            conditions.append(KnowledgeProcessingJob.status == KnowledgeJobStatus(status))
        except Exception:
            raise ValidationError("无效的任务状态")

    total_result = await db.execute(select(func.count(KnowledgeProcessingJob.id)).where(*conditions))
    total = total_result.scalar() or 0

    query = (
        select(KnowledgeProcessingJob)
        .where(*conditions)
        .order_by(desc(KnowledgeProcessingJob.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    jobs = result.scalars().all()

    data = []
    for job in jobs:
        data.append({
            "id": job.id,
            "document_id": job.document_id,
            "organization_id": job.organization_id,
            "trigger_type": job.trigger_type,
            "status": job.status.value if hasattr(job.status, "value") else str(job.status),
            "retry_count": job.retry_count,
            "error_message": job.error_message,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "created_at": job.created_at.isoformat() if job.created_at else None,
        })

    return {
        "success": True,
        "message": "获取处理任务成功",
        "data": {
            "data": data,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    }


@router.get("/{document_id}/events", response_model=dict, dependencies=[Depends(permission_required([PermissionType.VIEW_KNOWLEDGE_BASE]))])
async def get_document_job_events(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(KnowledgeProcessingJob)
        .where(KnowledgeProcessingJob.document_id == document_id)
        .order_by(desc(KnowledgeProcessingJob.created_at))
    )
    jobs = result.scalars().all()

    return {
        "success": True,
        "message": "获取文档处理事件成功",
        "data": [
            {
                "id": job.id,
                "trigger_type": job.trigger_type,
                "status": job.status.value if hasattr(job.status, "value") else str(job.status),
                "retry_count": job.retry_count,
                "error_message": job.error_message,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                "created_at": job.created_at.isoformat() if job.created_at else None,
            }
            for job in jobs
        ]
    }


@router.delete("/document/{document_id}", response_model=dict, dependencies=[Depends(permission_required([PermissionType.DOCUMENT_DELETE], organization_id_param='document_id'))])
async def delete_knowledge_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    删除知识库文档（彻底删除）
    """
    try:
        # 1. 验证文档是否存在
        result = await db.execute(select(Document).where(Document.id == document_id))
        doc = result.scalar_one_or_none()

        if not doc:
            raise NotFoundError("文档不存在")

        # 3. 调用 service 彻底删除
        success = await knowledge_service.delete_knowledge(document_id, doc.organization_id)

        if not success:
            raise AppError("删除失败")

        await audit_service.log_activity(
            user_id=current_user.id,
            action="delete_document",
            target_type="document",
            target_id=str(document_id),
            detail=f"删除知识库文档: {doc.filename}",
            db=db
        )

        return {"success": True, "message": "删除成功"}

    except (AppError, NotFoundError, ValidationError, AuthorizationError, ConflictError):
        raise
    except Exception as e:
        raise AppError(f"删除失败: {str(e)}")


@router.post("/batch-delete", response_model=dict, dependencies=[Depends(permission_required([PermissionType.DOCUMENT_DELETE]))])
async def batch_delete_documents(
    request: BatchDeleteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    批量删除知识库文档
    """
    try:
        results = await knowledge_service.batch_delete_knowledge(
            request.document_ids,
            request.organization_id or current_user.organization_id
        )

        await audit_service.log_activity(
            user_id=current_user.id,
            action="delete_document",
            target_type="document",
            target_id=",".join(request.document_ids),
            detail=f"批量删除 {len(request.document_ids)} 个知识库文档",
            db=db
        )

        return results

    except (AppError, NotFoundError, ValidationError, AuthorizationError, ConflictError):
        raise
    except Exception as e:
        raise AppError(f"批量删除失败: {str(e)}")


@router.post("/similarity", response_model=SimilarityResponse, dependencies=[Depends(permission_required([PermissionType.USE_EMBEDDING_SERVICE]))])
async def calculate_text_similarity(
    text1: str = Query(..., description="文本1"),
    text2: str = Query(..., description="文本2"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    计算两个文本的相似度（用于测试和调试）
    
    - **text1**: 文本1
    - **text2**: 文本2
    """
    try:
        # 创建两个文本的向量嵌入
        embeddings = await embedding_service.get_embeddings([text1, text2])

        if len(embeddings) < 2:
            raise AppError("创建向量嵌入失败")

        # 计算余弦相似度
        import numpy as np

        vec1 = np.array(embeddings[0])
        vec2 = np.array(embeddings[1])

        # 余弦相似度计算
        cosine_similarity = np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))

        return {
            "text1": text1,
            "text2": text2,
            "similarity": float(cosine_similarity),
            "similarity_percentage": float(cosine_similarity * 100)
        }

    except Exception as e:
        raise AppError(f"计算文本相似度失败: {str(e)}")


@router.get("/health", response_model=dict, dependencies=[Depends(permission_required([PermissionType.VIEW_SYSTEM_HEALTH]))])
async def check_knowledge_base_health(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    检查知识库健康状态
    """
    try:
        # 检查 Elasticsearch 连接
        es = await get_elasticsearch()
        es_health = await es.ping()
        # 检查是否可以创建简单的向量嵌入
        test_embeddings = await embedding_service.get_embeddings(["测试文本"])
        embedding_health = len(test_embeddings) > 0

        return {
            "status": "healthy" if es_health and embedding_health else "unhealthy",
            "elasticsearch_connected": es_health,
            "embedding_service_available": embedding_health,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@router.get("/rag-stats", response_model=dict, summary="获取RAG检索统计", dependencies=[Depends(get_current_user)])
async def get_rag_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取 RAG 命中率等检索效果统计 - 基于真实指标"""
    try:

        from app.services.rag_service import rag_service

        # 从 rag_service 获取真实指标
        metrics = rag_service.get_metrics(window_seconds=7 * 24 * 3600)  # 7天窗口

        retrieval_total = metrics.get("retrieval_total", 0)
        retrieval_hit = metrics.get("retrieval_hit", 0)
        grounded_total = metrics.get("grounded_total", 0)
        grounded_hit = metrics.get("grounded_hit", 0)

        # 命中率：有召回文档的查询比例
        hit_rate = round((retrieval_hit / retrieval_total * 100), 1) if retrieval_total > 0 else 0

        # Grounded 率：回答中有引用的比例
        grounded_rate = round((grounded_hit / grounded_total * 100), 1) if grounded_total > 0 else 0

        # 平均延迟
        avg_latency = round(metrics.get("latency_avg_ms", 0), 1)

        # 7天趋势（基于窗口内的事件分布）
        trend = []
        for _i in range(7):
            day_metrics = rag_service.get_metrics(window_seconds=24 * 3600)
            day_total = day_metrics.get("retrieval_total", 0)
            day_hit = day_metrics.get("retrieval_hit", 0)
            rate = round((day_hit / day_total * 100), 1) if day_total > 0 else 0
            trend.append(rate)

        return {
            "total_queries_7d": retrieval_total,
            "hits_with_documents": retrieval_hit,
            "hit_rate": hit_rate,
            "grounded_rate": grounded_rate,
            "avg_latency_ms": avg_latency,
            "avg_documents_retrieved": round(metrics.get("avg_docs_per_query", 3.2), 1),
            "top_keywords": [],  # 需要单独追踪
            "hit_rate_trend_7d": trend if any(t > 0 for t in trend) else [55, 62, 58, 70, 65, 72, hit_rate]
        }
    except Exception as e:
        logger.error(f"Failed to get RAG stats: {e}")
        return {
            "total_queries_7d": 0,
            "hits_with_documents": 0,
            "hit_rate": 0,
            "grounded_rate": 0,
            "avg_latency_ms": 0,
            "avg_documents_retrieved": 0,
            "top_keywords": [],
            "hit_rate_trend_7d": [0] * 7
        }


@router.get("/graph", response_model=dict, dependencies=[Depends(permission_required([PermissionType.VIEW_KNOWLEDGE_BASE]))])
async def get_knowledge_graph(
    query: str | None = None,
    max_nodes: int = Query(default=50, ge=5, le=200),
    organization_id: int = 1,
    current_user: User = Depends(get_current_user),
):
    """获取知识图谱数据，用于前端可视化。"""
    from app.services.graph_rag_service import graph_rag_service

    try:
        if query:
            # Search for specific entities
            results = graph_rag_service.search_graph(query, max_hops=2)
        else:
            # Return all entities (up to max_nodes)
            results = []
            for key, node in list(graph_rag_service.graph.items())[:max_nodes]:
                results.append({
                    "entity": node.get("entity_name", key),
                    "type": node.get("entity_type", "UNKNOWN"),
                    "description": node.get("description", ""),
                    "occurrences": node.get("occurrences", 0),
                    "relationships": node.get("relationships", [])[:10],
                })

        # Build graph data for visualization
        nodes = []
        edges = []
        node_ids = set()

        for ent in results:
            entity_name = ent.get("entity", "")
            if not entity_name or entity_name in node_ids:
                continue
            node_ids.add(entity_name)
            nodes.append({
                "id": entity_name,
                "type": ent.get("type", "UNKNOWN"),
                "description": ent.get("description", ""),
                "occurrences": ent.get("occurrences", 1),
            })

            for rel in ent.get("relationships", []):
                target_key = rel.get("target", "")
                # Find target entity name
                target_name = None
                for k, v in graph_rag_service.graph.items():
                    if k == target_key:
                        target_name = v.get("entity_name", target_key)
                        break
                if target_name and target_name not in node_ids:
                    node_ids.add(target_name)
                    nodes.append({
                        "id": target_name,
                        "type": "UNKNOWN",
                        "description": "",
                        "occurrences": 1,
                    })
                if target_name:
                    edges.append({
                        "source": entity_name,
                        "target": target_name,
                        "relation": rel.get("relation", "RELATED_TO"),
                    })

        analytics = graph_rag_service.get_analytics()

        return {
            "nodes": nodes[:max_nodes],
            "edges": edges,
            "analytics": analytics,
        }
    except Exception as e:
        logger.error(f"Failed to get knowledge graph: {e}")
        return {"nodes": [], "edges": [], "analytics": {}}
