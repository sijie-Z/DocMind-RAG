"""MCP 服务器状态端点测试（ASGI 全链 + dependency_overrides；不连真实服务）。

覆盖：管理员门控（普通用户 403 / admin 角色放行 / 超管放行）/
已配置服务器状态透出（disabled、not_connected、connected+工具名、last_error）/
无配置诚实空态 / 未初始化诚实空态。

端点读全局单例 ``mcp_manager``；测试用替身管理器替换模块属性（monkeypatch
自动恢复），替身是真实 ``MCPManager`` 实例——只注入内部状态，不发起连接。
"""

from types import SimpleNamespace

import httpx
import pytest

BASE = "/api/v1/mcp/servers"


def _make_user(uid=1, superuser=False, role="user"):
    return SimpleNamespace(
        id=uid,
        username=f"user{uid}",
        organization_id=1,
        is_superuser=superuser,
        is_active=True,
        role=role,
    )


@pytest.fixture
async def api(monkeypatch):
    from app.core.security import get_current_user
    from app.main import app

    state = SimpleNamespace(user=_make_user())

    def _override_user():
        return state.user

    app.dependency_overrides[get_current_user] = _override_user
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield SimpleNamespace(client=ac, state=state, monkeypatch=monkeypatch)
    app.dependency_overrides.clear()


def _use_manager(api, mgr):
    api.monkeypatch.setattr("app.agent.mcp.manager.mcp_manager", mgr)


async def _get(api):
    return await api.client.get(BASE)


@pytest.mark.asyncio
async def test_non_admin_forbidden(api):
    r = await _get(api)
    assert r.status_code == 403
    # 与 llm_config 门控同型：首参进 message（detail 留空）
    assert "管理员" in r.json()["message"]


@pytest.mark.asyncio
async def test_role_admin_label_is_not_platform_admin(api):
    """W0-1：users.role='admin' 不再映射为平台管理员（ADR-001 D3）。"""
    from app.agent.mcp.manager import MCPManager

    api.state.user = _make_user(role="admin")
    _use_manager(api, MCPManager(configs=[]))
    r = await _get(api)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_superuser_allowed(api):
    from app.agent.mcp.manager import MCPManager

    api.state.user = _make_user(superuser=True)
    _use_manager(api, MCPManager(configs=[]))
    r = await _get(api)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_uninitialized_manager_honest_empty(api):
    """未初始化（lifespan 前 / 从未加载）：started=false + 空列表，不编造。"""
    from app.agent.mcp.manager import MCPManager

    api.state.user = _make_user(superuser=True)
    _use_manager(api, MCPManager())  # configs=None（尚未加载）
    r = await _get(api)
    body = r.json()
    assert r.status_code == 200 and body["success"] is True
    assert body["data"]["started"] is False
    assert body["data"]["servers"] == []
    assert body["data"]["registered_tools"] == 0


@pytest.mark.asyncio
async def test_no_servers_configured_honest_empty(api):
    from app.agent.mcp.manager import MCPManager

    api.state.user = _make_user(superuser=True)
    mgr = MCPManager(configs=[])
    mgr._started = True
    _use_manager(api, mgr)
    r = await _get(api)
    body = r.json()
    assert body["data"]["started"] is True
    assert body["data"]["servers"] == []


@pytest.mark.asyncio
async def test_configured_servers_states_are_honest(api):
    """三种状态如实透出：disabled / not_connected / connected+工具名+错误。"""
    from app.agent.mcp.config import MCPServerConfig
    from app.agent.mcp.manager import MCPManager

    api.state.user = _make_user(superuser=True)
    mgr = MCPManager(
        configs=[
            MCPServerConfig(name="off", enabled=False, description="未启用"),
            MCPServerConfig(name="pending", enabled=True),
            MCPServerConfig(name="live", enabled=True),
        ]
    )
    mgr._started = True
    mgr._connections["live"] = SimpleNamespace(state="connected")
    mgr._registered["live"] = {"mcp__live__read_file": "read_file"}
    mgr._last_errors["pending"] = "MCPConnectError: 初始化失败: FileNotFoundError"
    _use_manager(api, mgr)

    r = await _get(api)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["started"] is True
    by_name = {s["name"]: s for s in data["servers"]}
    assert by_name["off"]["state"] == "disabled"
    assert by_name["pending"]["state"] == "not_connected"
    assert "FileNotFoundError" in by_name["pending"]["last_error"]
    assert by_name["live"]["state"] == "connected"
    assert by_name["live"]["tools"] == 1
    assert by_name["live"]["tool_names"] == ["read_file"]
    assert by_name["live"]["last_error"] is None
    assert data["registered_tools"] == 1
