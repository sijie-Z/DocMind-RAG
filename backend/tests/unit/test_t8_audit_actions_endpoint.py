"""T8 审计「操作类型」筛选项数据源端点判别用例（管理员域复核）。

复核根因（T8）：/audit-logs 前端下拉为硬编码 5 项，其中 'delete' 与库内真实
动作串 'delete_document' 不一致、且库中不存在的动作照样展示——选中即恒空
（实测数据：库内仅 login/delete_document/profile_updated 三种）。
修复：新增 GET /users/audit-logs/actions 按真实数据出选项（组织隔离口径与
列表接口一致，非超管按同组织过滤、无组织用 -1 哨兵 fail closed）。

判别：
- 超管：返回全部真实动作 + 计数（按 count 降序、同 count 按动作名升序）；
- 非超管（org=2）：只见本组织用户的动作；
- 无组织非超管：空（哨兵口径）。
变异点：删除端点 → ImportError 红；去掉组织隔离 → 第 2/3 条精确红。
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.api.v1.endpoints import users as users_module
from app.core.database import Base
from app.models.user import User
from app.models.user_audit import UserActivityLog


def _mk_user(uid: int = 1, org: int | None = None, superuser: bool = True) -> User:
    return User(
        id=uid,
        username=f"u{uid}",
        email=f"u{uid}@example.com",
        hashed_password="x",
        organization_id=org,
        is_superuser=superuser,
        is_active=True,
    )


@pytest.fixture
async def actions_db(tmp_path):
    """3 个用户跨 2 个组织 + 1 无组织；动作分布 login×2 / delete_document×1 / profile_updated×1。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'audit_actions.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                _mk_user(uid=1, org=2, superuser=False),
                _mk_user(uid=2, org=3, superuser=False),
                _mk_user(uid=3, org=None, superuser=False),
            ]
        )
        await session.flush()
        session.add_all(
            [
                UserActivityLog(user_id=1, action="login"),
                UserActivityLog(user_id=1, action="login"),
                UserActivityLog(user_id=2, action="delete_document"),
                UserActivityLog(user_id=3, action="profile_updated"),
            ]
        )
        await session.commit()
        yield session
    await engine.dispose()


async def _call(actions_db, user: User):
    r = await users_module.get_audit_log_actions(current_user=user, db=actions_db)
    assert r["success"] is True, r
    return [(it["action"], it["count"]) for it in r["data"]]


@pytest.mark.asyncio
async def test_actions_superuser_sees_real_distribution(actions_db):
    """超管：只出真实存在的动作与计数（不再有恒空选项）。"""
    rows = await _call(actions_db, _mk_user(uid=99, org=2, superuser=True))
    assert rows == [("login", 2), ("delete_document", 1), ("profile_updated", 1)], rows


@pytest.mark.asyncio
async def test_actions_org_admin_scoped_to_own_org(actions_db):
    """非超管（org=2）：只见本组织用户的动作。"""
    rows = await _call(actions_db, _mk_user(uid=99, org=2, superuser=False))
    assert rows == [("login", 2)], rows


@pytest.mark.asyncio
async def test_actions_orgless_admin_fail_closed(actions_db):
    """无组织非超管：-1 哨兵 → 空列表（与列表/导出接口同口径，不越权聚合）。"""
    rows = await _call(actions_db, _mk_user(uid=99, org=None, superuser=False))
    assert rows == [], rows
