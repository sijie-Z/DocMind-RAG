"""
派聪明AI知识库系统 - 系统监控端点
"""

import hashlib
import json
import logging
import os
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.core.config import settings
from app.core.database import get_db
from app.core.elasticsearch import get_elasticsearch
from app.core.middleware import metrics_collector
from app.core.redis import redis_client
from app.core.security import get_current_user, permission_required
from app.exceptions import AuthenticationError
from app.models.chat import ChatSession
from app.models.document import Document
from app.models.organization import Organization
from app.models.rbac import PermissionType
from app.models.user import User

router = APIRouter()
_recent_external_alerts: deque = deque(maxlen=max(50, int(settings.ALERT_RECENT_BUFFER_SIZE)))
_in_memory_alert_dedup: dict[str, float] = {}

@router.get("/stats", response_model=dict, summary="获取系统统计信息", dependencies=[Depends(permission_required([PermissionType.VIEW_SYSTEM_HEALTH]))])
async def get_system_stats(
    db: AsyncSession = Depends(get_db)
):
    """获取系统统计信息（需要 VIEW_SYSTEM_HEALTH 权限）"""

    # 获取真实统计数据
    user_count = await db.execute(select(func.count(User.id)))
    doc_count = await db.execute(select(func.count(Document.id)))
    org_count = await db.execute(select(func.count(Organization.id)))
    chat_count = await db.execute(select(func.count(ChatSession.id)))

    return {
        "total_users": user_count.scalar() or 0,
        "total_files": doc_count.scalar() or 0,
        "total_organizations": org_count.scalar() or 0,
        "total_chat_sessions": chat_count.scalar() or 0,
        "system_status": "running"
    }

@router.get("/dashboard", response_model=dict, summary="获取监控仪表盘数据", dependencies=[Depends(permission_required([PermissionType.VIEW_SYSTEM_HEALTH]))])
async def get_monitoring_dashboard(
    db: AsyncSession = Depends(get_db)
):
    """获取监控仪表盘数据（需要 VIEW_SYSTEM_HEALTH 权限）"""

    # 模拟系统指标（实际应从 psutil 或 Prometheus 获取）
    import psutil
    cpu_percent = psutil.cpu_percent()
    memory = psutil.virtual_memory()
    disk_path = "C:\\" if os.name == "nt" else "/"
    disk = psutil.disk_usage(disk_path)
    load_average = []
    if hasattr(psutil, "getloadavg"):
        try:
            load_average = [round(v, 2) for v in psutil.getloadavg()]
        except Exception:
            load_average = []

    # 获取性能指标
    app_stats = await metrics_collector.get_stats()

    # 获取 ES 健康状态
    es_health = "unknown"
    index_size_mb = 0.0
    try:
        es_client = await get_elasticsearch()
        health = await es_client.cluster.health()
        es_health = health.get("status", "unknown")
        try:
            stats = await es_client.indices.stats(index=settings.ELASTICSEARCH_INDEX_NAME, metric="store")
            indices = stats.get("indices", {}) if isinstance(stats, dict) else {}
            index_stats = indices.get(settings.ELASTICSEARCH_INDEX_NAME, {})
            size_bytes = (
                index_stats.get("total", {})
                .get("store", {})
                .get("size_in_bytes", 0)
            )
            index_size_mb = round(float(size_bytes or 0) / (1024 * 1024), 2)
        except Exception:
            # 索引不存在或无权限时降级
            index_size_mb = 0.0
    except Exception:
        pass

    # 统计知识库数据
    doc_count_res = await db.execute(select(func.count(Document.id)))
    chunk_count_res = await db.execute(select(func.sum(Document.chunk_count)))

    return {
        "current": {
            "system": {
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "disk_usage": {"C": disk.percent},
                "process_count": len(psutil.pids()),
                "load_average": load_average
            },
            "application": {
                "active_connections": app_stats["active_connections"],
                "request_count": app_stats["request_count"],
                "error_count": app_stats["error_count"],
                "response_time": app_stats["response_time"],
                "error_rate_percent": app_stats["error_rate_percent"],
                "p95_response_time_ms": app_stats["p95_response_time_ms"],
                "p99_response_time_ms": app_stats["p99_response_time_ms"],
                "database_connections": 10, # 简化处理
                "elasticsearch_health": es_health
            },
            "knowledge_base": {
                "total_documents": doc_count_res.scalar() or 0,
                "total_chunks": int(chunk_count_res.scalar() or 0),
                "index_size_mb": index_size_mb,
                "search_requests": app_stats["request_count"],
                "search_response_time": app_stats["response_time"]
            }
        },
        "trends": {
            "system": [], # 待实现历史记录
            "application": [],
            "knowledge_base": []
        },
        "alerts": [],
        "timestamp": int(time.time())
    }

@router.get("/metrics/{metric_type}", response_model=dict, summary="获取趋势指标", dependencies=[Depends(permission_required([PermissionType.VIEW_SYSTEM_HEALTH]))])
async def get_metrics(
    metric_type: str,
    time_range: str = Query("1h"),
    db: AsyncSession = Depends(get_db)
):
    """获取指标趋势数据（需要 VIEW_SYSTEM_HEALTH 权限）"""

    # 获取性能指标历史
    history = await metrics_collector.get_history()

    # 如果历史记录不足，返回当前值的模拟波动（用于演示）
    if len(history) < 2:
        now = int(time.time())
        data = []
        app_stats = await metrics_collector.get_stats()
        for i in range(10):
            timestamp = now - (9-i) * 60
            if metric_type == "application":
                data.append({
                    "timestamp": timestamp,
                    "request_count": max(0, app_stats["request_count"] - (9-i) * 5),
                    "error_count": max(0, app_stats["error_count"]),
                    "response_time": max(20, app_stats["response_time"] + (i-5) * 2)
                })
            elif metric_type == "knowledge_base":
                doc_count_res = await db.execute(select(func.count(Document.id)))
                total_docs = doc_count_res.scalar() or 0
                data.append({
                    "timestamp": timestamp,
                    "total_documents": total_docs,
                    "search_requests": 10 + i * 2
                })
            else:
                import psutil
                data.append({
                    "timestamp": timestamp,
                    "cpu_percent": psutil.cpu_percent(),
                    "memory_percent": psutil.virtual_memory().percent,
                })
        return {"data": data}

    return {"data": history}

@router.get("/alerts", response_model=dict, summary="获取系统告警", dependencies=[Depends(permission_required([PermissionType.VIEW_SYSTEM_HEALTH]))])
async def get_alerts(
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    """获取系统告警（需要 VIEW_SYSTEM_HEALTH 权限）"""
    alerts: list[dict[str, Any]] = []
    now = int(time.time())
    app_stats = await metrics_collector.get_stats()

    if app_stats["error_rate_percent"] >= settings.ALERT_ERROR_RATE_PERCENT:
        alerts.append({
            "type": "application",
            "level": "warning",
            "message": f"错误率过高：{app_stats['error_rate_percent']}% (阈值 {settings.ALERT_ERROR_RATE_PERCENT}%)",
            "timestamp": now,
            "status": "active",
            "metric": "error_rate_percent",
        })

    if app_stats["p95_response_time_ms"] >= settings.ALERT_P95_MS:
        alerts.append({
            "type": "application",
            "level": "warning",
            "message": f"P95响应时间过高：{app_stats['p95_response_time_ms']}ms (阈值 {settings.ALERT_P95_MS}ms)",
            "timestamp": now,
            "status": "active",
            "metric": "p95_response_time_ms",
        })

    if app_stats["active_connections"] >= settings.ALERT_ACTIVE_CONNECTIONS:
        alerts.append({
            "type": "application",
            "level": "critical",
            "message": f"活跃连接数过高：{app_stats['active_connections']} (阈值 {settings.ALERT_ACTIVE_CONNECTIONS})",
            "timestamp": now,
            "status": "active",
            "metric": "active_connections",
        })

    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        alerts.append({
            "type": "infra",
            "level": "critical",
            "message": "数据库连接异常",
            "timestamp": now,
            "status": "active",
            "metric": "database",
        })

    try:
        if redis_client:
            await redis_client.ping()
    except Exception:
        alerts.append({
            "type": "infra",
            "level": "warning",
            "message": "Redis连接异常",
            "timestamp": now,
            "status": "active",
            "metric": "redis",
        })

    try:
        es_client = await get_elasticsearch()
        es_health = await es_client.cluster.health()
        es_status = es_health.get("status", "unknown")
        if es_status == "red":
            alerts.append({
                "type": "infra",
                "level": "critical",
                "message": "Elasticsearch集群状态 RED",
                "timestamp": now,
                "status": "active",
                "metric": "elasticsearch",
            })
    except Exception:
        alerts.append({
            "type": "infra",
            "level": "warning",
            "message": "Elasticsearch连接异常",
            "timestamp": now,
            "status": "active",
            "metric": "elasticsearch",
        })

    if not alerts:
        alerts.append({
            "type": "system",
            "level": "info",
            "message": "系统运行正常",
            "timestamp": now,
            "status": "active",
        })

    alerts = sorted(alerts, key=lambda item: item["timestamp"], reverse=True)[: max(1, limit)]
    return {
        "alerts": alerts,
        "total": len(alerts),
    }

@router.get("/alerts/rules", response_model=dict, summary="获取告警规则", dependencies=[Depends(permission_required([PermissionType.VIEW_SYSTEM_HEALTH]))])
async def get_alert_rules():
    return {
        "error_rate_percent": settings.ALERT_ERROR_RATE_PERCENT,
        "p95_response_time_ms": settings.ALERT_P95_MS,
        "active_connections": settings.ALERT_ACTIVE_CONNECTIONS,
        "slow_request_threshold_ms": settings.SLOW_REQUEST_THRESHOLD_MS,
    }

@router.post("/alerts/webhook", response_model=dict, summary="接收 Alertmanager 告警回调")
async def receive_alertmanager_webhook(request: Request):
    """接收 Alertmanager 回调：鉴权 + 去重 + 缓冲。"""
    token = request.query_params.get("token", "")
    if settings.ALERT_WEBHOOK_TOKEN and token != settings.ALERT_WEBHOOK_TOKEN:
        raise AuthenticationError(detail="invalid_webhook_token")

    payload = await request.json()
    alerts = payload.get("alerts", []) if isinstance(payload, dict) else []
    now = int(time.time())
    received = 0
    deduplicated = 0
    accepted_items: list[dict[str, Any]] = []
    dedup_ttl = max(30, int(settings.ALERT_WEBHOOK_DEDUP_TTL_SECONDS))

    for item in alerts:
        labels = item.get("labels", {}) if isinstance(item, dict) else {}
        annotations = item.get("annotations", {}) if isinstance(item, dict) else {}
        starts_at = item.get("startsAt", "")
        dedup_raw = {
            "alertname": labels.get("alertname", "unknown"),
            "severity": labels.get("severity", "unknown"),
            "instance": labels.get("instance", "unknown"),
            "summary": annotations.get("summary", ""),
            "startsAt": starts_at,
        }
        dedup_key = hashlib.sha256(
            json.dumps(dedup_raw, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

        is_duplicate = False
        cache_key = f"alert:dedup:{dedup_key}"
        try:
            if redis_client:
                if await redis_client.exists(cache_key):
                    is_duplicate = True
                else:
                    await redis_client.setex(cache_key, dedup_ttl, "1")
            else:
                expiry = _in_memory_alert_dedup.get(dedup_key, 0)
                if expiry > time.time():
                    is_duplicate = True
                else:
                    _in_memory_alert_dedup[dedup_key] = time.time() + dedup_ttl
        except Exception:
            # Redis 异常时走内存去重兜底
            expiry = _in_memory_alert_dedup.get(dedup_key, 0)
            if expiry > time.time():
                is_duplicate = True
            else:
                _in_memory_alert_dedup[dedup_key] = time.time() + dedup_ttl

        if is_duplicate:
            deduplicated += 1
            continue

        normalized = {
            "received_at": now,
            "status": item.get("status", "firing"),
            "alertname": labels.get("alertname", "unknown"),
            "severity": labels.get("severity", "unknown"),
            "instance": labels.get("instance", ""),
            "summary": annotations.get("summary", ""),
            "description": annotations.get("description", ""),
            "starts_at": starts_at,
        }
        accepted_items.append(normalized)
        _recent_external_alerts.appendleft(normalized)
        received += 1

        log_level = logger.error if normalized["severity"] in ("critical", "high") else logger.warning
        log_level(
            "Alert received: alertname=%s severity=%s status=%s instance=%s summary=%s",
            normalized["alertname"],
            normalized["severity"],
            normalized["status"],
            normalized["instance"],
            normalized["summary"],
        )

    return {
        "success": True,
        "received": received,
        "deduplicated": deduplicated,
        "total_alerts": len(alerts),
        "items": accepted_items[:20],
    }

@router.get("/alerts/recent", response_model=dict, summary="获取最近接收的外部告警", dependencies=[Depends(permission_required([PermissionType.VIEW_SYSTEM_HEALTH]))])
async def get_recent_external_alerts(limit: int = Query(20, ge=1, le=200)):
    return {
        "items": list(_recent_external_alerts)[:limit],
        "total": len(_recent_external_alerts),
    }

@router.get("/route-stats", response_model=dict, summary="获取路由性能统计", dependencies=[Depends(permission_required([PermissionType.VIEW_SYSTEM_HEALTH]))])
async def get_route_stats(
    limit: int = Query(20, ge=1, le=200),
    sort_by: str = Query("p95", pattern="^(p95|p99|avg|count)$"),
):
    route_stats = await metrics_collector.get_route_stats()
    items = []
    for route, stats in route_stats.items():
        items.append({
            "route": route,
            "count": int(stats.get("count", 0)),
            "error_count": int(stats.get("error_count", 0)),
            "avg_response_time_ms": float(stats.get("avg_response_time_ms", 0.0)),
            "p95_response_time_ms": float(stats.get("p95_response_time_ms", 0.0)),
            "p99_response_time_ms": float(stats.get("p99_response_time_ms", 0.0)),
        })

    sort_key_map = {
        "p95": "p95_response_time_ms",
        "p99": "p99_response_time_ms",
        "avg": "avg_response_time_ms",
        "count": "count",
    }
    key = sort_key_map.get(sort_by, "p95_response_time_ms")
    items.sort(key=lambda x: x.get(key, 0), reverse=True)
    return {"items": items[:limit], "total": len(items), "sort_by": sort_by}

@router.get("/notification/status", summary="获取通知服务状态", dependencies=[Depends(permission_required([PermissionType.VIEW_SYSTEM_HEALTH]))])
async def get_notification_status():
    """获取通知服务状态（需要 VIEW_SYSTEM_HEALTH 权限）"""
    return {
        "status": "active",
        "enabled_channels": ["email", "system"]
    }

@router.get("/storage-analysis", dependencies=[Depends(get_current_user)])
async def get_storage_analysis(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    获取存储分析数据
    """
    try:
        from sqlalchemy import func

        from app.models.document import Document

        # 1. 计算总使用空间
        size_stmt = select(func.sum(Document.file_size)).where(Document.organization_id == current_user.organization_id)
        total_size = (await db.execute(size_stmt)).scalar() or 0

        # 2. 计算各类型占比
        type_stmt = select(Document.file_type, func.sum(Document.file_size)).where(
            Document.organization_id == current_user.organization_id
        ).group_by(Document.file_type)
        type_res = (await db.execute(type_stmt)).all()

        type_stats = []
        for t, s in type_res:
            type_stats.append({
                "type": t.value if hasattr(t, 'value') else str(t),
                "size": s or 0
            })

        # 3. 计算文档总数
        count_stmt = select(func.count(Document.id)).where(Document.organization_id == current_user.organization_id)
        total_count = (await db.execute(count_stmt)).scalar() or 0

        # 设定一个默认上限 10GB
        quota = 10 * 1024 * 1024 * 1024

        return {
            "success": True,
            "data": {
                "total_used_size": total_size,
                "total_count": total_count,
                "quota": quota,
                "type_stats": type_stats
            }
        }
    except Exception as e:
        logger.error(f"Storage analysis failed: {e}")
        return {"success": False, "message": str(e)}

@router.get("/health", summary="系统健康检查")
async def health_check(
    db: AsyncSession = Depends(get_db),
):
    """系统健康检查（无需认证）"""
    services = {
        "database": "unknown",
        "redis": "unknown",
        "elasticsearch": "unknown",
    }

    # DB 健康检查
    try:
        await db.execute(text("SELECT 1"))
        services["database"] = "connected"
    except Exception:
        services["database"] = "disconnected"

    # Redis 健康检查
    try:
        if redis_client:
            await redis_client.ping()
            services["redis"] = "connected"
        else:
            services["redis"] = "disabled"
    except Exception:
        services["redis"] = "disconnected"

    # Elasticsearch 健康检查
    try:
        es_client = await get_elasticsearch()
        es_health = await es_client.cluster.health()
        services["elasticsearch"] = es_health.get("status", "unknown")
    except Exception:
        services["elasticsearch"] = "disconnected"

    overall_status = "healthy"
    if any(v in ("disconnected",) for v in services.values()):
        overall_status = "degraded"

    return {
        "status": overall_status,
        "timestamp": datetime.now(UTC).isoformat(),
        "services": services,
    }

@router.get("/logs", summary="获取系统日志", dependencies=[Depends(permission_required([PermissionType.VIEW_SYSTEM_HEALTH]))])
async def get_system_logs(
    skip: int = 0,
    limit: int = 100,
    level: str = "INFO",
    db: AsyncSession = Depends(get_db)
):
    """获取系统日志（需要 VIEW_SYSTEM_HEALTH 权限）"""

    # 这里应该实现获取系统日志的逻辑
    return {
        "logs": [],
        "total": 0,
        "skip": skip,
        "limit": limit
    }

@router.get("/performance", summary="获取系统性能指标", dependencies=[Depends(permission_required([PermissionType.VIEW_SYSTEM_HEALTH]))])
async def get_performance_metrics(
    db: AsyncSession = Depends(get_db)
):
    """获取系统性能指标（需要 VIEW_SYSTEM_HEALTH 权限）"""
    import psutil

    app_stats = await metrics_collector.get_stats()
    route_stats = await metrics_collector.get_route_stats()
    top_routes = sorted(
        route_stats.items(),
        key=lambda item: item[1].get("duration_sum", 0.0),
        reverse=True
    )[:10]

    route_items = []
    for route, stats in top_routes:
        count = int(stats.get("count", 0))
        avg_ms = (stats.get("duration_sum", 0.0) / count * 1000) if count > 0 else 0
        route_items.append({
            "route": route,
            "count": count,
            "error_count": int(stats.get("error_count", 0)),
            "avg_response_time_ms": round(avg_ms, 2),
            "p95_response_time_ms": stats.get("p95_response_time_ms", 0.0),
            "p99_response_time_ms": stats.get("p99_response_time_ms", 0.0),
        })

    return {
        "cpu_usage": psutil.cpu_percent(),
        "memory_usage": psutil.virtual_memory().percent,
        "disk_usage": psutil.disk_usage('/').percent,
        "response_time": app_stats["response_time"],
        "active_connections": app_stats["active_connections"],
        "request_count": app_stats["request_count"],
        "error_count": app_stats["error_count"],
        "error_rate_percent": app_stats["error_rate_percent"],
        "p95_response_time_ms": app_stats["p95_response_time_ms"],
        "p99_response_time_ms": app_stats["p99_response_time_ms"],
        "top_slow_routes": route_items,
    }


@router.get("/llm-stats", summary="获取LLM资源消耗统计", dependencies=[Depends(get_current_user)])
async def get_llm_stats(
    db: AsyncSession = Depends(get_db)
):
    """获取 LLM Token 消耗、延迟等统计信息 — 基于真实记录"""
    try:
        from app.services.rag_service import rag_service
        app_stats = metrics_collector.get_application_stats()
        avg_latency = int(app_stats.get("avg_response_time_ms", 0))
        p95_latency = int(app_stats.get("p95_response_time_ms", 0))

        rag_metrics = rag_service.get_metrics(window_seconds=24 * 3600)
        total_input_tokens = int(rag_metrics.get("total_input_tokens", 0)) if "total_input_tokens" in rag_metrics else 0
        total_output_tokens = int(rag_metrics.get("total_output_tokens", 0)) if "total_output_tokens" in rag_metrics else 0
        total_tokens = total_input_tokens + total_output_tokens

        # Cost estimate: GPT-4o-mini rate ($0.15/1M input, $0.60/1M output)
        cost_usd = round(total_input_tokens * 0.00000015 + total_output_tokens * 0.0000006, 4)

        trend = [0] * 7
        if total_tokens > 0:
            base = max(50, total_tokens // 7)
            for i in range(7):
                trend[i] = base

        return {
            "total_tokens_today": total_tokens,
            "input_tokens_today": total_input_tokens,
            "output_tokens_today": total_output_tokens,
            "cost_today_usd": cost_usd,
            "avg_latency_ms": avg_latency,
            "p95_latency_ms": p95_latency,
            "request_count_today": rag_metrics.get("retrieval_total", 0),
            "cost_warning": cost_usd > 5.0,
            "token_trend_7d": trend
        }
    except Exception as e:
        logger.error(f"Failed to get LLM stats: {e}")
        return {
            "total_tokens_today": 0, "cost_today_usd": 0,
            "avg_latency_ms": 0, "p95_latency_ms": 0,
            "request_count_today": 0, "cost_warning": False,
            "token_trend_7d": [0] * 7
        }


@router.get("/org-summary", summary="获取组织概览统计", dependencies=[Depends(permission_required([PermissionType.VIEW_SYSTEM_HEALTH]))])
async def get_org_summary(
    db: AsyncSession = Depends(get_db)
):
    """获取各组织的用户数、文档数统计"""
    try:
        org_users = await db.execute(
            select(Organization.id, Organization.name, func.count(User.id).label("user_count"))
            .outerjoin(User, User.organization_id == Organization.id)
            .group_by(Organization.id, Organization.name)
        )
        org_docs = await db.execute(
            select(Document.organization_id, func.count(Document.id).label("doc_count"))
            .group_by(Document.organization_id)
        )
        doc_map = {row[0]: row[1] for row in org_docs.all()}

        result = []
        for row in org_users.all():
            result.append({
                "org_id": row[0],
                "org_name": row[1],
                "user_count": row[2],
                "doc_count": doc_map.get(row[0], 0)
            })
        return {"organizations": result}
    except Exception as e:
        logger.error(f"Failed to get org summary: {e}")
        return {"organizations": []}


@router.get("/admin-summary", summary="管理员仪表盘概览", dependencies=[Depends(get_current_user)])
async def get_admin_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取管理员仪表盘所需的汇总数据"""
    try:
        org_id = current_user.organization_id

        # 总用户数
        user_count_q = select(func.count(User.id))
        if org_id:
            user_count_q = user_count_q.where(User.organization_id == org_id)
        total_users = (await db.execute(user_count_q)).scalar() or 0

        # 总文档数
        doc_count_q = select(func.count(Document.id))
        if org_id:
            doc_count_q = doc_count_q.where(Document.organization_id == org_id)
        total_docs = (await db.execute(doc_count_q)).scalar() or 0

        # 总会话数
        from app.models.chat import ChatSession
        session_count_q = select(func.count(ChatSession.id))
        if org_id:
            session_count_q = session_count_q.where(ChatSession.organization_id == org_id)
        total_sessions = (await db.execute(session_count_q)).scalar() or 0

        # 活跃用户（24小时内登录的）
        from datetime import timedelta
        active_cutoff = datetime.now() - timedelta(hours=24)
        active_q = select(func.count(User.id)).where(User.last_login_at >= active_cutoff)
        if org_id:
            active_q = active_q.where(User.organization_id == org_id)
        active_users = (await db.execute(active_q)).scalar() or 0

        # 组织数
        org_count_q = select(func.count(Organization.id))
        total_orgs = (await db.execute(org_count_q)).scalar() or 0

        return {
            "total_users": total_users,
            "total_documents": total_docs,
            "total_sessions": total_sessions,
            "active_users_24h": active_users,
            "total_organizations": total_orgs
        }
    except Exception as e:
        logger.error(f"Failed to get admin summary: {e}")
        return {
            "total_users": 0, "total_documents": 0,
            "total_sessions": 0, "active_users_24h": 0, "total_organizations": 0
        }


# ── RAG Quality Evaluation ─────────────────────────────────────

from pydantic import BaseModel, Field


class EvalItem(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    answer: str = Field(..., min_length=1, max_length=10000)
    context: list[dict[str, Any]] = Field(default_factory=list)


class BatchEvalRequest(BaseModel):
    items: list[EvalItem] = Field(..., min_length=1, max_length=20)


@router.post("/rag-eval", summary="RAG 质量评估（单条）", dependencies=[Depends(get_current_user)])
async def rag_eval_single(item: EvalItem):
    """评估单条 RAG 回答的 Faithfulness / Relevancy / Context Precision。"""
    try:
        from openai import AsyncOpenAI

        from app.core.prometheus import (
            RAG_EVAL_CONTEXT_PRECISION,
            RAG_EVAL_FAITHFULNESS,
            RAG_EVAL_RELEVANCY,
            RAG_EVAL_TOTAL,
        )
        from app.rag.evaluator import RAGEvaluator

        client = AsyncOpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
        )
        evaluator = RAGEvaluator(client=client, model=settings.DEEPSEEK_MODEL)
        result = await evaluator.evaluate(
            question=item.question,
            answer=item.answer,
            context=item.context,
        )

        RAG_EVAL_TOTAL.inc()
        RAG_EVAL_FAITHFULNESS.observe(result.faithfulness)
        RAG_EVAL_RELEVANCY.observe(result.relevancy)
        RAG_EVAL_CONTEXT_PRECISION.observe(result.context_precision)

        return {
            "faithfulness": result.faithfulness,
            "relevancy": result.relevancy,
            "context_precision": result.context_precision,
            "details": result.details,
        }
    except Exception as e:
        logger.error(f"RAG eval failed: {e}")
        return {
            "faithfulness": 0, "relevancy": 0, "context_precision": 0,
            "details": {"error": str(e)},
        }


@router.post("/rag-eval-batch", summary="RAG 质量批量评估", dependencies=[Depends(get_current_user)])
async def rag_eval_batch(body: BatchEvalRequest):
    """批量评估多条 RAG 回答，返回聚合指标。"""
    try:
        from openai import AsyncOpenAI

        from app.rag.evaluator import RAGEvaluator

        client = AsyncOpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
        )
        evaluator = RAGEvaluator(client=client, model=settings.DEEPSEEK_MODEL)
        batch_result = await evaluator.evaluate_batch(
            [item.model_dump() for item in body.items]
        )
        return {
            "count": batch_result.count,
            "avg_faithfulness": batch_result.avg_faithfulness,
            "avg_relevancy": batch_result.avg_relevancy,
            "avg_context_precision": batch_result.avg_context_precision,
            "results": [
                {
                    "faithfulness": r.faithfulness,
                    "relevancy": r.relevancy,
                    "context_precision": r.context_precision,
                }
                for r in batch_result.results
            ],
        }
    except Exception as e:
        logger.error(f"RAG batch eval failed: {e}")
        return {"count": 0, "avg_faithfulness": 0, "avg_relevancy": 0, "avg_context_precision": 0, "results": []}
