"""issue #292 API 侧：/knowledge/search 检索后端故障 → 502 + RETRIEVAL_UNAVAILABLE。

修前：ES/embedding 故障被 `KnowledgeService.search_knowledge` 吞成 `[]`，
端点返回 200 + 空数组（"服务坏了" 呈现为 "没搜到"）。修后：如实 502。

链路取真实的两层（端点 → KnowledgeService → RagServiceFacade），只替换最底层
facade 的 `search_knowledge_base` 抛结构化故障 —— 钉住「层层不吞」。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user
from app.rag.errors import RetrievalUnavailableError


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def _make_mock_user():
    from app.models.user import User  # noqa: F401

    user = MagicMock()
    user.id = 1
    user.username = "testuser"
    user.email = "test@example.com"
    user.full_name = "Test User"
    user.role = "user"
    user.organization_id = 1
    user.is_superuser = True
    user.is_active = True
    return user


def _override_get_db():
    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.close = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock())
    mock_db.get = AsyncMock(return_value=_make_mock_user())

    async def _override():
        yield mock_db
        await mock_db.close()

    return _override


def test_search_retrieval_unavailable_maps_to_502_not_empty_200(client: TestClient):
    """检索故障 → 502 + error_code=RETRIEVAL_UNAVAILABLE（不是 200 + results: []）。

    变异验证：去掉端点/服务层的 RetrievalUnavailableError 分支（吞成 []）→ 红。
    """
    from app.main import app

    async def _override_user():
        return _make_mock_user()

    app.dependency_overrides[get_db] = _override_get_db()
    app.dependency_overrides[get_current_user] = _override_user
    try:
        with patch(
            "app.services.rag_service.RagServiceFacade.search_knowledge_base",
            new=AsyncMock(
                side_effect=RetrievalUnavailableError("ES connection refused", kind="failed")
            ),
        ):
            r = client.post(
                "/api/v1/knowledge/search",
                json={"query": "报销流程", "top_k": 5, "organization_id": 1},
                headers={"Authorization": "Bearer test_token"},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert r.status_code == 502, f"故障必须如实 502，实际 {r.status_code}: {r.text[:300]}"
    body = r.json()
    assert body["error_code"] == "RETRIEVAL_UNAVAILABLE"
    assert body["detail"] == {"kind": "failed"}
    assert body.get("results") is None, "不得以空结果形态伪装成功"


def test_search_timeout_kind_is_preserved_in_error_detail(client: TestClient):
    """三态区分穿透到 API 层：超时的 kind 保留在 error detail（可观测/可归因）。"""
    from app.main import app

    async def _override_user():
        return _make_mock_user()

    app.dependency_overrides[get_db] = _override_get_db()
    app.dependency_overrides[get_current_user] = _override_user
    try:
        with patch(
            "app.services.rag_service.RagServiceFacade.search_knowledge_base",
            new=AsyncMock(
                side_effect=RetrievalUnavailableError("read timeout", kind="timeout")
            ),
        ):
            r = client.post(
                "/api/v1/knowledge/search",
                json={"query": "报销流程", "top_k": 5, "organization_id": 1},
                headers={"Authorization": "Bearer test_token"},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert r.status_code == 502
    assert r.json()["detail"] == {"kind": "timeout"}
