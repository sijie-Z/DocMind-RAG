"""S1/S2 regression tests — org membership checks on organization & user endpoints.

S1: /organizations/{org_id}/... 只校验权限，不校验成员资格。全局 role=admin
    的用户持有整套 SysAdmin 权限，因此租户 B 的管理员可以读写/删除租户 A 的组织
    数据。修复后组织端点显式校验 current_user.organization_id == org_id。
S2: DELETE /users/{user_id} 缺少兄弟端点的组织校验；审计日志列表对无组织
    非超管放行了所有组织的日志。

测试通过 TestClient 覆盖真实 HTTP 状态码，同时直接调用辅助函数断言抛错行为。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints.organizations import _ensure_org_membership
from app.core.database import get_db
from app.core.security import get_current_user
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
    # 真实 User 模型没有该属性（get_user_org_id 的兜底取 None）
    u.token_organization_id = None
    return u


def _mock_db(*, permission_user=None):
    """Mock session.

    db.get 返回超管用户 → permission_required 先行放行，这正是漏洞场景：
    调用者（租户管理员）已通过权限依赖，只有显式的组织成员资格校验才能拦住。
    """
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.rollback = AsyncMock()
    db.close = AsyncMock()
    db.delete = AsyncMock()
    db.get = AsyncMock(return_value=permission_user or _make_user(is_superuser=True))
    return db


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


@pytest.fixture(autouse=True)
def _permissions_ok():
    """权限一律放行。

    develop 的 `permission_required` 现在每次都回源 DB 解析权限，本文件用的是
    轻量 mock session，解析必然为空 —— 那样端点会因为缺权限而 403，测不到
    S1/S2 真正要验证的组织成员资格校验。让权限全通过后，403 才有判别力。
    """
    from app.models.rbac import PermissionType

    with patch(
        "app.services.permission_service.permission_service.get_user_permissions",
        new=AsyncMock(return_value={p.value for p in PermissionType}),
    ):
        yield


class TestOrganizationMembership:
    """S1 — 组织端点必须校验成员资格，而不只是权限。"""

    def test_membership_helper_raises_for_cross_org(self):
        _ensure_org_membership(_make_user(organization_id=2), 2)
        _ensure_org_membership(_make_user(is_superuser=True, organization_id=None), 1)
        with pytest.raises(AuthorizationError):
            # 跨租户管理员（role=admin, org=2）访问 org 1
            _ensure_org_membership(_make_user(role="admin", organization_id=2), 1)
        with pytest.raises(AuthorizationError):
            # 无组织用户不允许访问任何组织
            _ensure_org_membership(_make_user(organization_id=None), 1)

    def test_cross_org_stats_forbidden(self, client):
        user = _make_user(role="admin", organization_id=2)
        db = _mock_db()
        app_ = client.app
        app_.dependency_overrides[get_db] = _override_get_db(db)
        app_.dependency_overrides[get_current_user] = _override_auth(user)
        try:
            resp = client.get("/api/v1/organizations/1/stats")
            assert resp.status_code == 403
        finally:
            app_.dependency_overrides.clear()

    def test_same_org_stats_allowed(self, client):
        user = _make_user(role="admin", organization_id=2)
        db = _mock_db()
        result = MagicMock()
        result.scalar = MagicMock(return_value=5)
        db.execute = AsyncMock(return_value=result)
        app_ = client.app
        app_.dependency_overrides[get_db] = _override_get_db(db)
        app_.dependency_overrides[get_current_user] = _override_auth(user)
        try:
            resp = client.get("/api/v1/organizations/2/stats")
            assert resp.status_code == 200
            assert resp.json()["data"]["member_count"] == 5
        finally:
            app_.dependency_overrides.clear()

    def test_cross_org_members_list_forbidden(self, client):
        user = _make_user(role="admin", organization_id=2)
        db = _mock_db()
        app_ = client.app
        app_.dependency_overrides[get_db] = _override_get_db(db)
        app_.dependency_overrides[get_current_user] = _override_auth(user)
        try:
            resp = client.get("/api/v1/organizations/1/members")
            assert resp.status_code == 403
        finally:
            app_.dependency_overrides.clear()

    def test_batch_delete_foreign_org_forbidden(self, client):
        user = _make_user(role="admin", organization_id=2)
        db = _mock_db()
        app_ = client.app
        app_.dependency_overrides[get_db] = _override_get_db(db)
        app_.dependency_overrides[get_current_user] = _override_auth(user)
        try:
            resp = client.request("DELETE", "/api/v1/organizations/batch", json={"ids": [1, 3]})
            assert resp.status_code == 403
            # 校验必须发生在删除之前
            db.execute.assert_not_called()
        finally:
            app_.dependency_overrides.clear()


class TestUserOrgGuards:
    """S2 — 删除用户必须校验组织；审计日志不得向无组织用户放开全部租户。"""

    def test_delete_foreign_org_user_forbidden(self, client):
        caller = _make_user(role="admin", organization_id=2, user_id=1)
        target = _make_user(organization_id=1, user_id=2)
        db = _mock_db()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=target)
        db.execute = AsyncMock(return_value=result)
        app_ = client.app
        app_.dependency_overrides[get_db] = _override_get_db(db)
        app_.dependency_overrides[get_current_user] = _override_auth(caller)
        try:
            resp = client.delete("/api/v1/users/2")
            assert resp.status_code == 403
        finally:
            app_.dependency_overrides.clear()

    def test_delete_same_org_user_allowed(self, client):
        caller = _make_user(role="admin", organization_id=2, user_id=1)
        target = _make_user(organization_id=2, user_id=2)
        db = _mock_db()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=target)
        db.execute = AsyncMock(return_value=result)
        app_ = client.app
        app_.dependency_overrides[get_db] = _override_get_db(db)
        app_.dependency_overrides[get_current_user] = _override_auth(caller)
        try:
            with patch("app.core.redis.RedisTools.delete_cache", new=AsyncMock()):
                resp = client.delete("/api/v1/users/2")
            assert resp.status_code == 200
            assert target.is_active is False
        finally:
            app_.dependency_overrides.clear()

    def test_audit_logs_orgless_caller_scoped_to_nothing(self, client):
        """无组织非超管不再看到全部租户日志：查询被 organization_id=-1 收紧。"""
        caller = _make_user(organization_id=None, user_id=7)
        db = _mock_db()
        result = MagicMock()
        result.scalar = MagicMock(return_value=0)
        result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        db.execute = AsyncMock(return_value=result)
        app_ = client.app
        app_.dependency_overrides[get_db] = _override_get_db(db)
        app_.dependency_overrides[get_current_user] = _override_auth(caller)
        try:
            resp = client.get("/api/v1/users/audit-logs")
            assert resp.status_code == 200

            sqls, params = [], []
            for call in db.execute.call_args_list:
                stmt = call.args[0]
                try:
                    compiled = stmt.compile()
                except Exception:
                    continue
                sqls.append(str(compiled))
                params.extend(compiled.params.values())
            # 构建出的审计日志查询必须按 organization_id 过滤（-1 兜底＝查不到任何组织）
            assert any("organization_id" in s for s in sqls)
            assert -1 in params
        finally:
            app_.dependency_overrides.clear()

    def test_audit_logs_org_member_scoped_to_own_org(self, client):
        caller = _make_user(organization_id=5, user_id=7)
        db = _mock_db()
        result = MagicMock()
        result.scalar = MagicMock(return_value=0)
        result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        db.execute = AsyncMock(return_value=result)
        app_ = client.app
        app_.dependency_overrides[get_db] = _override_get_db(db)
        app_.dependency_overrides[get_current_user] = _override_auth(caller)
        try:
            resp = client.get("/api/v1/users/audit-logs")
            assert resp.status_code == 200
            params = []
            for call in db.execute.call_args_list:
                stmt = call.args[0]
                try:
                    params.extend(stmt.compile().params.values())
                except Exception:
                    continue
            assert 5 in params
        finally:
            app_.dependency_overrides.clear()
