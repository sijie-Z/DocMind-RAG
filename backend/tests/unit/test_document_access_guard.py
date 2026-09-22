"""`get_document_for_user` 的异常契约。

回归 issue #90：该函数原本抛 `HTTPException(404/403)`。**状态码是对的**，但
`HTTPException` 不是 `AppError`，于是调用方清一色的

    except (AppError, NotFoundError, AuthorizationError, ...):
        raise
    except Exception as e:
        raise AppError(f"...失败: {e}")

接不住它 —— 落进 `except Exception` 被包装成 `AppError` → **500**。用户拿到 500 而不是
404/403，日志里还记着「404: 文档不存在」，自相矛盾。

**所以下面断言的核心不是「状态码是 404」，而是「异常类型必须是 `AppError` 子类」** ——
只有后者才能保证调用方的 `except` 子句接得住。这也是为什么第一条测试要显式断言
`isinstance(exc, AppError)`。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.security import get_document_for_user
from app.exceptions import AppError, AuthorizationError, NotFoundError


def _user(*, org_id: int | None = 1, role: str = "user", is_superuser: bool = False):
    user = MagicMock()
    user.organization_id = org_id
    user.role = role
    user.is_superuser = is_superuser
    return user


def _db_returning(document):
    db = AsyncMock()
    db.get = AsyncMock(return_value=document)
    return db


def _document(org_id: int = 1):
    doc = MagicMock()
    doc.organization_id = org_id
    return doc


# ── 断言异常类型，而不只是状态码 ────────────────────────────

@pytest.mark.asyncio
async def test_missing_document_raises_apperror_not_httpexception():
    """文档不存在 → `NotFoundError`（`AppError` 子类），不是裸的 404 状态码。"""
    with pytest.raises(NotFoundError) as exc:
        await get_document_for_user(_db_returning(None), _user(), "does-not-exist")

    assert isinstance(exc.value, AppError)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_cross_org_document_raises_apperror_not_httpexception():
    """跨组织访问 → `AuthorizationError`（`AppError` 子类）。"""
    db = _db_returning(_document(org_id=999))

    with pytest.raises(AuthorizationError) as exc:
        await get_document_for_user(db, _user(org_id=1), "doc-1")

    assert isinstance(exc.value, AppError)
    assert exc.value.status_code == 403


# ── 放行路径不受影响 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_same_org_document_is_returned():
    document = _document(org_id=1)

    assert await get_document_for_user(_db_returning(document), _user(org_id=1), "doc-1") is document


@pytest.mark.asyncio
async def test_superuser_crosses_org_boundary():
    document = _document(org_id=999)

    result = await get_document_for_user(
        _db_returning(document), _user(org_id=1, is_superuser=True), "doc-1"
    )

    assert result is document


@pytest.mark.asyncio
async def test_admin_crosses_org_boundary():
    document = _document(org_id=999)

    result = await get_document_for_user(
        _db_returning(document), _user(org_id=1, role="admin"), "doc-1"
    )

    assert result is document
