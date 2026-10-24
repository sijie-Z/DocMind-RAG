"""``require_scope`` / ``require_jwt_user`` 单元测试（issue #496）。

三态口径（JWT 用户行为必须与修复前一致）：
- JWT 用户没有 ``api_key_scopes`` 属性 → 全量（沿用 RBAC）；
- ``api_key_scopes`` 为 None（显式）→ 全量；
- ``dm_`` key → 列表必须显式包含所需 scope，缺失 403 ``AUTHORIZATION_ERROR``。

``/api-keys`` 专用门禁：JWT 放行；任何 ``dm_`` key（``api_key_id`` 已挂载）一律 403。
"""

from types import SimpleNamespace

import pytest

from app.core.security import require_jwt_user, require_scope
from app.exceptions import AuthorizationError


def _jwt_user(**extra):
    user = SimpleNamespace(id=1)
    for key, value in extra.items():
        setattr(user, key, value)
    return user


async def test_jwt_user_without_attribute_is_unrestricted():
    checker = require_scope("chat:write")
    user = _jwt_user()
    assert await checker(user) is user


async def test_explicit_none_scopes_is_unrestricted():
    checker = require_scope("rpc")
    user = _jwt_user(api_key_scopes=None)
    assert await checker(user) is user


async def test_api_key_with_required_scope_passes():
    checker = require_scope("knowledge:read")
    user = _jwt_user(api_key_id="k-1", api_key_scopes=["knowledge:read", "rpc"])
    assert await checker(user) is user


async def test_api_key_without_required_scope_is_403():
    checker = require_scope("chat:write")
    user = _jwt_user(api_key_id="k-1", api_key_scopes=["chat:read"])
    with pytest.raises(AuthorizationError) as exc:
        await checker(user)
    assert exc.value.status_code == 403
    assert exc.value.error_code == "AUTHORIZATION_ERROR"
    assert "chat:write" in exc.value.message


async def test_empty_scope_list_denies_everything():
    checker = require_scope("chat:read")
    user = _jwt_user(api_key_id="k-1", api_key_scopes=[])
    with pytest.raises(AuthorizationError):
        await checker(user)


@pytest.mark.parametrize("container", [tuple, set, frozenset])
async def test_scope_container_types_are_honoured(container):
    checker = require_scope("chat:read")
    user = _jwt_user(api_key_id="k-1", api_key_scopes=container(["chat:read"]))
    assert await checker(user) is user


async def test_require_jwt_user_allows_jwt():
    user = _jwt_user()
    assert await require_jwt_user(user) is user


async def test_require_jwt_user_rejects_api_key_even_with_all_scopes():
    user = _jwt_user(
        api_key_id="k-1",
        api_key_scopes=["chat:read", "chat:write", "knowledge:read", "rpc"],
    )
    with pytest.raises(AuthorizationError) as exc:
        await require_jwt_user(user)
    assert exc.value.status_code == 403
    assert exc.value.error_code == "AUTHORIZATION_ERROR"


async def test_require_jwt_user_rejects_scopes_marker_without_key_id():
    """纵深防御：将来若新增认证路径只挂 scopes 不挂 id，也不得管理 key。"""
    user = _jwt_user(api_key_scopes=["chat:read"])
    with pytest.raises(AuthorizationError):
        await require_jwt_user(user)
