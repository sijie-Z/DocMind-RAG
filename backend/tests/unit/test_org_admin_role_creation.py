"""OrgAdmin get-or-create 分支（Batch 16，评审 F1/F3）。

评审实测：只种 Permission、不种 OrgAdmin 角色时，旧实现的
``role.permissions.append(...)`` 会在 async 会话中触发惰性加载 →
MissingGreenlet（注册路径 500）。本文件锁住两个分支。
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.models.rbac import ORG_ADMIN_ROLE_NAME, Permission, Role
from app.services.permission_service import ORG_ADMIN_PERMISSION_NAMES, permission_service


async def _make_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_org_admin_role_created_when_only_permissions_seeded():
    engine, maker = await _make_db()
    try:
        async with maker() as s:
            s.add_all([Permission(name=n, description=n) for n in ORG_ADMIN_PERMISSION_NAMES])
            await s.commit()

            role = await permission_service.get_or_create_org_admin_role(s)
            await s.commit()
            assert {p.name for p in role.permissions} == set(ORG_ADMIN_PERMISSION_NAMES)

            # 第二次调用走「已有角色」分支，必须幂等且不重复建角色
            role2 = await permission_service.get_or_create_org_admin_role(s)
            await s.commit()
            assert {p.name for p in role2.permissions} == set(ORG_ADMIN_PERMISSION_NAMES)
            names = (await s.execute(select(Role.name))).scalars().all()
            assert names.count(ORG_ADMIN_ROLE_NAME) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_org_admin_role_extends_missing_permissions():
    engine, maker = await _make_db()
    try:
        async with maker() as s:
            # 先只种前 3 个权限 + 一个只带其中一个权限的 OrgAdmin 角色
            first_three = ORG_ADMIN_PERMISSION_NAMES[:3]
            seeded = [Permission(name=n, description=n) for n in first_three]
            s.add_all(seeded)
            await s.flush()
            role = Role(
                name=ORG_ADMIN_ROLE_NAME,
                description="OrgAdmin",
                is_system_role=False,
                permissions=[seeded[0]],
            )
            s.add(role)
            await s.commit()

            # 再补齐其余权限，调用 get-or-create 应把缺失项挂满
            s.add_all(
                [
                    Permission(name=n, description=n)
                    for n in ORG_ADMIN_PERMISSION_NAMES
                    if n not in first_three
                ]
            )
            await s.commit()

            role2 = await permission_service.get_or_create_org_admin_role(s)
            await s.commit()
            assert {p.name for p in role2.permissions} == set(ORG_ADMIN_PERMISSION_NAMES)
    finally:
        await engine.dispose()
