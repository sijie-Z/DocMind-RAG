"""入站 MCP Server（Streamable HTTP, stateless + json_response）——批次十六。

挂载口径：

- 复用既有 ``/api/v1/mcp`` 前缀（``backend/app/api/v1/endpoints/mcp.py``），
  最终 URL 为 ``/api/v1/mcp/server``；不新增 ``ROUTER_SPECS`` 键。
- 不用 ``Mount``：Starlette 的 Mount 会把「无尾斜杠的精确路径」307 重定向到
  带斜杠路径，部分 MCP 客户端不跟随重定向。这里用一个 FastAPI 路由返回
  ``ASGIResponse``，把原始 ``scope/receive/send`` 直接交给 MCP ASGI 应用，
  精确路径无重定向。
- 认证/限流在 :mod:`app.mcp_server.auth` 的纯 ASGI 中间件里完成，先于 MCP
  协议层；工具实现见 :mod:`app.mcp_server.tools`。
- Session Manager 的 ``run()`` 由 ``app/bootstrap.py`` 的 lifespan 通过
  ``AsyncExitStack`` 进出（mounted/sub-app 的 lifespan 不会被父应用自动执行）。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

from fastapi import APIRouter, Response
from mcp.server.mcpserver import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings

from app.core.database import AsyncSessionLocal
from app.mcp_server.auth import MCPAuthMiddleware
from app.mcp_server.tools import register_readonly_tools

logger = logging.getLogger(__name__)

SERVER_NAME = "docmind-knowledge"
SERVER_INSTRUCTIONS = (
    "DocMind read-only knowledge base. Use search_knowledge_base to find relevant "
    "document chunks, then get_document to read a bounded excerpt. All access is "
    "scoped to the organization bound to the presented API key; private documents "
    "follow the document ACL."
)


class ASGIResponse(Response):
    """把 FastAPI 路由的响应阶段直接转交给一个 ASGI 应用。

    FastAPI 看到返回值是 ``Response`` 实例时不会再做序列化，Starlette 随后
    以 ``await response(scope, receive, send)`` 调起本类；这里只做一次转发，
    从而让 MCP 的 ASGI 处理器拿到原始请求/响应流（包括 SSE/JSON 的差异）。
    """

    def __init__(self, app: Callable[[Any, Any, Any], Any]) -> None:
        super().__init__(status_code=200)
        self._app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await self._app(scope, receive, send)


class MCPInboundServer:
    """一个入站 MCP Server 实例：MCP 协议栈 + 认证中间件 + 生命周期。"""

    def __init__(self, *, session_factory: Callable[[], Any] | None = None) -> None:
        self._session_factory = session_factory or AsyncSessionLocal
        self._running = False

        server = MCPServer(
            name=SERVER_NAME,
            instructions=SERVER_INSTRUCTIONS,
        )
        register_readonly_tools(server)
        # 创建并注册 StreamableHTTPSessionManager；返回的 Starlette app 仅用于
        # 生成 manager，真正的请求入口是本类持有的 StreamableHTTPASGIApp。
        server.streamable_http_app(
            streamable_http_path="/",
            json_response=True,
            stateless_http=True,
            # 服务部署在任意域名/反代之后；DNS rebinding 防护是 localhost 默认
            # 策略，会拒绝合法 Host。认证已是 dm_ key，不依赖 Host 白名单。
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        self._server = server
        self._session_manager = server.session_manager
        mcp_app = StreamableHTTPASGIApp(self._session_manager)
        self.asgi_app = MCPAuthMiddleware(
            mcp_app,
            session_factory=self._session_factory,
            is_running=lambda: self._running,
        )

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def session_manager(self) -> Any:
        return self._session_manager

    @asynccontextmanager
    async def session_lifespan(self) -> AsyncIterator[None]:
        """进入/退出 MCP Session Manager；退出后任务组与连接均被清理。"""
        async with self._session_manager.run():
            self._running = True
            try:
                yield
            finally:
                self._running = False


_singleton: MCPInboundServer | None = None


def create_inbound_mcp_server(
    *, session_factory: Callable[[], Any] | None = None
) -> MCPInboundServer:
    """构造独立的入站 MCP Server（测试用；生产用进程级单例）。"""
    return MCPInboundServer(session_factory=session_factory)


def get_inbound_mcp_server() -> MCPInboundServer:
    """进程级入站 MCP Server 单例。

    路由在 import 时创建；lifespan 与路由必须指向同一个 manager，因此单例
    是刻意的。测试可 monkeypatch 本函数返回各自隔离的实例。
    """
    global _singleton
    if _singleton is None:
        _singleton = MCPInboundServer()
    return _singleton


def register_mcp_server_routes(router: APIRouter) -> None:
    """把 ``/server`` 挂到既有 mcp router（最终 ``/api/v1/mcp/server``）。"""

    async def inbound_mcp_server() -> Response:
        return ASGIResponse(get_inbound_mcp_server().asgi_app)

    # 显式登记无尾斜杠与带尾斜杠两个精确路径：避免 Starlette 的
    # redirect_slashes 在鉴权前发 307（Location 由请求 Host 拼出）。
    # 所有方法都交给认证中间件：关闭时 404，开启时非 POST 405。
    for path in ("/server", "/server/"):
        router.add_api_route(
            path,
            inbound_mcp_server,
            methods=["GET", "POST", "DELETE", "PUT", "PATCH"],
            include_in_schema=False,
            response_model=None,
        )
