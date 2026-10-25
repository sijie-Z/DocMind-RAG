"""健康检查与根路径 API 的简单测试。

issue #223：`/health` 保留为**恒 200 的详细视图**（诊断用途，不参与编排），
探针拆分为 `/livez`（存活，恒 200）与 `/readyz`（就绪，依赖故障 → 503）。
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


def test_health_check(client: TestClient):
    """GET /health 应返回 200 且 status 为 healthy 或 degraded。"""
    r = client.get("/health")
    assert r.status_code == 200, "/health 是恒 200 的诊断视图（#223）"
    data = r.json()
    assert "status" in data
    assert data["status"] in ("healthy", "degraded", "unhealthy")
    # #223：详细视图额外给出与 /readyz 同口径的就绪位
    assert isinstance(data.get("ready"), bool)


def test_api_v1_health(client: TestClient):
    """GET /api/v1 若存在根信息接口可在此扩展。"""
    # 若无统一根路径，仅保证健康检查可用
    r = client.get("/health")
    assert r.status_code == 200


def test_livez_is_always_200(client: TestClient):
    """存活探针不查依赖：依赖故障应摘流，不该让编排层重启 Pod。"""
    r = client.get("/livez")
    assert r.status_code == 200
    assert r.json()["status"] == "alive"


def _patch_readiness(
    database: str,
    elasticsearch: str,
    knowledge_base: tuple[str, dict],
    redis: str = "healthy",
):
    return (
        patch("app.bootstrap._check_database", new=AsyncMock(return_value=database)),
        patch("app.bootstrap._check_elasticsearch", new=AsyncMock(return_value=elasticsearch)),
        patch("app.bootstrap._check_knowledge_base", new=AsyncMock(return_value=knowledge_base)),
        patch("app.bootstrap._check_redis", new=AsyncMock(return_value=redis)),
    )


def test_readyz_returns_503_when_database_unhealthy(client: TestClient):
    """★ #223：就绪检查必须把依赖故障变成 503 —— 此前两个探针共用恒 200 的 /health。"""
    p_db, p_es, p_kb, p_redis = _patch_readiness("unhealthy", "healthy", ("healthy", {}))
    with p_db, p_es, p_kb, p_redis:
        r = client.get("/readyz")

    assert r.status_code == 503
    assert r.json()["ready"] is False


def test_readyz_returns_503_when_es_client_missing(client: TestClient):
    """ES 客户端不存在（启动初始化失败）不得算就绪；degraded 只告警不摘流。"""
    p_db, p_es, p_kb, p_redis = _patch_readiness("healthy", "disabled", ("degraded", {}))
    with p_db, p_es, p_kb, p_redis:
        r = client.get("/readyz")

    assert r.status_code == 503
    assert r.json()["ready"] is False


def test_readyz_returns_200_when_dependencies_healthy(client: TestClient):
    p_db, p_es, p_kb, p_redis = _patch_readiness("healthy", "healthy", ("healthy", {}))
    with p_db, p_es, p_kb, p_redis:
        r = client.get("/readyz")

    assert r.status_code == 200
    assert r.json()["ready"] is True


def test_readyz_returns_503_when_knowledge_base_unhealthy(client: TestClient):
    """★ #223 缺口二：知识库对账 unhealthy 必须摘流 —— 这是探针拆分的核心收益。"""
    p_db, p_es, p_kb, p_redis = _patch_readiness(
        "healthy", "healthy", ("unhealthy", {"mismatch": "9 篇文档完全检索不到"})
    )
    with p_db, p_es, p_kb, p_redis:
        r = client.get("/readyz")

    assert r.status_code == 503
    body = r.json()
    assert body["ready"] is False
    assert body["knowledge_base"]["mismatch"]


def test_readyz_returns_200_when_knowledge_base_degraded(client: TestClient):
    """KB degraded（如 95%–100% 覆盖率）只告警、不摘流 —— 固定这一刻意的不对称。"""
    p_db, p_es, p_kb, p_redis = _patch_readiness(
        "healthy", "healthy", ("degraded", {"coverage_ratio": 0.99})
    )
    with p_db, p_es, p_kb, p_redis:
        r = client.get("/readyz")

    assert r.status_code == 200
    assert r.json()["ready"] is True


@pytest.mark.parametrize("redis_status", ["disabled", "degraded"])
def test_redis_failure_makes_readyz_and_health_not_ready(client: TestClient, redis_status: str):
    """Redis 是认证黑名单的硬依赖；/health 仍恒 200，但 ready 与 /readyz 一致。"""
    p_db, p_es, p_kb, p_redis = _patch_readiness(
        "healthy", "healthy", ("healthy", {}), redis=redis_status
    )
    with (
        p_db,
        p_es,
        p_kb,
        p_redis,
        patch("app.bootstrap._check_minio", new=AsyncMock(return_value="disabled")),
        patch("app.bootstrap._check_kafka", new=AsyncMock(return_value="disabled")),
    ):
        ready = client.get("/readyz")
        health = client.get("/health")

    assert ready.status_code == 503
    assert ready.json()["ready"] is False
    assert ready.json()["services"]["redis"] == redis_status
    assert health.status_code == 200
    assert health.json()["ready"] is False
    assert health.json()["services"]["redis"] == redis_status


def test_redis_ping_timeout_makes_readyz_and_health_not_ready(client: TestClient):
    """真实 Redis 检查的超时路径折算为 degraded，不能漏掉 readiness。"""
    p_db, p_es, p_kb, _ = _patch_readiness("healthy", "healthy", ("healthy", {}))
    redis_client = AsyncMock()
    redis_client.ping.side_effect = TimeoutError("simulated Redis timeout")
    with (
        p_db,
        p_es,
        p_kb,
        patch("app.core.redis.redis_client", new=redis_client),
        patch("app.bootstrap._check_minio", new=AsyncMock(return_value="disabled")),
        patch("app.bootstrap._check_kafka", new=AsyncMock(return_value="disabled")),
    ):
        ready = client.get("/readyz")
        health = client.get("/health")

    assert redis_client.ping.await_count == 2
    assert ready.status_code == 503
    assert ready.json()["ready"] is False
    assert ready.json()["services"]["redis"] == "degraded"
    assert health.status_code == 200
    assert health.json()["ready"] is False
    assert health.json()["services"]["redis"] == "degraded"
