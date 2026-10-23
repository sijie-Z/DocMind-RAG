"""API Key 认证矩阵与管理端点（issue #480 §5.1 / §8）。

覆盖：
- 创建：明文只在创建响应出现一次；库里只有 bcrypt 哈希与前缀；
- 认证矩阵：有效 key 200、过期 401、吊销 401、不存在 401；
- GET 列表不泄露 ``key_hash`` / 明文；
- ``require_scope``：scope 缺失 403，JWT 用户（无 api_key_scopes）视为全量；
- 只能管理自己的 key（他人 key 吊销 404）。

基础设施：真实路由 + 真实 ``auth_service.get_current_user`` + sqlite（全链），
仅 Redis 黑名单检查用替身（JWT 路径用；dm_ 分支不经过 Redis）。
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.core.database import Base, get_db
from app.core.security import require_scope
from app.models.user import User
from app.services.api_key_service import api_key_service
from app.services.auth_service import auth_service


@pytest.fixture
async def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'api_keys.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
async def user(db_factory):
    async with db_factory() as session:
        row = User(
            username="keyuser",
            email="keyuser@example.com",
            hashed_password="x",
            is_active=True,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return SimpleNamespace(id=row.id, username=row.username)


@pytest.fixture
async def client(db_factory, monkeypatch):
    from app.main import app

    async def _override_db():
        async with db_factory() as session:
            yield session

    async def _no_blacklist(token):  # noqa: ANN001
        return False

    monkeypatch.setattr(auth_service, "is_token_blacklisted", _no_blacklist)
    app.dependency_overrides[get_db] = _override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://keytest") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _jwt(user_id: int) -> str:
    return auth_service.create_access_token({"user_id": user_id, "role": "user"})


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_via_service(db_factory, user_id: int, **kwargs):
    async with db_factory() as session:
        row = await session.get(User, user_id)
        record, raw = await api_key_service.create_key(
            session, user=row, name=kwargs.pop("name", "test"), **kwargs
        )
        return record.id, raw


@pytest.mark.asyncio
async def test_create_returns_plaintext_once_and_stores_only_hash(client, user, db_factory):
    r = await client.post("/api/v1/api-keys", json={"name": "ci"}, headers=_auth(_jwt(user.id)))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    raw = data["api_key"]
    assert raw.startswith("dm_") and len(raw) > 30
    assert data["prefix"] == raw[:8]
    assert data["scopes"] == ["chat:read", "chat:write", "knowledge:read", "rpc"]

    # 库里只有哈希：不等于明文，且是 bcrypt。
    async with db_factory() as session:
        record = await api_key_service.get_key(session, data["id"])
        assert record is not None
        assert record.key_hash != raw and record.key_hash.startswith("$2")
        assert record.scopes == data["scopes"]

    # 列表响应不出现明文与哈希。
    r2 = await client.get("/api/v1/api-keys", headers=_auth(raw))
    assert r2.status_code == 200, r2.text
    body = r2.text
    assert raw not in body and "key_hash" not in body
    listed = r2.json()["data"]
    assert [item["id"] for item in listed] == [data["id"]]
    assert listed[0]["last_used_at"] is not None, "认证成功后必须回填 last_used_at"


@pytest.mark.asyncio
async def test_valid_key_authenticates_and_jwt_path_is_untouched(client, user, db_factory):
    _, raw = await _create_via_service(db_factory, user.id)
    r = await client.get("/api/v1/api-keys", headers=_auth(raw))
    assert r.status_code == 200
    assert len(r.json()["data"]) == 1

    # JWT 路径行为不变（同一用户，同一列表）。
    r2 = await client.get("/api/v1/api-keys", headers=_auth(_jwt(user.id)))
    assert r2.status_code == 200
    assert len(r2.json()["data"]) == 1


@pytest.mark.asyncio
async def test_expired_key_is_401(client, user, db_factory):
    _, raw = await _create_via_service(
        db_factory, user.id, expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    r = await client.get("/api/v1/api-keys", headers=_auth(raw))
    assert r.status_code == 401
    assert r.json()["detail"] == "认证失败"


@pytest.mark.asyncio
async def test_revoked_key_is_401(client, user, db_factory):
    key_id, raw = await _create_via_service(db_factory, user.id)

    d = await client.delete(f"/api/v1/api-keys/{key_id}", headers=_auth(_jwt(user.id)))
    assert d.status_code == 200

    r = await client.get("/api/v1/api-keys", headers=_auth(raw))
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_unknown_key_prefix_is_401(client, user):
    r = await client.get("/api/v1/api-keys", headers=_auth("dm_not-a-real-key"))
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_cannot_revoke_other_users_key(client, user, db_factory):
    async with db_factory() as session:
        other = User(username="other", email="other@example.com", hashed_password="x")
        session.add(other)
        await session.commit()
        await session.refresh(other)
        other_id = other.id
    key_id, _ = await _create_via_service(db_factory, other_id, name="other-key")

    r = await client.delete(f"/api/v1/api-keys/{key_id}", headers=_auth(_jwt(user.id)))
    assert r.status_code == 404

    async with db_factory() as session:
        record = await api_key_service.get_key(session, key_id)
        assert record is not None and record.revoked_at is None


@pytest.mark.asyncio
async def test_require_scope_allows_jwt_and_enforces_api_key_scopes():
    checker = require_scope("rpc")
    jwt_user = SimpleNamespace(id=1, api_key_scopes=None)
    assert await checker(jwt_user) is jwt_user

    scoped = SimpleNamespace(id=2, api_key_scopes=["chat:read"])
    with pytest.raises(HTTPException) as exc:
        await checker(scoped)
    assert exc.value.status_code == 403

    allowed = SimpleNamespace(id=3, api_key_scopes=["rpc"])
    assert await checker(allowed) is allowed


@pytest.mark.asyncio
async def test_unknown_scope_rejected_at_creation(client, user):
    r = await client.post(
        "/api/v1/api-keys",
        json={"name": "bad", "scopes": ["root:everything"]},
        headers=_auth(_jwt(user.id)),
    )
    assert r.status_code == 422
