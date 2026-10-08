"""#242 收尾（用户面洞口）：注册必须落到"个人组织"，否则全组织域功能 403。

背景：安全加固后注册用户的 organization_id 为 None 且无自助建组织的权限 →
新用户注册即死路（实测：Agent 问答直接 403"未加入任何组织"）。
修复：注册时自动创建个人组织（owner=本人、is_private），**不并入任何既有组织**
（隔离保证不变）。

变异点：删注册端点里的 organization_service.create_organization 调用（回到
organization_id=None）→ 本文件用例红。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


class _AsyncCtxMgr:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def _override_get_db():
    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock())
    mock_db.begin_nested = MagicMock(return_value=_AsyncCtxMgr(mock_db))
    mock_db.get = AsyncMock(return_value=MagicMock())

    async def _override():
        yield mock_db

    return _override, mock_db


def _register(client, mock_user, org_service):
    from app.core.database import get_db
    from app.main import app

    override_func, mock_db = _override_get_db()
    captured: dict = {}

    class _Org:
        id = 7

    async def _create(db, data, owner_id):
        captured["data"] = data
        captured["owner_id"] = owner_id
        return _Org()

    with patch("app.api.v1.endpoints.auth.auth_service") as mas, \
            patch("app.services.organization_service.organization_service", new=org_service):
        mas.get_user_by_username = AsyncMock(return_value=None)
        mas.get_user_by_email = AsyncMock(return_value=None)
        mas.create_user = AsyncMock(return_value=mock_user)
        mas.create_access_token.return_value = "tok"
        mas.create_refresh_token.return_value = "rtok"

        org_service.create_organization = AsyncMock(side_effect=_create)

        app.dependency_overrides[get_db] = override_func
        try:
            r = client.post("/api/v1/auth/register", json={
                "username": "newbie",
                "email": "newbie@example.com",
                "password": "ValidPass123!",
                "full_name": "Newbie",
            })
        finally:
            app.dependency_overrides.pop(get_db, None)

    return r, captured, mas


def _mock_user():
    u = MagicMock()
    u.id = 42
    u.username = "newbie"
    u.email = "newbie@example.com"
    u.full_name = "Newbie"
    u.role = "user"
    u.organization_id = None  # 加固后的注册初态
    u.is_active = True
    return u


def test_register_creates_personal_org_and_backfills_user(client):
    org_service = MagicMock()
    org_service.get_organization_by_name = AsyncMock(return_value=None)
    user = _mock_user()

    r, captured, mas = _register(client, user, org_service)

    assert r.status_code == 200, f"register failed: {r.text[:200]}"
    assert captured["owner_id"] == 42, "个人组织必须属于注册者本人"
    assert captured["data"]["name"] == "newbie 的空间"
    assert captured["data"]["is_private"] is True, "个人空间应为私有（隔离语义）"
    assert user.organization_id == 7, "注册必须回填用户组织（否则全组织域功能 403）"
    jwt_data = mas.create_access_token.call_args.kwargs["data"]
    assert jwt_data["organization_id"] == 7, "JWT 里的组织上下文必须就绪（前端据此放行）"


def test_register_falls_back_on_name_collision(client):
    org_service = MagicMock()
    # 名字被占（理论不可达，username 唯一）→ 必须走带 id 后缀的兜底名，绝不因重名失败
    org_service.get_organization_by_name = AsyncMock(return_value=MagicMock())
    user = _mock_user()

    r, captured, _ = _register(client, user, org_service)

    assert r.status_code == 200
    assert captured["data"]["name"] == "newbie-42 的空间"
    assert user.organization_id == 7
