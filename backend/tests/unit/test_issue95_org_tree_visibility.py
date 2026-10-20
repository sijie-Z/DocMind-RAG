"""issue #95 回归 + W0-1 组织树租户边界（真执行 SQL）。

既有测试（`test_organization_tree_auth_guard.py`）全部用 `MagicMock` 顶替
`db.execute`，SQL 从不真正执行，因此任何 SQL 层面的缺陷都测不出来 —— 它甚至
在 docstring 里自己承认没覆盖非超管分支。本文件的价值就是：用内存 SQLite
真建表、真插入、真执行查询。

issue #95 缺陷本身：`Organization.is_private` 是 SQLAlchemy 的
`InstrumentedAttribute`（列描述符），`bool()` 它恒为 `True`。所以
`not Organization.is_private` 会先在 Python 层坍缩成 `False`，编译出字面量
`WHERE false = 1` —— 非超管恒拿到空树，且不抛错。

W0-1（ADR-001 D3）语义变更：组织树是跨租户信息面——
- 平台超管（显式 is_superuser）看到完整树；
- 非超管只返回与自己有关系的组织（沿革：W0-1 为“自己所属组织”单节点，
  W0-2 细化为成员表 / 组织角色关联 / 主组织的并集，且只在这些可见节点内
  构建森林）；
- 无组织用户 fail closed：W0-2 起为 403（不再用空树冒充成功）。
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.exceptions import AuthorizationError
from app.models import Base, Organization, User
from app.services.organization_service import organization_service


async def _make_seeded_session():
    """建内存库 + 建全部表 + 插入一公开一私有两条顶级组织，返回 (engine, session, public, private)。

    真跑 SQL 的关键：engine 用 aiosqlite 内存库，`create_all` 建真实 schema，
    组织行通过真实 INSERT 落库（id 由 SQLite 生成）。
    `Base.metadata.create_all` 已验证在 SQLite 上可整包建表（26 张），无需只挑单表。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session = async_sessionmaker(engine, expire_on_commit=False)()
    public = Organization(name="PublicOrg", is_private=False, owner_id=1)
    private = Organization(name="PrivateOrg", is_private=True, owner_id=1)
    session.add_all([public, private])
    await session.commit()
    return engine, session, public, private


@pytest.mark.asyncio
async def test_non_superuser_sees_only_own_org():
    """非超管只看到自己所属组织（公开/私有均同）；不会枚举其他租户。"""
    engine, session, public, private = await _make_seeded_session()
    try:
        tree = await organization_service.get_organization_tree(
            session, User(is_superuser=False, organization_id=public.id)
        )
        assert [node["id"] for node in tree] == [public.id]
        assert [node["name"] for node in tree] == ["PublicOrg"]

        # 私有组织对其成员可见（本租户），但不是"公开可见"
        tree = await organization_service.get_organization_tree(
            session, User(is_superuser=False, organization_id=private.id)
        )
        assert [node["id"] for node in tree] == [private.id]
        assert [node["name"] for node in tree] == ["PrivateOrg"]
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_orgless_non_superuser_gets_403():
    """无组织非超管 fail closed：W0-2 起为 403，不再返回空树。"""
    engine, session, _public, _private = await _make_seeded_session()
    try:
        with pytest.raises(AuthorizationError) as exc_info:
            await organization_service.get_organization_tree(
                session, User(is_superuser=False, organization_id=None)
            )
        assert exc_info.value.status_code == 403
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_superuser_sees_both_orgs():
    """对照组：超管分支不做租户过滤，两条都能看到（真跑 SQL）。"""
    engine, session, public, private = await _make_seeded_session()
    try:
        tree = await organization_service.get_organization_tree(session, User(is_superuser=True))

        assert {node["name"] for node in tree} == {"PublicOrg", "PrivateOrg"}
        assert {node["id"] for node in tree} == {public.id, private.id}
    finally:
        await session.close()
        await engine.dispose()
