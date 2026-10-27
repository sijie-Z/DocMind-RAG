"""S3/#168 regression tests — 无组织用户必须 fail closed（403），不得兜底到 1 号组织。

此前大量端点使用 `current_user.organization_id or 1`（或 "1" 字符串），
注册产生的 organization_id=None 用户会被静默归入 1 号组织，从而读写其他租户
的数据。修复后统一通过 app.core.security.require_organization 解析组织：
无组织即 403；超级管理员作为全局角色无组织时，解析库里**真实存在**的第一个组织
（issue #168：不再硬编码 1 号组织——1 号未必存在），库中没有任何组织时明确 403。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user, require_organization
from app.exceptions import AuthorizationError


def _make_user(*, is_superuser=False, role="user", organization_id=1, user_id=1):
    u = MagicMock()
    u.id = user_id
    u.username = "testuser"
    u.email = "test@example.com"
    u.full_name = "Test User"
    u.role = role
    u.organization_id = organization_id
    u.is_superuser = is_superuser
    u.is_active = True
    u.preferences = None
    # 真实 User 模型没有该属性（get_user_org_id 的兜底取 None）；
    # MagicMock 会自动生成属性，这里显式置 None 保持语义一致
    u.token_organization_id = None
    return u


def _mock_db(*, permission_user=None):
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.rollback = AsyncMock()
    db.close = AsyncMock()
    db.get = AsyncMock(return_value=permission_user or _make_user(is_superuser=True))
    db.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [])))
    return db


def _allow_all_permissions():
    """Pretend the caller holds every permission.

    develop 的 `permission_required` 每次请求都回源 DB 解析权限；不打这个桩，
    端点会因为"查询返回空权限"而 403 —— 那是假阳性，测不到本文件要验证的
    组织守卫。让权限全通过，403 才真正来自 S3 的 fail-closed 逻辑。
    """
    from app.models.rbac import PermissionType

    return patch(
        "app.services.permission_service.permission_service.get_user_permissions",
        new=AsyncMock(return_value={p.value for p in PermissionType}),
    )


def _override_get_db(db):
    async def _override():
        yield db
        await db.close()

    return _override


def _override_auth(user):
    async def _override_auth_impl():
        return user

    return _override_auth_impl


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


class TestRequireOrganizationHelper:
    """require_organization 的 fail-closed 语义。"""

    @pytest.mark.asyncio
    async def test_org_less_user_rejected(self):
        with pytest.raises(AuthorizationError):
            await require_organization(_make_user(organization_id=None), _mock_db())

    @pytest.mark.asyncio
    async def test_org_less_admin_role_rejected(self):
        # 全局 role=admin（SysAdmin 权限集）也不例外：无组织即 403
        with pytest.raises(AuthorizationError):
            await require_organization(_make_user(role="admin", organization_id=None), _mock_db())

    @pytest.mark.asyncio
    async def test_member_gets_own_org(self):
        assert await require_organization(_make_user(organization_id=42), _mock_db()) == 42

    @pytest.mark.asyncio
    async def test_org_less_superuser_resolves_real_org_from_db(self):
        # issue #168：不再硬编码兜底到 1 号组织，而是解析库里真实存在的组织
        db = _mock_db_with_first_org(3)
        assert (
            await require_organization(_make_user(is_superuser=True, organization_id=None), db) == 3
        )


def _mock_db_with_first_org(org_id):
    """构造一个「DB 里第一个真实存在的组织是 org_id（或不存在 → None）」的 mock。

    `resolve_existing_organization_id` 走 `db.execute(...).scalar_one_or_none()`。
    """
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=org_id)
    db.execute = AsyncMock(return_value=result)
    return db


class TestRequireOrganizationIssue168Regression:
    """issue #168 回归：无组织的超管不得拿到任何写死的组织编号。

    症状：`require_organization` 对无组织超管 `return 1`，而 1 号组织未必存在
    （本库唯一组织 id=3）→ 上传文档时 INSERT organization_id=1 → 外键 1452 → 500。
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("real_org_id", [3, 7])
    async def test_returns_the_real_org_from_db_not_a_constant(self, real_org_id):
        """DB 里有哪个组织就返回哪个 —— 结果跟随 DB，而不是任何写死的常数。"""
        db = _mock_db_with_first_org(real_org_id)
        got = await require_organization(_make_user(is_superuser=True, organization_id=None), db)
        assert got == real_org_id
        assert got != 1, "issue #168：1 号组织不存在时绝不能返回 1"

    @pytest.mark.asyncio
    async def test_org1_absent_never_returns_1(self):
        """构造「1 号组织不存在」的场景（库里第一个组织是 3）→ 绝不返回 1。"""
        db = _mock_db_with_first_org(3)
        got = await require_organization(_make_user(is_superuser=True, organization_id=None), db)
        assert got == 3
        assert got != 1

    @pytest.mark.asyncio
    async def test_no_organization_at_all_is_explicit_error(self):
        """一个组织都没有 → 明确报错（403 语义），不是返回 1/None，也不是等写库 500。"""
        db = _mock_db_with_first_org(None)
        with pytest.raises(AuthorizationError) as ei:
            await require_organization(_make_user(is_superuser=True, organization_id=None), db)
        assert "组织" in str(ei.value)

    @pytest.mark.asyncio
    async def test_member_path_never_consults_org_table(self):
        """有组织的用户走原路径：不额外查 organizations 表。"""
        db = _mock_db_with_first_org(3)
        assert await require_organization(_make_user(organization_id=42), db) == 42
        assert db.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_resolution_is_logged(self, caplog):
        """解析结果可观测：日志里能看到「为该超管解析到的组织是 X」。"""
        import logging as _logging

        db = _mock_db_with_first_org(3)
        with caplog.at_level(_logging.INFO, logger="app.core.security"):
            await require_organization(_make_user(is_superuser=True, organization_id=None), db)
        assert any("3" in r.getMessage() and "组织" in r.getMessage() for r in caplog.records)


class TestOrgLessCallersForbidden:
    """注册产生（organization_id=None）的用户不得读写任何组织的数据。"""

    @pytest.fixture(autouse=True)
    def _permissions_ok(self):
        """权限一律放行，确保 403 来自组织守卫而非权限解析失败。"""
        with _allow_all_permissions():
            yield

    def _client_with(self, client, user, db):
        app_ = client.app
        app_.dependency_overrides[get_db] = _override_get_db(db)
        app_.dependency_overrides[get_current_user] = _override_auth(user)
        return app_

    def test_knowledge_search_forbidden(self, client):
        user = _make_user(organization_id=None)
        db = _mock_db()
        app_ = self._client_with(client, user, db)
        try:
            resp = client.post(
                "/api/v1/knowledge/search",
                json={"query": "revenue", "organization_id": 1},
            )
            assert resp.status_code == 403
        finally:
            app_.dependency_overrides.clear()

    def test_knowledge_stats_forbidden(self, client):
        user = _make_user(organization_id=None)
        db = _mock_db()
        app_ = self._client_with(client, user, db)
        try:
            resp = client.get("/api/v1/knowledge/stats/1")
            assert resp.status_code == 403
        finally:
            app_.dependency_overrides.clear()

    def test_knowledge_suggestions_forbidden(self, client):
        user = _make_user(organization_id=None)
        db = _mock_db()
        app_ = self._client_with(client, user, db)
        try:
            resp = client.get("/api/v1/knowledge/suggestions?q=annual&organization_id=1")
            assert resp.status_code == 403
        finally:
            app_.dependency_overrides.clear()

    def test_files_list_forbidden(self, client):
        user = _make_user(organization_id=None)
        db = _mock_db()
        app_ = self._client_with(client, user, db)
        try:
            resp = client.get("/api/v1/files/list")
            assert resp.status_code == 403
        finally:
            app_.dependency_overrides.clear()

    def test_document_upload_forbidden(self, client):
        """上传不得再回退到最低 ID 的组织或自动创建 Default 组织。"""
        user = _make_user(organization_id=None)
        db = _mock_db()
        db.merge = AsyncMock(return_value=user)
        app_ = self._client_with(client, user, db)
        try:
            resp = client.post(
                "/api/v1/documents/upload",
                files={"file": ("note.txt", b"hello world", "text/plain")},
                data={"title": "note"},
            )
            assert resp.status_code == 403
        finally:
            app_.dependency_overrides.clear()

    def test_document_read_forbidden(self, client):
        """get_document_for_user：无组织用户不再兜底到 1 号组织读文档。"""
        user = _make_user(organization_id=None)
        doc = MagicMock()
        doc.id = "doc-1"
        doc.organization_id = 1
        db = _mock_db(permission_user=doc)
        app_ = self._client_with(client, user, db)
        try:
            resp = client.get("/api/v1/documents/doc-1")
            assert resp.status_code == 403
        finally:
            app_.dependency_overrides.clear()

    def test_create_user_in_org_forbidden(self, client):
        """无组织管理员不得在 1 号组织（或其他任何组织）创建用户。"""
        user = _make_user(role="admin", organization_id=None)
        db = _mock_db()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        db.execute = AsyncMock(return_value=result)
        app_ = self._client_with(client, user, db)
        try:
            resp = client.post(
                "/api/v1/users/",
                json={
                    "username": "newbie",
                    "email": "newbie@example.com",
                    "password": "ValidPass123!",
                    "organization_ids": [1],
                },
            )
            assert resp.status_code == 403
        finally:
            app_.dependency_overrides.clear()

    def test_update_foreign_user_forbidden(self, client):
        """无组织管理员不得修改任何组织的用户。"""
        user = _make_user(role="admin", organization_id=None)
        target = _make_user(organization_id=1, user_id=2)
        db = _mock_db()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=target)
        db.execute = AsyncMock(return_value=result)
        app_ = self._client_with(client, user, db)
        try:
            resp = client.put("/api/v1/users/2", json={"nickname": "hacked"})
            assert resp.status_code == 403
        finally:
            app_.dependency_overrides.clear()

    def test_read_foreign_user_detail_forbidden(self, client):
        """无组织管理员不得读取任何组织的用户详情。"""
        user = _make_user(role="admin", organization_id=None)
        target = _make_user(organization_id=1, user_id=2)
        db = _mock_db()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=target)
        db.execute = AsyncMock(return_value=result)
        app_ = self._client_with(client, user, db)
        try:
            resp = client.get("/api/v1/users/2")
            assert resp.status_code == 403
        finally:
            app_.dependency_overrides.clear()


class TestCrossOrgAdminCannotReadDocuments:
    """租户管理员（role=admin）不再绕过文档的组织隔离。"""

    @pytest.fixture(autouse=True)
    def _permissions_ok(self):
        """权限一律放行，确保 403 来自组织守卫而非权限解析失败。"""
        with _allow_all_permissions():
            yield

    def test_cross_org_admin_document_read_forbidden(self, client):
        user = _make_user(role="admin", organization_id=2)
        doc = MagicMock()
        doc.id = "doc-1"
        doc.organization_id = 1  # 属于租户 1
        db = _mock_db(permission_user=doc)
        app_ = client.app
        app_.dependency_overrides[get_db] = _override_get_db(db)
        app_.dependency_overrides[get_current_user] = _override_auth(user)
        try:
            resp = client.get("/api/v1/documents/doc-1")
            assert resp.status_code == 403
        finally:
            app_.dependency_overrides.clear()

    def test_same_org_document_read_allowed(self, client):
        user = _make_user(role="admin", organization_id=2)
        doc = MagicMock()
        doc.id = "doc-1"
        doc.organization_id = 2
        doc.title = "Doc"
        doc.filename = "doc.txt"
        doc.file_size = 10
        doc.file_type = MagicMock()
        doc.file_type.value = "txt"
        doc.status = MagicMock()
        doc.status.value = "indexed"
        doc.parse_error = None
        doc.created_at = None
        doc.updated_at = None
        doc.parsed_at = None
        doc.indexed_at = None
        doc.chunk_count = 0
        doc.description = None
        doc.keywords = []
        db = _mock_db(permission_user=doc)
        app_ = client.app
        app_.dependency_overrides[get_db] = _override_get_db(db)
        app_.dependency_overrides[get_current_user] = _override_auth(user)
        try:
            resp = client.get("/api/v1/documents/doc-1")
            assert resp.status_code == 200
            assert resp.json()["success"] is True
        finally:
            app_.dependency_overrides.clear()
