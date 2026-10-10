"""MCP manager 测试 —— 注册表集成、命名空间、冲突降级、热刷新、生命周期。

全部基于仓内假 server 真子进程；通过全局 tool_registry 做端到端断言。
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

import app.agent.tools  # noqa: F401 —— 注册 33 个原生工具（保证“原生行为不受影响”断言有基线）
from app.agent.mcp.config import MCPServerConfig
from app.agent.mcp.manager import MCPManager, namespace_tool_name
from app.agent.registry import ToolResult, tool_registry

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


@pytest.fixture()
def registry_isolation():
    """快照注册表键集；测试结束移除测试期间新增的键，恢复既有 33 工具原状。"""
    before = set(tool_registry._tools)
    yield
    for name in list(tool_registry._tools):
        if name not in before:
            tool_registry.unregister(name)


async def _wait_for(cond, timeout: float = 8.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(interval)
    return cond()


@pytest.mark.asyncio
async def test_discovery_register_call_e2e(registry_isolation):
    """发现 → 注册 → 经 registry 调用（E2E 主链）。"""
    mgr = MCPManager(configs=[make_config()])
    try:
        status = await mgr.initialize()
        assert status["registered_tools"] == 5
        assert status["servers"][0]["state"] == "connected"

        entry = tool_registry.get("mcp__fake__echo")
        assert entry is not None
        assert "mcp" in entry.tags and "external" in entry.tags

        out = await tool_registry.execute("mcp__fake__echo", {"text": "hi"})
        assert out == "echo:hi"

        detailed = await tool_registry.execute_detailed("mcp__fake__echo", {"text": "z"})
        assert detailed.success and detailed.data == "echo:z"
    finally:
        await mgr.shutdown()

    assert tool_registry.get("mcp__fake__echo") is None  # shutdown 摘除


@pytest.mark.asyncio
async def test_input_schema_validation_before_remote_call(registry_isolation):
    mgr = MCPManager(configs=[make_config()])
    try:
        await mgr.initialize()
        result = await tool_registry.execute_detailed("mcp__fake__echo", {})
        assert not result.success
        assert result.error.code == "validation_error"
        assert "required" in result.error.message.lower() or "text" in result.error.message
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_iserror_maps_to_structured_failure(registry_isolation):
    """服务器 isError 绝不伪装成功。"""
    mgr = MCPManager(configs=[make_config()])
    try:
        await mgr.initialize()
        out = await tool_registry.execute("mcp__fake__fail", {})
        assert out.startswith("Error:")
        detailed = await tool_registry.execute_detailed("mcp__fake__fail", {})
        assert not detailed.success and detailed.error.code == "api_error"
        assert "boom" in detailed.error.message
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_timeout_result_and_session_survives(registry_isolation):
    mgr = MCPManager(configs=[make_config(timeout=2.0)])
    try:
        await mgr.initialize()
        out = await tool_registry.execute_detailed("mcp__fake__slow", {"seconds": 10})
        assert not out.success and out.error.code == "timeout"
        # 超时后同连接可继续用
        again = await tool_registry.execute("mcp__fake__echo", {"text": "still"})
        assert again == "echo:still"
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_dead_server_fails_then_reconnects_on_next_call(registry_isolation):
    mgr = MCPManager(configs=[make_config()])
    try:
        await mgr.initialize()
        await tool_registry.execute("mcp__fake__die", {})
        await asyncio.sleep(0.3)

        first = await tool_registry.execute_detailed("mcp__fake__echo", {"text": "x"})
        assert not first.success and first.error.code == "unreachable"

        # 下一次调用触发惰性重连并成功
        second = await tool_registry.execute("mcp__fake__echo", {"text": "reborn"})
        assert second == "echo:reborn"
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_namespace_isolation_two_servers_same_tools(registry_isolation):
    mgr = MCPManager(configs=[make_config("alpha"), make_config("beta")])
    try:
        await mgr.initialize()
        assert tool_registry.get("mcp__alpha__echo") is not None
        assert tool_registry.get("mcp__beta__echo") is not None
        assert await tool_registry.execute("mcp__alpha__echo", {"text": "A"}) == "echo:A"
        assert await tool_registry.execute("mcp__beta__echo", {"text": "B"}) == "echo:B"
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_name_conflict_with_existing_tool_skipped(registry_isolation, caplog):
    """命名冲突 → 首个注册者保留 + warning（降级不覆盖）。"""
    async def native_stub(**kwargs) -> ToolResult:
        return ToolResult.ok(data="native-wins")

    tool_registry.register(
        name="mcp__fake__echo", description="pre-existing", parameters={"type": "object"},
        handler=native_stub,
    )
    mgr = MCPManager(configs=[make_config()])
    try:
        with caplog.at_level("WARNING"):
            await mgr.initialize()
        assert any("命名冲突" in r.message for r in caplog.records)
        # 未被 MCP 覆盖
        out = await tool_registry.execute("mcp__fake__echo", {"text": "x"})
        assert out == "native-wins"
        # 其余工具正常注册
        fail_out = await tool_registry.execute("mcp__fake__fail", {})
        assert fail_out.startswith("Error:")
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_init_failure_does_not_block_and_registers_nothing(registry_isolation, caplog):
    bad = make_config("broken", command="docmind-no-such-binary-xyz")
    good = make_config("fake")
    mgr = MCPManager(configs=[bad, good])
    try:
        with caplog.at_level("WARNING"):
            status = await mgr.initialize()  # 绝不抛
        states = {s["name"]: s["state"] for s in status["servers"]}
        assert states["broken"] in ("not_connected", "dead")
        assert states["fake"] == "connected"
        # 坏 server 的工具一个都没注册；好 server 正常
        assert tool_registry.get("mcp__broken__echo") is None
        assert tool_registry.get("mcp__fake__echo") is not None
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_refresh_on_tools_list_changed(registry_isolation):
    """tools/list_changed → 增量注册新工具、移除消失工具。"""
    mgr = MCPManager(configs=[make_config()])
    try:
        await mgr.initialize()
        assert tool_registry.get("mcp__fake__echo2") is None

        await tool_registry.execute("mcp__fake__set_tools", {"name": "echo2", "enabled": True})
        assert await _wait_for(lambda: tool_registry.get("mcp__fake__echo2") is not None)
        assert await tool_registry.execute("mcp__fake__echo2", {"value": "v"}) == "echo2:v"

        await tool_registry.execute("mcp__fake__set_tools", {"name": "echo2", "enabled": False})
        assert await _wait_for(lambda: tool_registry.get("mcp__fake__echo2") is None)
        # echo 仍在
        assert await tool_registry.execute("mcp__fake__echo", {"text": "keep"}) == "echo:keep"
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_shutdown_removes_only_mcp_tools(registry_isolation):
    native_before = {n for n in tool_registry._tools if not n.startswith("mcp__")}
    assert "calculate" in native_before  # 基线：原生工具在（utility.py，随 app.agent.tools 注册）

    mgr = MCPManager(configs=[make_config()])
    await mgr.initialize()
    assert any(n.startswith("mcp__fake__") for n in tool_registry._tools)
    await mgr.shutdown()

    assert not any(n.startswith("mcp__fake__") for n in tool_registry._tools)
    native_after = {n for n in tool_registry._tools if not n.startswith("mcp__")}
    assert native_after == native_before  # 原生工具零变化


class TestRegistryUnregister:
    def test_unregister_removes_and_is_idempotent(self, registry_isolation):
        async def stub(**kwargs) -> str:
            return "ok"

        tool_registry.register(name="__tmp_dyn__", description="d", parameters={}, handler=stub)
        assert tool_registry.get("__tmp_dyn__") is not None
        assert tool_registry.unregister("__tmp_dyn__") is True
        assert tool_registry.get("__tmp_dyn__") is None
        assert tool_registry.unregister("__tmp_dyn__") is False  # 幂等
        assert tool_registry.unregister("never-existed-zzz") is False

    def test_unregister_does_not_touch_others(self, registry_isolation):
        async def stub(**kwargs) -> str:
            return "ok"

        tool_registry.register(name="__tmp_a__", description="d", parameters={}, handler=stub)
        before = set(tool_registry._tools)
        tool_registry.unregister("__tmp_a__")
        assert set(tool_registry._tools) == before - {"__tmp_a__"}


@pytest.mark.asyncio
async def test_initialize_is_idempotent(registry_isolation):
    mgr = MCPManager(configs=[make_config()])
    try:
        first = await mgr.initialize()
        second = await mgr.initialize()
        assert first["registered_tools"] == second["registered_tools"] == 5
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_disabled_server_never_connected(registry_isolation):
    mgr = MCPManager(configs=[make_config("off", enabled=False)])
    try:
        status = await mgr.initialize()
        assert status["registered_tools"] == 0
        assert status["servers"][0]["state"] == "disabled"
        assert tool_registry.get("mcp__off__echo") is None
    finally:
        await mgr.shutdown()


# ── 状态视图（管理面数据源） ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_view_started_tool_names_and_description(registry_isolation):
    """状态视图：未启动/已启动、工具原始名列表、description 透出。"""
    cfg = make_config("demo")
    cfg.description = "假 server（测试用）"
    mgr = MCPManager(configs=[cfg])
    before = mgr.get_status()
    assert before["started"] is False
    assert before["servers"][0]["state"] == "not_connected"  # enabled 但尚未连接

    try:
        status = await mgr.initialize()
        assert status["started"] is True
        srv = status["servers"][0]
        assert srv["name"] == "demo"
        assert srv["state"] == "connected"
        assert srv["description"] == "假 server（测试用）"
        assert srv["tools"] == 5
        assert srv["tool_names"] == ["die", "echo", "fail", "set_tools", "slow"]
        assert srv["last_error"] is None
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_status_reports_connect_failure_and_clears_stale_error(registry_isolation):
    """接入失败原文进状态视图；成功接入清除历史错误；shutdown 清空。"""
    broken = make_config("broken", command="docmind-no-such-binary-xyz")
    good = make_config("good")
    mgr = MCPManager(configs=[broken, good])
    mgr._last_errors["good"] = "stale-from-previous-run"  # 预置脏状态：成功接入必须清除

    try:
        status = await mgr.initialize()
        by_name = {s["name"]: s for s in status["servers"]}
        assert by_name["broken"]["last_error"], "接入失败必须在状态视图可见"
        assert "broken" in by_name["broken"]["last_error"]
        assert by_name["good"]["last_error"] is None
    finally:
        await mgr.shutdown()

    after = mgr.get_status()
    assert after["started"] is False
    assert all(s["last_error"] is None for s in after["servers"])


@pytest.mark.asyncio
async def test_status_tool_names_track_refresh(registry_isolation):
    """tools/list_changed 刷新后，状态视图的 tool_names 同步增删。"""
    mgr = MCPManager(configs=[make_config()])
    try:
        await mgr.initialize()
        assert "echo2" not in mgr.get_status()["servers"][0]["tool_names"]

        await tool_registry.execute("mcp__fake__set_tools", {"name": "echo2", "enabled": True})
        assert await _wait_for(lambda: "echo2" in mgr.get_status()["servers"][0]["tool_names"])

        await tool_registry.execute("mcp__fake__set_tools", {"name": "echo2", "enabled": False})
        assert await _wait_for(lambda: "echo2" not in mgr.get_status()["servers"][0]["tool_names"])
    finally:
        await mgr.shutdown()


class TestNamespace:
    def test_default_naming(self):
        assert namespace_tool_name("github", "search_repos") == "mcp__github__search_repos"

    def test_unsafe_chars_sanitized(self):
        assert namespace_tool_name("my.server", "fs.read file") == "mcp__my_server__fs_read_file"

    def test_overlong_names_hashed(self):
        name = namespace_tool_name("s" * 40, "t" * 60)
        assert len(name) <= 64
        # 稳定：同样入参恒出同名
        assert name == namespace_tool_name("s" * 40, "t" * 60)
