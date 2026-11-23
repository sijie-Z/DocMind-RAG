"""竞品批次四：结构化索引字段的 API 暴露与组织隔离（判别性测试）。

变异点：从任一响应移除 index_pipeline、透传非法码、或放宽 jobs/events 的
组织过滤，本文件的对应用例会红。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.knowledge_job import KnowledgeJobStatus, KnowledgeProcessingJob
from app.schemas.knowledge import IndexPipelineStatus

from .test_api_knowledge import (
    _make_mock_document,
    _make_mock_user,
    _override_get_db,
)


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def _mock_job(job_id=9, *, organization_id=1, doc_id="doc-1"):
    job = MagicMock(spec=KnowledgeProcessingJob)
    job.id = job_id
    job.document_id = doc_id
    job.organization_id = organization_id
    job.trigger_type = "rebuild"
    job.status = KnowledgeJobStatus.FAILED
    job.retry_count = 0
    job.error_message = "原始异常文本不得消失"
    job.index_stage = "embed"
    job.index_progress = 50
    job.index_error_code = "EMBED_FAILED"
    job.index_error_detail = "stage=embed exception=RuntimeError"
    job.started_at = None
    job.finished_at = None
    job.created_at = None
    return job


class _Result:
    def __init__(self, rows=None, scalar_value=0):
        self._rows = rows or []
        self._scalar_value = scalar_value

    def scalar(self):
        return self._scalar_value

    def scalars(self):
        result = MagicMock()
        result.all.return_value = self._rows
        return result


class TestIndexPipelineApi:
    def test_list_exposes_pipeline_and_keeps_legacy_parse_error(self, client: TestClient):
        doc = _make_mock_document(status_name="failed")
        doc.parse_error = "原始解析异常文本"
        doc.index_stage = "embed"
        doc.index_progress = 50
        doc.index_error_code = "EMBED_FAILED"
        doc.index_error_detail = "stage=embed exception=RuntimeError"

        override, mock_db = _override_get_db()
        mock_db.execute = AsyncMock(return_value=_Result([doc], 1))

        from app.main import app

        async def _user():
            return _make_mock_user(is_superuser=True)

        app.dependency_overrides[get_db] = override
        app.dependency_overrides[get_current_user] = _user
        try:
            body = client.get("/api/v1/knowledge/").json()
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

        row = body["data"]["data"][0]
        assert row["parse_error"] == "原始解析异常文本"  # legacy 语义不变
        assert row["index_pipeline"] == {
            "stage": "embed",
            "progress": 50,
            "error_code": "EMBED_FAILED",
            "error_detail": "stage=embed exception=RuntimeError",
        }

    def test_jobs_and_events_expose_pipeline(self, client: TestClient):
        """jobs/events 都带 index_pipeline，稳定码闭集且脱敏。"""
        from app.main import app

        job = _mock_job()
        override, mock_db = _override_get_db()
        mock_db.execute = AsyncMock(return_value=_Result([job], 1))

        async def _user():
            return _make_mock_user(is_superuser=True)

        app.dependency_overrides[get_db] = override
        app.dependency_overrides[get_current_user] = _user
        try:
            jobs = client.get("/api/v1/knowledge/jobs").json()
            events = client.get("/api/v1/knowledge/doc-1/events").json()
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

        assert jobs["data"]["data"][0]["index_pipeline"]["error_code"] == "EMBED_FAILED"
        assert jobs["data"]["data"][0]["error_message"] == "原始异常文本不得消失"
        assert events["data"][0]["index_pipeline"]["stage"] == "embed"

    def test_unknown_stored_code_is_not_emitted(self, client: TestClient):
        """DB 中出现非闭集字符串时 API 返回 null，不泄漏原始文本。"""
        from app.main import app

        doc = _make_mock_document(status_name="failed")
        doc.parse_error = "raw"
        doc.index_stage = "parse"
        doc.index_progress = 25
        doc.index_error_code = "RuntimeError: search query = TOP SECRET"
        doc.index_error_detail = "x" * 500

        override, mock_db = _override_get_db()
        mock_db.execute = AsyncMock(return_value=_Result([doc], 1))

        async def _user():
            return _make_mock_user(is_superuser=True)

        app.dependency_overrides[get_db] = override
        app.dependency_overrides[get_current_user] = _user
        try:
            row = client.get("/api/v1/knowledge/").json()["data"]["data"][0]
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

        assert row["index_pipeline"]["error_code"] is None
        assert row["index_pipeline"]["error_detail"] is None

    def test_index_pipeline_schema_rejects_unknown_code(self):
        """响应 schema 也是闭集：非法码/超长 detail 直接 422 级校验失败。"""
        with pytest.raises(ValueError):
            IndexPipelineStatus(stage="parse", error_code="NOT_A_STABLE_CODE")
        with pytest.raises(ValueError):
            IndexPipelineStatus(stage="parsing")
        assert IndexPipelineStatus(stage="index", progress=100).error_code is None


class TestDocumentStatusPipeline:
    def test_document_status_exposes_normalized_pipeline(self, client: TestClient, monkeypatch):
        """GET /documents/{id}/status 必须暴露 index_pipeline（detail.vue 依赖）。

        变异点：从 documents.py 的 get_document_status 响应删除 index_pipeline
        → 本用例红；把未知码/超长 detail 原样透传 → 形状断言红。
        """
        from app.core.security import permission_service
        from app.main import app
        from app.models.rbac import PermissionType

        async def _grant_read(db, user, organization_id=None):
            return {PermissionType.DOCUMENT_READ.value}

        monkeypatch.setattr(permission_service, "get_user_permissions", _grant_read)

        doc = _make_mock_document(status_name="failed")
        doc.parse_error = "原始异常文本仍须保留"
        doc.index_stage = "embed"
        doc.index_progress = 50
        doc.index_error_code = "EMBED_FAILED"
        doc.index_error_detail = "stage=embed exception=RuntimeError"

        override, mock_db = _override_get_db()
        mock_db.get = AsyncMock(return_value=doc)

        async def _user():
            return _make_mock_user(is_superuser=False)

        app.dependency_overrides[get_db] = override
        app.dependency_overrides[get_current_user] = _user
        try:
            response = client.get("/api/v1/documents/doc-1")
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["parse_error"] == "原始异常文本仍须保留"
        assert data["index_pipeline"] == {
            "stage": "embed",
            "progress": 50,
            "error_code": "EMBED_FAILED",
            "error_detail": "stage=embed exception=RuntimeError",
        }

    def test_document_status_normalizes_unsafe_pipeline_values(
        self, client: TestClient, monkeypatch
    ):
        """旧/脏数据不得把闭集外码或超长文本送到前端。"""
        from app.core.security import permission_service
        from app.main import app
        from app.models.rbac import PermissionType

        async def _grant_read(db, user, organization_id=None):
            return {PermissionType.DOCUMENT_READ.value}

        monkeypatch.setattr(permission_service, "get_user_permissions", _grant_read)

        doc = _make_mock_document(status_name="failed")
        doc.parse_error = "raw"
        doc.index_stage = "garbage"
        doc.index_progress = 999
        doc.index_error_code = "RuntimeError: query = TOP SECRET"
        doc.index_error_detail = "x" * 500

        override, mock_db = _override_get_db()
        mock_db.get = AsyncMock(return_value=doc)

        async def _user():
            return _make_mock_user(is_superuser=False)

        app.dependency_overrides[get_db] = override
        app.dependency_overrides[get_current_user] = _user
        try:
            pipeline = client.get("/api/v1/documents/doc-1").json()["data"]["index_pipeline"]
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

        assert pipeline == {
            "stage": None,
            "progress": 100,
            "error_code": None,
            "error_detail": None,
        }


class TestOrgBoundaries:
    def test_events_org_mismatch_returns_empty(self, client: TestClient, monkeypatch):
        """非超管只能看到本组织 job；跨组织文档 id 不得借 events 探测。"""
        from app.core.security import permission_service
        from app.main import app
        from app.models.rbac import PermissionType

        async def _grant_read(db, user, organization_id=None):
            return {PermissionType.VIEW_KNOWLEDGE_BASE.value}

        monkeypatch.setattr(permission_service, "get_user_permissions", _grant_read)

        override, mock_db = _override_get_db()
        mock_db.execute = AsyncMock(return_value=_Result([]))

        async def _user():
            return _make_mock_user(is_superuser=False)

        app.dependency_overrides[get_db] = override
        app.dependency_overrides[get_current_user] = _user
        try:
            body = client.get("/api/v1/knowledge/foreign-doc/events").json()
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

        assert body["success"] is True
        assert body["data"] == []
        stmt = str(mock_db.execute.await_args.args[0])
        assert "organization_id" in stmt

    def test_document_status_org_boundary_unchanged(self, client: TestClient, monkeypatch):
        """文档状态端点仍走 get_document_for_user：跨组织 403。"""
        from app.core.security import permission_service
        from app.main import app
        from app.models.rbac import PermissionType

        async def _grant_read(db, user, organization_id=None):
            return {PermissionType.DOCUMENT_READ.value}

        monkeypatch.setattr(permission_service, "get_user_permissions", _grant_read)

        doc = _make_mock_document(status_name="failed")
        doc.organization_id = 999
        doc.index_stage = "parse"
        doc.index_progress = 0
        doc.index_error_code = None
        doc.index_error_detail = None

        override, mock_db = _override_get_db()
        mock_db.get = AsyncMock(return_value=doc)
        mock_db.get.side_effect = None

        async def _user():
            return _make_mock_user(is_superuser=False)

        app.dependency_overrides[get_db] = override
        app.dependency_overrides[get_current_user] = _user
        try:
            response = client.get("/api/v1/documents/doc-1")
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code in (403, 404)
