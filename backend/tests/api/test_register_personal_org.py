"""注册必须落到"个人组织"——#242 收尾契约 + #254 复核残余收口。

#242：安全加固后注册用户的 organization_id 为 None 且无自助建组织的权限 →
新用户注册即死路（实测：Agent 问答直接 403"未加入任何组织"）。
修复：注册时自动创建个人组织（owner=本人、is_private），**不并入任何既有组织**
（隔离保证不变）。

#254（本文件本轮扩展）：复核 5 条 minor 中与注册路径相关的收口——
- 重名兜底注释校正：该分支真实可达（管理员预建同名 / MySQL CI collation 近名 /
  用户名复用），保留分支、改措辞；
- 三级兜底：两级重名后追加随机后缀，注册不再因组织名 UNIQUE 冲突 → 500；
- 真库固化（内存 SQLite，FK 强制 ON）：注册事务/成员/回填语义 + 崩溃注入用例，
  把复核当时用真 MySQL 承担的关键路径验证沉入 CI（先例
  tests/unit/test_issue95_org_tree_visibility.py）。

变异点：删注册端点里的 organization_service.create_organization 调用（回到
organization_id=None）→ 本文件用例红。
"""

import re
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


class _AsyncCtxMgr:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def _override_get_db():
    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock())
    mock_db.begin_nested = MagicMock(return_value=_AsyncCtxMgr(mock_db))
    mock_db.get = AsyncMock(return_value=MagicMock())

    async def _override():
        yield mock_db

    return _override, mock_db


def _register(client, mock_user, org_service):
    from app.core.database import get_db
    from app.main import app

    override_func, mock_db = _override_get_db()
    captured: dict = {}

    class _Org:
        id = 7

    async def _create(db, data, owner_id):
        captured["data"] = data
        captured["owner_id"] = owner_id
        return _Org()

    with (
        patch("app.api.v1.endpoints.auth.auth_service") as mas,
        patch("app.services.organization_service.organization_service", new=org_service),
    ):
        mas.get_user_by_username = AsyncMock(return_value=None)
        mas.get_user_by_email = AsyncMock(return_value=None)
        mas.create_user = AsyncMock(return_value=mock_user)
        mas.create_access_token.return_value = "tok"
        mas.create_refresh_token.return_value = "rtok"

        org_service.create_organization = AsyncMock(side_effect=_create)

        app.dependency_overrides[get_db] = override_func
        try:
            r = client.post(
                "/api/v1/auth/register",
                json={
                    "username": "newbie",
                    "email": "newbie@example.com",
                    "password": "ValidPass123!",
                    "full_name": "Newbie",
                },
            )
        finally:
            app.dependency_overrides.pop(get_db, None)

    return r, captured, mas


def _mock_user():
    u = MagicMock()
    u.id = 42
    u.username = "newbie"
    u.email = "newbie@example.com"
    u.full_name = "Newbie"
    u.role = "user"
    u.organization_id = None  # 加固后的注册初态
    u.is_active = True
    return u


def test_register_creates_personal_org_and_backfills_user(client):
    org_service = MagicMock()
    org_service.get_organization_by_name = AsyncMock(return_value=None)
    user = _mock_user()

    r, captured, mas = _register(client, user, org_service)

    assert r.status_code == 200, f"register failed: {r.text[:200]}"
    assert captured["owner_id"] == 42, "个人组织必须属于注册者本人"
    assert captured["data"]["name"] == "newbie 的空间"
    assert captured["data"]["is_private"] is True, "个人空间应为私有（隔离语义）"
    assert user.organization_id == 7, "注册必须回填用户组织（否则全组织域功能 403）"
    jwt_data = mas.create_access_token.call_args.kwargs["data"]
    assert jwt_data["organization_id"] == 7, "JWT 里的组织上下文必须就绪（前端据此放行）"


def test_register_falls_back_on_name_collision(client):
    org_service = MagicMock()
    # 一级名被占（真实可达：管理员预建同名组织 / CI collation 近名命中，
    # 见 auth.py 注释）→ 必须走带 id 后缀的兜底名，绝不因重名失败
    org_service.get_organization_by_name = AsyncMock(side_effect=[MagicMock(), None])
    user = _mock_user()

    r, captured, _ = _register(client, user, org_service)

    assert r.status_code == 200
    assert captured["data"]["name"] == "newbie-42 的空间"
    assert user.organization_id == 7


def test_register_random_suffix_when_both_names_taken(client):
    """两级兜底名都被占（管理员预构造的前置条件）→ 第三级随机后缀，注册必须成功。

    变异验证：删掉 register 端点的三级兜底块 → 本用例红
    （名字停在 "newbie-42 的空间"，无随机后缀）。
    """
    org_service = MagicMock()
    org_service.get_organization_by_name = AsyncMock(side_effect=[MagicMock(), MagicMock()])
    user = _mock_user()

    r, captured, _ = _register(client, user, org_service)

    assert r.status_code == 200, f"二次重名不得导致注册失败: {r.text[:200]}"
    assert re.fullmatch(r"newbie-42-[0-9a-f]{8} 的空间", captured["data"]["name"]), captured[
        "data"
    ]["name"]
    assert user.organization_id == 7


# ---------------------------------------------------------------------------
# 真库固化（#254 第 5 条）：上面用 MagicMock 顶替 db，事务/成员/UNIQUE 约束均不
# 真执行；下面在内存 SQLite（FK 强制 ON）上真建表、真注册、真断言，把复核当时
# 用真 MySQL 做的「全量落盘 + 崩溃注入」验证沉淀进 CI。
# ---------------------------------------------------------------------------


async def _make_real_db():
    """建内存 SQLite 全表；StaticPool 单连接保证请求会话与断言会话同库。

    PRAGMA foreign_keys=ON：与评审的 SQLite 底线一致，FK 违规直接报错，
    不吃 SQLite 默认"外键不强制"的静默。
    """
    from app.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _real_db_override(factory):
    async def _override():
        async with factory() as session:
            yield session

    return _override


async def _post_register(payload: dict):
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        return await ac.post("/api/v1/auth/register", json=payload)


@pytest.mark.asyncio
async def test_register_real_db_creates_org_member_and_backfill():
    """真库判别（#254 第 5 条）：注册一次原子落盘——恰 1 个本人私有组织、
    恰 1 条成员行、organization_id 回填到位、欢迎通知与前者同属一个事务。

    变异验证：删端点的 `user.organization_id = org.id` 回填行 → 本用例红。
    """
    from app.core.database import get_db
    from app.main import app
    from app.models import Organization, User
    from app.models.notification import Notification
    from app.models.organization import user_organization

    engine, factory = await _make_real_db()
    app.dependency_overrides[get_db] = _real_db_override(factory)
    try:
        r = await _post_register(
            {
                "username": "realnewbie",
                "email": "realnewbie@example.com",
                "password": "ValidPass123!",
                "full_name": "Real Newbie",
            }
        )
        assert r.status_code == 200, f"register failed: {r.text[:300]}"
        org_id = r.json()["data"]["user_info"]["organization_id"]
        assert org_id is not None, "#252 契约：注册成功必有个人组织且回填"

        async with factory() as s:
            user = (await s.execute(select(User).where(User.username == "realnewbie"))).scalar_one()
            assert user.organization_id == org_id

            orgs = (await s.execute(select(Organization))).scalars().all()
            assert len(orgs) == 1, "注册路径只新建本组织，不并入任何既有组织"
            org = orgs[0]
            assert org.id == org_id
            assert org.owner_id == user.id, "个人组织必须属于注册者本人"
            assert org.is_private is True, "个人空间应为私有（隔离语义）"
            assert org.name == "realnewbie 的空间"

            member_rows = (await s.execute(select(user_organization))).all()
            assert [(m.user_id, m.organization_id) for m in member_rows] == [(user.id, org_id)]

            notif_count = (
                await s.execute(
                    select(func.count())
                    .select_from(Notification)
                    .where(Notification.user_id == user.id)
                )
            ).scalar()
            assert notif_count == 1, "欢迎通知与用户/组织同一事务落盘"
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_register_real_db_crash_before_commit_leaves_no_partial_rows():
    """崩溃注入（真库）：回填之后、commit 之前抛错 → 注册 500，且 user/组织/
    成员/通知零落盘——不存在"组织建了、用户没回填"的半程窗口。

    变异验证：在回填行后插一行 `await db.commit()`（提前落盘）→ 本用例红
    （崩溃后 user/org 行残留）。
    """
    from app.core.database import get_db
    from app.main import app
    from app.models import Organization, User
    from app.models.notification import Notification
    from app.models.organization import user_organization

    engine, factory = await _make_real_db()
    app.dependency_overrides[get_db] = _real_db_override(factory)
    try:
        with patch(
            "app.api.v1.endpoints.auth.Notification",
            side_effect=RuntimeError("注入崩溃：回填后、commit 前"),
        ):
            r = await _post_register(
                {
                    "username": "crashbaby",
                    "email": "crashbaby@example.com",
                    "password": "ValidPass123!",
                }
            )
        assert r.status_code == 500, f"注入崩溃应表现为 500，实得 {r.status_code}"

        async with factory() as s:
            leftover = (
                await s.execute(select(User).where(User.username == "crashbaby"))
            ).scalar_one_or_none()
            assert leftover is None, "崩溃后用户行不得残留"
            for model in (Organization, user_organization, Notification):
                count = (await s.execute(select(func.count()).select_from(model))).scalar()
                assert count == 0, f"{model.__name__} 残留 {count} 行：事务非原子"
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_register_real_db_second_collision_uses_random_suffix():
    """真库判别（#254 第 3 条）：预构造两级重名（管理员前置条件）→ 注册仍 200，
    组织名落到第三级随机后缀。若三级兜底被删，create_organization 的 flush 会
    在这里真抛 UNIQUE(name) 冲突 → 500。

    前置构造：直插占位用户（id=1）使注册者确定性拿到 id=2，再以占位用户为
    owner 预建 "newbie 的空间" 与 "newbie-2 的空间"。
    """
    from app.core.database import get_db
    from app.main import app
    from app.models import Organization, User

    engine, factory = await _make_real_db()
    async with factory() as s:
        squatter = User(
            username="squatter", email="squatter@example.com", hashed_password="x", is_active=True
        )
        s.add(squatter)
        await s.flush()
        assert squatter.id == 1  # 前置自检：注册者将确定性拿到 id=2
        s.add_all(
            [
                Organization(name="newbie 的空间", is_private=True, owner_id=squatter.id),
                Organization(name="newbie-2 的空间", is_private=True, owner_id=squatter.id),
            ]
        )
        await s.commit()

    app.dependency_overrides[get_db] = _real_db_override(factory)
    try:
        r = await _post_register(
            {
                "username": "newbie",
                "email": "newbie@example.com",
                "password": "ValidPass123!",
            }
        )
        assert r.status_code == 200, f"预构造重名不得使注册 500: {r.text[:300]}"
        org_id = r.json()["data"]["user_info"]["organization_id"]

        async with factory() as s:
            user = (await s.execute(select(User).where(User.username == "newbie"))).scalar_one()
            assert user.id == 2  # 前提成立性自检（前置构造依赖此 id）
            org = (
                await s.execute(select(Organization).where(Organization.id == org_id))
            ).scalar_one()
            assert re.fullmatch(r"newbie-2-[0-9a-f]{8} 的空间", org.name), org.name
            assert user.organization_id == org_id
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()
