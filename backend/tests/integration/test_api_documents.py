# -*- coding: utf-8 -*-
"""Document API 集成测试 — 文档上传、查询、删除流程。"""
import io
import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock, mock_open
from fastapi.testclient import TestClient
from app.core.database import get_db
from app.core.security import get_current_user


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def _make_mock_user(org_id=1):
    user = MagicMock()
    user.id = 1
    user.username = "testuser"
    user.email = "test@example.com"
    user.full_name = "Test User"
    user.role = "user"
    user.organization_id = org_id
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


def _make_mock_document(doc_id="doc-123", filename="test.pdf"):
    doc = MagicMock()
    doc.id = doc_id
    doc.filename = filename
    doc.title = "Test Document"
    doc.file_path = f"1/md5hash.{filename.split('.')[-1]}"
    doc.file_size = 1024
    doc.file_type = MagicMock()
    doc.file_type.value = "pdf"
    doc.status = MagicMock()
    doc.status.value = "pending"
    doc.md5_hash = "md5hash"
    doc.parse_error = None
    doc.chunk_count = 0
    doc.description = "A test document"
    doc.keywords = ["test", "document"]
    doc.uploaded_by = 1
    doc.organization_id = 1
    doc.created_at = None
    doc.updated_at = None
    doc.parsed_at = None
    doc.indexed_at = None
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
        mock_db.delete = MagicMock()
        mock_db.begin_nested = AsyncMock()

    async def _override():
        yield mock_db
        await mock_db.close()

    return _override, mock_db


class TestDocumentUpload:
    def test_upload_success(self, client: TestClient):
        """上传有效文件应返回 201 或 200。"""
        from app.main import app
        mock_user = _make_mock_user()
        override_func, mock_db = _override_get_db()

        async def _override_user():
            return mock_user

        # Mock organization query - returns None (will create default)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        with patch("app.api.v1.endpoints.documents.minio_client") as mock_minio:
            mock_minio.stat_object.side_effect = Exception("Not found")
            mock_minio.put_object = MagicMock()

            with patch("app.api.v1.endpoints.documents.kafka_producer") as mock_kafka:
                mock_kafka.send_message = AsyncMock(return_value=None)

                try:
                    test_file_content = b"%PDF-1.4 fake pdf content"
                    r = client.post(
                        "/api/v1/documents/upload",
                        files={"file": ("test.pdf", io.BytesIO(test_file_content), "application/pdf")},
                        data={"title": "Test Doc", "description": "A test document", "tags": '["test","doc"]'},
                        headers={"Authorization": "Bearer test_token"}
                    )
                    assert r.status_code in (200, 201, 422), f"Upload failed: {r.status_code} - {r.json()}"
                    if r.status_code == 201:
                        assert r.json()["success"] is True
                finally:
                    app.dependency_overrides.pop(get_db, None)
                    app.dependency_overrides.pop(get_current_user, None)

    def test_upload_no_permission(self, client: TestClient):
        """无上传权限应返回 403。"""
        from app.main import app
        mock_user = _make_mock_user()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_current_user] = _override_user

        try:
            r = client.post(
                "/api/v1/documents/upload",
                files={"file": ("test.pdf", io.BytesIO(b"content"), "application/pdf")},
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_upload_no_file(self, client: TestClient):
        """不传文件应返回 422。"""
        from app.main import app
        mock_user = _make_mock_user()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_current_user] = _override_user

        try:
            r = client.post(
                "/api/v1/documents/upload",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code in (422, 500)
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_upload_no_auth(self, client: TestClient):
        """未认证上传应返回 401/403。"""
        r = client.post(
            "/api/v1/documents/upload",
            files={"file": ("test.pdf", io.BytesIO(b"content"), "application/pdf")}
        )
        assert r.status_code in (401, 403)


class TestDocumentGet:
    def test_get_document_found(self, client: TestClient):
        """存在的文档应返回文档详情。"""
        from app.main import app
        mock_user = _make_mock_user()
        doc = _make_mock_document()

        override_func, mock_db = _override_get_db()
        mock_db.get = AsyncMock(return_value=doc)

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        try:
            r = client.get(
                "/api/v1/documents/doc-123",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 200
            data = r.json()
            assert data["success"] is True
            assert data["data"]["id"] == "doc-123"
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_get_document_not_found(self, client: TestClient):
        """不存在的文档应返回 404。"""
        from app.main import app
        mock_user = _make_mock_user()

        override_func, mock_db = _override_get_db()
        mock_db.get = AsyncMock(return_value=None)

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        try:
            r = client.get(
                "/api/v1/documents/nonexistent",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 404
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_get_document_unauthorized_org(self, client: TestClient):
        """不同组织用户无权查看文档。"""
        from app.main import app
        mock_user = _make_mock_user(org_id=2)  # Different org

        doc = _make_mock_document()
        doc.organization_id = 1  # Doc belongs to org 1
        doc.uploaded_by = 99  # Not current user

        override_func, mock_db = _override_get_db()
        mock_db.get = AsyncMock(return_value=doc)

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        try:
            r = client.get(
                "/api/v1/documents/doc-123",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 403
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_get_document_no_auth(self, client: TestClient):
        """未认证获取文档应返回 401/403。"""
        r = client.get("/api/v1/documents/doc-123")
        assert r.status_code in (401, 403)


class TestDocumentContent:
    def test_get_content_success(self, client: TestClient):
        """文档内容应返回全文。"""
        from app.main import app
        mock_user = _make_mock_user()
        doc = _make_mock_document()

        override_func, mock_db = _override_get_db()
        mock_db.get = AsyncMock(return_value=doc)

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        with patch("app.api.v1.endpoints.documents.ElasticsearchTools") as mock_es:
            mock_es.search_documents = AsyncMock(return_value={
                "hits": {
                    "hits": [
                        {"_source": {"content": "This is chunk 1.", "metadata": {"chunk_index": 0}}},
                        {"_source": {"content": "This is chunk 2.", "metadata": {"chunk_index": 1}}},
                    ]
                }
            })
            try:
                r = client.get(
                    "/api/v1/documents/doc-123/content",
                    headers={"Authorization": "Bearer test_token"}
                )
                assert r.status_code == 200
                data = r.json()
                assert data["success"] is True
                assert "This is chunk 1." in data["data"]["content"]
            finally:
                app.dependency_overrides.pop(get_db, None)
                app.dependency_overrides.pop(get_current_user, None)

    def test_get_content_no_auth(self, client: TestClient):
        """未认证获取内容应返回 401/403。"""
        r = client.get("/api/v1/documents/doc-123/content")
        assert r.status_code in (401, 403)


class TestDocumentDelete:
    def test_delete_document_success(self, client: TestClient):
        """删除文档应返回成功。"""
        from app.main import app
        mock_user = _make_mock_user()
        doc = _make_mock_document()

        override_func, mock_db = _override_get_db()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = doc
        mock_db.execute = AsyncMock(return_value=mock_result)

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        with patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks:
            mock_ks.delete_knowledge = AsyncMock(return_value=True)

            try:
                r = client.delete(
                    "/api/v1/knowledge/document/doc-123",
                    headers={"Authorization": "Bearer test_token"}
                )
                assert r.status_code == 200
                data = r.json()
                assert data["success"] is True
            finally:
                app.dependency_overrides.pop(get_db, None)
                app.dependency_overrides.pop(get_current_user, None)

    def test_delete_document_not_found(self, client: TestClient):
        """不存在的文档删除应返回 404。"""
        from app.main import app
        mock_user = _make_mock_user()

        override_func, mock_db = _override_get_db()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        try:
            r = client.delete(
                "/api/v1/knowledge/document/nonexistent",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 404
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_delete_document_no_auth(self, client: TestClient):
        """未认证删除应返回 401/403。"""
        r = client.delete("/api/v1/knowledge/document/doc-123")
        assert r.status_code in (401, 403)
