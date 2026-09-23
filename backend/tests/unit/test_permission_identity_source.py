"""`permission_required` 的身份来源（issue #82 / PR B）。

`check_permissions` 原先会先 `await db.get(User, current_user.id)` 再判一次超管位。
在 `get_current_user` 已经**每次请求回源 DB** 之后，那次二次查询既多余、又有害：
它暗示「存在第二个更权威的 user 行」，而 PR B 的前提恰恰是**只有一个权威**。
它只补救了超管位一个字段，其余字段（is_active / role / organization_id）
仍然可能来自别处 —— 半套真相比没有真相更容易骗人。

`get_user_permissions` 的 `user is None` 防护则是纵深防御：它支持传 int 的分支，
`db.get` 查不到时会返回 None，而「认证主体不存在」是 401 不是 500。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.security import permission_required
from app.exceptions import AuthenticationError
from app.models.rbac import PermissionType
from app.services.permission_service import permission_service


def _request():
    """`check_permissions` 只用到 path_params / method / query_params。"""
    request = MagicMock()
    request.path_params = {}
    request.method = "GET"
    request.query_params = {}
    return request


def _user(is_superuser: bool = False, role: str = "user") -> MagicMock:
    user = MagicMock()
    user.id = 42
    user.is_superuser = is_superuser
    user.role = role
    return user


def _db_with_no_permissions() -> MagicMock:
    """非超管路径会查系统角色权限；这里让它查到空集。"""
    db = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result)
    return db


# ── check_permissions：直接信任 current_user ─────────────────────


@pytest.mark.asyncio
async def test_superuser_bypass_reads_current_user_without_a_second_query():
    """超管放行只看 `current_user`，不再回查一次用户表。"""
    db = MagicMock()
    db.get = AsyncMock(side_effect=AssertionError("不应再二次查询 User"))

    checker = permission_required([PermissionType.VIEW_USER_DETAIL])

    assert await checker(request=_request(), current_user=_user(is_superuser=True), db=db) is True


@pytest.mark.asyncio
async def test_bypass_is_not_granted_by_a_second_db_lookup():
    """反向守卫：即使有人把二次查询加回来，也拿不到「另一个更权威的我」。

    `current_user` 已经是 DB 的当前状态，不存在第二套真相；这条会抓住
    「再查一次用户表来决定超管位」的写法 —— 那种写法会让权限判定与
    身份判定读取两个不同的快照。
    """
    db = _db_with_no_permissions()
    db.get = AsyncMock(return_value=_user(is_superuser=True))  # 若又查一次，就会拿到超管

    checker = permission_required([PermissionType.VIEW_USER_DETAIL])

    with pytest.raises(HTTPException) as exc:
        await checker(request=_request(), current_user=_user(role="user"), db=db)

    assert exc.value.status_code == 403
    db.get.assert_not_called()


# ── get_user_permissions：None 主体的防护 ────────────────────────


@pytest.mark.asyncio
async def test_none_principal_raises_authentication_error():
    """传 None 进来 → 401，而不是一路走到 `user.is_superuser` 抛 500。"""
    with pytest.raises(AuthenticationError) as exc:
        await permission_service.get_user_permissions(MagicMock(), None)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_unknown_user_id_raises_authentication_error():
    """传 int 且该用户已不存在（`db.get` 返回 None）→ 401 而不是 500。"""
    db = MagicMock()
    db.get = AsyncMock(return_value=None)

    with pytest.raises(AuthenticationError) as exc:
        await permission_service.get_user_permissions(db, 999999)

    assert exc.value.status_code == 401
