"""集成测试共享 fixture：往**真库**里 seed 一条 id=1 的组织与用户。

背景（issue #83）
-----------------
`tests/integration/` 下有一部分测试只 override 了 `get_current_user`，没有
override `get_db`。于是 `app/core/security.py:82` 的

    user = await db.get(User, current_user.id)

会打到**真实数据库**；真库是空的 → 返回 None → 紧接着
`app/services/permission_service.py:30` 对 None 取 `.is_superuser` →
`AttributeError` → 500。

本 fixture 不去替换任何 dependency（那是测试自己的意图，不能动），
只负责让真库里**真的有**那条数据，从而把「数据缺失」与「产品缺陷」两类
失败区分开。

seed 内容
---------
* `organizations.id = 1`（`organizations.owner_id` 是 NOT NULL 外键，必须先有用户）
* `users.id = 1`（测试里 mock 的 `current_user.id` 恒为 1）
* 可选：`permission_service.initialize_default_permissions_and_roles()`
  建出 `Permission` / `Role` / `SysAdmin` / `User` 等 RBAC 基线数据

`users.organization_id` → `organizations.id` 与 `organizations.owner_id` → `users.id`
互为外键，插入顺序必须拆成三步（先建用户且 organization_id 留空，再建组织，
最后回填），否则会撞外键约束。

模式开关
--------
`DOCMIND_TEST_SEED_MODE` 控制 seed 的深度，用于隔离实验变量：

* ``full``（默认）—— 建用户 + 组织 + 调 `initialize_default_permissions_and_roles()`。
  非超管用户 role="user"，因此拿到 `User` 系统角色的权限集合
  （含 DOCUMENT_UPLOAD / VIEW_KNOWLEDGE_BASE / SEARCH_KNOWLEDGE_BASE）。
* ``bare``   —— 只建用户 + 组织，不建任何 RBAC 行。
  此时非超管用户的权限集合恒为空。
* ``superuser`` —— 建用户 + 组织，并把该用户置为 `is_superuser=True`。
  这样 `security.py:83-84` 的上帝模式会直接放行，不需要任何 RBAC 行。
* ``off``   —— 完全不 seed，用作复现未修复基线的对照组。

teardown 会把上述记录清掉（含 `initialize_default_permissions_and_roles()`
写下的**全局** RBAC 基线数据），保证可重复运行。
"""
import asyncio
import os

import pytest

_SEED_MODE = os.getenv("DOCMIND_TEST_SEED_MODE", "full").strip().lower()

SEED_USER_ID = 1
SEED_ORG_ID = 1
SEED_USERNAME = "pytest_seed_user"
SEED_USER_EMAIL = "pytest_seed_user@example.invalid"
SEED_ORG_NAME = "pytest-seed-org"

# 建组织前必须先解除 users.organization_id 的引用，否则删组织会撞外键。
_PURGE_SQL = (
    "DELETE FROM user_organization WHERE user_id = :uid OR organization_id = :oid",
    "DELETE FROM user_organization_role_association WHERE user_id = :uid OR organization_id = :oid",
    "UPDATE users SET organization_id = NULL WHERE organization_id = :oid",
    "DELETE FROM organizations WHERE id = :oid OR owner_id = :uid",
    "DELETE FROM users WHERE id = :uid",
)

# `initialize_default_permissions_and_roles()` 写的是**全局**基线数据，不属于
# 某个测试。若不清理，第一次 `full` 运行留下的 23 条 permission / 2 个 role 会
# 让后续 `bare` 运行「看起来也有权限」——这正是本 fixture 最初漏掉的坑。
_RBAC_PURGE_SQL = (
    "DELETE FROM user_organization_role_association",
    "DELETE FROM role_permission_association",
    "DELETE FROM roles WHERE name IN ('SysAdmin', 'User')",
)


async def _purge(db) -> None:
    """删掉本 fixture 可能留下的记录（幂等）。"""
    from sqlalchemy import text

    for stmt in _PURGE_SQL:
        try:
            await db.execute(text(stmt), {"uid": SEED_USER_ID, "oid": SEED_ORG_ID})
            await db.commit()
        except Exception:
            await db.rollback()


async def _purge_rbac(db) -> None:
    """清掉 `initialize_default_permissions_and_roles()` 产生的全局基线数据。

    permission 名单由 `PermissionType` 枚举驱动，不手抄，避免和产品代码漂移。
    """
    from sqlalchemy import bindparam, text

    from app.models.rbac import PermissionType

    for stmt in _RBAC_PURGE_SQL:
        try:
            await db.execute(text(stmt))
            await db.commit()
        except Exception:
            await db.rollback()

    names = [p.value for p in PermissionType]
    try:
        await db.execute(
            text("DELETE FROM permissions WHERE name IN :names").bindparams(
                bindparam("names", value=names, expanding=True)
            )
        )
        await db.commit()
    except Exception:
        await db.rollback()


async def _seed() -> None:
    from app.core.database import AsyncSessionLocal, engine
    from app.models.organization import Organization
    from app.models.user import User

    try:
        async with AsyncSessionLocal() as db:
            await _purge(db)
            await _purge_rbac(db)

            # 第 1 步：先建用户，organization_id 暂留空（否则组织还不存在，外键失败）。
            user = User(
                id=SEED_USER_ID,
                username=SEED_USERNAME,
                email=SEED_USER_EMAIL,
                hashed_password="not-a-real-hash-seed-only",
                full_name="Pytest Seed User",
                organization_id=None,
                role="user",
                is_active=True,
                is_superuser=(_SEED_MODE == "superuser"),
            )
            db.add(user)
            await db.flush()

            # 第 2 步：组织，owner_id 指向刚建好的用户（NOT NULL 外键）。
            org = Organization(
                id=SEED_ORG_ID,
                name=SEED_ORG_NAME,
                description="seed for integration tests",
                is_private=False,
                parent_id=None,
                level=1,
                sort_order=0,
                owner_id=SEED_USER_ID,
            )
            db.add(org)
            await db.flush()

            # 第 3 步：回填用户的组织归属。
            user.organization_id = SEED_ORG_ID
            await db.commit()

        # RBAC 基线数据走产品自己的初始化方法（它内部另开 session）。
        if _SEED_MODE == "full":
            from app.services.permission_service import permission_service

            await permission_service.initialize_default_permissions_and_roles()
    finally:
        # seed 用的是 asyncio.run 的临时事件循环；连接池里可能留着绑定到该
        # 循环的连接，dispose 掉，避免后续 TestClient 的循环复用到它们。
        await engine.dispose()


async def _teardown() -> None:
    from app.core.database import AsyncSessionLocal, engine

    try:
        async with AsyncSessionLocal() as db:
            await _purge(db)
            await _purge_rbac(db)
    except Exception:
        pass
    finally:
        await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def seeded_test_data():
    """会话级 autouse：跑任何集成测试前，先把 id=1 的组织/用户写进真库。"""
    if _SEED_MODE == "off":
        # 对照组：完全不 seed，用来复现未修复的基线。
        yield
        return
    asyncio.run(_seed())
    yield
    asyncio.run(_teardown())
