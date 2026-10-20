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
from app.core.request_id import RequestIDMiddleware, request_id_var
from app.core.security import get_user_org_id, is_platform_admin, optional_current_user
from app.core.system_metrics import system_metrics_sampler
from app.core.tracing import setup_opentelemetry
from app.exceptions import AppError
from app.models.user import User


async def metrics_snapshot_task():
    """定期记录性能指标快照（应用指标 + 系统指标）"""
    while True:
        try:
            await metrics_collector.take_snapshot()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Failed to take metrics snapshot: {e}")
        try:
            # 系统指标采样（round2/sampling）：CPU%/内存% → dashboard trends.system
            # 与 /monitoring/metrics/system 的数据源。与应用快照各自独立兜异常，
            # 一侧失败不吞另一侧；无 Redis 时仅内存环形缓冲。
            await system_metrics_sampler.record_sample()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Failed to record system metrics sample: {e}")
        await asyncio.sleep(max(5, int(settings.MONITORING_INTERVAL)))


async def scheduled_tasks_loop():
    """进程内定时任务调度循环（ENABLE_SCHEDULER 门控，见 lifespan）。

    循环体 = services/scheduler/engine.SchedulerEngine.run_loop（tick 内部
    自带异常隔离，单次失败不终止调度器）；关闭时由 lifespan cancel，
    在途运行随事件循环终止（其 agent_tasks 行保持 RUNNING，可经 /agent 恢复）。
    """
    from app.services.scheduler.engine import scheduler_engine

    await scheduler_engine.run_loop()

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

        # A3-P0：本机处理任务启动对账——上次进程退出时未完成的本机任务
        # （local_fallback=true 且非终局）在此重新调度，避免永久 PENDING/PARSING。
        # 对账失败只告警，不阻塞启动（任务行仍在，下次启动继续回收）。
        try:
            from app.services.local_processing import reconcile_local_jobs
            recovered = await reconcile_local_jobs()
            if recovered:
                logger.info("本机处理任务启动对账：已重新调度 %s 个任务", recovered)
        except Exception as e:
            logger.warning(f"本机处理任务对账失败（不影响启动）: {e}")

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

        # 启动定时任务调度器（ENABLE_SCHEDULER 门控，模式同 ENABLE_MONITORING）：
        # 只控制进程内调度循环；/api/v1/scheduled-tasks 的 CRUD 与 run-now 独立。
        # 单实例语义：多 worker 部署会各跑一份调度（已知边界，需外部调度器）。
        scheduler_task: asyncio.Task | None = None
        if settings.ENABLE_SCHEDULER:
            scheduler_task = asyncio.create_task(scheduled_tasks_loop())
        else:
            logger.info("ENABLE_SCHEDULER=false，定时任务调度器不启动")

        logger.info("所有服务初始化完成")

    except Exception as e:
        logger.error(f"服务初始化失败: {str(e)}")
        raise

    yield

    # 关闭服务
    logger.info("DocMind AI知识库系统关闭中...")

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

    # 取消定时任务调度器（在途运行随事件循环终止；其任务行保持 RUNNING 可恢复）
    try:
        if scheduler_task:
            scheduler_task.cancel()
            logger.info("定时任务调度器已取消")
    except Exception as e:
        logger.warning(f"取消定时任务调度器失败: {e}")

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
    {"name": "定时任务", "description": "定时 agent 任务的 CRUD、暂停/恢复、立即触发"},
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

# 1. Request ID — registered first ⇒ innermost of this stack (Starlette 的
#    add_middleware 插到列表头部、构建时反向包裹，最后注册的最外层)。因此它
#    在 CORS 之后执行，下游代码都能拿到 request.state.request_id 与
#    get_request_id()。
app.add_middleware(RequestIDMiddleware)

# 2. CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

# 3. GZip compression
app.add_middleware(GZipMiddleware, minimum_size=1000)

# 4. Rate-limit + performance metrics (pure ASGI — no BaseHTTPMiddleware).
#    Deliberately no global success-envelope middleware (T11 / 方案 B): success
#    payloads keep their per-endpoint shapes (bare / {data} / {data:{data}}),
#    the frontend unwraps tolerantly (utils/apiUnwrap.ts), and error paths use
#    the unified envelope from the exception handlers below. The old wrapper
#    (app/core/response_middleware.py) was never registered and could not have
#    worked — responses from call_next expose no `.body`, so it always passed
#    through unchanged — and is removed as dead code.
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

    # 这条 handler 由 ServerErrorMiddleware（RequestIDMiddleware 外层）调用：
    # contextvar 此时已被 reset，`get_request_id()` 在这里返回 None。为了 5xx
    # 日志也能关联请求，只用 state 里的 id 临时绑定，写完立即复位。
    token = request_id_var.set(request_id) if request_id else None
    try:
        logger.error(f"未处理的异常: {err_msg}\n{traceback.format_exc()}")
    finally:
        if token is not None:
            request_id_var.reset(token)

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "code": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "message": "服务器内部错误，请稍后重试或联系管理员",
            "detail": err_msg if settings.EXPOSE_EXCEPTION_DETAIL else "internal_server_error",
            "request_id": request_id,
            "data": None
        },
        # 该响应由最外层的 ServerErrorMiddleware 直接发出，不经过
        # RequestIDMiddleware 的 send_wrapper，故这里显式补响应头。
        headers={"X-Request-ID": request_id} if request_id else None,
    )


# ── Application start time for health check uptime ──────────────
_APP_START_TIME: float = time.time()

# 知识库对账阈值（issue #223）：索引 chunk 覆盖率低于该值判 unhealthy；
# [阈值, 1.0) 判 degraded（部分 chunk 缺失，尚可服务但需告警）。
_KB_COVERAGE_THRESHOLD: float = 0.95

# ES cardinality 在 precision_threshold（见下方查询，取 ES 允许的最大值 40000）
# 以内近似精确；超过后是 HLL 估计值（p=16，标准误约 0.4%）。大库按 2%（约 5σ）
# 容差判断「文档数变少」，且估计值对同一索引快照是确定性的 —— 容差放窄会让
# 一次 bad draw 持续摘流直到索引数据变化，而不是靠 failureThreshold 自愈。
_KB_DOC_CARDINALITY_EXACT_LIMIT: int = 40000
_KB_DOC_COUNT_TOLERANCE: float = 0.98


async def _knowledge_base_health() -> tuple[str, dict]:
    """检查知识库**索引的内容**，而不只是 ES 进程活着。

    `_check_elasticsearch` 查的是集群健康（green/yellow）—— **一个全新的空集群同样
    healthy**，所以它对「索引里一篇都没有」完全沉默。issue #183 就是这个缺口：
    文档表里 10 篇标着 INDEXED、ES 里 0 篇，而 `/health` 五项全绿。

    issue #223 起按**内容对账**，能抓出四类不一致：

    1. 索引配置错误（维度不匹配等，见 `get_index_error`）→ `unhealthy`
    2. 有文档声称已索引，但索引里一条 chunk 都没有 → `unhealthy`（#183 的「假状态」）
    3. **部分丢失**：索引里出现的去重 document_id 数 < 文档表声称 INDEXED 的文档数
       → `unhealthy`（旧口径 `indexed_chunks == 0` 对「10 篇只进 1 篇」完全沉默）
    4. **chunk 覆盖率不足**：ES chunk 总数 / 文档表 `SUM(chunk_count)` < 0.95
       → `unhealthy`；0.95–1.0 → `degraded`（可服务，但要告警）

    索引为空且没有文档声称已索引 → `healthy`（全新部署，合法）。

    对账基线**排除有未终局处理任务（QUEUED/PROCESSING）的文档**：重建接口会先删
    ES 旧 chunk、但让文档保持 INDEXED 直到 worker 接单（见 `/knowledge/rebuild`
    与 worker 的 PARSING 接管）。不排除的话，一次正常的单文档重建就会让所有副本
    `/readyz` 摘流（worker 积压时窗口无上界）。

    `details` 暴露 `indexed_chunks` / `indexed_documents` / `documents_marked_indexed` /
    `expected_chunks` / `coverage_ratio` / `document_coverage_ratio` / `mismatch`；
    索引中多出的陈旧/在途文档与 chunk（`extra_documents` / `extra_chunks`）会虚增
    统计、可能掩盖部分丢失，一并暴露便于诊断。

    抽成模块级函数是为了能独立测试 —— 埋在 `/health` 的闭包里就只能整端点测。
    """
    from app.core.elasticsearch import get_elasticsearch, get_index_error

    details: dict = {}

    cfg_err = get_index_error()
    if cfg_err:
        details["index_error"] = cfg_err
        return "unhealthy", details

    # 一次 ES 查询同时取 chunk 总数与去重 document_id 数。只看 count 不够 ——
    # 1 条 chunk 也能让「10 篇声称 INDEXED」看起来有数据（#223）。
    client = await get_elasticsearch()
    res = await asyncio.wait_for(
        client.search(
            index=settings.ELASTICSEARCH_INDEX_NAME,
            size=0,
            track_total_hits=True,
            aggs={
                "documents": {
                    # 精度阈值设高：对账要的是「文档数是否变少」，近似误差会误报。
                    "cardinality": {
                        "field": "document_id",
                        "precision_threshold": _KB_DOC_CARDINALITY_EXACT_LIMIT,
                    }
                }
            },
        ),
        timeout=3,
    )
    hits_total = (res.get("hits") or {}).get("total") or {}
    documents_agg = (res.get("aggregations") or {}).get("documents") or {}
    indexed_chunks = int(hits_total.get("value") or 0)
    indexed_docs = int(documents_agg.get("value") or 0)
    details["indexed_chunks"] = indexed_chunks
    details["indexed_documents"] = indexed_docs

    # 与数据库对账：有多少文档**声称**已索引、按 chunk_count 应有多少 chunk。
    # 有未终局处理任务的文档从基线中排除（重建窗口：ES chunk 已删、状态仍 INDEXED，
    # 直到 worker 接单才变 PARSING —— 见 `/knowledge/rebuild`）。
    from sqlalchemy import func, select

    # 局部导入是**有意的**（勿"顺手清理"）：测试以 patch("app.core.database.AsyncSessionLocal")
    # 替换会话工厂，需在调用时重新解析；模块级绑定会绕过 patch（见 test_index_visibility）。
    from app.core.database import AsyncSessionLocal
    from app.models.document import Document, DocumentStatus
    from app.models.knowledge_job import KnowledgeJobStatus, KnowledgeProcessingJob

    # 只排除**最新**处理任务未终局的文档：更早的 task 可能永远停在 QUEUED
    # （STALE_RUN 不回写旧 job），若按「存在任一未终局 job」排除，该文档会永久
    # 退出对账基线，后续真实丢失就再也发现不了。
    latest_job_id = (
        select(func.max(KnowledgeProcessingJob.id))
        .where(KnowledgeProcessingJob.document_id == Document.id)
        .correlate(Document)
        .scalar_subquery()
    )
    active_job = (
        select(KnowledgeProcessingJob.id)
        .where(
            KnowledgeProcessingJob.id == latest_job_id,
            KnowledgeProcessingJob.status.in_(
                [KnowledgeJobStatus.QUEUED, KnowledgeJobStatus.PROCESSING]
            ),
        )
        .exists()
    )

    async with AsyncSessionLocal() as sess:
        q = await sess.execute(
            select(
                func.count(),
                func.coalesce(func.sum(Document.chunk_count), 0),
            )
            .select_from(Document)
            .where(Document.status == DocumentStatus.INDEXED)
            .where(~active_job)
        )
        claimed, expected_chunks = q.one()
    claimed = int(claimed or 0)
    expected_chunks = int(expected_chunks or 0)
    details["documents_marked_indexed"] = claimed
    details["expected_chunks"] = expected_chunks
    # 索引中不属于对账基线的多余文档/chunk（失败任务残留、在途文档、陈旧数据）
    # 会虚增统计并可能掩盖部分丢失 —— 暴露出来，便于定位「为何覆盖率看起来是满的」。
    details["extra_documents"] = max(0, indexed_docs - claimed)
    if expected_chunks > 0:
        details["extra_chunks"] = max(0, indexed_chunks - expected_chunks)

    if claimed == 0:
        # 全新部署（或索引里只有未被声称的陈旧数据）：没有「状态 vs 现实」矛盾。
        return "healthy", details

    if indexed_chunks == 0:
        # 状态在撒谎 —— 这正是「检索恒返回 0 条却没人在意」的根源（#183）
        details["mismatch"] = f"{claimed} 篇文档状态为 INDEXED，但索引里一篇都没有"
        return "unhealthy", details

    # 按 document_id 去重数对账：部分文档整篇丢失时，chunk 总数可能仍然"够"（#223）。
    # cardinality 在 precision_threshold 以内近似精确；超过后允许 2% 的 HLL 估计误差。
    # 用 max(claimed, indexed_docs) 判定是否进入近似区间 —— 多余/在途文档可能让
    # 真实基数超过阈值，而 claimed 本身还在阈值以内。
    doc_coverage_ratio = min(indexed_docs / claimed, 1.0)
    details["document_coverage_ratio"] = round(doc_coverage_ratio, 4)
    if max(claimed, indexed_docs) > _KB_DOC_CARDINALITY_EXACT_LIMIT:
        doc_shortfall = indexed_docs < claimed * _KB_DOC_COUNT_TOLERANCE
    else:
        doc_shortfall = indexed_docs < claimed

    if doc_shortfall:
        details["mismatch"] = (
            f"{claimed} 篇文档状态为 INDEXED，索引中只找到 {indexed_docs} 篇的 chunk —— "
            f"{claimed - indexed_docs} 篇文档完全检索不到"
        )
        return "unhealthy", details

    # chunk 覆盖率：ES chunk 总数 / 文档表 chunk_count 之和。
    # 历史数据未回填 chunk_count（expected_chunks == 0）时退回文档覆盖率。
    if expected_chunks > 0:
        coverage_ratio = indexed_chunks / expected_chunks
    else:
        coverage_ratio = min(indexed_docs / claimed, 1.0)
    details["coverage_ratio"] = round(coverage_ratio, 4)

    if coverage_ratio < _KB_COVERAGE_THRESHOLD:
        details["mismatch"] = (
            f"索引 chunk 覆盖率 {coverage_ratio:.1%} 低于阈值 {_KB_COVERAGE_THRESHOLD:.0%}"
            f"（文档表声称 {expected_chunks} 个 chunk，索引中 {indexed_chunks} 个）"
        )
        return "unhealthy", details

    if coverage_ratio < 1.0:
        details["mismatch"] = (
            f"索引 chunk 覆盖率 {coverage_ratio:.1%}"
            f"（文档表声称 {expected_chunks} 个 chunk，索引中 {indexed_chunks} 个）—— "
            f"部分 chunk 未入索引"
        )
        return "degraded", details

    return "healthy", details


# ── 探针检查项（issue #223：从 /health 闭包里抽出来，供 /health 与 /readyz 复用）──
# 每个函数返回状态字符串；知识库检查额外返回细节 dict。
# 函数内**局部导入**依赖是有意的：测试通过 patch("app.core.database.AsyncSessionLocal")
# 等目标替换全局对象，模块级绑定会绕过 patch。
async def _check_database() -> str:
    try:
        from app.core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as sess:
            from sqlalchemy import text
            await sess.execute(text("SELECT 1"))
        return "healthy"
    except Exception as e:
        logger.warning("Health check — database: %s", e)
        return "unhealthy"


async def _check_redis() -> str:
    try:
        from app.core.redis import redis_client as _rc
        if _rc:
            await asyncio.wait_for(_rc.ping(), timeout=2)
            return "healthy"
        return "disabled"
    except TimeoutError:
        return "degraded"
    except Exception as e:
        logger.debug("Health check — redis: %s", e)
        return "degraded"


async def _check_elasticsearch() -> str:
    try:
        from app.core.elasticsearch import es_client as _ec
        if _ec:
            health = await asyncio.wait_for(_ec.cluster.health(), timeout=3)
            es_status = health.get("status", "unknown")
            return "healthy" if es_status in ("green", "yellow") else "unhealthy"
        return "disabled"
    except TimeoutError:
        return "degraded"
    except Exception as e:
        logger.debug("Health check — elasticsearch: %s", e)
        return "degraded"


async def _check_minio() -> str:
    try:
        from app.core.minio_client import minio_client as _mc
        if _mc and _mc.client:
            bucket = _mc.bucket_name
            exists = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, _mc.client.bucket_exists, bucket),
                timeout=3,
            )
            return "healthy" if exists else "degraded"
        return "disabled"
    except TimeoutError:
        return "degraded"
    except Exception as e:
        logger.debug("Health check — minio: %s", e)
        return "degraded"


async def _check_kafka() -> str:
    try:
        from app.core.kafka_client import kafka_producer as _kp
        if _kp and _kp.producer:
            # Kafka producer doesn't have a great liveness check without sending,
            # just report if it was started successfully
            return "healthy"
        return "disabled"
    except Exception as e:
        logger.debug("Health check — kafka: %s", e)
        return "degraded"


async def _check_knowledge_base() -> tuple[str, dict]:
    """薄封装 —— 真正的判断在模块级 `_knowledge_base_health()` 里（便于单测）。"""
    from elasticsearch import ApiError, NotFoundError

    try:
        return await _knowledge_base_health()
    except TimeoutError:
        # 瞬时超时：保留告警语义（/health degraded），不因此摘流。
        return "degraded", {}
    except NotFoundError as e:
        # 索引被带外删除 / 别名丢失：不是抖动，检索已完全不可用 → unhealthy。
        # 若吞成 degraded，/readyz 会在检索全挂时仍返回 200（fail-open）。
        return "unhealthy", {"index_error": f"知识库索引不存在或不可读: {e}"}
    except ApiError as e:
        # 确定性客户端错误（mapping 400、鉴权 401/403、索引 404）不是抖动，
        # 同样 fail-closed；429/5xx 等瞬时错误留给 degraded。
        if e.status_code in (400, 401, 403, 404):
            return "unhealthy", {
                "index_error": f"知识库索引查询失败（HTTP {e.status_code}）: {e}"
            }
        logger.debug("Health check — knowledge_base: %s", e)
        return "degraded", {}
    except Exception as e:
        logger.debug("Health check — knowledge_base: %s", e)
        return "degraded", {}


async def _collect_checks(checks: dict) -> tuple[dict, dict]:
    """并发执行一组检查并归一化结果（异常不拖垮整个端点）。

    检查返回 ``str`` 或 ``(str, dict)``；意外异常按 fail-closed 记为 ``unhealthy``。
    """
    names = list(checks)
    results = await asyncio.gather(*checks.values(), return_exceptions=True)
    services: dict[str, str] = {}
    details: dict = {}
    for name, result in zip(names, results, strict=True):
        if isinstance(result, BaseException):
            services[name] = "unhealthy"
            logger.warning("Health check — %s: %s", name, result)
        elif isinstance(result, tuple):
            status, extra = result
            services[name] = status
            details.update(extra or {})
        else:
            services[name] = result
    return services, details


async def _run_health_checks() -> tuple[dict, dict]:
    """`/health` 的六项依赖检查（含 redis/minio/kafka 等非关键项）。"""
    return await _collect_checks({
        "database": _check_database(),
        "redis": _check_redis(),
        "elasticsearch": _check_elasticsearch(),
        "minio": _check_minio(),
        "kafka": _check_kafka(),
        "knowledge_base": _check_knowledge_base(),
    })


async def _run_readiness_checks() -> tuple[dict, dict]:
    """`/readyz` 的三项就绪检查：DB SELECT 1 + ES 集群健康 + 知识库对账。"""
    return await _collect_checks({
        "database": _check_database(),
        "elasticsearch": _check_elasticsearch(),
        "knowledge_base": _check_knowledge_base(),
    })


def _is_ready(services: dict) -> bool:
    """就绪口径（issue #223）：DB 与 ES 必须 healthy，知识库对账不得为 unhealthy。

    ES 的 ``disabled``（客户端不存在）与 ``degraded``（连接失败/超时）都视为不可用 ——
    检索依赖 ES，不能把流量导给一个搜不到东西的实例。
    KB 的 ``degraded``（如 95%–100% 的 chunk 覆盖率）只告警、不摘流：索引抖动时
    整批 Pod 被摘流的代价大于少量 chunk 暂时缺失。
    """
    if services.get("database") != "healthy":
        return False
    if services.get("elasticsearch") != "healthy":
        return False
    return services.get("knowledge_base") != "unhealthy"


def _overall_status(services: dict) -> str:
    """Overall status: all healthy -> healthy, any unhealthy -> unhealthy, else degraded."""
    status_values = list(services.values())
    if all(v == "healthy" for v in status_values):
        return "healthy"
    if any(v == "unhealthy" for v in status_values):
        return "unhealthy"
    return "degraded"


# 健康检查端点 — enhanced with per-service dependency checks
@app.get("/health")
async def health_check():
    """Enhanced health check with per-service dependency status.

    **诊断视图，恒 200** —— 编排探针请用 `/livez`（存活）与 `/readyz`（就绪）：
    让 200/503 决定摘流，而不是让「能返回一份 JSON」被误当成「一切正常」（#223）。

    Returns overall status, readiness flag, individual service health, version,
    and uptime. Each service check has a short timeout and does not block the
    full check on a single failure.
    """
    services, kb_details = await _run_health_checks()

    uptime = time.time() - _APP_START_TIME if _APP_START_TIME > 0 else 0

    return {
        "status": _overall_status(services),
        # 与 /readyz 同口径；老调用方（前端 api/health.ts）继续读 status/version。
        "ready": _is_ready(services),
        "version": settings.APP_VERSION,
        "services": services,
        "uptime_seconds": round(uptime, 1),
        # 仅在有内容时出现，避免给老调用方增加噪音
        **({"knowledge_base": kb_details} if kb_details else {}),
    }


@app.get("/livez")
async def liveness_check():
    """K8s liveness：只证明进程/事件循环还活着，**恒 200**。

    不要在这里检查 DB/ES —— 依赖故障应摘流（readiness），而不是重启 Pod（#223）。
    """
    return {"status": "alive"}


@app.get("/readyz")
async def readiness_check():
    """K8s readiness / compose healthcheck：依赖不可用 → 503。

    检查 DB `SELECT 1`、ES 集群健康与知识库索引对账（与 `/health` 同一套收集函数）。
    只有明确的不可用（unhealthy / disabled / degraded 的 ES，见 `_is_ready`）才摘流；
    知识库的 degraded（≥95% 覆盖率的部分缺失）只告警。
    """
    services, kb_details = await _run_readiness_checks()
    ready = _is_ready(services)
    payload = {
        "ready": ready,
        "status": "ready" if ready else "not_ready",
        "services": services,
        **({"knowledge_base": kb_details} if kb_details else {}),
    }
    return JSONResponse(status_code=200 if ready else 503, content=payload)


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
    # W0-1：跨组织文件旁路只留给显式平台管理员（is_superuser）；组织管理员与
    # users.role='admin' 一律按本组织前缀校验。
    if current_user is not None and not is_platform_admin(current_user):
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
