"""WS 握手子协议回显回归（issue #258）。

背景：前端把 token 放进 ``Sec-WebSocket-Protocol: auth.<token>``（不落 URL/日志）。
后端鉴权一直能读到该子协议，但 ``websocket.accept()`` 从未回显 —— 浏览器在
"客户端请求了子协议而 101 响应未回显其中之一"时**必须**判定握手失败
（Chromium 报 1006 + "Sent non-empty 'Sec-WebSocket-Protocol' header but no
response was received"），通知与聊天两条 WS 因此在真实浏览器全部连不上。

TestClient 只记录 ``accepted_subprotocol``、从不校验它与客户端请求是否一致，
所以此前测试全绿。本文件直接断言回显，堵住该盲区；同时保证：无子协议时行为与
裸 ``accept()`` 一致（走 ``?token=`` 兼容回退，不回显任何东西），且缺 / 错令牌的
拒绝路径完全不变。

与 ``tests/api`` 的约定一致，不连接真实服务：DB 依赖用 mock 覆盖，令牌用真实
JWT 签名，Redis 黑名单查询固定为 False —— 本文件被测点是"回显"，黑名单语义由
其它测试覆盖。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.database import get_db
from app.core.ws_auth import extract_ws_subprotocol
from app.services.auth_service import auth_service

CHAT_WS = "/api/v1/chat/ws"
NOTIFY_WS = "/api/v1/notifications/ws"


def _mint_token(user_id: int = 1) -> str:
    return auth_service.create_access_token({"sub": "subecho", "user_id": user_id, "role": "user"})


def _mock_db(user_id: int = 1, is_active: bool = True, organization_id: int = 1) -> MagicMock:
    """假会话：``_load_user_state`` 的 ``await db.execute(select(...)).first()`` 命中一行。"""
    db = MagicMock()
    result = MagicMock()
    result.first = MagicMock(
        return_value=SimpleNamespace(
            id=user_id, is_active=is_active, organization_id=organization_id
        )
    )
    db.execute = AsyncMock(return_value=result)
    db.close = AsyncMock()
    return db


@pytest.fixture
def client(monkeypatch):
    """TestClient，get_db 指向 mock 会话；黑名单直通（本文件不依赖 Redis）。"""
    from app.main import app

    monkeypatch.setattr(
        "app.core.ws_auth.auth_service.is_token_blacklisted", AsyncMock(return_value=False)
    )
    db = _mock_db()

    async def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


# ── 纯提取逻辑 ───────────────────────────────────────────────────────────────
class TestExtractWsSubprotocol:
    """原样、按客户端顺序、只认 ``auth.`` 前缀（与鉴权选中同一个子协议）。"""

    @staticmethod
    def _ws(header: str | None):
        headers = {} if header is None else {"sec-websocket-protocol": header}
        return SimpleNamespace(headers=headers)

    def test_single_auth_subprotocol(self):
        assert extract_ws_subprotocol(self._ws("auth.abc.def")) == "auth.abc.def"

    def test_multiple_subprotocols_pick_first_auth_in_client_order(self):
        # 浏览器可带多个子协议：取客户端顺序里第一个 auth.*，与 extract_ws_token 一致
        assert extract_ws_subprotocol(self._ws("graphql-ws, auth.one, auth.two")) == "auth.one"

    def test_raw_string_returned_verbatim(self):
        # 引号等字符必须按原文返回：鉴权侧会清洗，回显侧绝不能清洗（浏览器逐字匹配）
        assert extract_ws_subprotocol(self._ws('auth."quoted"')) == 'auth."quoted"'

    def test_case_sensitive_and_non_auth_ignored(self):
        assert extract_ws_subprotocol(self._ws("Auth.abc")) is None
        assert extract_ws_subprotocol(self._ws("bearer, other")) is None
        assert extract_ws_subprotocol(self._ws(None)) is None
        assert extract_ws_subprotocol(self._ws("")) is None


# ── 两条端点的握手回显 ──────────────────────────────────────────────────────
class TestSubprotocolEchoOnHandshake:
    """修复前两条端点都回显 None（浏览器 1006）；这里逐条断言原样回显。"""

    def test_chat_ws_echoes_auth_subprotocol(self, client):
        token = _mint_token()
        with client.websocket_connect(CHAT_WS, subprotocols=[f"auth.{token}"]) as ws:
            assert ws.accepted_subprotocol == f"auth.{token}"

    def test_notification_ws_echoes_auth_subprotocol(self, client):
        token = _mint_token()
        with client.websocket_connect(NOTIFY_WS, subprotocols=[f"auth.{token}"]) as ws:
            assert ws.accepted_subprotocol == f"auth.{token}"

    def test_auth_subprotocol_echoed_even_when_not_first(self, client):
        token = _mint_token()
        with client.websocket_connect(
            NOTIFY_WS, subprotocols=["graphql-ws", f"auth.{token}"]
        ) as ws:
            assert ws.accepted_subprotocol == f"auth.{token}"

    def test_echo_is_verbatim_not_cleaned(self, client):
        # 客户端原文带引号：鉴权靠清洗后仍成功，回显必须是未清洗的原文
        token = _mint_token()
        raw = f'auth."{token}"'
        with client.websocket_connect(CHAT_WS, subprotocols=[raw]) as ws:
            assert ws.accepted_subprotocol == raw

    def test_no_subprotocol_query_token_echoes_nothing(self, client):
        # 兼容回退路径（?token=）：无请求子协议 → 不回显，行为与裸 accept() 一致
        token = _mint_token()
        with client.websocket_connect(f"{CHAT_WS}?token={token}") as ws:
            assert ws.accepted_subprotocol is None
        with client.websocket_connect(f"{NOTIFY_WS}?token={token}") as ws:
            assert ws.accepted_subprotocol is None


# ── 拒绝路径保持不变 ────────────────────────────────────────────────────────
class TestRejectionPathsUnchanged:
    """缺 / 错令牌在 accept() 之前即拒绝，回显修复不触碰该契约。"""

    def test_missing_token_rejected_4003(self, client):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(NOTIFY_WS):
                pass
        assert (exc_info.value.code, exc_info.value.reason) == (4003, "Token required")

    def test_invalid_token_rejected_4001(self, client):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(CHAT_WS, subprotocols=["auth.not-a-real-jwt"]):
                pass
        assert (exc_info.value.code, exc_info.value.reason) == (4001, "Invalid token")

    def test_invalid_token_rejected_4001_notifications(self, client):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(NOTIFY_WS, subprotocols=["auth.not-a-real-jwt"]):
                pass
        assert (exc_info.value.code, exc_info.value.reason) == (4001, "Invalid token")
