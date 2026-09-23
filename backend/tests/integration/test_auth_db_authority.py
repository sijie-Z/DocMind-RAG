"""HTTP 认证路径的身份权威性（issue #82 / PR B）。

**不变量**：`current_user` 永远来自**当前 DB 状态**，而不是 Redis 里的 User 历史快照。

为什么这条不变量需要真 MySQL + 真 Redis 的用例：缓存失效本质上无法自证 ——
漏删一处、删早一步（`db.commit()` 之前）、或并发回填，旧身份就能在 TTL（默认
24h）内继续放行。唯一可靠的保证是**让缓存根本不参与身份判定**。所以下面把
「Redis 里确实躺着一个陈旧快照」这个前提显式摆出来，再断言仍然 401；
而不是断言代码里没有 `get_cache`（那是实现细节，改个函数名就失效）。

用真表而不是 mock 的另一个理由：「行已被删」（`user is None`）与「行还在但
`is_active=False`」在 mock 下只是同一个返回值换个数，只有真表才能证明两者走的
是同一条路径、产出同一个响应。

依赖：MySQL(3306) + Redis(6379)，见模块级 skipif。
"""

import json
import os
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, update

from app.core.database import AsyncSessionLocal, engine
from app.core.redis import close_redis, get_redis
from app.main import app
from app.models.user import User
from app.services.auth_service import auth_service

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="需要真实 MySQL/Redis（设置 RUN_INTEGRATION=1）",
)

_ME = "/api/v1/auth/me"


# ── 夹具 ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture(autouse=True)
async def _fresh_pools():
    """每个用例**前后**都释放连接池。

    pytest-asyncio 给每个用例开一个新事件循环，而 `engine` / `redis_client` 是模块级
    单例：池里指向上一个循环的连接被下一个用例复用时，抛出的错与真实症状毫无关系
    （Windows 上是 `AttributeError: 'NoneType' object has no attribute 'send'`）。

    只在 teardown 释放不够 —— 本文件的**第一个**用例仍会捡到别的测试模块留下的
    连接。这也是为什么这条夹具写成「前后都清」。
    """
    await _reset_pools()
    yield
    await _reset_pools()


async def _reset_pools() -> None:
    await engine.dispose()
    await close_redis()  # 内部已吞掉「旧循环上关连接」的异常并重置 client


@pytest_asyncio.fixture
async def http_client():
    """不触发 lifespan 的 ASGI 客户端。

    刻意用 `AsyncClient` + `ASGITransport` 而不是同步 `TestClient`：后者的
    portal 把 app 跑在另一个事件循环里，而本用例的夹具要在同一个循环里写 DB，
    跨循环复用 asyncmy 连接会炸。
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def make_user():
    """在真表里建用户；测试结束后连同 Redis 键一起清理。"""
    created: list[int] = []

    async def _make(*, is_active: bool = True) -> User:
        suffix = uuid.uuid4().hex[:12]
        async with AsyncSessionLocal() as db:
            user = User(
                username=f"prb_{suffix}",
                email=f"prb_{suffix}@example.com",
                hashed_password="not-a-real-bcrypt-hash",
                role="user",
                is_active=is_active,
                is_superuser=False,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
        created.append(user.id)
        return user

    yield _make

    redis = await get_redis()
    async with AsyncSessionLocal() as db:
        for user_id in created:
            await redis.delete(f"user:{user_id}")
            await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


async def _set_active(user_id: int, active: bool) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(update(User).where(User.id == user_id).values(is_active=active))
        await db.commit()


async def _delete_user(user_id: int) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


def _auth_header(user_id: int) -> dict[str, str]:
    token = auth_service.create_access_token({"user_id": user_id, "role": "user"})
    return {"Authorization": f"Bearer {token}"}


def _snapshot_as_written_by_login(user: User) -> str:
    """复刻 `auth.py` 登录路径写进 Redis 的那份快照。

    关键：它是**精简字典**，没有 `is_active`（也没有 `is_superuser`）。
    这正是当年最危险的一种陈旧快照 —— 读路径用 `.get("is_active", True)` 兜底，
    于是「字段缺失」被当成「账号有效」。
    """
    return json.dumps(
        {
            "id": user.id,
            "username": user.username,
            "email": user.email or "",
            "full_name": None,
            "organization_id": None,
            "role": "user",
            "preferences": None,
        }
    )


async def _plant_stale_snapshot(user: User, payload: str | None = None) -> str:
    """往 Redis 里放一个陈旧快照，返回键名。"""
    key = f"user:{user.id}"
    redis = await get_redis()
    await redis.setex(key, 3600, payload if payload is not None else _snapshot_as_written_by_login(user))
    return key


# ── 认证测试矩阵（已冻结）──────────────────────────────────────────


@pytest.mark.asyncio
async def test_valid_user_allowed(http_client, make_user):
    """有效用户 → 正常放行。"""
    user = await make_user()

    response = await http_client.get(_ME, headers=_auth_header(user.id))

    assert response.status_code == 200, response.text
    assert response.json()["data"]["id"] == user.id


@pytest.mark.asyncio
async def test_disabled_user_gets_401(http_client, make_user):
    """disabled → 401。"""
    user = await make_user()
    await _set_active(user.id, False)

    response = await http_client.get(_ME, headers=_auth_header(user.id))

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_deleted_user_gets_401(http_client, make_user):
    """deleted（行已不在）→ 401。"""
    user = await make_user()
    await _delete_user(user.id)

    response = await http_client.get(_ME, headers=_auth_header(user.id))

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_jwt_gets_401(http_client):
    """JWT 无效 → 401。"""
    response = await http_client.get(_ME, headers={"Authorization": "Bearer not.a.jwt"})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_disabled_and_deleted_are_indistinguishable(http_client, make_user):
    """最关键的一条：disabled 与 deleted 的公开响应必须**完全一致**。

    否则拿到一个过期 token 就能轮询出「这个 id 曾经存在过」，即用户枚举 oracle
    （issue #82 的原始症状）。
    """
    disabled = await make_user()
    await _set_active(disabled.id, False)
    deleted = await make_user()
    await _delete_user(deleted.id)

    disabled_resp = await http_client.get(_ME, headers=_auth_header(disabled.id))
    deleted_resp = await http_client.get(_ME, headers=_auth_header(deleted.id))

    assert disabled_resp.status_code == deleted_resp.status_code == 401
    assert (
        disabled_resp.headers.get("www-authenticate")
        == deleted_resp.headers.get("www-authenticate")
        == "Bearer"
    )

    def _body(resp) -> dict:
        # request_id 每次请求天然不同，是唯一允许不一致的字段
        return {k: v for k, v in resp.json().items() if k != "request_id"}

    assert _body(disabled_resp) == _body(deleted_resp)


# ── 核心：缓存不再被信任 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_stale_snapshot_of_disabled_user_is_not_trusted(http_client, make_user):
    """Redis 里躺着「禁用前」的快照 → 仍然 401。

    改造前这条必然失败：读路径缓存命中后不再回查 DB，直接 `User(**快照)` 放行。
    """
    user = await make_user()
    key = await _plant_stale_snapshot(user)
    await _set_active(user.id, False)

    redis = await get_redis()
    assert await redis.get(key) is not None, "前提不成立：陈旧快照没写进 Redis"

    response = await http_client.get(_ME, headers=_auth_header(user.id))

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_stale_snapshot_of_deleted_user_is_not_trusted(http_client, make_user):
    """Redis 里躺着「删除前」的快照 → 仍然 401。"""
    user = await make_user()
    full_snapshot = json.dumps(
        {
            "id": user.id,
            "username": user.username,
            "email": user.email or "",
            "full_name": None,
            "organization_id": None,
            "role": "user",
            "preferences": None,
            "is_active": True,  # 快照拍摄时账号确实有效
            "is_superuser": False,
        }
    )
    key = await _plant_stale_snapshot(user, full_snapshot)
    await _delete_user(user.id)

    redis = await get_redis()
    assert await redis.get(key) is not None, "前提不成立：陈旧快照没写进 Redis"

    response = await http_client.get(_ME, headers=_auth_header(user.id))

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_redis_outage_does_not_change_the_verdict(http_client, make_user, monkeypatch):
    """Redis 挂掉不应改变判定结果：DB 才是权威。

    token 黑名单仍依赖 Redis，但它的检查是 fail-open 的（查不到就当没吊销）；
    身份判定则完全不碰 Redis。
    """
    user = await make_user()
    calls: list[str] = []

    async def _redis_down(*args, **_kwargs):
        # 记一笔，证明这次「Redis 挂了」不是空转（否则用例可能只是没走到 Redis）
        calls.append(str(args[0]) if args else "?")
        raise ConnectionError("Redis down")

    monkeypatch.setattr("app.services.auth_service.RedisTools.exists", _redis_down)
    monkeypatch.setattr("app.services.auth_service.RedisTools.get_cache", _redis_down)

    response = await http_client.get(_ME, headers=_auth_header(user.id))

    assert response.status_code == 200
    assert any(key.startswith("blacklist:") for key in calls), (
        f"前提不成立：认证路径没有查过 Redis 黑名单，这次「宕机」是空转（calls={calls}）"
    )


@pytest.mark.asyncio
async def test_auth_path_writes_no_user_cache(http_client, make_user):
    """认证路径不得再写 `user:{id}` 快照。

    这是「彻底取消缓存」在行为上的可观察面：请求前删掉键，请求后它必须仍然不存在。
    有了它，将来谁把写缓存加回来都会被抓住。
    """
    user = await make_user()
    key = f"user:{user.id}"
    redis = await get_redis()
    await redis.delete(key)

    response = await http_client.get(_ME, headers=_auth_header(user.id))

    assert response.status_code == 200
    assert await redis.get(key) is None
