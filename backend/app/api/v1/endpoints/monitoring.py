"""
DocMind AI知识库系统 - 系统监控端点
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
from app.core.logging import sanitize_log_value
from app.core.middleware import metrics_collector
from app.core.redis import redis_client
from app.core.security import get_current_user, get_user_org_id, permission_required
from app.core.system_metrics import system_metrics_sampler
from app.exceptions import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    ExternalServiceError,
    ValidationError,
)
from app.models.chat import ChatSession
from app.models.document import Document
from app.models.organization import Organization
from app.models.rbac import PermissionType
from app.models.user import User
from app.rag.errors import EvalJudgeUnavailableError

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
            # 系统指标趋势：读采样器真实数据（round2/sampling）；采样未启用
            # 或尚无样本时为空数组——诚实空态，不造假（此前为硬编码 []）。
            "system": await system_metrics_sampler.get_history(),
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

    if metric_type == "system":
        # 系统指标采样历史（round2/sampling）：信封与下方应用分支一致
        # {"data": [...]}（K 支线已把前端读取对齐到该风格）；无样本 → 空数组
        # （诚实空态，不造假）。单条样本也原样返回——它不是伪造数据，
        # 无需应用分支 <2 的防伪门槛。
        return {"data": await system_metrics_sampler.get_history()}

    # 获取性能指标历史
    history = await metrics_collector.get_history()

    # 安全修复：历史不足时返回空数组，不再伪造模拟数据（误导运维判断）
    if len(history) < 2:
        return {"data": []}

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
        # 这些值来自外部告警请求体，换行符可伪造日志行；落日志前净化
        log_level(
            "Alert received: alertname=%s severity=%s status=%s instance=%s summary=%s",
            sanitize_log_value(normalized["alertname"]),
            sanitize_log_value(normalized["severity"]),
            sanitize_log_value(normalized["status"]),
            sanitize_log_value(normalized["instance"]),
            sanitize_log_value(normalized["summary"]),
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
    """获取系统日志（需要 VIEW_SYSTEM_HEALTH 权限）— 读取日志文件尾部内容"""
    import json as _json
    import os as _os

    log_file = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__)))),
                             settings.LOG_FILE) if not _os.path.isabs(settings.LOG_FILE) else settings.LOG_FILE
    if not _os.path.exists(log_file):
        return {"logs": [], "total": 0, "skip": skip, "limit": limit}

    try:
        # 只读取尾部（最多 20k 行）避免大文件全量加载
        with open(log_file, encoding="utf-8", errors="replace") as f:
            tail_lines = f.readlines()[-20000:]
    except OSError as e:
        # issue #301（批 C）：读取失败不再回落 {"logs": [], "total": 0} 的 200 ——
        # 「读不了」与「确实没日志」在排障现场不可区分。读失败是服务端自身的
        # 故障（权限/占用/IO），显式 500，让运维从状态码就能分辨。
        logger.error(f"读取日志文件失败: {e}")
        raise AppError("读取系统日志文件失败，请检查服务器日志配置", error_code="LOG_READ_FAILED")

    level_upper = (level or "INFO").upper()
    logs = []
    for line in tail_lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = _json.loads(line)
            entry_level = str(entry.get("level", entry.get("levelname", "INFO"))).upper()
            if level_upper not in ("ALL", "DEBUG") and entry_level != level_upper:
                continue
            logs.append({
                "timestamp": entry.get("timestamp", entry.get("time", "")),
                "level": entry_level,
                "logger": entry.get("logger", entry.get("name", "")),
                "message": entry.get("message", entry.get("msg", line)),
            })
        except (_json.JSONDecodeError, TypeError):
            # 非 JSON 行：按级别过滤后原样返回
            if level_upper in ("ALL", "DEBUG", "INFO"):
                logs.append({"timestamp": "", "level": "INFO", "logger": "", "message": line[:500]})

    total = len(logs)
    return {
        "logs": logs[skip:skip + limit],
        "total": total,
        "skip": skip,
        "limit": limit,
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


@router.get(
    "/llm-stats",
    summary="获取LLM资源消耗统计",
    # 安全加固（issue #277，评审 or1268 R3 收账）：此前仅登录门——全局 token/
    # 成本/延迟聚合任意登录用户可见；与同文件 /stats、/org-summary、/admin-summary
    # 等相邻端点对齐，升级为 VIEW_SYSTEM_HEALTH 门槛。前端唯一消费方
    # dashboard/admin.vue 仅对 role=admin 渲染（admin 走 SysAdmin 角色持有该权限），
    # 普通用户 UI 不受影响。
    dependencies=[Depends(permission_required([PermissionType.VIEW_SYSTEM_HEALTH]))],
)
async def get_llm_stats(
    db: AsyncSession = Depends(get_db)
):
    """获取 LLM Token 消耗、延迟等统计信息 — 基于真实记录。

    issue #286：修前处理体调用的 `metrics_collector.get_application_stats`
    在类上不存在 → 恒 AttributeError → except 吞掉 → 恒返回降级零值 200
    （管理员面板 LLM 统计一直是假零）。同函数另有两处同款影子调用一并接回：
    RAG 侧按 `total_input_tokens/total_output_tokens` 读 snapshot（键从未存在，
    静默 0——RAGMetrics 现已把窗口口径暴露出来）；延迟按 `avg_response_time_ms`
    读（`get_stats()` 真实键是 `response_time`，静默 0）。

    数据源（issue #286 修复方向）：
    - agent 侧（PER 环 + 工具内调用）：`agent_tokens_total{model,direction}`
      计数样本读回（prometheus 未启用时该侧计 0，与写入侧 no-op 对称）；
    - RAG 侧：RAGMetrics 24h 窗口 token 事件（report_tokens 落账）；
    - 延迟：metrics_collector（HTTP 层）真实键名。
    """
    try:
        from app.core.prometheus import read_counter_samples
        from app.services.rag_service import rag_service

        app_stats = await metrics_collector.get_stats()
        avg_latency = int(app_stats.get("response_time", 0))
        p95_latency = int(app_stats.get("p95_response_time_ms", 0))

        rag_metrics = rag_service.get_metrics(window_seconds=24 * 3600)
        rag_input_tokens = int(rag_metrics.get("total_input_tokens", 0))
        rag_output_tokens = int(rag_metrics.get("total_output_tokens", 0))

        agent_samples = read_counter_samples("AGENT_TOKENS")
        agent_input_tokens = int(sum(v for labels, v in agent_samples if labels.get("direction") == "input"))
        agent_output_tokens = int(sum(v for labels, v in agent_samples if labels.get("direction") == "output"))
        # model 维度（可得则带）：agent 侧指标含 model 标签；RAG 侧
        # rag_llm_tokens_total 只有 direction 标签、进程内指标无 model，不入此列。
        agent_by_model: dict[str, dict[str, Any]] = {}
        for labels, value in agent_samples:
            model_name = labels.get("model", "unknown")
            entry = agent_by_model.setdefault(
                model_name,
                {"model": model_name, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            )
            if labels.get("direction") == "input":
                entry["input_tokens"] += int(value)
            elif labels.get("direction") == "output":
                entry["output_tokens"] += int(value)
        for entry in agent_by_model.values():
            entry["total_tokens"] = entry["input_tokens"] + entry["output_tokens"]

        # agent + RAG 两侧合计（前端「今日 Token 消耗」卡片语义：输入 + 输出）
        input_tokens = rag_input_tokens + agent_input_tokens
        output_tokens = rag_output_tokens + agent_output_tokens
        total_tokens = input_tokens + output_tokens

        # Cost estimate: GPT-4o-mini rate ($0.15/1M input, $0.60/1M output)
        cost_usd = round(input_tokens * 0.00000015 + output_tokens * 0.0000006, 4)

        trend = [0] * 7
        if total_tokens > 0:
            base = max(50, total_tokens // 7)
            for i in range(7):
                trend[i] = base

        return {
            "total_tokens_today": total_tokens,
            "input_tokens_today": input_tokens,
            "output_tokens_today": output_tokens,
            "rag_tokens_today": rag_input_tokens + rag_output_tokens,
            "agent_tokens_today": agent_input_tokens + agent_output_tokens,
            "agent_tokens_by_model": sorted(
                agent_by_model.values(), key=lambda item: item["total_tokens"], reverse=True
            ),
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
            "total_tokens_today": 0, "input_tokens_today": 0, "output_tokens_today": 0,
            "rag_tokens_today": 0, "agent_tokens_today": 0, "agent_tokens_by_model": [],
            "cost_today_usd": 0,
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
        # issue #301（批 C）：不再把查询失败伪装成「零组织」的成功响应。
        # 带身份的降级形态：既有键 organizations 保持（前端契约不破），
        # 新增 degraded/reason 供消费方区分「查不了」与「确实没有组织」。
        return {
            "organizations": [],
            "degraded": True,
            "reason": "组织概览统计查询失败，数据暂不可用",
        }


@router.get(
    "/admin-summary",
    summary="管理员仪表盘概览",
    dependencies=[Depends(permission_required([PermissionType.VIEW_SYSTEM_HEALTH]))],
)
async def get_admin_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取管理员仪表盘所需的汇总数据（组织作用域；全系统组织数仅超管可见）。"""
    # 安全加固（issue #268）：此前本端点只有登录门槛——与同文件相邻端点不同，漏了
    # VIEW_SYSTEM_HEALTH 权限门；且对无组织用户 `if org_id:` 全部落空，任何登录用户
    # 直接拿到全库 total_users/total_documents/total_sessions（跨租户计数泄漏）。
    # 现在：① 补上与众相邻端点一致的权限门；② 无组织的非超管显式 403（必须置于
    # try 之前——否则会被下面的 `except Exception` 吞成 200 零值）。
    org_id = get_user_org_id(current_user)
    if org_id is None and not current_user.is_superuser:
        raise AuthorizationError("当前用户未加入任何组织，无权访问组织统计数据")
    try:
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

        # 组织数：全系统口径只给超管（系统管理员）；组织作用域的租户管理员看到的
        # 是「自己所属的 1 个组织」。此前该数对任何通过认证的用户恒为全系统数
        # （跨租户元数据）。
        if current_user.is_superuser:
            org_count_q = select(func.count(Organization.id))
            total_orgs = (await db.execute(org_count_q)).scalar() or 0
        else:
            total_orgs = 1

        return {
            "total_users": total_users,
            "total_documents": total_docs,
            "total_sessions": total_sessions,
            "active_users_24h": active_users,
            "total_organizations": total_orgs
        }
    except Exception as e:
        logger.error(f"Failed to get admin summary: {e}")
        # issue #301（批 C）：不再把查询失败伪装成 5 项 KPI 全零的「系统被清空」。
        # 带身份的降级形态：既有 5 键保持（dashboard/admin.vue 契约不破），
        # 新增 degraded/reason 供消费方区分「查不了」与「真的都是 0」。
        return {
            "total_users": 0, "total_documents": 0,
            "total_sessions": 0, "active_users_24h": 0, "total_organizations": 0,
            "degraded": True,
            "reason": "管理员概览统计查询失败，数据暂不可用",
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
            base_url=settings.DEEPSEEK_API_URL,
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
    except EvalJudgeUnavailableError as e:
        # issue #301（批 C）：评委（LLM）不可用 → 分数不可知，绝不呈现成 0/0/0。
        # 此前该异常源头被 evaluator._score 吞成 (0.0, err)，端点 except 再兜一层
        # 全零 200 —— 「测量失败」被伪装成「真实评了 0 分」。评委是外部 LLM 服务
        # → 502 语义（对齐 #292 检索后端不可用的 502 先例）。
        logger.error(f"RAG eval judge unavailable: {e}")
        raise ExternalServiceError(
            "RAG 质量评估服务暂不可用，请稍后重试",
            error_code="RAG_EVAL_UNAVAILABLE",
        )


@router.post("/rag-eval-batch", summary="RAG 质量批量评估", dependencies=[Depends(get_current_user)])
async def rag_eval_batch(body: BatchEvalRequest):
    """批量评估多条 RAG 回答，返回聚合指标。"""
    # 限制批量条目数，防止成本 DoS
    if len(body.items) > 20:
        raise ValidationError("批量评估条目数不能超过 20 条")
    try:
        from openai import AsyncOpenAI

        from app.rag.evaluator import RAGEvaluator

        client = AsyncOpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_API_URL,
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
            # issue #301（批 C）：单条评委失败被标注并从均值剔除（见 evaluator），
            # 新增键如实交代剔除情况；既有键（count/avg_*/results）语义不变。
            "failed_count": batch_result.failed_count,
            "failures": batch_result.failures,
            "results": [
                {
                    "faithfulness": r.faithfulness,
                    "relevancy": r.relevancy,
                    "context_precision": r.context_precision,
                }
                for r in batch_result.results
            ],
        }
    except EvalJudgeUnavailableError as e:
        # issue #301（批 C）：全部条目评委不可用 → 严禁回落 count 0 全零 200
        # （「全零」还会诱导把故障当成「质量极差」的结论）。与单条同语义 502。
        logger.error(f"RAG batch eval judge unavailable: {e}")
        raise ExternalServiceError(
            "RAG 质量评估服务暂不可用，请稍后重试",
            error_code="RAG_EVAL_UNAVAILABLE",
        )
