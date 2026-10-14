"""T8 用户组织分配「后端只取首个」判别用例。

复核结论（T8）：属实——create/update 对 organization_ids 多值静默取 [0]。
但系统数据模型为单组织口径（User.organization_id 单外键；组织成员端点
add/remove 亦写同一字段；注册流回填同一字段）。修复：多值显式 400
（ValidationError），不再静默截断；前端已改为单选，正常路径不触发。
单值 / 空值语义保持（空列表=不指定/不修改）。

变异点：删 guard → 多值两用例红（静默截断仍然"成功"，不抛 ValidationError）。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.endpoints import users as users_module
from app.exceptions import ValidationError


def _mk_user(uid: int = 99, org: int | None = 2, superuser: bool = True) -> MagicMock:
    u = MagicMock()
    u.id = uid
    u.username = f"u{uid}"
    u.email = f"u{uid}@example.com"
    u.organization_id = org
    u.role = "user"
    u.is_superuser = superuser
    u.is_active = True
    u.preferences = None
    u.full_name = None
    return u


def _mock_db(first_result=None):
    """第一次 execute 返回 first_result（含 scalar_one_or_none），其余同为 mock。"""
    db = AsyncMock()
    db.add = MagicMock()  # users.py 里 db.add 为同步调用；用 MagicMock 免 AsyncMock 警告
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=first_result)
    result.all = MagicMock(return_value=[])
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_create_user_multi_org_rejected(monkeypatch):
    """创建传 2 个组织 → 400 ValidationError（不再静默取首个）。"""
    db = _mock_db(first_result=None)  # 用户名/邮箱不存在
    body = users_module.UserCreateAdmin(
        username="newbie", email="newbie@example.com", password="ValidPass123!",
        organization_ids=[2, 3],
    )
    with pytest.raises(ValidationError):
        await users_module.create_user_admin(
            body=body, current_user=_mk_user(superuser=True), db=db
        )


@pytest.mark.asyncio
async def test_create_user_single_org_kept(monkeypatch):
    """单组织语义不变：organization_id=[2] → auth_service 收到 organization_id=2。"""
    created = _mk_user(uid=100, org=2)
    create_mock = AsyncMock(return_value=created)
    monkeypatch.setattr(users_module.auth_service, "create_user", create_mock)
    db = _mock_db(first_result=None)
    body = users_module.UserCreateAdmin(
        username="newbie", email="newbie@example.com", password="ValidPass123!",
        organization_ids=[2],
    )
    r = await users_module.create_user_admin(
        body=body, current_user=_mk_user(superuser=True), db=db
    )
    assert r.get("user_id") == 100, r
    assert create_mock.await_args.kwargs.get("organization_id") == 2


@pytest.mark.asyncio
async def test_update_user_multi_org_rejected(monkeypatch):
    """更新传 2 个组织 → 400 ValidationError（不再静默取首个）。"""
    target = _mk_user(uid=2, org=2, superuser=False)
    db = _mock_db(first_result=target)
    body = users_module.UserUpdateAdmin(organization_ids=[2, 3])
    with pytest.raises(ValidationError):
        await users_module.update_user_admin(
            user_id=2, body=body, current_user=_mk_user(superuser=True), db=db
        )
    # 未被静默改写
    assert target.organization_id == 2


@pytest.mark.asyncio
async def test_update_user_single_org_kept(monkeypatch):
    """单组织语义不变：organization_ids=[3] → 落 organization_id=3。"""
    target = _mk_user(uid=2, org=2, superuser=False)
    db = _mock_db(first_result=target)
    body = users_module.UserUpdateAdmin(organization_ids=[3])
    r = await users_module.update_user_admin(
        user_id=2, body=body, current_user=_mk_user(superuser=True), db=db
    )
    assert r.get("user_id") == 2, r
    assert target.organization_id == 3
