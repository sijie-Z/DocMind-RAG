"""存量 owner 回填 OrgAdmin 脚本测试（Batch 16，评审 F2）。

- 缺失关联 → granted；已有 → already；重复执行幂等；
- dry-run 不写库；
- 无 owner 的组织计入 skipped。
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.models.organization import Organization
from app.models.rbac import (
    ORG_ADMIN_ROLE_NAME,
    Permission,
    Role,
    user_organization_role_association,
)
from app.models.user import User
from app.services.permission_service import ORG_ADMIN_PERMISSION_NAMES, permission_service
from scripts.backfill_org_owner_admin import backfill_org_owners


async def _make_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(maker):
    async with maker() as s:
        s.add_all([Permission(name=n, description=n) for n in ORG_ADMIN_PERMISSION_NAMES])
        u1 = User(username="u1", email="u1@x.com", hashed_password="x", is_active=True)
        u2 = User(username="u2", email="u2@x.com", hashed_password="x", is_active=True)
        s.add_all([u1, u2])
        await s.flush()
        org1 = Organization(name="o1", owner_id=u1.id, is_private=True)
        org2 = Organization(name="o2", owner_id=u2.id, is_private=True)
        s.add_all([org1, org2])
        await s.commit()
        # 预置 org2 的 owner 已有 OrgAdmin：先走一次真实 get-or-create 路径
        role = await permission_service.get_or_create_org_admin_role(s)
        await s.execute(
            user_organization_role_association.insert().values(
                user_id=u2.id, organization_id=org2.id, role_id=role.id
            )
        )
        await s.commit()
        return {"u1": u1.id, "u2": u2.id, "org1": org1.id, "org2": org2.id}


@pytest.mark.asyncio
async def test_backfill_grants_missing_and_is_idempotent():
    engine, maker = await _make_db()
    try:
        ids = await _seed(maker)
        async with maker() as s:
            report = await backfill_org_owners(s)
            assert report["granted"] == [ids["org1"]]
            assert set(report["already"]) == {ids["org2"]}
            assert report["skipped"] == []

            report2 = await backfill_org_owners(s)
            assert report2["granted"] == []
            assert set(report2["already"]) == {ids["org1"], ids["org2"]}

            role_id = (
                await s.execute(select(Role.id).where(Role.name == ORG_ADMIN_ROLE_NAME))
            ).scalar_one()
            for uid, oid in ((ids["u1"], ids["org1"]), (ids["u2"], ids["org2"])):
                row = (
                    await s.execute(
                        select(user_organization_role_association).where(
                            user_organization_role_association.c.user_id == uid,
                            user_organization_role_association.c.organization_id == oid,
                            user_organization_role_association.c.role_id == role_id,
                        )
                    )
                ).first()
                assert row is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_dry_run_writes_nothing():
    engine, maker = await _make_db()
    try:
        ids = await _seed(maker)
        async with maker() as s:
            report = await backfill_org_owners(s, dry_run=True)
            assert report["granted"] == [ids["org1"]]
            row = (
                await s.execute(
                    select(user_organization_role_association).where(
                        user_organization_role_association.c.user_id == ids["u1"],
                        user_organization_role_association.c.organization_id == ids["org1"],
                    )
                )
            ).first()
            assert row is None, "dry-run 不得写关联"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_specific_organization_only():
    engine, maker = await _make_db()
    try:
        ids = await _seed(maker)
        async with maker() as s:
            report = await backfill_org_owners(s, organization_id=ids["org2"])
            assert report["granted"] == []
            assert report["already"] == [ids["org2"]]
    finally:
        await engine.dispose()
