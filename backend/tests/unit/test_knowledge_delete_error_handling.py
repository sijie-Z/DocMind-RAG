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

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import OperationalError

import app.services.knowledge_service as ks_mod
from app.exceptions import AppError, ConflictError, NotFoundError


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
        {
            "document_id": "bad",
            "reason": "删除文档失败: IntegrityError: (1451, ...)",
            "error_code": "INTERNAL_ERROR",
        }
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


# ── MySQL 事务冲突：1205/1213 映射为可重试 409（Batch 11） ────────────────────


class _ConflictSession(_ExplodingSession):
    """第一条查询即抛指定错误码的 OperationalError（死锁/锁等待超时）。"""

    def __init__(self, code: int):
        self._code = code

    async def execute(self, *args, **kwargs):
        # 真实 asyncmy/aiomysql 驱动异常是 (errno, message) 两元组
        raise OperationalError("SELECT ... FOR UPDATE", {}, Exception(self._code, "driver message"))


@pytest.mark.asyncio
async def test_delete_deadlock_maps_to_retryable_conflict(monkeypatch):
    """1213 死锁：删除应返回可重试 409，而不是 500 吞进通用文案。"""
    monkeypatch.setattr(ks_mod, "AsyncSessionLocal", lambda: _ConflictSession(1213))

    with pytest.raises(ConflictError) as exc_info:
        await _svc().delete_knowledge_thoroughly("doc-1")

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "DOCUMENT_DELETE_CONFLICT"
    assert "重试" in exc_info.value.message
    # 原始驱动异常必须保留在 cause 链里（排障依据），不能被吞掉
    assert isinstance(exc_info.value.__cause__, OperationalError)
    assert exc_info.value.__cause__.orig.args[0] == 1213


@pytest.mark.asyncio
async def test_delete_lock_wait_timeout_maps_to_retryable_conflict(monkeypatch):
    """1205 锁等待超时（innodb_deadlock_detect=OFF 部署）同样可重试。"""
    monkeypatch.setattr(ks_mod, "AsyncSessionLocal", lambda: _ConflictSession(1205))

    with pytest.raises(ConflictError) as exc_info:
        await _svc().delete_knowledge_thoroughly("doc-1")

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "DOCUMENT_DELETE_CONFLICT"
    assert "重试" in exc_info.value.message
    assert isinstance(exc_info.value.__cause__, OperationalError)
    assert exc_info.value.__cause__.orig.args[0] == 1205


@pytest.mark.asyncio
async def test_delete_non_retryable_operational_error_stays_500(monkeypatch):
    """非 1205/1213 的 OperationalError 保持原 500 语义，不得误报为可重试。"""
    monkeypatch.setattr(ks_mod, "AsyncSessionLocal", lambda: _ConflictSession(1317))

    with pytest.raises(AppError) as exc_info:
        await _svc().delete_knowledge_thoroughly("doc-1")

    assert exc_info.value.status_code == 500
    assert "OperationalError" in exc_info.value.message


@pytest.mark.asyncio
async def test_batch_delete_collects_conflict_and_continues(monkeypatch):
    """批量删除遇到可重试冲突：按条收集原因、不中断后续文档。"""
    svc = _svc()
    calls: list[str] = []

    async def _fake_delete(doc_id, organization_id=None):
        calls.append(doc_id)
        if doc_id == "busy":
            raise ConflictError(
                "文档删除与并发操作冲突，请重试",
                error_code="DOCUMENT_DELETE_CONFLICT",
            )
        return True

    monkeypatch.setattr(svc, "delete_knowledge_thoroughly", _fake_delete)

    result = await svc.batch_delete_knowledge(["ok-1", "busy", "ok-2"])

    assert calls == ["ok-1", "busy", "ok-2"], "冲突后必须继续处理后续文档"
    assert result["success_count"] == 2
    assert result["fail_count"] == 1
    assert result["errors"] == [
        {
            "document_id": "busy",
            "reason": "文档删除与并发操作冲突，请重试",
            "error_code": "DOCUMENT_DELETE_CONFLICT",
        }
    ]


_DOC = SimpleNamespace(id="doc-1", organization_id=1, file_path="1/doc-1.txt")


class _DocRowResult:
    def scalar_one_or_none(self):
        return _DOC


class _NoCaseRefsResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _ZeroCountResult:
    def scalar(self):
        return 0


class _CommitConflictSession:
    """文档存在、引用检查通过、ES 删除成功，最终 commit 抛 1213。"""

    def __init__(self):
        self._results = [_DocRowResult(), _NoCaseRefsResult(), _ZeroCountResult()]
        self.commit_attempted = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, *args, **kwargs):
        return self._results.pop(0) if self._results else None

    async def commit(self):
        self.commit_attempted = True
        raise OperationalError("COMMIT", {}, Exception(1213, "Deadlock found"))


@pytest.mark.asyncio
async def test_delete_commit_deadlock_is_retryable_after_es_deleted(monkeypatch):
    """ES 已删、DB commit 前的 1213：仍映射 409 可重试（重试幂等收敛）。"""
    session = _CommitConflictSession()
    monkeypatch.setattr(ks_mod, "AsyncSessionLocal", lambda: session)
    svc = _svc()
    es_calls: list[str] = []

    async def _es_ok(doc_id):
        es_calls.append(doc_id)
        return True

    monkeypatch.setattr(svc, "delete_es_by_document_id", _es_ok)

    with pytest.raises(ConflictError) as exc_info:
        await svc.delete_knowledge_thoroughly("doc-1")

    assert es_calls == ["doc-1"], "commit 前的 ES 删除已经发生"
    assert session.commit_attempted is True
    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "DOCUMENT_DELETE_CONFLICT"
    assert isinstance(exc_info.value.__cause__, OperationalError)
    assert exc_info.value.__cause__.orig.args[0] == 1213


@pytest.mark.asyncio
async def test_endpoint_propagates_delete_conflict_409(monkeypatch):
    """端点层不得把可重试冲突吞掉或改写成 500。"""
    from app.api.v1.endpoints import knowledge as knowledge_api

    doc = MagicMock()
    doc.organization_id = 1
    doc.filename = "x.txt"
    monkeypatch.setattr(knowledge_api, "get_document_for_user", AsyncMock(return_value=doc))
    monkeypatch.setattr(
        knowledge_api.knowledge_service,
        "delete_knowledge",
        AsyncMock(
            side_effect=ConflictError(
                "文档删除与并发操作冲突，请重试", error_code="DOCUMENT_DELETE_CONFLICT"
            )
        ),
    )
    audit = AsyncMock()
    monkeypatch.setattr(knowledge_api.audit_service, "log_activity", audit)

    with pytest.raises(ConflictError) as exc_info:
        await knowledge_api.delete_knowledge_document(
            "doc-1", current_user=MagicMock(), db=MagicMock()
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "DOCUMENT_DELETE_CONFLICT"
    audit.assert_not_awaited()
