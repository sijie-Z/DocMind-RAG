"""批次十二 · 会话分享过期/审计/no-store：判别性 API 测试。

用真实 SQLite + ASGI 客户端：公开 GET 不覆盖 ``get_current_user``，
确保「匿名只读」路径被实际执行；管理面覆盖 chat 分享与批次八的会话树分享
（两者复用同一 ``share_token_hash`` / 公开端点）。

变异点（本文件必须能变红）：
- 去掉公开端点的过期检查 → ``test_expired_token_*``；
- 撤销只清 token 不清 expires → ``test_revoke_clears_three_columns``；
- 去掉 create/status 的 no-store → ``test_create_and_status_are_no_store``；
- 树分享忽略 ``expires_in_days`` → ``test_tree_share_honours_expiry``；
- 审计 detail 记录 token → ``test_share_actions_write_audit_without_token``。
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.security import get_current_user
from app.models import Base
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType

SESSION_OWNED = "expiry-owned"
SESSION_ROOT = "expiry-tree-root"
SESSION_CHILD = "expiry-tree-child"
BASE = "/api/v1/chat"
TREE_BASE = "/api/v1/sessions"


def _user(user_id: int = 1):
    return SimpleNamespace(
        id=user_id,
        organization_id=None,
        token_organization_id=None,
        is_superuser=False,
        role="user",
        is_active=True,
        username=f"u{user_id}",
    )


@pytest.fixture
async def share_env():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all(
            [
                ChatSession(
                    id=SESSION_OWNED,
                    user_id=1,
                    organization_id=None,
                    title="Share expiry conversation",
                    status=ChatSessionStatus.ACTIVE,
                ),
                ChatSession(
                    id=SESSION_ROOT,
                    user_id=1,
                    organization_id=None,
                    title="Tree root",
                    status=ChatSessionStatus.ACTIVE,
                ),
                ChatSession(
                    id=SESSION_CHILD,
                    user_id=1,
                    organization_id=None,
                    title="Tree child",
                    status=ChatSessionStatus.ACTIVE,
                    parent_session_id=SESSION_ROOT,
                    root_session_id=SESSION_ROOT,
                ),
            ]
        )
        db.add(
            ChatMessage(
                id="expiry-m1",
                session_id=SESSION_OWNED,
                seq=1,
                content="expiry snapshot body",
                message_type=MessageType.USER,
            )
        )
        await db.commit()
    yield factory
    await engine.dispose()


class _Client:
    """登录态客户端；user=None 时不安装 auth override（真匿名）。"""

    def __init__(self, factory, user):
        self.factory = factory
        self.user = user

    async def __aenter__(self):
        from app.main import app

        async def _db():
            async with self.factory() as session:
                yield session

        self._overrides = {get_db: _db}
        if self.user is not None:

            async def _current_user():
                return self.user

            self._overrides[get_current_user] = _current_user
        app.dependency_overrides.update(self._overrides)
        self._app = app
        self._client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://share-expiry.test"
        )
        return self._client

    async def __aexit__(self, *exc):
        await self._client.aclose()
        for key in self._overrides:
            self._app.dependency_overrides.pop(key, None)
        return False


def _client(factory, user=None):
    return _Client(factory, user)


async def _share_row(factory, session_id=SESSION_OWNED):
    async with factory() as db:
        row = (
            await db.execute(select(ChatSession).where(ChatSession.id == session_id))
        ).scalar_one()
        return row.share_token_hash, row.share_created_at, row.share_expires_at


async def _set_expiry(factory, session_id, value):
    async with factory() as db:
        row = await db.get(ChatSession, session_id)
        row.share_expires_at = value
        await db.commit()


def _parse_iso(value):
    assert value, "expiresAt 不得为空"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


async def test_create_with_expiry_returns_and_persists_expires_at(share_env):
    async with _client(share_env, _user(1)) as client:
        resp = await client.post(
            f"{BASE}/conversations/{SESSION_OWNED}/share", json={"expires_in_days": 7}
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        expires_at = _parse_iso(data["expiresAt"])
        remaining = expires_at - datetime.now(UTC)
        assert timedelta(days=6, hours=23) < remaining < timedelta(days=7, minutes=1)

        digest, created_at, db_expires_at = await _share_row(share_env)
        assert digest == hashlib.sha256(data["token"].encode()).hexdigest()
        assert created_at is not None
        assert db_expires_at is not None

        # 过期前公开读取 200。
        public = await client.get(f"{BASE}/shares/{data['token']}")
        assert public.status_code == 200, public.text

        status = await client.get(f"{BASE}/conversations/{SESSION_OWNED}/share")
        assert status.status_code == 200, status.text
        payload = status.json()["data"]
        assert payload["shared"] is True
        assert payload["expired"] is False
        assert _parse_iso(payload["expiresAt"]) == expires_at


async def test_legacy_null_expiry_never_expires(share_env):
    """旧链接（share_expires_at IS NULL）永久有效：本批不强制过期。"""
    legacy_token = "legacy-permanent-token"
    async with share_env() as db:
        row = await db.get(ChatSession, SESSION_OWNED)
        row.share_token_hash = hashlib.sha256(legacy_token.encode()).hexdigest()
        row.share_created_at = datetime.now(UTC) - timedelta(days=500)
        row.share_expires_at = None
        await db.commit()

    async with _client(share_env) as anon:
        public = await anon.get(f"{BASE}/shares/{legacy_token}")
        assert public.status_code == 200, public.text

    async with _client(share_env, _user(1)) as owner:
        # 不传 body（旧客户端）同样创建永久链接。
        created = await owner.post(f"{BASE}/conversations/{SESSION_OWNED}/share")
        assert created.status_code == 200, created.text
        assert created.json()["data"]["expiresAt"] is None
        status = await owner.get(f"{BASE}/conversations/{SESSION_OWNED}/share")
        assert status.json()["data"]["expiresAt"] is None
        assert status.json()["data"]["expired"] is False
        assert (
            await owner.get(f"{BASE}/shares/{created.json()['data']['token']}")
        ).status_code == 200


async def test_expired_token_returns_same_404_as_missing_and_revoked(share_env):
    async with _client(share_env, _user(1)) as client:
        token = (
            await client.post(
                f"{BASE}/conversations/{SESSION_OWNED}/share", json={"expires_in_days": 1}
            )
        ).json()["data"]["token"]

    # 把过期时间推到过去（模拟到期）；变异点：去掉过期检查 → 本断言红。
    await _set_expiry(share_env, SESSION_OWNED, datetime.now(UTC) - timedelta(seconds=5))

    async with _client(share_env) as anon:
        expired = await anon.get(f"{BASE}/shares/{token}")
        missing = await anon.get(f"{BASE}/shares/definitely-not-a-real-token")
    assert expired.status_code == 404
    assert missing.status_code == 404
    # 与「不存在」完整同形（request_id 每次不同，剔除后必须一致）：不泄露失效原因。
    expired_body = {k: v for k, v in expired.json().items() if k != "request_id"}
    missing_body = {k: v for k, v in missing.json().items() if k != "request_id"}
    assert expired_body == missing_body

    async with _client(share_env, _user(1)) as owner:
        status = await owner.get(f"{BASE}/conversations/{SESSION_OWNED}/share")
        assert status.json()["data"]["shared"] is True, "token 仍在，只是已过期"
        assert status.json()["data"]["expired"] is True


async def test_revoke_clears_three_columns(share_env):
    """变异点：撤销只清 token、不清 expires → 本用例红。"""
    async with _client(share_env, _user(1)) as client:
        token = (
            await client.post(
                f"{BASE}/conversations/{SESSION_OWNED}/share", json={"expires_in_days": 30}
            )
        ).json()["data"]["token"]
        assert (await client.get(f"{BASE}/shares/{token}")).status_code == 200

        revoked = await client.delete(f"{BASE}/conversations/{SESSION_OWNED}/share")
        assert revoked.status_code == 200
        assert revoked.json()["data"] == {"shared": False}

        assert await _share_row(share_env) == (None, None, None), (
            "撤销必须清空 token/created/expires 三列"
        )
        assert (await client.get(f"{BASE}/shares/{token}")).status_code == 404
        status = (await client.get(f"{BASE}/conversations/{SESSION_OWNED}/share")).json()["data"]
        assert status == {
            "shared": False,
            "createdAt": None,
            "expiresAt": None,
            "expired": False,
        }
        # 重复撤销幂等。
        assert (
            await client.delete(f"{BASE}/conversations/{SESSION_OWNED}/share")
        ).status_code == 200


async def test_create_and_status_are_no_store(share_env):
    """token 是凭证：create/status 响应不得被中间缓存留存。"""
    async with _client(share_env, _user(1)) as client:
        created = await client.post(
            f"{BASE}/conversations/{SESSION_OWNED}/share", json={"expires_in_days": 7}
        )
        assert created.headers.get("cache-control") == "no-store"
        status = await client.get(f"{BASE}/conversations/{SESSION_OWNED}/share")
        assert status.headers.get("cache-control") == "no-store"


async def test_rotate_resets_expiry_and_old_token_dies(share_env):
    async with _client(share_env, _user(1)) as client:
        first = (
            await client.post(
                f"{BASE}/conversations/{SESSION_OWNED}/share", json={"expires_in_days": 7}
            )
        ).json()["data"]
        rotated = await client.post(f"{BASE}/conversations/{SESSION_OWNED}/share")
        assert rotated.status_code == 200
        data = rotated.json()["data"]
        assert data["expiresAt"] is None, "不传 expires_in_days 的轮换回到永久链接"
        assert data["token"] != first["token"]
        assert (await client.get(f"{BASE}/shares/{first['token']}")).status_code == 404
        assert (await client.get(f"{BASE}/shares/{data['token']}")).status_code == 200


@pytest.mark.parametrize("days", [0, -1, 91, 1000])
async def test_expires_in_days_out_of_range_rejected(share_env, days):
    async with _client(share_env, _user(1)) as client:
        resp = await client.post(
            f"{BASE}/conversations/{SESSION_OWNED}/share", json={"expires_in_days": days}
        )
        assert resp.status_code == 422, (days, resp.text)
    assert await _share_row(share_env) == (None, None, None), "拒绝请求不得写入任何分享列"


async def test_share_actions_write_audit_without_token(monkeypatch, share_env):
    """create/rotate/revoke 各一条审计；detail 只含会话定位/过期，不含 token。

    变异点：审计记 token 或额外字段 → 本用例红。
    """
    from app.api.v1.endpoints import chat as chat_endpoint

    calls: list[dict[str, object]] = []

    async def _fake(
        user_id,
        action,
        target_type=None,
        target_id=None,
        detail=None,
        request=None,
        db=None,
    ):
        calls.append(
            {
                "user_id": user_id,
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "detail": detail,
            }
        )

    monkeypatch.setattr(chat_endpoint.audit_service, "log_activity", _fake)
    async with _client(share_env, _user(1)) as client:
        first = (
            await client.post(
                f"{BASE}/conversations/{SESSION_OWNED}/share", json={"expires_in_days": 7}
            )
        ).json()["data"]
        second = (await client.post(f"{BASE}/conversations/{SESSION_OWNED}/share")).json()["data"]
        assert (
            await client.delete(f"{BASE}/conversations/{SESSION_OWNED}/share")
        ).status_code == 200

    assert [call["action"] for call in calls] == [
        "share_conversation",
        "rotate_conversation_share",
        "revoke_conversation_share",
    ]
    assert all(call["target_type"] == "chat_session" for call in calls)
    assert all(call["target_id"] == SESSION_OWNED for call in calls)
    assert all(call["user_id"] == 1 for call in calls)

    first_payload = json.loads(str(calls[0]["detail"]))
    assert set(first_payload) == {"conversation_id", "expires_at"}
    assert first_payload["conversation_id"] == SESSION_OWNED
    assert first_payload["expires_at"] == first["expiresAt"]
    second_payload = json.loads(str(calls[1]["detail"]))
    assert second_payload == {"conversation_id": SESSION_OWNED, "expires_at": None}
    revoke_payload = json.loads(str(calls[2]["detail"]))
    assert revoke_payload == {"conversation_id": SESSION_OWNED, "expires_at": None}

    for call, token in zip(calls, (first["token"], second["token"], None), strict=True):
        detail = str(call["detail"])
        if token is not None:
            assert token not in detail, "审计 detail 不得含 token 明文"
        assert hashlib.sha256(str(token or "").encode()).hexdigest()[:8] not in detail


async def test_audit_failure_does_not_break_share_endpoints(monkeypatch, share_env):
    """审计服务故障不得反噬 create/rotate/revoke。"""
    from app.api.v1.endpoints import chat as chat_endpoint

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(chat_endpoint.audit_service, "log_activity", _boom)
    async with _client(share_env, _user(1)) as client:
        assert (
            await client.post(
                f"{BASE}/conversations/{SESSION_OWNED}/share", json={"expires_in_days": 7}
            )
        ).status_code == 200
        assert (await client.post(f"{BASE}/conversations/{SESSION_OWNED}/share")).status_code == 200
        assert (
            await client.delete(f"{BASE}/conversations/{SESSION_OWNED}/share")
        ).status_code == 200


async def test_tree_share_honours_expiry(share_env):
    """批次八树分享同样支持过期；过期后公开读取 404。

    变异点：树分享忽略 expires_in_days → 本用例红。
    """
    async with _client(share_env, _user(1)) as client:
        created = await client.post(
            f"{TREE_BASE}/tree/{SESSION_ROOT}/share", json={"expires_in_days": 7}
        )
        assert created.status_code == 200, created.text
        data = created.json()["data"]
        expires_at = _parse_iso(data["expiresAt"])
        assert (
            timedelta(days=6, hours=23)
            < expires_at - datetime.now(UTC)
            < timedelta(days=7, minutes=1)
        )
        assert created.headers.get("cache-control") == "no-store"
        digest, _, db_expires_at = await _share_row(share_env, SESSION_ROOT)
        assert digest == hashlib.sha256(data["token"].encode()).hexdigest()
        assert db_expires_at is not None
        assert (await client.get(f"{BASE}/shares/{data['token']}")).status_code == 200

        # 子会话仍不能作为树根分享（batch8 约束不回归）。
        assert (await client.post(f"{TREE_BASE}/tree/{SESSION_CHILD}/share")).status_code == 400

    await _set_expiry(share_env, SESSION_ROOT, datetime.now(UTC) - timedelta(seconds=5))
    async with _client(share_env) as anon:
        assert (await anon.get(f"{BASE}/shares/{data['token']}")).status_code == 404

    # 轮换不带 expires_in_days → 回到永久链接，旧 token 失效。
    async with _client(share_env, _user(1)) as client:
        rotated = await client.post(f"{TREE_BASE}/tree/{SESSION_ROOT}/share")
        assert rotated.status_code == 200
        assert rotated.json()["data"]["expiresAt"] is None
        assert (await client.get(f"{BASE}/shares/{data['token']}")).status_code == 404
        assert (
            await client.get(f"{BASE}/shares/{rotated.json()['data']['token']}")
        ).status_code == 200
        status = (await client.get(f"{BASE}/conversations/{SESSION_ROOT}/share")).json()["data"]
        assert status["expiresAt"] is None
        assert status["expired"] is False


async def test_tree_share_writes_audit_without_token(monkeypatch, share_env):
    from app.api.v1.endpoints import chat as chat_endpoint

    calls: list[dict[str, object]] = []

    async def _fake(
        user_id,
        action,
        target_type=None,
        target_id=None,
        detail=None,
        request=None,
        db=None,
    ):
        calls.append(
            {
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "detail": detail,
            }
        )

    monkeypatch.setattr(chat_endpoint.audit_service, "log_activity", _fake)
    async with _client(share_env, _user(1)) as client:
        first = (
            await client.post(
                f"{TREE_BASE}/tree/{SESSION_ROOT}/share", json={"expires_in_days": 90}
            )
        ).json()["data"]
        second = await client.post(f"{TREE_BASE}/tree/{SESSION_ROOT}/share")
        assert second.status_code == 200

    assert [call["action"] for call in calls] == [
        "share_session_tree",
        "rotate_session_tree_share",
    ]
    assert all(call["target_type"] == "chat_session" for call in calls)
    assert all(call["target_id"] == SESSION_ROOT for call in calls)
    payload = json.loads(str(calls[0]["detail"]))
    assert set(payload) == {"conversation_id", "expires_at"}
    assert payload["conversation_id"] == SESSION_ROOT
    assert payload["expires_at"] == first["expiresAt"]
    for call, token in zip(calls, (first["token"], second.json()["data"]["token"]), strict=True):
        assert token not in str(call["detail"])
