"""WebSocket 认证生命周期回归测试（issue #82 / PR C）。

覆盖：

* 握手阶段必须查库——有效用户可连接；已禁用 / 已删除 / 令牌非法一律拒绝
* 已删除与已禁用返回**完全一致**的 close code + reason（全局契约）
* 连接建立后用户被删除 / 被禁用 → 后续消息被拒绝，且不再触发 RAG/LLM

与 ``test_security_regressions.py`` 里 mock 数据库的风格不同，这里刻意走**真实
MySQL**：本次修复的核心就是"握手要查库"，把 DB mock 掉等于把被测逻辑一起 mock
掉。建连方式与 ``TestWsRejectsRefreshToken`` 一致（TestClient + Sec-WebSocket-Protocol）。

需要 MySQL/Redis 容器：

    RUN_INTEGRATION=1 pytest tests/integration/test_ws_auth_lifecycle.py
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.services.auth_service import auth_service

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="set RUN_INTEGRATION=1 to run with MySQL/Redis",
)

CHAT_WS = "/api/v1/chat/ws"
NOTIFY_WS = "/api/v1/notifications/ws"

# 契约值：与 app/core/ws_auth.py 的约定一致。刻意写死，改动必须是显式行为。
AUTH_FAILED_CLOSE = (4001, "Invalid token")

# TestClient 每次 websocket_connect 都会新开一个 event loop（portal），而应用默认引擎
# 带连接池——池中连接跨 loop 复用会直接报错。这里换成 NullPool：同一个真实库、
# 同一条 SQL 路径，只是不复用连接，避免测试基础设施问题掩盖被测逻辑。
_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
_Session = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


# ── 真实数据库助手（NullPool 保证连接不跨 loop / 不跨调用复用）──────────────
def _run(coro):
    return asyncio.run(coro)


def _create_user(*, is_active: bool = True) -> tuple[int, str]:
    """建一个真实用户，返回 (id, username)。

    用 Core 直插而不是 ORM 实体：本仓库模型与迁移存在列级漂移
    （``python scripts/check_schema.py --report-columns``），ORM 实体加载会连带
    selectin 拉取关联表并撞上缺列；这里只关心 users 表本身。
    """
    uniq = uuid.uuid4().hex[:12]
    username = f"wsuser_{uniq}"

    async def _go() -> int:
        async with _Session() as session:
            result = await session.execute(
                insert(User).values(
                    username=username,
                    email=f"{username}@example.com",
                    hashed_password="not-a-real-hash",
                    role="user",
                    is_active=is_active,
                )
            )
            await session.commit()
            return int(result.inserted_primary_key[0])

    return _run(_go()), username


def _set_active(user_id: int, active: bool) -> None:
    async def _go() -> None:
        async with _Session() as session:
            await session.execute(
                update(User).where(User.id == user_id).values(is_active=active)
            )
            await session.commit()

    _run(_go())


def _delete_user(user_id: int) -> None:
    async def _go() -> None:
        async with _Session() as session:
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()

    _run(_go())


def _mint_token(user_id: int, username: str) -> str:
    return auth_service.create_access_token(
        {"sub": username, "user_id": user_id, "role": "user"}
    )


def _connect(client: TestClient, url: str, token: str) -> tuple[int, str] | None:
    """用指定令牌建连：返回服务端关闭时的 (code, reason)；连接被接受则返回 None。"""
    try:
        with client.websocket_connect(url, subprotocols=[f"auth.{token}"]):
            return None
    except WebSocketDisconnect as exc:
        return exc.code, exc.reason


# ── fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture
def client():
    """TestClient，并把 get_db 指向 NullPool 会话工厂（同一个真实数据库）。"""
    from app.main import app

    async def _override_get_db():
        async with _Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def make_ws_user():
    """工厂 fixture：本条用例里建出来的真实用户，结束时统一删除。

    用例中途断言失败也不会把用户残留在库里；重复删除是幂等的（测试里自己删过的
    用户会再删一次，无副作用）。
    """
    created: list[int] = []

    def _make(*, is_active: bool = True) -> dict:
        user_id, username = _create_user(is_active=is_active)
        created.append(user_id)
        return {"id": user_id, "username": username, "token": _mint_token(user_id, username)}

    yield _make
    for user_id in created:
        _delete_user(user_id)


@pytest.fixture
def ws_user(make_ws_user):
    """一个真实存在、状态正常的用户。"""
    return make_ws_user()


@pytest.fixture
def rag_spy(monkeypatch):
    """RAG/LLM 长链路不该在用户失效后被触发：记录调用，且一被调用就立刻失败。

    消息阶段用的是 ``AsyncSessionLocal``（不是 get_db），一并指向 NullPool 工厂。
    """
    calls: list[dict] = []

    async def _spy(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("RAG pipeline must not run once the user is gone")

    monkeypatch.setattr("app.api.v1.endpoints.chat.run_rag_pipeline", _spy)
    monkeypatch.setattr("app.api.v1.endpoints.chat.AsyncSessionLocal", _Session)
    return calls


# ── 握手阶段 ────────────────────────────────────────────────────────────────
class TestHandshakeChecksUserState:
    """握手必须查库：JWT 合法 ≠ 用户仍然可用。"""

    def test_active_user_can_connect(self, client, ws_user):
        assert _connect(client, CHAT_WS, ws_user["token"]) is None

    def test_active_user_can_connect_notifications(self, client, ws_user):
        assert _connect(client, NOTIFY_WS, ws_user["token"]) is None

    def test_disabled_user_rejected(self, client, ws_user):
        _set_active(ws_user["id"], False)
        assert _connect(client, CHAT_WS, ws_user["token"]) == AUTH_FAILED_CLOSE

    def test_disabled_user_rejected_notifications(self, client, ws_user):
        _set_active(ws_user["id"], False)
        assert _connect(client, NOTIFY_WS, ws_user["token"]) == AUTH_FAILED_CLOSE

    def test_deleted_user_rejected(self, client, ws_user):
        _delete_user(ws_user["id"])
        assert _connect(client, CHAT_WS, ws_user["token"]) == AUTH_FAILED_CLOSE

    def test_deleted_user_rejected_notifications(self, client, ws_user):
        _delete_user(ws_user["id"])
        assert _connect(client, NOTIFY_WS, ws_user["token"]) == AUTH_FAILED_CLOSE

    def test_invalid_jwt_rejected(self, client):
        assert _connect(client, CHAT_WS, "not-a-real-jwt") == AUTH_FAILED_CLOSE

    def test_token_without_user_id_rejected(self, client):
        # 令牌本身合法（签名/类型都对），但缺少 user_id 声明 → 不得建连
        token = auth_service.create_access_token({"sub": "ghost", "role": "user"})
        assert _connect(client, CHAT_WS, token) is not None

    def test_deleted_and_disabled_are_indistinguishable(self, client, make_ws_user):
        deleted = make_ws_user()
        _delete_user(deleted["id"])
        disabled = make_ws_user(is_active=False)

        deleted_close = _connect(client, CHAT_WS, deleted["token"])
        disabled_close = _connect(client, CHAT_WS, disabled["token"])

        assert deleted_close is not None, "已删除用户不得建连"
        assert disabled_close is not None, "已禁用用户不得建连"
        assert deleted_close == disabled_close, "已删除与已禁用必须不可区分"
        assert deleted_close == AUTH_FAILED_CLOSE

    def test_deleted_and_disabled_are_indistinguishable_notifications(self, client, make_ws_user):
        deleted = make_ws_user()
        _delete_user(deleted["id"])
        disabled = make_ws_user(is_active=False)

        deleted_close = _connect(client, NOTIFY_WS, deleted["token"])
        disabled_close = _connect(client, NOTIFY_WS, disabled["token"])

        assert deleted_close is not None, "已删除用户不得建连"
        assert disabled_close is not None, "已禁用用户不得建连"
        assert deleted_close == disabled_close, "已删除与已禁用必须不可区分"
        assert deleted_close == AUTH_FAILED_CLOSE


# ── 消息阶段 ────────────────────────────────────────────────────────────────
class TestMessagesRejectedAfterUserInvalidated:
    """连接建立后用户失效 → 后续消息必须被拒绝，且不得回退组织继续跑 RAG。"""

    def test_message_rejected_after_user_deleted(self, client, ws_user, rag_spy):
        with client.websocket_connect(CHAT_WS, subprotocols=[f"auth.{ws_user['token']}"]) as ws:
            _delete_user(ws_user["id"])
            ws.send_text(json.dumps({"content": "还在吗", "conversationId": "c-1"}))
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_text()

        assert (exc_info.value.code, exc_info.value.reason) == AUTH_FAILED_CLOSE
        assert rag_spy == [], "用户已删除后不得再触发 RAG/LLM"

    def test_message_rejected_after_user_disabled(self, client, ws_user, rag_spy):
        with client.websocket_connect(CHAT_WS, subprotocols=[f"auth.{ws_user['token']}"]) as ws:
            _set_active(ws_user["id"], False)
            ws.send_text(json.dumps({"content": "还在吗", "conversationId": "c-1"}))
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_text()

        assert (exc_info.value.code, exc_info.value.reason) == AUTH_FAILED_CLOSE
        assert rag_spy == [], "用户被禁用后不得再触发 RAG/LLM"
