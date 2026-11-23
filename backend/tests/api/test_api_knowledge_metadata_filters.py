"""Knowledge search API 的 metadata_filters 契约（竞品对标 P0）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def _override_auth(app):
    user = MagicMock()
    user.id = 1
    user.is_superuser = True
    user.is_active = True

    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=10)))

    async def _db():
        yield db

    async def _user():
        return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user


@pytest.mark.parametrize("blank_kb_id", ["", "   ", "\t\n"])
def test_blank_knowledge_base_id_is_422(client: TestClient, blank_kb_id: str):
    """空白 KB id 必须在入口拒绝，且错误定位到 body.knowledge_base_id。

    变异点：删掉 schema 的 strip/blank 校验 → 端点会把空白 id 当成「不限定」
    全组织检索，本条红。
    """
    from app.main import app

    _override_auth(app)
    try:
        with patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks:
            mock_ks.search_knowledge_with_meta = AsyncMock(return_value=([], None))
            response = client.post(
                "/api/v1/knowledge/search",
                json={
                    "query": "合同",
                    "organization_id": 1,
                    "knowledge_base_id": blank_kb_id,
                },
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert any(error["loc"][:2] == ["body", "knowledge_base_id"] for error in detail), detail
    mock_ks.search_knowledge_with_meta.assert_not_awaited()


def test_metadata_filters_reach_service(client: TestClient):
    from app.main import app

    _override_auth(app)
    try:
        with patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks:
            mock_ks.search_knowledge_with_meta = AsyncMock(return_value=([], None))
            response = client.post(
                "/api/v1/knowledge/search",
                json={
                    "query": "合同",
                    "organization_id": 1,
                    "metadata_filters": {"tags": ["2024", "secret"], "file_type": "pdf"},
                },
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    kwargs = mock_ks.search_knowledge_with_meta.await_args.kwargs
    assert kwargs["metadata_filters"] == {
        "tags": ["2024", "secret"],
        "file_type": ["pdf"],
    }


def test_kb_id_filters_and_document_ids_are_forwarded_exactly(client: TestClient):
    """KB id + 元数据过滤 + 显式文档范围一起下传，viewer 来自认证用户。

    变异点：端点丢掉 knowledge_base_id / metadata_filters / document_ids 任一
    kwargs，或服务调用写死 viewer_user_id → 本条红（断言完整 kwargs）。
    """
    from app.main import app

    _override_auth(app)
    try:
        with (
            patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks,
            patch(
                "app.api.v1.endpoints.knowledge._diagnose_empty_results",
                new=AsyncMock(return_value=("ok", 3)),
            ),
            patch(
                "app.api.v1.endpoints.knowledge._diagnose_document_scope",
                new=AsyncMock(return_value=[]),
            ),
        ):
            mock_ks.search_knowledge_with_meta = AsyncMock(return_value=([], None))
            response = client.post(
                "/api/v1/knowledge/search",
                json={
                    "query": "合同",
                    "organization_id": 1,
                    "top_k": 7,
                    "knowledge_base_id": "kb-1",
                    "metadata_filters": {"tags": ["2024"], "file_type": "pdf"},
                    "document_ids": ["doc-1", "doc-2"],
                },
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200, response.text
    mock_ks.search_knowledge_with_meta.assert_awaited_once()
    assert mock_ks.search_knowledge_with_meta.await_args.kwargs == {
        "query": "合同",
        "organization_id": 1,
        "top_k": 7,
        "search_mode": None,
        "document_ids": ["doc-1", "doc-2"],
        "metadata_filters": {"tags": ["2024"], "file_type": ["pdf"]},
        "knowledge_base_id": "kb-1",
        "debug": False,
        "viewer_user_id": 1,
    }


def test_invalid_metadata_field_is_422(client: TestClient):
    from app.main import app

    _override_auth(app)
    try:
        with patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks:
            mock_ks.search_knowledge_with_meta = AsyncMock(return_value=([], None))
            response = client.post(
                "/api/v1/knowledge/search",
                json={
                    "query": "合同",
                    "organization_id": 1,
                    "metadata_filters": {"content": "x"},
                },
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422
    mock_ks.search_knowledge_with_meta.assert_not_awaited()
