"""Knowledge search API 的 metadata_filters 契约（竞品对标 P0）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user


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
