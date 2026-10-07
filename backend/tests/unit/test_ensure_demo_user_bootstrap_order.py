"""issue #233：空库首启的自举顺序。

`organizations.owner_id` 外键指向 `users.id`，而 `users.organization_id` 允许 NULL。
正确顺序：先建用户（organization_id=None）→ 再建组织（owner_id=该用户）→ 回填。
旧顺序（先建 owner_id=1 的组织）在空库上外键必然失败，且异常被吞——
全新部署表现为"登录不了"，而非"种子失败"。
"""
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.core.ensure_demo_user as edu


def _patch_session(monkeypatch, existing_user):
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = existing_user
    db.execute = AsyncMock(return_value=exec_result)

    @contextlib.asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(edu, "AsyncSessionLocal", _session)
    return db


@pytest.mark.asyncio
async def test_fresh_db_creates_user_before_org(monkeypatch):
    """空库：create_user(organization_id=None) → ensure_org(owner_id=新用户 id) → 回填。"""
    calls = []
    db = _patch_session(monkeypatch, existing_user=None)

    async def fake_create_user(db_, **kwargs):
        calls.append(("create_user", kwargs))
        return SimpleNamespace(id=7, organization_id=kwargs.get("organization_id"))

    monkeypatch.setattr(edu.auth_service, "create_user", fake_create_user)

    async def fake_ensure_org(db_, owner_id):
        calls.append(("ensure_org", owner_id))
        return 42

    monkeypatch.setattr(edu, "_ensure_default_org", fake_ensure_org)

    await edu.ensure_demo_user()

    kinds = [c[0] for c in calls]
    assert kinds == ["create_user", "ensure_org"], (
        f"自举顺序错误（issue #233）：必须先建用户再建组织，实际 {calls}"
    )
    assert calls[0][1]["organization_id"] is None, "建用户时应先置空组织（循环外键的解法）"
    assert calls[1][1] == 7, "组织的 owner_id 必须是刚建出的用户 id，而不是硬编码 1"
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_existing_user_without_org_gets_backfilled(monkeypatch):
    """老库升级路径：用户已存在但 organization_id 为空 → 补齐，不重置密码。"""
    calls = []
    user = SimpleNamespace(id=3, organization_id=None)
    db = _patch_session(monkeypatch, existing_user=user)

    async def fake_create_user(db_, **kwargs):
        raise AssertionError("用户已存在，不应再创建")

    monkeypatch.setattr(edu.auth_service, "create_user", fake_create_user)

    async def fake_ensure_org(db_, owner_id):
        calls.append(owner_id)
        return 9

    monkeypatch.setattr(edu, "_ensure_default_org", fake_ensure_org)

    await edu.ensure_demo_user()

    assert calls == [3], "组织应以已存在用户的 id 为 owner"
    assert user.organization_id == 9, "应回填用户的组织归属"
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_existing_user_with_org_is_untouched(monkeypatch):
    """稳定态：用户与组织齐备 → 不做任何 commit（不重置密码）。"""
    user = SimpleNamespace(id=3, organization_id=9)
    db = _patch_session(monkeypatch, existing_user=user)

    async def fake_ensure_org(db_, owner_id):
        return 9

    monkeypatch.setattr(edu, "_ensure_default_org", fake_ensure_org)

    await edu.ensure_demo_user()

    assert user.organization_id == 9
    assert db.commit.await_count == 0
