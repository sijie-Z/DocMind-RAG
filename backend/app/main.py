"""
DocMind AI Knowledge Base System - Main Application Entry
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import AsyncSessionLocal, close_db, init_db
from app.core.elasticsearch import es_client, init_elasticsearch
from app.core.kafka_client import kafka_producer
from app.core.logging import setup_logging
from app.core.middleware import PerformanceMiddleware, RateLimitMiddleware, metrics_collector
from app.core.minio_client import minio_client
from app.core.notification_ws import notification_ws_manager
from app.core.redis import close_redis, init_redis
from app.core.request_id import RequestIDMiddleware
from app.core.response_middleware import ResponseFormatMiddleware
from app.core.tracing import setup_opentelemetry
from app.exceptions import AppError


async def metrics_snapshot_task():
    """定期记录性能指标快照"""
    while True:
        try:
            await metrics_collector.take_snapshot()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Failed to take metrics snapshot: {e}")
        await asyncio.sleep(max(5, int(settings.MONITORING_INTERVAL)))

# 设置日志
setup_logging()
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info(f"DocMind v{settings.APP_VERSION} starting up...")

    # 自动同步端口到 .backend-port 文件，Vite 代理依赖此文件
    actual_port = int(os.environ.get("DOCMIND_PORT", settings.PORT))
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    port_file = os.path.join(backend_dir, ".backend-port")
    with open(port_file, "w") as f:
        f.write(str(actual_port))
    logger.info(f"后端端口已同步到 .backend-port: {actual_port}")

    try:
        await init_db()
        logger.info("数据库连接成功")

        try:
            await init_redis()
        except Exception as e:
            logger.warning(f"Redis初始化失败: {e} - 应用将继续启动")

        try:
            await init_elasticsearch()
            if es_client:
                logger.info("Elasticsearch连接成功")
        except Exception as e:
            logger.warning(f"Elasticsearch初始化失败: {e} - 应用将继续启动")

        try:
            await kafka_producer.start()
            logger.info("Kafka连接成功")
        except Exception as e:
            logger.warning(f"Kafka初始化失败: {e} - 应用将继续启动")

        from app.services.permission_service import permission_service
        await permission_service.initialize_default_permissions_and_roles()

        # Wire embedding provider for agent memory system
        from app.dependencies import wire_memory_embedding_provider
        await wire_memory_embedding_provider()

        if settings.ENABLE_DEMO_ACCOUNT:
            from app.core.ensure_demo_user import ensure_demo_user
            from app.services.auth_service import auth_service
            await ensure_demo_user()

            async with AsyncSessionLocal() as db:
                demo_user = await auth_service.authenticate_user(db, "guest", "123456")
                if demo_user:
                    logger.info("演示账号验证通过: guest/123456")
                else:
                    logger.error(
                        "演示账号验证失败: guest/123456 无法通过认证，请检查数据库或运行 python reset_admin.py"
                    )

        # 启动指标监控快照任务
        metrics_task = asyncio.create_task(metrics_snapshot_task())

        logger.info("所有服务初始化完成")

    except Exception as e:
        logger.error(f"服务初始化失败: {str(e)}")
        raise

    yield

    # 关闭服务
    logger.info("派聪明AI知识库系统关闭中...")

    # 取消指标监控快照任务
    try:
        if metrics_task:
            metrics_task.cancel()
            logger.info("指标监控快照任务已取消")
    except Exception as e:
        logger.warning(f"取消指标监控快照任务失败: {e}")

    # 关闭Kafka Producer
    await kafka_producer.stop()

    # 关闭Elasticsearch连接
    try:
        if es_client:
            await es_client.close()
    except Exception as e:
        logger.error(f"关闭Elasticsearch连接失败: {e}")

    # 关闭Redis连接
    try:
        await close_redis()
    except Exception as e:
        logger.error(f"关闭Redis连接失败: {e}")

    # 关闭MinIO客户端
    try:
        if minio_client:
            pass  # MinIO sync client - no explicit close needed
    except Exception as e:
        logger.error(f"关闭MinIO连接失败: {e}")

    # 关闭数据库连接
    try:
        await close_db()
    except Exception as e:
        logger.error(f"关闭数据库连接失败: {e}")


# 创建FastAPI应用
OPENAPI_TAGS = [
    {"name": "认证", "description": "用户注册、登录、Token 管理、密码修改"},
    {"name": "用户管理", "description": "用户 CRUD、个人资料、会话管理、审计日志"},
    {"name": "文件管理", "description": "文件上传、下载、MinIO 存储"},
    {"name": "文档管理(RAG)", "description": "文档解析、向量化、知识库构建"},
    {"name": "聊天", "description": "会话管理、消息发送、WebSocket/SSE 流式问答"},
    {"name": "知识库", "description": "知识库 CRUD、检索、统计"},
    {"name": "组织管理", "description": "组织 CRUD、成员管理、角色权限"},
    {"name": "系统监控", "description": "性能指标、健康检查、告警、RAG 评估"},
    {"name": "通知管理", "description": "通知 CRUD、已读标记、WebSocket 推送"},
    {"name": "提示词管理", "description": "提示词模板 CRUD"},
    {"name": "操作手册", "description": "系统操作手册管理"},
    {"name": "Agent工作流", "description": "工作流 CRUD、执行、调试"},
    {"name": "Agent记忆", "description": "短期/长期/工作记忆管理"},
    {"name": "智能Agent", "description": "ReAct Agent 对话、工具调用、技能学习"},
]

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="基于 DeepSeek 的企业级 RAG 知识库系统，支持文档管理、混合检索、流式问答、自主 Agent",
    lifespan=lifespan,
    openapi_tags=OPENAPI_TAGS,
    contact={"name": "DocMind Team", "url": "https://github.com/docmind"},
    license_info={"name": "MIT"},
    openapi_url="/api/v1/openapi.json",
)

# ---------------------------------------------------------------------------
# Middleware stack (order matters — outermost runs first)
# ---------------------------------------------------------------------------

# 1. Request ID — runs first so all downstream code has request.state.request_id
app.add_middleware(RequestIDMiddleware)

# 2. CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

# 3. Response format wrapper — wraps 2xx JSON into standard envelope
app.add_middleware(ResponseFormatMiddleware)

# 4. GZip compression
app.add_middleware(GZipMiddleware, minimum_size=1000)

# 5. Rate limiting
if settings.ENABLE_RATE_LIMIT:
    app.add_middleware(RateLimitMiddleware)

# 6. Performance monitoring (includes request_id context var injection)
app.add_middleware(PerformanceMiddleware)


# 异常处理
@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    """Domain exception handler — converts AppError subclasses to JSON."""
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "code": exc.status_code,
            "error_code": exc.error_code,
            "message": exc.message,
            "detail": exc.detail,
            "request_id": request_id,
            "data": None,
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTP异常处理：同时返回 message 和 detail，便于前端展示"""
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "code": exc.status_code,
            "message": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
            "detail": exc.detail,
            "request_id": request_id,
            "data": None
        }
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """请求参数验证异常处理"""
    request_id = getattr(getattr(request, "state", None), "request_id", None)

    # 提取错误信息中友好的提示
    errors = exc.errors()
    error_messages = []
    safe_errors = []
    for error in errors:
        field = ".".join(str(loc) for loc in error.get("loc", []))
        msg = error.get("msg", "")
        error_messages.append(f"字段 '{field}' 错误: {msg}")
        # 确保所有值都是 JSON 可序列化的
        safe_error = {}
        for k, v in error.items():
            if isinstance(v, Exception):
                safe_error[k] = str(v)
            elif isinstance(v, dict):
                safe_error[k] = {kk: str(vv) if isinstance(vv, Exception) else vv for kk, vv in v.items()}
            else:
                safe_error[k] = v
        safe_errors.append(safe_error)

    error_msg = "; ".join(error_messages)

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "success": False,
            "code": status.HTTP_422_UNPROCESSABLE_ENTITY,
            "message": f"参数校验失败: {error_msg}",
            "detail": safe_errors,
            "request_id": request_id,
            "data": None
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """通用异常处理"""
    import traceback
    err_msg = f"{type(exc).__name__}: {str(exc)}"
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    logger.error(f"未处理的异常: {err_msg}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "code": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "message": "服务器内部错误，请稍后重试或联系管理员",
            "detail": err_msg if settings.EXPOSE_EXCEPTION_DETAIL else "internal_server_error",
            "request_id": request_id,
            "data": None
        }
    )


# ── Application start time for health check uptime ──────────────
_APP_START_TIME: float = time.time()


# 健康检查端点 — enhanced with per-service dependency checks
@app.get("/health")
async def health_check():
    """Enhanced health check with per-service dependency status.

    Returns overall status, individual service health, version, and uptime.
    Each service check has a short timeout and does not block the full check
    on a single failure.
    """
    services: dict = {}

    async def _check_db():
        try:
            from app.core.database import AsyncSessionLocal
            async with AsyncSessionLocal() as sess:
                from sqlalchemy import text
                await sess.execute(text("SELECT 1"))
            services["database"] = "healthy"
        except Exception as e:
            services["database"] = "unhealthy"
            logger.warning("Health check — database: %s", e)

    async def _check_redis():
        try:
            from app.core.redis import redis_client as _rc
            if _rc:
                await asyncio.wait_for(_rc.ping(), timeout=2)
                services["redis"] = "healthy"
            else:
                services["redis"] = "disabled"
        except TimeoutError:
            services["redis"] = "degraded"
        except Exception as e:
            services["redis"] = "degraded"
            logger.debug("Health check — redis: %s", e)

    async def _check_elasticsearch():
        try:
            from app.core.elasticsearch import es_client as _ec
            if _ec:
                health = await asyncio.wait_for(_ec.cluster.health(), timeout=3)
                es_status = health.get("status", "unknown")
                if es_status in ("green", "yellow"):
                    services["elasticsearch"] = "healthy"
                else:
                    services["elasticsearch"] = "unhealthy"
            else:
                services["elasticsearch"] = "disabled"
        except TimeoutError:
            services["elasticsearch"] = "degraded"
        except Exception as e:
            services["elasticsearch"] = "degraded"
            logger.debug("Health check — elasticsearch: %s", e)

    async def _check_minio():
        try:
            from app.core.minio_client import minio_client as _mc
            if _mc and _mc.client:
                bucket = _mc.bucket_name
                exists = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, _mc.client.bucket_exists, bucket),
                    timeout=3,
                )
                services["minio"] = "healthy" if exists else "degraded"
            else:
                services["minio"] = "disabled"
        except TimeoutError:
            services["minio"] = "degraded"
        except Exception as e:
            services["minio"] = "degraded"
            logger.debug("Health check — minio: %s", e)

    async def _check_kafka():
        try:
            from app.core.kafka_client import kafka_producer as _kp
            if _kp and _kp.producer:
                # Kafka producer doesn't have a great liveness check without sending,
                # just report if it was started successfully
                services["kafka"] = "healthy"
            else:
                services["kafka"] = "disabled"
        except Exception as e:
            services["kafka"] = "degraded"
            logger.debug("Health check — kafka: %s", e)

    # Run all checks concurrently
    await asyncio.gather(
        _check_db(),
        _check_redis(),
        _check_elasticsearch(),
        _check_minio(),
        _check_kafka(),
        return_exceptions=True,
    )

    # Overall status: all healthy -> healthy, any unhealthy -> unhealthy, else degraded
    status_values = list(services.values())
    if all(v == "healthy" for v in status_values):
        overall_status = "healthy"
    elif any(v == "unhealthy" for v in status_values):
        overall_status = "unhealthy"
    else:
        overall_status = "degraded"

    uptime = time.time() - _APP_START_TIME if _APP_START_TIME > 0 else 0

    return {
        "status": overall_status,
        "version": settings.APP_VERSION,
        "services": services,
        "uptime_seconds": round(uptime, 1),
    }


# 系统信息端点
@app.get("/info")
async def system_info():
    """系统信息"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "description": "基于DeepSeek的AI知识库系统",
        "features": [
            "文档上传与管理",
            "多格式文档解析",
            "向量嵌入与存储",
            "混合搜索（关键词+向量）",
            "知识问答",
            "多轮对话",
            "TextToSQL",
            "WebSocket实时通信"
        ]
    }


@app.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics():
    """Prometheus 指标导出 — HTTP + WebSocket + RAG 管线指标"""
    http_metrics = await metrics_collector.get_prometheus_text()
    ws_stats = notification_ws_manager.get_stats()

    ws_lines = [
        "# HELP app_ws_active_connections Current active websocket connections",
        "# TYPE app_ws_active_connections gauge",
        f"app_ws_active_connections {ws_stats['active_connections']}",
        "# HELP app_ws_active_users Current active websocket users",
        "# TYPE app_ws_active_users gauge",
        f"app_ws_active_users {ws_stats['active_users']}",
        "# HELP app_ws_connect_total Total websocket connects",
        "# TYPE app_ws_connect_total counter",
        f"app_ws_connect_total {ws_stats['connect_total']}",
        "# HELP app_ws_disconnect_total Total websocket disconnects",
        "# TYPE app_ws_disconnect_total counter",
        f"app_ws_disconnect_total {ws_stats['disconnect_total']}",
        "# HELP app_ws_push_total Total websocket push operations",
        "# TYPE app_ws_push_total counter",
        f"app_ws_push_total {ws_stats['push_total']}",
        "# HELP app_ws_deliver_total Total websocket successful deliveries",
        "# TYPE app_ws_deliver_total counter",
        f"app_ws_deliver_total {ws_stats['deliver_total']}",
        "# HELP app_ws_send_fail_total Total websocket send failures",
        "# TYPE app_ws_send_fail_total counter",
        f"app_ws_send_fail_total {ws_stats['send_fail_total']}",
    ]

    # RAG pipeline metrics (prometheus_client registry)
    from app.core.prometheus import get_prometheus_metrics
    rag_metrics = get_prometheus_metrics().decode("utf-8")

    return http_metrics + "\n" + "\n".join(ws_lines) + "\n" + rag_metrics


# OpenTelemetry 链路追踪（通过 ENABLE_TRACING 环境变量启用）
setup_opentelemetry(app)

# 注册API路由
app.include_router(api_router, prefix="/api/v1")

# 静态文件服务
static_dir = "static"
if not os.path.exists(static_dir):
    os.makedirs(static_dir)

try:
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
except Exception as e:
    logger.warning(f"静态文件挂载失败: {e}")

@app.get("/files/{file_path:path}")
async def get_minio_file(file_path: str):
    """
    资源代理：允许浏览器通过后端访问 MinIO 文件
    例如：http://localhost:8000/files/avatars/1.png
    """
    # 路径穿越防护
    import os
    from urllib.parse import unquote
    file_path = unquote(file_path)
    if file_path.startswith("/"):
        raise HTTPException(status_code=400, detail="无效的文件路径")
    normalized = os.path.normpath(file_path)
    if normalized.startswith("..") or ".." in file_path:
        raise HTTPException(status_code=400, detail="无效的文件路径")

    try:
        response = minio_client.get_object(normalized)
        media_type = "application/octet-stream"
        if normalized.endswith(".png"):
            media_type = "image/png"
        elif normalized.endswith((".jpg", ".jpeg")):
            media_type = "image/jpeg"
        elif normalized.endswith(".gif"):
            media_type = "image/gif"
        elif normalized.endswith(".webp"):
            media_type = "image/webp"
        return StreamingResponse(response, media_type=media_type)
    except Exception:
        raise HTTPException(status_code=404)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower()
    )
