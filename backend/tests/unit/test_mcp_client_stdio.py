"""MCP stdio 连接层测试 —— 真子进程 E2E（fixture: tests/fixtures/fake_mcp_server.py）。

不依赖网络与真实 MCP server；子进程用当前解释器拉起仓内假 server。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from app.agent.mcp.client import (
    MCPConnectError,
    MCPServerConnection,
    MCPTimeoutError,
    MCPTransportError,
)
from app.agent.mcp.config import MCPServerConfig

FIXTURE = str(Path(__file__).resolve().parent.parent / "fixtures" / "fake_mcp_server.py")


def make_config(name: str = "fake", **overrides) -> MCPServerConfig:
    values = {
        "name": name,
        "transport": "stdio",
        "enabled": True,
        "timeout": 5.0,
        "command": sys.executable,
        "args": [FIXTURE],
    }
    values.update(overrides)
    return MCPServerConfig(**values)


@pytest.mark.asyncio
async def test_connect_list_call_e2e():
    conn = MCPServerConnection(make_config())
    try:
        await conn.connect(timeout=10)
        assert conn.is_connected and conn.state == "connected"

        tools = await conn.list_tools()
        names = {t.name for t in tools}
        assert {"echo", "fail", "slow", "die", "set_tools"} <= names
        echo = next(t for t in tools if t.name == "echo")
        assert echo.input_schema.get("required") == ["text"]

        result = await conn.call_tool("echo", {"text": "hi"})
        assert result.is_error is False
        assert result.content[0]["text"] == "echo:hi"
    finally:
        await conn.close()
    assert conn.state == "closed"


@pytest.mark.asyncio
async def test_remote_jsonrpc_error_keeps_session_alive():
    conn = MCPServerConnection(make_config())
    try:
        await conn.connect()
        with pytest.raises(Exception) as ei:
            await conn.call_tool("no-such-tool", {})
        from app.agent.mcp.client import MCPRemoteError

        assert isinstance(ei.value, MCPRemoteError)
        assert ei.value.code == -32602
        # 服务器活着：下一条调用照常
        result = await conn.call_tool("echo", {"text": "still-alive"})
        assert result.content[0]["text"] == "echo:still-alive"
        assert conn.is_connected
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_timeout_then_session_reusable():
    conn = MCPServerConnection(make_config(timeout=2.0))
    try:
        await conn.connect()
        with pytest.raises(MCPTimeoutError):
            await conn.call_tool("slow", {"seconds": 10}, timeout=1.0)
        # 超时不得毒化会话
        result = await conn.call_tool("echo", {"text": "after-timeout"})
        assert result.content[0]["text"] == "echo:after-timeout"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_crash_detected_then_reconnect():
    conn = MCPServerConnection(make_config())
    try:
        await conn.connect()
        # die：先回结果再 os._exit —— 响应与进程退出是竞态，两种结局都合法：
        # 1) 响应已送达（call 正常返回，后续调用才发现连接死）
        # 2) 响应丢失（call 直接抛 MCPTransportError，连接已标记 dead）
        try:
            await conn.call_tool("die", {})
        except MCPTransportError:
            pass
        await asyncio.sleep(0.3)

        from app.agent.mcp.client import MCPClientError

        with pytest.raises(MCPClientError):
            await conn.call_tool("echo", {"text": "x"})
        assert not conn.is_connected

        # 惰性重连：同一连接对象重新 connect
        await conn.connect(timeout=10)
        assert conn.is_connected
        result = await conn.call_tool("echo", {"text": "reborn"})
        assert result.content[0]["text"] == "echo:reborn"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_silent_server_connect_timeout_then_clean():
    cfg = make_config(args=[FIXTURE, "--silent"], timeout=3.0)
    conn = MCPServerConnection(cfg)
    with pytest.raises(MCPConnectError):
        await conn.connect(timeout=2)
    assert not conn.is_connected
    await conn.close()  # 必须快速、幂等、不挂

    # 取消路径不得拖坏同进程里的下一次连接
    good = MCPServerConnection(make_config(timeout=5.0))
    try:
        await good.connect(timeout=10)
        result = await good.call_tool("echo", {"text": "ok"})
        assert result.content[0]["text"] == "echo:ok"
    finally:
        await good.close()


@pytest.mark.asyncio
async def test_bad_command_connect_error():
    conn = MCPServerConnection(make_config(command="docmind-no-such-binary-xyz"))
    with pytest.raises(MCPConnectError):
        await conn.connect(timeout=5)
    assert conn.state in ("dead", "closed")
    await conn.close()


@pytest.mark.asyncio
async def test_close_is_idempotent_and_safe_before_connect():
    conn = MCPServerConnection(make_config())
    await conn.close()  # 从未连接
    await conn.close()
    assert conn.state == "closed"


@pytest.mark.asyncio
async def test_ping_health():
    conn = MCPServerConnection(make_config())
    try:
        await conn.connect()
        assert await conn.ping(timeout=5) is True
        await conn.close()
        assert await conn.ping(timeout=1) is False
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_concurrent_calls_from_multiple_tasks():
    conn = MCPServerConnection(make_config())
    try:
        await conn.connect()

        async def one(text: str):
            r = await conn.call_tool("echo", {"text": text})
            return r.content[0]["text"]

        results = await asyncio.gather(*(asyncio.create_task(one(f"t{i}")) for i in range(3)))
        assert results == ["echo:t0", "echo:t1", "echo:t2"]
    finally:
        await conn.close()
