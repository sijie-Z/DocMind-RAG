"""
DocMind AI Agent System - Main Application Entry (PER Architecture)
"""
import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from minio.error import S3Error

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import close_db, init_db
from app.core.elasticsearch import es_client, init_elasticsearch
from app.core.kafka_client import kafka_producer
from app.core.logging import setup_logging
from app.core.middleware import (
    PerformanceASGIMiddleware,
    RateLimitASGIMiddleware,
    metrics_collector,
)
from app.core.minio_client import minio_client
from app.core.notification_ws import notification_ws_manager
from app.core.redis import close_redis, init_redis
from app.core.request_id import RequestIDMiddleware
from app.core.security import get_user_org_id, optional_current_user
from app.core.tracing import setup_opentelemetry
from app.exceptions import AppError
from app.models.user import User


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

def _parse_cli_port(argv: list[str]) -> int | None:
    """从命令行参数中提取 ``--port``（支持 ``--port 8000`` 与 ``--port=8000``）。

    重复出现时取最后一次（与 uvicorn 的 click CLI 语义一致）；
    缺值 / 非法值 / 越界一律视为未指定，返回 None。
    """
    port: int | None = None
    for i, arg in enumerate(argv):
        value: str | None = None
        if arg == "--port" and i + 1 < len(argv):
            value = argv[i + 1]
        elif arg.startswith("--port="):
            value = arg.split("=", 1)[1]
        if value is None:
            continue
        try:
            parsed = int(value)
        except ValueError:
            continue
        if 0 < parsed < 65536:
            port = parsed
    return port


def _port_from_env(name: str) -> int | None:
    """读取环境变量中的端口；未设置 / 空串 / 非法值返回 None（不抛异常）。"""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        return None
    return parsed if 0 < parsed < 65536 else None


def resolve_backend_port() -> int:
    """解析本进程实际监听的端口，供 ``.backend-port`` 同步使用（issue #170）。

    优先级（前三级都是「真实端口」来源，最后一级才是配置兜底）：

    1. 命令行 ``--port``：uvicorn 按它绑定。``--reload`` 模式下 lifespan 跑在
       spawn 出的子进程里，但 stdlib ``multiprocessing.spawn`` 会把父进程的
       ``sys.argv`` 传给子进程（``spawn.py`` 的 ``sys_argv``），因此子进程里同样
       能看到 ``--port``；
    2. ``DOCMIND_PORT``：``run.py`` 启动时设置（且同时传 ``--port``），
       作为 argv 不可得时的后备通道；
    3. ``UVICORN_PORT``：uvicorn CLI 的 click ``auto_envvar_prefix="UVICORN"``，
       未传 ``--port`` 时它才是实际绑定端口；
    4. ``settings.PORT``：以上都没有时落回配置值。

    不要把 ``settings.PORT`` 提前 —— 「配置端口 ≠ 实际绑定端口」正是 issue #170
    的根因：README 教的 ``uvicorn ... --port 8000`` 配 ``.env`` 里的 ``PORT=8010``，
    旧实现把 8010 写进文件，前端代理因此打到无人监听的端口。
    """
    port = _parse_cli_port(sys.argv)
    if port is not None:
        return port
    for name in ("DOCMIND_PORT", "UVICORN_PORT"):
        port = _port_from_env(name)
        if port is not None:
            return port
    return int(settings.PORT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info(f"DocMind v{settings.APP_VERSION} starting up...")

    # 自动同步端口到 .backend-port 文件，Vite 代理依赖此文件。
    # 必须写入本进程实际监听的端口（issue #170），而不是配置里的端口。
    actual_port = resolve_backend_port()
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

        # MCP client（动态外部工具，feat/mcp-client）：配置（backend/mcp_servers.json）
        # 驱动连接 enabled server，把 tools/list 注册为 mcp__<server>__<tool>。
        # 任何 server 起不来只 warning，不阻塞主启动；无配置 = 零行为变化。
        try:
            from app.agent.mcp.manager import mcp_manager
            await mcp_manager.initialize()
        except Exception as e:
            logger.warning(f"MCP 初始化失败（不影响启动）: {e}")

        # 演示账号（issue #246）：只做创建/补齐（ensure_demo_user 内部自带日志）。
        # 先前版本在此处以固定口令 "123456" 真登录做"自检"——加固后该口令永不可能正确，
        # 每次启动都会给 login:fail:guest +1；锁死后 AccountLockedError 直接打崩启动。
        # 启动路径不得再产生登录尝试；任何自检都必须不阻塞启动。
        if settings.ENABLE_DEMO_ACCOUNT:
            from app.core.ensure_demo_user import ensure_demo_user
            await ensure_demo_user()

        # 启动指标监控快照任务（issue #242：ENABLE_MONITORING 此前被静默忽略——
        # .env 样例、CI、测试都按预期设过它）。默认 true，行为与接线前一致；
        # 置 false 才不启动。该开关只管后台快照任务：/metrics 端点与
        # PROMETHEUS_ENABLED 均独立，不受影响。
        metrics_task: asyncio.Task | None = None
        if settings.ENABLE_MONITORING:
            metrics_task = asyncio.create_task(metrics_snapshot_task())
        else:
            logger.info("ENABLE_MONITORING=false，指标快照任务不启动")

        logger.info("所有服务初始化完成")

    except Exception as e:
        logger.error(f"服务初始化失败: {str(e)}")
        raise

    yield

    # 关闭服务
    logger.info("DocMind AI 知识库系统关闭中...")

    # 先摘 MCP：注销动态工具 + 关闭常驻子进程/会话（内部自兜异常）
    try:
        from app.agent.mcp.manager import mcp_manager
        await mcp_manager.shutdown()
    except Exception as e:
        logger.warning(f"MCP 关闭失败: {e}")

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
    {"name": "文档管理", "description": "文档解析、向量化、知识库构建（RAG 管线）"},
    {"name": "聊天", "description": "会话管理、消息发送、WebSocket/SSE 流式问答"},
    {"name": "知识库", "description": "知识库 CRUD、检索、统计"},
    {"name": "组织管理", "description": "组织 CRUD、成员管理、角色权限"},
    {"name": "系统监控", "description": "性能指标、健康检查、告警、RAG 质量评估"},
    {"name": "通知管理", "description": "通知 CRUD、已读标记、WebSocket 推送"},
    {"name": "提示词管理", "description": "提示词模板 CRUD"},
    {"name": "操作手册", "description": "系统操作手册管理"},
    {"name": "Agent工作流", "description": "工作流 CRUD、执行、调试"},
    {"name": "Agent记忆", "description": "短期/长期/工作记忆管理"},
    {"name": "智能Agent", "description": "PER Agent 对话、工具调用、技能学习"},
]

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="基于 DeepSeek 的企业级 AI Agent 系统（PER 架构），支持自主推理、工具调用、记忆学习、文档管理、混合检索与流式问答",
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

# 4. GZip compression
app.add_middleware(GZipMiddleware, minimum_size=1000)

# 3. Response format wrapper — disabled: BaseHTTPMiddleware conflicts with
#    SQLAlchemy async sessions. The ASGI metrics/rate-limit middleware below
#    does not have that problem.
if settings.ENABLE_RATE_LIMIT:
    app.add_middleware(RateLimitASGIMiddleware)
app.add_middleware(PerformanceASGIMiddleware)


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
        # RFC 9110：401 MUST 携带 `WWW-Authenticate`。`AuthenticationError` 及其子类
        # 走的是这条 handler，此前同样没有该头（issue #82）。
        headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None,
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
        },
        # 必须透传：RFC 9110 规定 401 MUST 携带 `WWW-Authenticate`。
        # 原先没传这一项，导致 auth_service 里显式设置的 `WWW-Authenticate: Bearer`
        # 全部到不了客户端（issue #82）。
        headers=exc.headers,
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


async def _knowledge_base_health() -> tuple[str, dict]:
    """检查知识库**索引的内容**，而不只是 ES 进程活着。

    `_check_elasticsearch` 查的是集群健康（green/yellow）—— **一个全新的空集群同样
    healthy**，所以它对「索引里一篇都没有」完全沉默。issue #183 就是这个缺口：
    文档表里 10 篇标着 INDEXED、ES 里 0 篇，而 `/health` 五项全绿。

    返回 `(状态, 细节)`。能抓出三种情况：

    1. 索引配置错误（维度不匹配等，见 `get_index_error`）→ `unhealthy`
    2. **有文档声称已索引，但索引里一篇都没有** → `unhealthy` ← 正是 #183 的「假状态」
    3. 索引为空且没有文档声称已索引 → `healthy`（全新部署，合法）

    抽成模块级函数是为了能独立测试 —— 埋在 `/health` 的闭包里就只能整端点测。
    """
    from app.core.elasticsearch import get_elasticsearch, get_index_error

    details: dict = {}

    cfg_err = get_index_error()
    if cfg_err:
        details["index_error"] = cfg_err
        return "unhealthy", details

    client = await get_elasticsearch()
    res = await asyncio.wait_for(
        client.count(index=settings.ELASTICSEARCH_INDEX_NAME), timeout=3
    )
    indexed_chunks = int(res.get("count", 0))
    details["indexed_chunks"] = indexed_chunks

    # 与数据库对账：有多少文档**声称**已索引
    from sqlalchemy import func, select

    # 局部导入是**有意的**（勿"顺手清理"）：测试以 patch("app.core.database.AsyncSessionLocal")
    # 替换会话工厂，需在调用时重新解析；模块级绑定会绕过 patch（见 test_index_visibility）。
    from app.core.database import AsyncSessionLocal
    from app.models.document import Document, DocumentStatus

    async with AsyncSessionLocal() as sess:
        q = await sess.execute(
            select(func.count())
            .select_from(Document)
            .where(Document.status == DocumentStatus.INDEXED)
        )
        claimed = int(q.scalar() or 0)
    details["documents_marked_indexed"] = claimed

    if indexed_chunks == 0 and claimed > 0:
        # 状态在撒谎 —— 这正是「检索恒返回 0 条却没人在意」的根源
        details["mismatch"] = f"{claimed} 篇文档状态为 INDEXED，但索引里一篇都没有"
        return "unhealthy", details

    return "healthy", details


# 健康检查端点 — enhanced with per-service dependency checks
@app.get("/health")
async def health_check():
    """Enhanced health check with per-service dependency status.

    Returns overall status, individual service health, version, and uptime.
    Each service check has a short timeout and does not block the full check
    on a single failure.
    """
    services: dict = {}
    # 知识库检查的细节（索引里有几篇、文档表声称几篇）——
    # 这些数字让「为什么 unhealthy」一眼可见，而不是只给一个状态词（issue #183）
    kb_details: dict = {}

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

    async def _check_knowledge_base():
        """薄封装 —— 真正的判断在模块级 `_knowledge_base_health()` 里（便于单测）。"""
        try:
            status, details = await _knowledge_base_health()
            services["knowledge_base"] = status
            kb_details.update(details)
        except TimeoutError:
            services["knowledge_base"] = "degraded"
        except Exception as e:
            services["knowledge_base"] = "degraded"
            logger.debug("Health check — knowledge_base: %s", e)

    # Run all checks concurrently
    await asyncio.gather(
        _check_db(),
        _check_redis(),
        _check_elasticsearch(),
        _check_minio(),
        _check_kafka(),
        _check_knowledge_base(),
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
        # 仅在有内容时出现，避免给老调用方增加噪音
        **({"knowledge_base": kb_details} if kb_details else {}),
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
async def prometheus_metrics(request: Request):
    """Prometheus 指标导出 — HTTP + WebSocket + RAG 管线指标"""
    # 安全加固：配置了 METRICS_TOKEN 时要求 Bearer 认证，防止内部运行数据泄露
    if settings.METRICS_TOKEN:
        auth_header = request.headers.get("authorization", "")
        expected = f"Bearer {settings.METRICS_TOKEN}"
        if auth_header != expected:
            raise HTTPException(status_code=401, detail="unauthorized")
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
async def get_minio_file(
    file_path: str,
    current_user: User | None = Depends(optional_current_user),
):
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
    normalized = os.path.normpath(file_path).replace("\\", "/")
    if normalized.startswith("..") or ".." in file_path:
        raise HTTPException(status_code=400, detail="无效的文件路径")

    # 头像和演示文件保持公开；其他对象需要登录后才能通过代理访问。
    if not normalized.startswith(("avatars/", "demo/")) and current_user is None:
        raise HTTPException(status_code=401, detail="需要登录")

    # 安全加固：按对象名前缀校验组织归属，防止跨租户读取其他组织的文件。
    # 对象命名规范：documents/{org_id}/{uuid}.ext（file_service 路径）或 {org_id}/{md5}.ext（documents 路径）
    if current_user is not None and not current_user.is_superuser and current_user.role != "admin":
        org_prefix: str | None = None
        if normalized.startswith("documents/"):
            parts = normalized.split("/")
            if len(parts) >= 2:
                org_prefix = parts[1]
        else:
            first_seg = normalized.split("/", 1)[0]
            if first_seg.isdigit():
                org_prefix = first_seg
        if org_prefix is not None:
            # 安全加固（issue #268）：此前 `... or 1` 把无组织用户算作 1 号组织的
            # 阅读者——`documents/1/…` 与 `1/…` 前缀的对象对他直接放行（跨租户读取）。
            # 现在无组织（且非超管）显式 403，绝不按 1 号组织授权。
            user_org = get_user_org_id(current_user)
            if user_org is None or not org_prefix.isdigit() or int(org_prefix) != int(user_org):
                raise HTTPException(status_code=403, detail="无权访问该文件")

    try:
        response = minio_client.get_object(normalized)
    except S3Error as exc:
        # issue #301：此前非 NoSuchKey 的故障（MinIO 宕机/凭证错/NoSuchBucket 等）
        # 也一律伪装 404「文件不存在」，监控面永远只有 4xx。现在只有「对象确实
        # 不存在」才是 404，其余存储故障如实 503 + 日志。
        if exc.code in ("NoSuchKey", "NoSuchObject"):
            raise HTTPException(status_code=404, detail="文件不存在") from exc
        logger.error(
            "MinIO 读取对象失败（存储故障，非对象缺失）: object=%s code=%s message=%s",
            normalized, exc.code, exc.message,
        )
        raise HTTPException(status_code=503, detail="文件存储暂不可用，请稍后重试") from exc
    except Exception as exc:
        logger.error(
            "MinIO 读取对象异常（存储故障）: object=%s %s: %s",
            normalized, type(exc).__name__, exc,
        )
        raise HTTPException(status_code=503, detail="文件存储暂不可用，请稍后重试") from exc

    media_type = "application/octet-stream"
    if normalized.endswith(".png"):
        media_type = "image/png"
    elif normalized.endswith((".jpg", ".jpeg")):
        media_type = "image/jpeg"
    elif normalized.endswith(".gif"):
        media_type = "image/gif"
    elif normalized.endswith(".webp"):
        media_type = "image/webp"
    # 安全加固：非图片对象一律作为附件下载，且禁用 MIME 嗅探，防存储型 XSS
    headers = {"X-Content-Type-Options": "nosniff"}
    if media_type == "application/octet-stream":
        headers["Content-Disposition"] = "attachment"
    return StreamingResponse(response, media_type=media_type, headers=headers)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower()
    )
