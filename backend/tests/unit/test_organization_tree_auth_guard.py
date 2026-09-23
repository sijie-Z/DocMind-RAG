"""`organization_service.get_organization_tree` 的认证主体防护（issue #82 的 5.2）。

原实现直接解引用 `user.is_superuser`。`user` 为 None 时抛 `AttributeError`，
而方法体里的 `except Exception` 把它吞成**空树** —— 认证失败被伪装成一次
成功的空查询，与 `permission_service.py` 的同类缺陷是同一种病。

修后语义（裁定理由见下）：主体不存在 = 401 `AuthenticationError`。
- 401 而非 403：403 的含义是「已认证但权限不足」，而这里根本不知道请求者是谁。
- 401 而非空结果：空结果会把「身份没建立起来」静默降级成一次正常响应，
  调用方（`GET /organization/tree`）无法区分，缺陷只留在日志里。
- 与 issue #82 的 PR A 已统一的 401 契约一致（`_auth_failed()` 的
  「用户不存在 / 账号被禁用 / **主体无法建立**」共用同一响应）。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.endpoints.organizations import (
    get_organization_tree as get_organization_tree_endpoint,
)
from app.exceptions import AuthenticationError
from app.services.organization_service import organization_service


def _make_user(is_superuser: bool = True) -> MagicMock:
    user = MagicMock()
    user.id = 1
    user.username = "admin"
    user.is_superuser = is_superuser
    return user


def _make_org(org_id: int, name: str, parent_id: int | None = None, level: int = 1) -> MagicMock:
    org = MagicMock()
    org.id = org_id
    org.name = name
    org.description = None
    org.color = "#18a058"
    org.is_private = False
    org.parent_id = parent_id
    org.level = level
    org.sort_order = 0
    org.created_at = None
    return org


def _db_returning(orgs: list) -> MagicMock:
    db = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = orgs
    db.execute = AsyncMock(return_value=result)
    return db


async def test_none_user_raises_authentication_error():
    """user 为 None 时必须抛 401，而不是静默返回空树。"""
    db = _db_returning([])

    with pytest.raises(AuthenticationError) as exc_info:
        await organization_service.get_organization_tree(db, None)

    assert exc_info.value.status_code == 401
    # 守卫必须在 `try` 之外：放进 try 会被 `except Exception: return []` 吞掉。
    db.execute.assert_not_awaited()


async def test_superuser_still_gets_tree():
    """正常路径不受影响：超管拿到的树结构与父子关系保持不变。

    （只覆盖超管分支：非超管分支的 `not Organization.is_private` 是既有缺陷，
    恒为 `where(false)` —— 见报告，不在本次修复范围内。）
    """
    orgs = [_make_org(1, "Root"), _make_org(2, "Child", parent_id=1, level=2)]
    db = _db_returning(orgs)

    tree = await organization_service.get_organization_tree(db, _make_user())

    assert [node["name"] for node in tree] == ["Root"]
    assert [child["name"] for child in tree[0]["children"]] == ["Child"]


async def test_endpoint_does_not_repack_authentication_error_as_500():
    """端点必须原样放行 401，不能包成 AppError(500)。

    `organizations.py` 的 `except Exception` 会把领域异常重新包装成 500，
    与 issue #90 是同一修法：先 `except AppError: raise`。
    """
    db = _db_returning([])

    with pytest.raises(AuthenticationError) as exc_info:
        await get_organization_tree_endpoint(current_user=None, db=db)

    assert exc_info.value.status_code == 401


async def test_endpoint_returns_tree_for_authenticated_user():
    """端点的成功路径不受影响。"""
    db = _db_returning([_make_org(1, "Root")])

    response = await get_organization_tree_endpoint(current_user=_make_user(), db=db)

    assert response["success"] is True
    assert [node["name"] for node in response["data"]] == ["Root"]
