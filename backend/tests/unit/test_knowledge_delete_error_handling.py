"""删除文档的错误处理契约（issue #174）—— 失败不再被吞成 False/bool。

修复前：`delete_knowledge_thoroughly` 的 `except Exception: ... return False` 把原因
吞掉，调用方只能报笼统的「删除失败」；`batch_delete_knowledge` 把一个失败计成
fail_count 后原因同样无处可见。修复后：
- service 失败 → 抛 `AppError`，消息带原始异常类型与内容（500 响应体可定位问题）；
- 文档不存在 → 返回 True（幂等删除；**不得**变成 500）；
- 批量删除 → 单条失败不中断整批，原因逐条收进 `errors`；
- 端点 → 放行 service 的 AppError（消息保留），NotFoundError 保持 404。

不依赖数据库：会话工厂/服务方法按用例替换。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.knowledge_service as ks_mod
from app.exceptions import AppError, NotFoundError


def _svc():
    return ks_mod.KnowledgeService()


class _ExplodingSession:
    """Simulate a DB session whose first query fails (e.g. connection lost)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, *args, **kwargs):
        raise RuntimeError("db exploded")

    async def commit(self):
        raise AssertionError("不应走到 commit")


class _MissingDocResult:
    def scalar_one_or_none(self):
        return None


class _MissingDocSession(_ExplodingSession):
    async def execute(self, *args, **kwargs):
        return _MissingDocResult()


# ── service：失败抛 AppError（带原因）；不再 return False ─────────────────────


@pytest.mark.asyncio
async def test_delete_failure_raises_app_error_with_cause(monkeypatch):
    monkeypatch.setattr(ks_mod, "AsyncSessionLocal", _ExplodingSession)

    with pytest.raises(AppError) as exc_info:
        await _svc().delete_knowledge_thoroughly("doc-1")

    assert exc_info.value.status_code == 500
    # 原因必须可见（修复前只剩笼统的 False → 调用方只能报「删除失败」）
    assert "RuntimeError" in exc_info.value.message
    assert "db exploded" in exc_info.value.message


@pytest.mark.asyncio
async def test_delete_missing_document_is_idempotent_success(monkeypatch):
    """文档不存在按幂等成功返回 True —— 绝不能变成异常/500。"""
    monkeypatch.setattr(ks_mod, "AsyncSessionLocal", _MissingDocSession)

    result = await _svc().delete_knowledge_thoroughly("missing")
    assert result.success is True
    assert result.file_cleanup_pending is False


# ── batch：单条失败不中断，原因逐条收集 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_delete_continues_and_collects_reasons(monkeypatch):
    svc = _svc()
    calls: list[str] = []

    async def _fake_delete(doc_id, organization_id=None):
        calls.append(doc_id)
        if doc_id == "bad":
            raise AppError("删除文档失败: IntegrityError: (1451, ...)")
        return True

    monkeypatch.setattr(svc, "delete_knowledge_thoroughly", _fake_delete)

    result = await svc.batch_delete_knowledge(["ok-1", "bad", "ok-2"])

    assert calls == ["ok-1", "bad", "ok-2"], "失败后必须继续处理后续文档"
    assert result["success"] is True
    assert result["success_count"] == 2
    assert result["fail_count"] == 1
    assert result["errors"] == [
        {"document_id": "bad", "reason": "删除文档失败: IntegrityError: (1451, ...)"}
    ]


@pytest.mark.asyncio
async def test_batch_delete_all_success_has_no_errors_key(monkeypatch):
    svc = _svc()
    monkeypatch.setattr(svc, "delete_knowledge_thoroughly", AsyncMock(return_value=True))

    result = await svc.batch_delete_knowledge(["a", "b"])

    assert result == {
        "success": True,
        "success_count": 2,
        "fail_count": 0,
        "message": "成功删除 2 个文档，失败 0 个",
    }


# ── 端点：service 失败原因原样上抛（500 可定位）；404 保持 404 ────────────────


@pytest.mark.asyncio
async def test_endpoint_surfaces_service_failure_reason(monkeypatch):
    from app.api.v1.endpoints import knowledge as knowledge_api

    doc = MagicMock()
    doc.organization_id = 1
    doc.filename = "x.txt"
    monkeypatch.setattr(knowledge_api, "get_document_for_user", AsyncMock(return_value=doc))
    monkeypatch.setattr(
        knowledge_api.knowledge_service,
        "delete_knowledge",
        AsyncMock(side_effect=AppError("删除文档失败: IntegrityError: (1451, 'foreign key')")),
    )
    audit = AsyncMock()
    monkeypatch.setattr(knowledge_api.audit_service, "log_activity", audit)

    with pytest.raises(AppError) as exc_info:
        await knowledge_api.delete_knowledge_document(
            "doc-1", current_user=MagicMock(), db=MagicMock()
        )

    assert exc_info.value.status_code == 500
    # 不再被吞成笼统的「删除失败」：消息里能定位到原因
    assert "IntegrityError" in exc_info.value.message and "1451" in exc_info.value.message
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_endpoint_delete_success_returns_success_payload(monkeypatch):
    from app.api.v1.endpoints import knowledge as knowledge_api

    doc = MagicMock()
    doc.organization_id = 1
    doc.filename = "x.txt"
    monkeypatch.setattr(knowledge_api, "get_document_for_user", AsyncMock(return_value=doc))
    monkeypatch.setattr(
        knowledge_api.knowledge_service, "delete_knowledge", AsyncMock(return_value=True)
    )
    audit = AsyncMock()
    monkeypatch.setattr(knowledge_api.audit_service, "log_activity", audit)

    result = await knowledge_api.delete_knowledge_document(
        "doc-1", current_user=MagicMock(), db=MagicMock()
    )

    assert result == {"success": True, "message": "删除成功"}
    audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_endpoint_keeps_404_for_missing_document(monkeypatch):
    from app.api.v1.endpoints import knowledge as knowledge_api

    monkeypatch.setattr(
        knowledge_api,
        "get_document_for_user",
        AsyncMock(side_effect=NotFoundError("文档不存在")),
    )

    with pytest.raises(NotFoundError) as exc_info:
        await knowledge_api.delete_knowledge_document(
            "missing", current_user=MagicMock(), db=MagicMock()
        )

    assert exc_info.value.status_code == 404
