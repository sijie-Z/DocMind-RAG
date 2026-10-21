"""A2 ④：token 组织回退必须校验 user_organization 成员关系。

- 成员在册：旧 token 携带的 organization_id 可作为回退上下文；
- 成员被移出（成员关系删除）：旧 token 下一次请求立即 403（不等待 token 过期）；
- 用户主组织（DB 权威字段）不依赖成员表，但成员管理的两个端点必须维护成员表，
  否则「移除」无法让回退 token 失效。

真 SQLite + 真实签名的 JWT，仅将 token 黑名单查询打桩（本机无 Redis）。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.security import get_user_org_id
from app.models import Base
from app.models.organization import Organization, user_organization
from app.models.rbac import Role, user_organization_role_association
from app.models.user import User
from app.services.auth_service import auth_service


async def _setup():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all(
            [
                Organization(id=1, name="OrgA", owner_id=8),
                Organization(id=2, name="OrgB", owner_id=9),
                User(
                    id=7,
                    username="moved",
                    email="m7@example.com",
                    hashed_password="x",
                    role="user",
                    organization_id=None,
                    is_active=True,
                ),
                User(
                    id=8,
                    username="adminA",
                    email="m8@example.com",
                    hashed_password="x",
                    role="admin",
                    organization_id=1,
                    is_active=True,
                ),
                User(
                    id=9,
                    username="primary",
                    email="m9@example.com",
                    hashed_password="x",
                    role="user",
                    organization_id=2,
                    is_active=True,
                ),
                Role(id=1, name="test-role"),
            ]
        )
        await s.flush()
        await s.execute(user_organization.insert().values(user_id=7, organization_id=1))
        await s.execute(
            user_organization_role_association.insert().values(
                user_id=7, organization_id=1, role_id=1
            )
        )
        await s.commit()
    return engine, factory


def _token(user_id: int = 7, org_id: int = 1) -> str:
    return auth_service.create_access_token(
        {"sub": f"user{user_id}", "user_id": user_id, "role": "user", "organization_id": org_id}
    )


async def _authenticate(factory, token: str):
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with patch.object(auth_service, "is_token_blacklisted", new=AsyncMock(return_value=False)):
        async with factory() as s:
            return await auth_service.get_current_user(credentials=creds, db=s)


@pytest.mark.asyncio
async def test_member_token_fallback_is_accepted():
    engine, factory = await _setup()
    try:
        user = await _authenticate(factory, _token(7, 1))
        assert user.token_organization_id == 1
        assert get_user_org_id(user) == 1, "在册成员的 token 组织回退应可用"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_removed_member_old_token_immediately_gets_403():
    engine, factory = await _setup()
    try:
        # 移出成员：删除成员关系（remove_organization_member 的新行为）
        async with factory() as s:
            await s.execute(
                user_organization.delete().where(
                    (user_organization.c.user_id == 7) & (user_organization.c.organization_id == 1)
                )
            )
            await s.commit()

        with pytest.raises(HTTPException) as ei:
            await _authenticate(factory, _token(7, 1))
        assert ei.value.status_code == 403, "被移出组织的旧 token 必须立即 403"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_primary_org_is_authoritative_without_membership_row():
    """主组织取自 DB 用户行本身，不因成员表缺行而误伤（避免过度收紧）。"""
    engine, factory = await _setup()
    try:
        user = await _authenticate(factory, _token(9, 2))
        assert get_user_org_id(user) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_remove_member_endpoint_deletes_membership_row():
    from app.api.v1.endpoints.organizations import remove_organization_member

    engine, factory = await _setup()
    try:
        # 现实状态：待移除成员的 users.organization_id 指向该组织
        async with factory() as s:
            member = await s.get(User, 7)
            member.organization_id = 1
            await s.commit()

        admin = SimpleNamespace(
            id=8, organization_id=1, role="admin", is_superuser=False, is_active=True
        )
        async with factory() as s:
            with patch("app.api.v1.endpoints.organizations.create_notification", new=AsyncMock()):
                await remove_organization_member(1, 7, admin, s)
            row = (
                await s.execute(
                    select(user_organization.c.user_id).where(user_organization.c.user_id == 7)
                )
            ).first()
            assert row is None, "移除成员必须删除 user_organization 成员关系"
            role_rows = (
                await s.execute(select(user_organization_role_association.c.user_id))
            ).all()
            assert role_rows == [], "移除成员必须清理该组织下的角色关联（评审 m-4）"
            moved = await s.get(User, 7)
            assert moved.organization_id is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ws_handshake_rejects_removed_member_token():
    """评审 m-3：WS 握手复用同一成员校验，被移出成员的旧 token 不能建连。"""
    import app.core.ws_auth as ws_auth

    class _FakeWS:
        def __init__(self, token: str):
            self.headers = {"sec-websocket-protocol": f"auth.{token}"}
            self.query_params: dict[str, str] = {}
            self.closed: tuple[int, str] | None = None

        async def close(self, code: int = 1000, reason: str = ""):
            self.closed = (code, reason)

    engine, factory = await _setup()
    try:
        token = _token(7, 1)
        with patch.object(auth_service, "is_token_blacklisted", new=AsyncMock(return_value=False)):
            # 成员在册：握手成功，返回用户状态
            async with factory() as s:
                state = await ws_auth.authenticate_ws(_FakeWS(token), s)
            assert state is not None and state.id == 7

            # 移出组织后：握手立即拒绝（close 4001 + 明确 reason）
            async with factory() as s:
                await s.execute(
                    user_organization.delete().where(
                        (user_organization.c.user_id == 7)
                        & (user_organization.c.organization_id == 1)
                    )
                )
                await s.commit()
            ws = _FakeWS(token)
            async with factory() as s:
                state = await ws_auth.authenticate_ws(ws, s)
            assert state is None
            assert ws.closed == (4001, ws_auth.WS_REASON_ORG_MEMBERSHIP)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_add_member_endpoint_inserts_membership_row():
    from app.api.v1.endpoints.organizations import add_organization_member

    engine, factory = await _setup()
    try:
        # 先移除，再添加，验证成员表被重建
        async with factory() as s:
            await s.execute(user_organization.delete().where(user_organization.c.user_id == 7))
            await s.commit()

        admin = SimpleNamespace(
            id=8, organization_id=1, role="admin", is_superuser=False, is_active=True
        )
        async with factory() as s:
            with patch("app.api.v1.endpoints.organizations.create_notification", new=AsyncMock()):
                await add_organization_member(1, 7, admin, s)
            row = (
                await s.execute(
                    select(user_organization.c.user_id).where(
                        (user_organization.c.user_id == 7)
                        & (user_organization.c.organization_id == 1)
                    )
                )
            ).first()
            assert row is not None, "添加成员必须写入 user_organization 成员关系"
    finally:
        await engine.dispose()
