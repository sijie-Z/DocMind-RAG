"""DocMind AI Agent System - Main Application Entry (compatibility shell).

issue #479 起，FastAPI 组装、middleware、健康检查与路由挂载全部位于
:mod:`app.bootstrap`；本模块保留为兼容入口：

- ``uvicorn app.main:app``（Dockerfile CMD / 现有文档命令）继续可用；
- ``python -m app.main`` 也继续可用；
- 现有接线测试通过 ``monkeypatch.setattr(app.main, "<name>", ...)`` 替换
  ``init_db`` / ``close_db`` / ``es_client`` / ``kafka_producer`` /
  ``metrics_snapshot_task`` / ``scheduled_tasks_loop`` 等进程级依赖；
  :func:`lifespan` 在进入时按本模块的**当前绑定**生成依赖快照，语义不变。

新部署入口请使用 ``python -m app --profile web``（见 app/entrypoints/）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from app.bootstrap import (
    LifespanDeps,
    _check_database,  # noqa: F401 - 健康检查兼容导出
    _check_elasticsearch,  # noqa: F401 - 健康检查兼容导出
    _knowledge_base_health,  # noqa: F401 - test_index_visibility 兼容导出
    _parse_cli_port,  # noqa: F401 - test_backend_port_resolution 兼容导出
    _port_from_env,  # noqa: F401 - test_backend_port_resolution 兼容导出
    _run_health_checks,  # noqa: F401 - 健康检查兼容导出
    _run_readiness_checks,  # noqa: F401 - 健康检查兼容导出
    app_error_handler,  # noqa: F401 - test_auth_failure_contract 兼容导出
    build_app,
    build_lifespan,
    es_client,
    general_exception_handler,  # noqa: F401 - 兼容导出
    http_exception_handler,  # noqa: F401 - test_auth_failure_contract 兼容导出
    kafka_producer,
    metrics_collector,  # noqa: F401 - test_system_metrics_sampling 兼容导出
    metrics_snapshot_task,
    minio_client,  # noqa: F401 - test_issue268_or1_family 兼容导出
    resolve_backend_port,
    scheduled_tasks_loop,
    system_metrics_sampler,  # noqa: F401 - test_system_metrics_sampling 兼容导出
    validation_exception_handler,  # noqa: F401 - 兼容导出
)
from app.bootstrap import (
    _check_knowledge_base as _bootstrap_check_knowledge_base,
)
from app.core.config import settings
from app.core.database import close_db, init_db
from app.core.elasticsearch import init_elasticsearch
from app.core.logging import setup_logging
from app.core.redis import close_redis, init_redis

setup_logging()


async def _check_knowledge_base() -> tuple[str, dict]:
    """兼容壳：测试会 patch ``app.main._knowledge_base_health`` 再调用本函数。

    把本模块当前的 ``_knowledge_base_health`` 绑定显式传给 bootstrap 实现，
    使 monkeypatch 语义与实现搬家前一致。
    """
    return await _bootstrap_check_knowledge_base(_knowledge_base_health)


@asynccontextmanager
async def lifespan(app: Any) -> AsyncIterator[None]:
    """兼容壳：按调用时的模块级绑定生成依赖快照后进入 bootstrap lifespan。

    快照必须在进入时读取（而不是 import 时），否则
    ``monkeypatch.setattr(app.main, "<name>", ...)`` 对启动/关闭接线不生效。
    """
    deps = LifespanDeps(
        init_db=init_db,
        init_redis=init_redis,
        init_elasticsearch=init_elasticsearch,
        close_redis=close_redis,
        close_db=close_db,
        kafka_producer=kafka_producer,
        es_client=es_client,
        resolve_backend_port=resolve_backend_port,
        metrics_snapshot_task=metrics_snapshot_task,
        scheduled_tasks_loop=scheduled_tasks_loop,
    )
    async with build_lifespan(deps)(app):
        yield


app = build_app(lifespan_override=lifespan)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
