"""MCP streamable HTTP 传输测试 —— httpx2.MockTransport 模拟服务端（无端口/无网络）。

覆盖：initialize（含 Mcp-Session-Id 下发）→ tools/list（SSE 响应）→ tools/call
（JSON 响应）→ 会话头/协议版本头透传 → DELETE 收尾；以及 manager 级注册 E2E。
"""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest

from app.agent.mcp.client import MCPConnectError, MCPRemoteError, MCPServerConnection
from app.agent.mcp.config import MCPServerConfig
from app.agent.mcp.manager import MCPManager
from app.agent.registry import tool_registry

SESSION_ID = "sess-docmind-http"


class FakeStreamableHTTP:
    """极简 streamable HTTP MCP server（按 MCP 2025-11-25 消息流）。"""

    def __init__(self, *, fail_initialize: bool = False):
        self.fail_initialize = fail_initialize
        self.seen: list[dict[str, Any]] = []
        self.deleted = False

    # ── 工具面 ──
    def _tools(self) -> list[dict[str, Any]]:
        return [{
            "name": "remote_echo",
            "description": "HTTP 端回显",
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                            "required": ["text"]},
        }]

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        headers = {k.lower(): str(v) for k, v in request.headers.items()}
        if request.method == "GET":
            return httpx2.Response(405)  # 允许：不提供 server→client 流
        if request.method == "DELETE":
            self.deleted = True
            return httpx2.Response(200)

        body = json.loads(request.content) if request.content else None
        method = body.get("method") if isinstance(body, dict) else None
        self.seen.append({
            "method": method,
            "session": headers.get("mcp-session-id"),
            "protocol": headers.get("mcp-protocol-version"),
        })

        if method == "initialize":
            if self.fail_initialize:
                return httpx2.Response(500, json={"error": "boom"})
            assert not headers.get("mcp-session-id"), "initialize 不应携带 session id"
            return httpx2.Response(
                200,
                headers={"content-type": "application/json", "mcp-session-id": SESSION_ID},
                json={"jsonrpc": "2.0", "id": body["id"], "result": {
                    "protocolVersion": body["params"]["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-http", "version": "0.1"}}},
            )
        if method == "notifications/initialized":
            return httpx2.Response(202)

        if method == "tools/list":
            payload = {"jsonrpc": "2.0", "id": body["id"], "result": {"tools": self._tools()}}
            # 有意走 SSE 分支：验证客户端对 text/event-stream 的解析
            sse = "event: message\ndata: " + json.dumps(payload) + "\n\n"
            return httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, content=sse.encode())
        if method == "tools/call":
            params = body.get("params") or {}
            if params.get("name") == "remote_echo":
                args = params.get("arguments") or {}
                return httpx2.Response(200, headers={"content-type": "application/json"}, json={
                    "jsonrpc": "2.0", "id": body["id"], "result": {
                        "content": [{"type": "text", "text": "remote:" + str(args.get("text", ""))}]}})
            return httpx2.Response(200, headers={"content-type": "application/json"}, json={
                "jsonrpc": "2.0", "id": body["id"],
                "error": {"code": -32602, "message": "Unknown tool"}})
        if method == "ping":
            return httpx2.Response(200, headers={"content-type": "application/json"},
                                   json={"jsonrpc": "2.0", "id": body["id"], "result": {}})
        return httpx2.Response(400, json={"error": f"unknown method {method}"})


def make_factory(fake: FakeStreamableHTTP):
    def factory() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(fake))
    return factory


def make_config(**overrides) -> MCPServerConfig:
    values = {
        "name": "remote",
        "transport": "http",
        "enabled": True,
        "timeout": 5.0,
        "url": "http://mock.local/mcp",
    }
    values.update(overrides)
    return MCPServerConfig(**values)


@pytest.fixture()
def registry_isolation():
    before = set(tool_registry._tools)
    yield
    for name in list(tool_registry._tools):
        if name not in before:
            tool_registry.unregister(name)


@pytest.mark.asyncio
async def test_http_connect_list_call_e2e():
    fake = FakeStreamableHTTP()
    conn = MCPServerConnection(make_config(), http_client_factory=make_factory(fake))
    try:
        await conn.connect(timeout=5)
        assert conn.is_connected

        tools = await conn.list_tools()
        assert [t.name for t in tools] == ["remote_echo"]

        result = await conn.call_tool("remote_echo", {"text": "hi"})
        assert result.content[0]["text"] == "remote:hi"

        # 会话/协议头透传断言（initialize 之后的每个请求）
        later = [s for s in fake.seen if s["method"] not in ("initialize",)]
        assert later and all(s["session"] == SESSION_ID for s in later)
        assert all(s["protocol"] for s in later)
    finally:
        await conn.close()
    assert fake.deleted is True  # terminate_on_close 发出 DELETE


@pytest.mark.asyncio
async def test_http_remote_error_raises_remote_error():
    fake = FakeStreamableHTTP()
    conn = MCPServerConnection(make_config(), http_client_factory=make_factory(fake))
    try:
        await conn.connect(timeout=5)
        with pytest.raises(MCPRemoteError) as ei:
            await conn.call_tool("nope", {})
        assert ei.value.code == -32602
        assert conn.is_connected  # 服务器活着
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_http_initialize_failure_raises_connect_error():
    fake = FakeStreamableHTTP(fail_initialize=True)
    conn = MCPServerConnection(make_config(), http_client_factory=make_factory(fake))
    with pytest.raises(MCPConnectError):
        await conn.connect(timeout=5)
    assert not conn.is_connected
    await conn.close()


@pytest.mark.asyncio
async def test_http_through_manager_registers_and_calls(registry_isolation):
    fake = FakeStreamableHTTP()
    mgr = MCPManager(configs=[make_config()], http_client_factory=make_factory(fake))
    try:
        status = await mgr.initialize()
        assert status["registered_tools"] == 1
        assert tool_registry.get("mcp__remote__remote_echo") is not None
        out = await tool_registry.execute("mcp__remote__remote_echo", {"text": "via-registry"})
        assert out == "remote:via-registry"
        # inputSchema 校验在本地先行
        bad = await tool_registry.execute_detailed("mcp__remote__remote_echo", {})
        assert not bad.success and bad.error.code == "validation_error"
    finally:
        await mgr.shutdown()
    assert tool_registry.get("mcp__remote__remote_echo") is None
