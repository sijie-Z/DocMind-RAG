"""
派聪明AI知识库系统 - 主应用入口
"""
import re
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from app.core.minio_client import minio_client
from app.core.config import settings
from app.core.database import init_db, AsyncSessionLocal
from app.core.redis import init_redis
from app.core.elasticsearch import init_elasticsearch, es_client
from app.api.v1.router import api_router
from app.core.logging import setup_logging
from app.core.kafka_client import kafka_producer
from app.core.middleware import PerformanceMiddleware, RateLimitMiddleware, metrics_collector
from app.core.notification_ws import notification_ws_manager

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

# 全局服务实例
auth_service = None
chat_service = None
file_service = None
knowledge_service = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("派聪明AI知识库系统启动中...")
    
    # 初始化服务
    try:
        # 初始化数据库
        await init_db()
        logger.info("数据库连接成功")
        
        # 初始化Redis
        try:
            await init_redis()
        except Exception as e:
            logger.warning(f"Redis初始化失败: {e} - 应用将继续启动")
        
        # 初始化Elasticsearch
        try:
            await init_elasticsearch()
            if es_client is not None:
                logger.info("Elasticsearch连接成功")
        except Exception as e:
            logger.warning(f"Elasticsearch初始化失败: {e} - 应用将继续启动")

        # 初始化Kafka
        try:
            await kafka_producer.start()
            logger.info("Kafka连接成功")
        except Exception as e:
            logger.warning(f"Kafka初始化失败: {e} - 应用将继续启动")
        
        # 初始化服务
        global auth_service, chat_service, file_service, knowledge_service
        from app.services.auth_service import auth_service
        from app.services.chat_service import chat_service
        from app.services.file_service import file_service
        from app.services.knowledge_service import knowledge_service
        from app.services.permission_service import permission_service
        
        # 初始化默认权限和角色
        await permission_service.initialize_default_permissions_and_roles()
        
        if settings.ENABLE_DEMO_ACCOUNT:
            from app.core.ensure_demo_user import ensure_demo_user
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
        asyncio.create_task(metrics_snapshot_task())
        
        logger.info("所有服务初始化完成")
        
    except Exception as e:
        logger.error(f"服务初始化失败: {str(e)}")
        raise
    
    yield
    
    # 关闭服务
    logger.info("派聪明AI知识库系统关闭中...")
    
    # 关闭Kafka Producer
    await kafka_producer.stop()


# 创建FastAPI应用
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="基于DeepSeek的AI知识库系统，支持文档管理、知识检索和智能问答",
    lifespan=lifespan
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 配置GZip压缩（对JSON响应压缩效果显著）
app.add_middleware(GZipMiddleware, minimum_size=1000)

# 配置性能监控中间件
if settings.ENABLE_RATE_LIMIT:
    app.add_middleware(RateLimitMiddleware)
app.add_middleware(PerformanceMiddleware)


# 异常处理
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
    for error in errors:
        field = ".".join(str(loc) for loc in error.get("loc", []))
        msg = error.get("msg", "")
        error_messages.append(f"字段 '{field}' 错误: {msg}")
        
    error_msg = "; ".join(error_messages)
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "success": False,
            "code": status.HTTP_422_UNPROCESSABLE_ENTITY,
            "message": f"参数校验失败: {error_msg}",
            "detail": errors,
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


# 健康检查端点
@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "timestamp": asyncio.get_event_loop().time()
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
    """Prometheus 指标导出"""
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

    return http_metrics + "\n" + "\n".join(ws_lines) + "\n"


# 注册API路由
app.include_router(api_router, prefix="/api/v1")

# 静态文件服务
import os
static_dir = "static"
if not os.path.exists(static_dir):
    os.makedirs(static_dir)

try:
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
except Exception as e:
    logger.warning(f"静态文件挂载失败: {e}")

@app.get("/paicongming/{file_path:path}")
async def get_minio_file(file_path: str):
    """
    ⚡ 资源代理：允许浏览器直接通过后端 8000 端口访问 MinIO 文件
    例如：http://localhost:8000/paicongming/avatars/1.png
    """
    try:
        # file_path 会拿到 "avatars/1.png"
        response = minio_client.get_object(file_path)
        return StreamingResponse(
            response, 
            media_type="image/png" if file_path.endswith(".png") else "image/jpeg"
        )
    except Exception:
        raise HTTPException(status_code=404)

if __name__ == "__main__":
    import uvicorn
    
    # 启动应用
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower()
    )
