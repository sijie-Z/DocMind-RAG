"""W0-4 跨组织 / 契约矩阵：DELETE /api/v1/files/{id}（真实 SQLite + ASGI）。

用真实内存 SQLite 驱动 ASGI app（同一事件循环），让权限依赖、org 归属校验与
文档查询走真实的 SQLAlchemy 语义；统一删除链路本身用 AsyncMock 打桩（它自开
独立 session / 连 ES / 连 Redis，属于单元测试的覆盖面，见
``tests/unit/test_w0_4_files_delete_chain.py``）。

矩阵：
- 同组织 → 200 ``{"success": true, "message": "删除成功"}``，委托统一链路且
  入参 org 正确，旧 ``file_service.delete_document`` 不再被调用；
- 跨组织 → 403（``get_document_for_user`` 的 AuthorizationError，与
  ``DELETE /api/v1/knowledge/document/{id}`` 及
  ``tests/unit/test_document_access_guard.py`` 的既有契约一致），文档行保留；
- 文档不存在 → 404（NotFoundError）；
- 非超管无组织 → 403（fail-closed）；
- 超管跨组织 → 200（既有旁路，与知识删除端点一致）。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.security import get_current_user
from app.models import Base
from app.models.document import Document, DocumentType
from app.models.organization import Organization
from app.models.rbac import Permission, PermissionType, Role
from app.models.user import User

ORG_A = 7
ORG_B = 8
DOC_A = "doc-w0-4-a"
DOC_B = "doc-w0-4-b"

ENDPOINT = "/api/v1/files/{document_id}"


async def _seeded_factory():
    """内存库：A/B 两组织、各一篇文档、一个默认 User 系统角色。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as s:
        s.add_all(
            [
                Organization(id=ORG_A, name="Org A", owner_id=0),
                Organization(id=ORG_B, name="Org B", owner_id=0),
            ]
        )
        # 默认系统角色带 document:delete —— 让权限依赖放行，401/403 的分歧只可能
        # 来自归属校验（get_document_for_user），测试才判别得到目标行为。
        role = Role(name="User", is_system_role=True)
        role.permissions.append(Permission(name=PermissionType.DOCUMENT_DELETE.value))
        s.add(role)
        s.add_all(
            [
                User(
                    id=1,
                    username="w04-user-a",
                    email="w04-user-a@example.com",
                    hashed_password="x",
                    organization_id=ORG_A,
                    role="user",
                    is_active=True,
                ),
                User(
                    id=2,
                    username="w04-user-no-org",
                    email="w04-user-no-org@example.com",
                    hashed_password="x",
                    organization_id=None,
                    role="user",
                    is_active=True,
                ),
            ]
        )
        s.add_all(
            [
                Document(
                    id=DOC_A,
                    filename="a.pdf",
                    file_path="7/a.pdf",
                    file_size=1,
                    file_type=DocumentType.PDF,
                    organization_id=ORG_A,
                    uploaded_by=1,
                ),
                Document(
                    id=DOC_B,
                    filename="b.pdf",
                    file_path="8/b.pdf",
                    file_size=1,
                    file_type=DocumentType.PDF,
                    organization_id=ORG_B,
                    uploaded_by=1,
                ),
            ]
        )
        await s.commit()
    return engine, factory


def _user(org_id, *, user_id=1, superuser=False, token_org=None):
    return SimpleNamespace(
        id=user_id,
        organization_id=org_id,
        token_organization_id=token_org,
        is_superuser=superuser,
        role="user",
        is_active=True,
        username=f"user{user_id}",
    )


class _ApiCtx:
    """安装 dependency_overrides → 返回同一事件循环内的 ASGI 客户端。"""

    def __init__(self, user, factory):
        self._user = user
        self._factory = factory

    async def __aenter__(self):
        from app.main import app

        async def _current_user():
            return self._user

        async def _db():
            async with self._factory() as session:
                yield session

        app.dependency_overrides[get_current_user] = _current_user
        app.dependency_overrides[get_db] = _db
        self._app = app
        self._client = AsyncClient(transport=ASGITransport(app=app), base_url="http://w0del")
        return self._client

    async def __aexit__(self, exc_type, exc, tb):
        await self._client.aclose()
        self._app.dependency_overrides.pop(get_current_user, None)
        self._app.dependency_overrides.pop(get_db, None)
        return False


def _patch_delete_chain(monkeypatch):
    """打桩统一链路与审计；返回 (unified, legacy, audit) 供断言。

    打桩目标是 service 单例的方法而非端点模块里的名字：修复前端点尚未接入
    统一链路时，测试会以「unified 未被 await」的行为断言失败，而不是在
    打桩阶段抛 AttributeError。
    """
    from app.api.v1.endpoints import files as files_api
    from app.services.knowledge_service import knowledge_service as ks_singleton

    unified = AsyncMock(return_value=True)
    legacy = AsyncMock(return_value=True)
    audit = AsyncMock()
    monkeypatch.setattr(ks_singleton, "delete_knowledge", unified)
    monkeypatch.setattr(files_api.file_service, "delete_document", legacy)
    monkeypatch.setattr(files_api.audit_service, "log_activity", audit)
    return unified, legacy, audit


# ── 弃用契约：文档管理端点标 deprecated，头像上传保持受支持 ────────────────


def test_files_document_management_routes_marked_deprecated():
    """#416：/files 文档管理端点保留但标 deprecated（兼容壳）；
    /files/upload/avatar 是唯一头像入口，不得误标。"""
    from app.main import app

    schema = app.openapi()
    deprecated_pairs = [
        ("/api/v1/files/upload", "post"),
        ("/api/v1/files/upload-chunk", "post"),
        ("/api/v1/files/list", "get"),
        ("/api/v1/files/{document_id}", "delete"),
    ]
    for path, method in deprecated_pairs:
        assert path in schema["paths"], f"{path} 应保留（兼容壳，不删端点）"
        assert schema["paths"][path][method].get("deprecated") is True, (
            f"{method.upper()} {path} 未标记 deprecated"
        )
    avatar = schema["paths"]["/api/v1/files/upload/avatar"]["post"]
    assert avatar.get("deprecated") is not True, "头像上传是唯一入口，不应标记弃用"


# ── 同组织：委托统一链路 + 契约不变 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_org_delete_delegates_to_unified_chain(monkeypatch):
    engine, factory = await _seeded_factory()
    unified, legacy, audit = _patch_delete_chain(monkeypatch)
    try:
        async with _ApiCtx(_user(ORG_A), factory) as ac:
            r = await ac.delete(ENDPOINT.format(document_id=DOC_A))
        assert r.status_code == 200, r.text
        # FileDeleteResponse 契约不变：恰好 success / message 两个字段
        assert r.json() == {"success": True, "message": "删除成功"}
        unified.assert_awaited_once_with(DOC_A, ORG_A)
        legacy.assert_not_awaited()
        # 成功路径恰好记一次删除审计（action 保持 delete_file）
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["action"] == "delete_file"
        assert audit.await_args.kwargs["target_id"] == DOC_A
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_token_org_fallback_delete_allowed(monkeypatch):
    """多组织成员：organization_id 为空但 token_organization_id 已校验 → 放行。"""
    engine, factory = await _seeded_factory()
    unified, legacy, _audit = _patch_delete_chain(monkeypatch)
    try:
        async with _ApiCtx(_user(None, token_org=ORG_A), factory) as ac:
            r = await ac.delete(ENDPOINT.format(document_id=DOC_A))
        assert r.status_code == 200, r.text
        unified.assert_awaited_once_with(DOC_A, ORG_A)
        legacy.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_superuser_cross_org_delete_allowed(monkeypatch):
    """超管旁路按文档所属 org 委托统一链路（与知识删除端点一致）。"""
    engine, factory = await _seeded_factory()
    unified, _legacy, _audit = _patch_delete_chain(monkeypatch)
    try:
        async with _ApiCtx(_user(ORG_A, superuser=True), factory) as ac:
            r = await ac.delete(ENDPOINT.format(document_id=DOC_B))
        assert r.status_code == 200, r.text
        unified.assert_awaited_once_with(DOC_B, ORG_B)
    finally:
        await engine.dispose()


# ── 跨组织 / 不存在 / 无组织：拒绝且不触碰删除链路 ─────────────────────────


@pytest.mark.asyncio
async def test_cross_org_delete_denied_and_document_survives(monkeypatch):
    engine, factory = await _seeded_factory()
    unified, legacy, audit = _patch_delete_chain(monkeypatch)
    try:
        async with _ApiCtx(_user(ORG_B), factory) as ac:
            r = await ac.delete(ENDPOINT.format(document_id=DOC_A))
        assert r.status_code == 403, r.text
        # 判别来源：403 必须是 get_document_for_user 的 AuthorizationError，
        # 而不是权限依赖的 HTTPException（后者没有 error_code 字段）。
        assert r.json().get("error_code") == "AUTHORIZATION_ERROR"
        unified.assert_not_awaited()
        legacy.assert_not_awaited()
        audit.assert_not_awaited()
        async with factory() as s:
            assert await s.get(Document, DOC_A) is not None, "跨组织删除不得动到文档行"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_missing_document_returns_404(monkeypatch):
    engine, factory = await _seeded_factory()
    unified, legacy, audit = _patch_delete_chain(monkeypatch)
    try:
        async with _ApiCtx(_user(ORG_A), factory) as ac:
            r = await ac.delete(ENDPOINT.format(document_id="does-not-exist"))
        assert r.status_code == 404, r.text
        assert r.json().get("error_code") == "NOT_FOUND"
        unified.assert_not_awaited()
        legacy.assert_not_awaited()
        audit.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_user_without_org_denied(monkeypatch):
    engine, factory = await _seeded_factory()
    unified, legacy, audit = _patch_delete_chain(monkeypatch)
    try:
        async with _ApiCtx(_user(None, user_id=2), factory) as ac:
            r = await ac.delete(ENDPOINT.format(document_id=DOC_A))
        assert r.status_code == 403, r.text
        assert r.json().get("error_code") == "AUTHORIZATION_ERROR"
        unified.assert_not_awaited()
        legacy.assert_not_awaited()
        audit.assert_not_awaited()
    finally:
        await engine.dispose()
