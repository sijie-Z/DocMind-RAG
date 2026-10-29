"""Batch 5 生命周期保护：被核验案件引用的文档禁止物理删除（真实 SQLite 语义之外的失败序）。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.knowledge_service as ks_mod
from app.exceptions import ConflictError
from app.models.review import ReviewCase


class _Result:
    def __init__(self, *, value=None, rows=None):
        self._value = value
        self._rows = rows if rows is not None else []

    def scalar_one_or_none(self):
        return self._value

    def scalar(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _ScriptedSession:
    """按顺序返回脚本化结果；记录 execute/commit 调用。"""

    def __init__(self, results):
        self._results = list(results)
        self.executed = 0
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, *_args, **_kwargs):
        self.executed += 1
        if self._results:
            return self._results.pop(0)
        return None

    async def commit(self):
        self.committed = True


def _factory(session):
    return lambda: session


def _doc():
    return SimpleNamespace(id="doc-1", file_path=None, organization_id=1, filename="x.txt")


@pytest.mark.asyncio
async def test_delete_referenced_document_fails_before_side_effects(monkeypatch):
    session = _ScriptedSession(
        [
            _Result(value=_doc()),
            _Result(rows=["case-b", "case-a"]),
        ]
    )
    monkeypatch.setattr(ks_mod, "AsyncSessionLocal", _factory(session))
    es_delete = AsyncMock(return_value=True)
    monkeypatch.setattr(ks_mod.KnowledgeService, "delete_es_by_document_id", es_delete)

    with pytest.raises(ConflictError) as excinfo:
        await ks_mod.KnowledgeService().delete_knowledge_thoroughly("doc-1", 1)

    err = excinfo.value
    assert err.status_code == 409
    assert err.error_code == "REVIEW_DOCUMENT_IN_USE"
    assert err.detail["case_ids"] == ["case-a", "case-b"]
    assert es_delete.await_count == 0, "引用检查必须在 ES/DB 任何副作用之前"
    assert session.committed is False


@pytest.mark.asyncio
async def test_delete_unreferenced_document_still_succeeds(monkeypatch):
    session = _ScriptedSession([_Result(value=_doc()), _Result(rows=[])])
    monkeypatch.setattr(ks_mod, "AsyncSessionLocal", _factory(session))
    es_delete = AsyncMock(return_value=True)
    monkeypatch.setattr(ks_mod.KnowledgeService, "delete_es_by_document_id", es_delete)
    from app.rag.cache import RetrievalCache, SemanticCache

    monkeypatch.setattr(RetrievalCache, "clear_for_org", AsyncMock())
    monkeypatch.setattr(SemanticCache, "clear_for_org", AsyncMock())

    outcome = await ks_mod.KnowledgeService().delete_knowledge_thoroughly("doc-1", 1)

    assert outcome.success is True
    assert es_delete.await_count == 1
    assert session.committed is True


@pytest.mark.asyncio
async def test_batch_delete_reports_conflict_per_item(monkeypatch):
    async def _delete(doc_id, _org=None):
        if doc_id == "doc-1":
            raise ConflictError(
                "文档被核验案件引用，不能删除",
                error_code="REVIEW_DOCUMENT_IN_USE",
                detail={"document_id": "doc-1", "case_ids": ["case-a"]},
            )
        return ks_mod.DeleteOutcome(success=True)

    monkeypatch.setattr(
        ks_mod.KnowledgeService, "delete_knowledge_thoroughly", AsyncMock(side_effect=_delete)
    )

    result = await ks_mod.KnowledgeService().batch_delete_knowledge(["doc-1", "doc-2"], 1)

    assert result["success_count"] == 1
    assert result["fail_count"] == 1
    assert result["errors"][0]["document_id"] == "doc-1"
    assert "核验案件" in result["errors"][0]["reason"]


@pytest.mark.asyncio
async def test_endpoint_surfaces_review_conflict_without_audit(monkeypatch):
    from app.api.v1.endpoints import knowledge as knowledge_api

    doc = SimpleNamespace(organization_id=1, filename="x.txt")
    monkeypatch.setattr(knowledge_api, "get_document_for_user", AsyncMock(return_value=doc))
    monkeypatch.setattr(
        knowledge_api.knowledge_service,
        "delete_knowledge",
        AsyncMock(
            side_effect=ConflictError(
                "文档被核验案件引用，不能删除",
                error_code="REVIEW_DOCUMENT_IN_USE",
                detail={"document_id": "doc-1", "case_ids": ["case-a"]},
            )
        ),
    )
    audit = AsyncMock()
    monkeypatch.setattr(knowledge_api.audit_service, "log_activity", audit)

    with pytest.raises(ConflictError) as excinfo:
        await knowledge_api.delete_knowledge_document(
            "doc-1", current_user=SimpleNamespace(id=1), db=AsyncMock()
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.error_code == "REVIEW_DOCUMENT_IN_USE"
    audit.assert_not_awaited()


def test_review_case_document_columns_are_plain_strings():
    """案件引用的三份文档刻意不建外键：删除保护靠本守卫而不是 DB FK。"""
    for column in ("policy_document_id", "base_document_id", "revised_document_id"):
        assert column in ReviewCase.__table__.columns
        assert not ReviewCase.__table__.columns[column].foreign_keys
