"""会话只读分享链接：判别性 API 测试。

用真实 SQLite + ASGI 客户端：公开 GET 不覆盖 get_current_user，
确保"未登录只读"与"登录后管理"两条路径都被实际执行。
"""

import hashlib
import logging
import re
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.security import get_current_user
from app.models import Base
from app.models.chat import (
    ChatMessage,
    ChatSession,
    ChatSessionStatus,
    DeletedChatSession,
    MessageType,
)

SESSION_OWNED = "share-owned"
SESSION_FOREIGN = "share-foreign"
BASE = "/api/v1/chat"


def _user(user_id: int, *, superuser: bool = False):
    return SimpleNamespace(
        id=user_id,
        organization_id=None,
        token_organization_id=None,
        is_superuser=superuser,
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
                    title="Private conversation",
                    status=ChatSessionStatus.ACTIVE,
                ),
                ChatSession(
                    id=SESSION_FOREIGN,
                    user_id=2,
                    organization_id=None,
                    title="Other user conversation",
                    status=ChatSessionStatus.ACTIVE,
                ),
            ]
        )
        db.add_all(
            [
                ChatMessage(
                    id="m1",
                    session_id=SESSION_OWNED,
                    seq=1,
                    content="user secret",
                    message_type=MessageType.USER,
                ),
                ChatMessage(
                    id="m2",
                    session_id=SESSION_OWNED,
                    seq=2,
                    content="assistant answer",
                    message_type=MessageType.ASSISTANT,
                    meta_data='{"sources": []}',
                ),
                ChatMessage(
                    id="m3",
                    session_id=SESSION_FOREIGN,
                    seq=1,
                    content="foreign message",
                    message_type=MessageType.USER,
                ),
            ]
        )
        await db.commit()
    yield factory
    await engine.dispose()


class _Client:
    """可切换 current user；user=None 时**不**安装 auth override（真匿名）。"""

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
        self._client = AsyncClient(transport=ASGITransport(app=app), base_url="http://share.test")
        return self._client

    async def __aexit__(self, *exc):
        await self._client.aclose()
        for key in self._overrides:
            self._app.dependency_overrides.pop(key, None)
        return False


def _client(factory, user=None):
    return _Client(factory, user)


async def _token_row(factory, session_id=SESSION_OWNED):
    async with factory() as db:
        row = (
            await db.execute(select(ChatSession).where(ChatSession.id == session_id))
        ).scalar_one()
        return row.share_token_hash, row.share_created_at


async def test_owner_creates_urlsafe_token_only_once_in_response(share_env):
    async with _client(share_env, _user(1)) as client:
        resp = await client.post(f"{BASE}/conversations/{SESSION_OWNED}/share")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        token = body["data"]["token"]
        assert re.fullmatch(r"[A-Za-z0-9_-]{32}", token), token
        assert body["data"]["url"] == f"/share/{token}"
        # 第二次创建轮换 token，旧 token 立即失效
        again = await client.post(f"{BASE}/conversations/{SESSION_OWNED}/share")
        assert again.status_code == 200
        assert again.json()["data"]["token"] != token
        old = await client.get(f"{BASE}/shares/{token}")
        assert old.status_code == 404
        assert (await _token_row(share_env))[0] == hashlib.sha256(
            again.json()["data"]["token"].encode()
        ).hexdigest()


@pytest.mark.parametrize(
    ("user", "expected"),
    [
        (_user(2), 404),  # 非所有者：不泄露会话是否存在
        (_user(1, superuser=True), 200),  # 本人是超管仍可分享自己的会话
    ],
)
async def test_non_owner_cannot_create_share_even_as_superuser(share_env, user, expected):
    async with _client(share_env, user) as client:
        resp = await client.post(f"{BASE}/conversations/{SESSION_OWNED}/share")
        assert resp.status_code == expected

    # 非所有者无法分享 owned 会话；超管只有在自己就是所有者时才可分享（参数化覆盖）。


async def test_revoke_immediately_invalidates_token(share_env):
    async with _client(share_env, _user(1)) as client:
        token = (await client.post(f"{BASE}/conversations/{SESSION_OWNED}/share")).json()["data"][
            "token"
        ]
        assert (await client.get(f"{BASE}/shares/{token}")).status_code == 200
        resp = await client.delete(f"{BASE}/conversations/{SESSION_OWNED}/share")
        assert resp.status_code == 200
        assert resp.json()["data"] == {"shared": False}
        assert (await client.get(f"{BASE}/shares/{token}")).status_code == 404
        assert (await _token_row(share_env)) == (None, None)
        # 重复撤销是幂等的
        assert (
            await client.delete(f"{BASE}/conversations/{SESSION_OWNED}/share")
        ).status_code == 200


async def test_public_endpoint_is_readonly_and_does_not_change_source_session(share_env):
    async with _client(share_env, _user(1)) as owner:
        token = (await owner.post(f"{BASE}/conversations/{SESSION_OWNED}/share")).json()["data"][
            "token"
        ]

    before = await _token_row(share_env)
    async with _client(share_env) as anon:  # 无 get_current_user override
        resp = await anon.get(f"{BASE}/shares/{token}")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["title"] == "Private conversation"
        assert [m["content"] for m in data["messages"]] == ["user secret", "assistant answer"]
        assert all(m["messageType"] in {"user", "assistant"} for m in data["messages"])
        # 快照不含反馈、文件、用户/组织元数据
        assert not any(k in m for m in data["messages"] for k in ("feedback", "files", "userId"))

        for method, path in (
            ("post", f"{BASE}/conversations/{SESSION_OWNED}/share"),
            ("delete", f"{BASE}/conversations/{SESSION_OWNED}/share"),
            ("delete", f"{BASE}/conversations/{SESSION_OWNED}"),
            ("put", f"{BASE}/conversations/{SESSION_OWNED}"),
            ("post", f"{BASE}/conversations/{SESSION_OWNED}/unbind-docs"),
            ("post", f"{BASE}/messages/m1/feedback"),
            ("post", f"{BASE}/stream"),
        ):
            payload = {"message": "injected"} if method in {"post", "put"} else None
            denied = (
                await getattr(anon, method)(path, json=payload)
                if payload
                else await getattr(anon, method)(path)
            )
            assert denied.status_code == 401, (method, path, denied.status_code)

    assert await _token_row(share_env) == before
    async with share_env() as db:
        assert (
            (await db.execute(select(ChatMessage).where(ChatMessage.session_id == SESSION_OWNED)))
            .scalars()
            .all()
        )
        session = (
            await db.execute(select(ChatSession).where(ChatSession.id == SESSION_OWNED))
        ).scalar_one()
        assert session.title == "Private conversation"


async def test_share_dies_with_session_and_accepts_no_logged_in_bypass(share_env):
    """会话删除（含墓碑）后分享 404；超管未持 token 也不能读私有会话。"""
    async with _client(share_env, _user(1)) as client:
        token = (await client.post(f"{BASE}/conversations/{SESSION_OWNED}/share")).json()["data"][
            "token"
        ]

    async with _client(share_env, _user(9, superuser=True)) as superuser:
        denied = await superuser.get(f"{BASE}/shares/not-a-valid-token")
        assert denied.status_code == 404

    async with share_env() as db:
        session = await db.get(ChatSession, SESSION_OWNED)
        await db.execute(delete(ChatMessage).where(ChatMessage.session_id == SESSION_OWNED))
        await db.delete(session)
        db.add(DeletedChatSession(session_id=SESSION_OWNED, user_id=1, organization_id=None))
        await db.commit()

    async with _client(share_env) as anon:
        assert (await anon.get(f"{BASE}/shares/{token}")).status_code == 404


def test_share_token_is_redacted_from_log_records():
    """Access-log records carrying the public URL must never expose the token."""
    from app.core.logging import ShareTokenRedactingFilter

    token = "T0kenThatMustNotAppear"
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        f'"GET /api/v1/chat/shares/{token} HTTP/1.1" 200',
        (),
        None,
    )
    assert ShareTokenRedactingFilter().filter(record) is True
    rendered = record.getMessage()
    assert token not in rendered
    assert "/shares/[REDACTED]" in rendered
