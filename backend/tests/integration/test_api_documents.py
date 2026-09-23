"""Document API 集成测试 — 文档上传、查询、删除流程。"""
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.document import Document
from app.models.user import User


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


def _make_mock_db_user(is_superuser=True):
    """用于 db.get(User, ...) 返回的用户，默认超管以绕过权限检查。"""
    u = MagicMock()
    u.id = 1
    u.is_superuser = is_superuser
    u.role = "admin" if is_superuser else "user"
    u.organization_id = 1
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


def _make_db_get_side_effect(document, user=None):
    """按模型分派的 `db.get` side_effect。

    同一个 `db.get` 被两处共用：`permission_required` 查 `User`（app/core/security.py:82），
    `get_document_for_user` 查 `Document`（app/core/security.py:60）。所以只有 `Document`
    路由能用笼统的 `return_value=`；同时经过权限校验的路由必须按模型分派，
    否则用户 mock 会被当成文档返回（或反之），权限/存在性判断全部失真。
    """
    user = user if user is not None else _make_mock_db_user()

    async def _side_effect(model, *args, **kwargs):
        if model is User:
            return user
        if model is Document:
            return document
        return None

    return _side_effect


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
        mock_db.merge = AsyncMock(return_value=_make_mock_user())
        mock_db.get = AsyncMock(return_value=_make_mock_db_user())
        mock_db.delete = AsyncMock()
        mock_db.begin_nested = MagicMock(return_value=_AsyncCtxMgr(mock_db))

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

        override_func, mock_db = _override_get_db()
        # permission_required 不看注入的 current_user，而是用 `db.get(User, current_user.id)`
        # 重查（app/core/security.py:82）；只 override get_current_user 会让校验打到真库，
        # 查不到 id=1 的用户 -> get_user_permissions(db, None, ...) 直接 AttributeError。
        # 这里让重查返回非超管，才会真正走到「权限不足 -> 403」。
        mock_db.get = AsyncMock(return_value=_make_mock_db_user(is_superuser=False))

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        try:
            r = client.post(
                "/api/v1/documents/upload",
                files={"file": ("test.pdf", io.BytesIO(b"content"), "application/pdf")},
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 403
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_upload_no_file(self, client: TestClient):
        """不传文件应返回 422。"""
        from app.main import app
        mock_user = _make_mock_user()

        # 默认 _override_get_db() 的 db.get 返回超管 -> 权限放行，才会走到缺 file 的 422。
        override_func, _mock_db = _override_get_db()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user

        try:
            r = client.post(
                "/api/v1/documents/upload",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 422
        finally:
            app.dependency_overrides.pop(get_db, None)
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

        with patch("app.core.elasticsearch.ElasticsearchTools") as mock_es:
            # 索引侧同时写 `content` 与 `chunk_text` 两个字段（app/worker/doc_processor.py:174-175），
            # 而端点读的是 `chunk_text`（app/api/v1/endpoints/documents.py:351）。
            # 原 mock 只给 `content`，于是每个 hit 都取到空串，join 出 "\n"。
            mock_es.search_documents = AsyncMock(return_value={
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "content": "This is chunk 1.",
                                "chunk_text": "This is chunk 1.",
                                "metadata": {"chunk_index": 0},
                            }
                        },
                        {
                            "_source": {
                                "content": "This is chunk 2.",
                                "chunk_text": "This is chunk 2.",
                                "metadata": {"chunk_index": 1},
                            }
                        },
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
        # 端点走的是 get_document_for_user -> db.get(Document, ...)，
        # 不是 db.execute；且 db.get 还要先给 permission_required 返回 User，故按模型分派。
        mock_db.get = AsyncMock(side_effect=_make_db_get_side_effect(doc))

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
        # Document 查不到 -> get_document_for_user 抛 404；User 仍返回超管让权限放行。
        mock_db.get = AsyncMock(side_effect=_make_db_get_side_effect(None))

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
