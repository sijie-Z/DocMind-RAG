"""入站 MCP Server 认证、身份上下文与 per-key 限流（批次十六）。

安全口径（与任务书/计划逐条对应）：

- **只接受 ``dm_`` API Key**：任何非 ``dm_`` 前缀的 Bearer（含合法 JWT）在
  进入 MCP 协议层之前即 401 —— 入站 MCP 不是网页会话的替代入口。
- **scope 必须含 ``knowledge:read``**：缺失 403，先于任何检索/读文档动作。
- **组织只取 key 绑定组织**：``get_bound_api_key_org_id`` 只认
  ``api_key_service.authenticate`` 在认证成功时挂载的精确 int；org-unbound
  key 一律 403，绝不回落到用户 primary org（避免用户切组织后漂移）。
- **per-key 限流**：进程内固定窗口桶，key 为 ``api_key_id``（不是 IP、不是
  原始 key）；超限 429 + ``Retry-After``。多副本各算各的（已知边界）。
- **审计/日志不含敏感值**：本模块不记录 Authorization、原始 key、请求正文；
  认证失败只留固定文案。

认证结果经 :data:`ContextVar` 传给同一次 HTTP 请求内执行的工具处理器。
mcp SDK 2.x 的 stateless Streamable HTTP 在请求任务内启动工具执行任务，任务
创建时复制当前 context，因此工具里能读到本中间件设置的身份（有测试钉住）。
"""

from __future__ import annotations

import logging
import math
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.security import get_bound_api_key_org_id
from app.services.api_key_service import api_key_service

logger = logging.getLogger(__name__)

REQUIRED_SCOPE = "knowledge:read"
RATE_LIMIT_WINDOW_SECONDS = 60

#: 打开一次 DB 会话的工厂（生产为 ``AsyncSessionLocal``；测试注入内存库）。
SessionFactory = Callable[[], Any]


@dataclass(frozen=True)
class MCPIdentity:
    """一次已认证 MCP 请求的身份快照（只含工具所需的最小字段）。"""

    user_id: int
    organization_id: int
    api_key_id: str
    session_factory: SessionFactory


_identity_var: ContextVar[MCPIdentity | None] = ContextVar("mcp_request_identity", default=None)


def get_mcp_identity() -> MCPIdentity | None:
    """当前 MCP 请求的身份；不在 MCP 请求内时为 None。"""
    return _identity_var.get()


class FixedWindowRateLimiter:
    """按 key 的进程内固定窗口计数器。

    单事件循环内同步读写，无需加锁；桶数量有上界，超过后清掉过期窗口
    （极端情况下整体清空 —— 只影响限流精度，不会增长到无界内存）。
    """

    def __init__(
        self,
        *,
        window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
        max_buckets: int = 10_000,
    ) -> None:
        self._window_seconds = max(1, int(window_seconds))
        self._max_buckets = max(1, int(max_buckets))
        self._buckets: dict[str, tuple[int, int]] = {}

    def hit(self, key: str, limit: int) -> tuple[bool, int]:
        """记一次请求；返回 ``(allowed, retry_after_seconds)``。

        ``limit <= 0`` 表示不限流（仍返回 allowed）。
        """
        if limit <= 0:
            return True, 0

        now = time.monotonic()
        window = int(now // self._window_seconds)
        bucket = self._buckets.get(key)
        if bucket is None or bucket[0] != window:
            if len(self._buckets) >= self._max_buckets:
                self._prune(window)
            self._buckets[key] = (window, 1)
            return True, 0

        count = bucket[1]
        if count >= limit:
            retry_after = max(1, math.ceil(self._window_seconds - (now % self._window_seconds)))
            return False, retry_after

        self._buckets[key] = (window, count + 1)
        return True, 0

    def _prune(self, current_window: int) -> None:
        stale = [key for key, (window, _) in self._buckets.items() if window != current_window]
        for key in stale:
            del self._buckets[key]
        if len(self._buckets) >= self._max_buckets:
            # 仍在当前窗口但桶太多：整体清空，保证内存有界；限流精度可接受。
            self._buckets.clear()


def _json_response(
    status_code: int,
    error: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        {"error": error, "message": message},
        status_code=status_code,
        headers=headers,
    )


def _bearer_token(scope: Scope) -> str | None:
    """从 Authorization 头取 Bearer token；缺失/格式不对返回 None。"""
    value = Headers(scope=scope).get("authorization")
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


class MCPAuthMiddleware:
    """纯 ASGI 认证/限流中间件，包在 MCP Session Manager 外层。

    顺序刻意固定：开关（404）→ 认证（401）→ scope（403）→ 绑定组织（403）
    → 限流（429）→ 交给 MCP 协议层。任何失败都不进入 MCP，也不触碰检索。
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        session_factory: SessionFactory | None = None,
        rate_limiter: FixedWindowRateLimiter | None = None,
        is_running: Callable[[], bool] | None = None,
    ) -> None:
        self.app = app
        self._session_factory = session_factory or AsyncSessionLocal
        self._rate_limiter = rate_limiter or FixedWindowRateLimiter()
        self._is_running = is_running or (lambda: True)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 1) 功能开关：关闭时对外完全不可见（不区分路径/方法）。
        if not bool(getattr(settings, "MCP_SERVER_ENABLED", False)):
            await _json_response(404, "not_found", "MCP server is not enabled")(
                scope, receive, send
            )
            return

        # 1b) 传输形态是 stateless + json_response：没有 server->client SSE，
        #     也没有可终止的会话。只允许 POST，避免一个 key 用 GET 挂起无界流。
        if scope.get("method", "").upper() != "POST":
            await _json_response(
                405,
                "method_not_allowed",
                "This stateless MCP endpoint only supports POST",
                headers={"Allow": "POST"},
            )(scope, receive, send)
            return

        # 2) 认证：显式拒绝 JWT / 其它凭证，只走 dm_ API Key。
        token = _bearer_token(scope)
        if token is None or not token.startswith("dm_"):
            await _json_response(
                401,
                "invalid_api_key",
                "A dm_ API key is required",
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return

        async with self._session_factory() as db:
            user = await api_key_service.authenticate(token, db)
        if user is None:
            await _json_response(
                401,
                "invalid_api_key",
                "Invalid, revoked or expired API key",
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return

        # 3) scope：knowledge:read 是入站 MCP 的唯一能力面。
        scopes = getattr(user, "api_key_scopes", None)
        if not isinstance(scopes, (list, tuple, set, frozenset)) or REQUIRED_SCOPE not in scopes:
            await _json_response(
                403,
                "insufficient_scope",
                f"API key requires the {REQUIRED_SCOPE} scope",
            )(scope, receive, send)
            return

        # 4) 组织只取 key 绑定组织；org-unbound key 不使用 primary org 兜底。
        org_id = get_bound_api_key_org_id(user)
        if org_id is None:
            await _json_response(
                403,
                "organization_required",
                "The API key is not bound to an organization",
            )(scope, receive, send)
            return

        # 5) 管理面开关打开但 lifespan 未启动（接线错误）：503 而不是泄漏堆栈。
        if not self._is_running():
            await _json_response(503, "unavailable", "MCP server is starting up")(
                scope, receive, send
            )
            return

        # 6) per-key 限流：认证之后、MCP 协议层之前。
        limit = int(getattr(settings, "MCP_SERVER_RATE_LIMIT_PER_MINUTE", 60) or 0)
        api_key_id = str(getattr(user, "api_key_id", ""))
        allowed, retry_after = self._rate_limiter.hit(api_key_id, limit)
        if not allowed:
            await _json_response(
                429,
                "rate_limited",
                "Too many requests for this API key",
                headers={"Retry-After": str(retry_after)},
            )(scope, receive, send)
            return

        identity = MCPIdentity(
            user_id=int(user.id),
            organization_id=int(org_id),
            api_key_id=api_key_id,
            session_factory=self._session_factory,
        )
        context_token = _identity_var.set(identity)
        try:
            await self.app(scope, receive, send)
        finally:
            _identity_var.reset(context_token)
