"""Knowledge Base API 集成测试 — 知识库查询与统计。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


class _AsyncCtxMgr:
    """支持 async with 的 mock 上下文管理器。"""
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def _make_mock_user():
    user = MagicMock()
    user.id = 1
    user.username = "testuser"
    user.email = "test@example.com"
    user.full_name = "Test User"
    user.role = "user"
    user.organization_id = 1
    user.is_superuser = False
    user.is_active = True
    return user


def _make_mock_db_user():
    """用于 db.get(User, ...) 返回的超管用户，绕过权限检查。"""
    u = MagicMock()
    u.id = 1
    u.is_superuser = True
    u.role = "admin"
    return u


def _make_mock_document(doc_id="doc-1", filename="report.pdf", status_name="indexed"):
    doc = MagicMock()
    doc.id = doc_id
    doc.filename = filename
    doc.title = "Annual Report"
    doc.file_size = 2048
    doc.file_type = MagicMock()
    doc.file_type.value = "pdf"
    doc.status = MagicMock()
    doc.status.value = status_name
    doc.keywords = ["annual", "report"]
    doc.description = "Annual financial report"
    doc.parse_error = None
    doc.created_at = None
    doc.updated_at = None
    doc.uploaded_by = 1
    doc.organization_id = 1
    return doc


def _override_get_db(mock_db=None):
    if mock_db is None:
        mock_db = MagicMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.rollback = AsyncMock()
        mock_db.close = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock())
        mock_db.merge = MagicMock(return_value=_make_mock_user())
        mock_db.get = AsyncMock(return_value=_make_mock_db_user())
        mock_db.delete = AsyncMock()
        mock_db.begin_nested = MagicMock(return_value=_AsyncCtxMgr(mock_db))

    async def _override():
        yield mock_db
        await mock_db.close()

    return _override, mock_db


class TestKnowledgeList:
    def test_list_knowledge_bases(self, client: TestClient):
        """GET /knowledge/ 应返回文档列表。"""
        from app.main import app
        mock_user = _make_mock_user()
        doc = _make_mock_document()

        override_func, mock_db = _override_get_db()

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [doc]

        mock_result = MagicMock()
        mock_result.scalar.return_value = 1
        mock_result.scalars.return_value = mock_scalars

        mock_db.execute = AsyncMock(return_value=mock_result)

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        try:
            r = client.get(
                "/api/v1/knowledge/",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 200
            data = r.json()
            assert data["success"] is True
            assert len(data["data"]["data"]) == 1
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_list_knowledge_bases_pagination(self, client: TestClient):
        """分页参数应正确返回。"""
        from app.main import app
        mock_user = _make_mock_user()
        doc1 = _make_mock_document(doc_id="doc-1")
        doc2 = _make_mock_document(doc_id="doc-2", filename="budget.xlsx")

        override_func, mock_db = _override_get_db()

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [doc1, doc2]

        mock_result = MagicMock()
        mock_result.scalar.return_value = 2
        mock_result.scalars.return_value = mock_scalars

        mock_db.execute = AsyncMock(return_value=mock_result)

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        try:
            r = client.get(
                "/api/v1/knowledge/?page=1&page_size=10",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 200
            data = r.json()
            assert data["data"]["total"] == 2
            assert data["data"]["page"] == 1
            assert data["data"]["page_size"] == 10
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_list_knowledge_bases_empty(self, client: TestClient):
        """无文档时应返回空列表。"""
        from app.main import app
        mock_user = _make_mock_user()

        override_func, mock_db = _override_get_db()

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []

        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_result.scalars.return_value = mock_scalars

        mock_db.execute = AsyncMock(return_value=mock_result)

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        try:
            r = client.get(
                "/api/v1/knowledge/",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 200
            assert r.json()["data"]["total"] == 0
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_list_knowledge_bases_no_auth(self, client: TestClient):
        """未认证请求应返回 401/403。"""
        r = client.get("/api/v1/knowledge/")
        assert r.status_code in (401, 403)


class TestKnowledgeStats:
    def test_get_stats_success(self, client: TestClient):
        """GET /knowledge/stats/{org_id} 应返回统计信息。"""
        from app.main import app
        mock_user = _make_mock_user()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_current_user] = _override_user

        with patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks:
            mock_ks.get_knowledge_stats = AsyncMock(return_value={
                "total_documents": 42,
                "total_chunks": 1500,
                "document_types": {"pdf": 20, "docx": 22},
            })
            try:
                r = client.get(
                    "/api/v1/knowledge/stats/1",
                    headers={"Authorization": "Bearer test_token"}
                )
                assert r.status_code == 200
                data = r.json()
                assert data["success"] is True
                assert "stats" in data
            finally:
                app.dependency_overrides.pop(get_current_user, None)

    def test_get_stats_no_auth(self, client: TestClient):
        """未认证请求应返回 401/403。"""
        r = client.get("/api/v1/knowledge/stats/1")
        assert r.status_code in (401, 403)


class TestKnowledgeSearch:
    def test_search_success(self, client: TestClient):
        """POST /knowledge/search 应返回搜索结果。"""
        from app.main import app
        mock_user = _make_mock_user()

        override_func, mock_db = _override_get_db()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        with patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks:
            mock_ks.search_knowledge = AsyncMock(return_value=[
                {
                    "id": "chunk-1",
                    "text": "Annual report content about revenue",
                    "score": 0.95,
                    "source": {"filename": "report.pdf"},
                    "metadata": {"document_id": "doc-1"},
                    "has_keyword": True,
                    "has_vector": True,
                    "rewrite_hits": 2,
                    "fresh_factor": 1.0,
                }
            ])
            try:
                r = client.post(
                    "/api/v1/knowledge/search",
                    json={"query": "annual report", "top_k": 5, "organization_id": 1},
                    headers={"Authorization": "Bearer test_token"}
                )
                assert r.status_code == 200
                data = r.json()
                assert "results" in data
                assert len(data["results"]) > 0
            finally:
                app.dependency_overrides.pop(get_db, None)
                app.dependency_overrides.pop(get_current_user, None)

    def test_search_no_results(self, client: TestClient):
        """无匹配结果应返回空列表。"""
        from app.main import app
        mock_user = _make_mock_user()

        override_func, mock_db = _override_get_db()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        with patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks:
            mock_ks.search_knowledge = AsyncMock(return_value=[])
            try:
                r = client.post(
                    "/api/v1/knowledge/search",
                    json={"query": "nonexistent term", "organization_id": 1},
                    headers={"Authorization": "Bearer test_token"}
                )
                assert r.status_code == 200
                data = r.json()
                assert len(data["results"]) == 0
                assert data["total_count"] == 0
            finally:
                app.dependency_overrides.pop(get_db, None)
                app.dependency_overrides.pop(get_current_user, None)

    def test_search_missing_query(self, client: TestClient):
        """缺少查询参数应返回 422。"""
        from app.main import app
        mock_user = _make_mock_user()

        override_func, mock_db = _override_get_db()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        try:
            r = client.post(
                "/api/v1/knowledge/search",
                json={},
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 422
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)


class TestKnowledgeSuggestions:
    def test_get_suggestions(self, client: TestClient):
        """GET /knowledge/suggestions 应返回建议列表。"""
        from app.main import app
        mock_user = _make_mock_user()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_current_user] = _override_user

        with patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks:
            mock_ks.get_search_suggestions = AsyncMock(return_value=[
                "annual report", "annual budget", "annual review"
            ])
            try:
                r = client.get(
                    "/api/v1/knowledge/suggestions?q=annual&organization_id=1",
                    headers={"Authorization": "Bearer test_token"}
                )
                assert r.status_code == 200
                data = r.json()
                assert len(data["suggestions"]) == 3
            finally:
                app.dependency_overrides.pop(get_current_user, None)

    def test_get_suggestions_no_auth(self, client: TestClient):
        """未认证请求应返回 401/403。"""
        r = client.get("/api/v1/knowledge/suggestions?q=test&organization_id=1")
        assert r.status_code in (401, 403)


class TestKnowledgeRebuild:
    def test_rebuild_success(self, client: TestClient):
        """POST /knowledge/rebuild/{id} 应提交重建任务。"""
        from app.main import app
        mock_user = _make_mock_user()

        doc = MagicMock()
        doc.id = "doc-1"
        doc.filename = "report.pdf"
        doc.title = "Report"
        doc.file_path = "1/md5.pdf"
        doc.file_type = MagicMock()
        doc.file_type.value = "pdf"
        doc.organization_id = 1

        override_func, mock_db = _override_get_db()
        mock_scalar = MagicMock()
        mock_scalar.scalar_one_or_none.return_value = doc
        mock_db.execute = AsyncMock(return_value=mock_scalar)
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        with patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks:
            mock_ks.delete_es_by_document_id = AsyncMock(return_value=None)

            with patch("app.api.v1.endpoints.knowledge.kafka_producer") as mock_kafka:
                mock_kafka.send_message = AsyncMock(return_value=None)

                try:
                    r = client.post(
                        "/api/v1/knowledge/rebuild/doc-1",
                        headers={"Authorization": "Bearer test_token"}
                    )
                    assert r.status_code == 200
                    data = r.json()
                    assert data["success"] is True
                finally:
                    app.dependency_overrides.pop(get_db, None)
                    app.dependency_overrides.pop(get_current_user, None)

    def test_rebuild_no_auth(self, client: TestClient):
        """未认证重建请求应返回 401/403。"""
        r = client.post("/api/v1/knowledge/rebuild/doc-1")
        assert r.status_code in (401, 403)
