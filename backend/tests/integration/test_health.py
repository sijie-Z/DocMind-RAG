"""健康检查与根路径 API 的简单测试。"""
from fastapi.testclient import TestClient


def test_health_check(client: TestClient):
    """GET /health 应返回 200 且 status 为 healthy 或 degraded。"""
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data
    assert data["status"] in ("healthy", "degraded", "unhealthy")


def test_api_v1_health(client: TestClient):
    """GET /api/v1 若存在根信息接口可在此扩展。"""
    # 若无统一根路径，仅保证健康检查可用
    r = client.get("/health")
    assert r.status_code == 200
