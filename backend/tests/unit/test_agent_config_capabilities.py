"""GET /agent/config 暴露服务端能力：前端据此禁用/隐藏不可用配置。"""

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_get_config_exposes_enforced_tools_and_terminal_flag(monkeypatch):
    from app.api.v1.endpoints import agent as agent_api

    async def _no_saved(_key):
        return None

    monkeypatch.setattr(agent_api.AgentConfig, "load_from_redis", _no_saved)
    monkeypatch.setattr(agent_api.terminal_service, "enabled", lambda: False)

    payload = await agent_api.get_config(current_user=SimpleNamespace(id=1))

    caps = payload["capabilities"]
    assert caps["terminal_enabled"] is False
    assert "execute_python" in caps["enforced_disabled_tools"]
    assert "execute_sql" in caps["enforced_disabled_tools"]
    assert "mcp_call" in caps["enforced_disabled_tools"]
    # 配置本体仍按原契约在 data
    assert "model" in payload["data"]

@pytest.mark.asyncio
async def test_get_config_reports_terminal_enabled_when_available(monkeypatch):
    from app.api.v1.endpoints import agent as agent_api

    async def _no_saved(_key):
        return None

    monkeypatch.setattr(agent_api.AgentConfig, "load_from_redis", _no_saved)
    monkeypatch.setattr(agent_api.terminal_service, "enabled", lambda: True)

    payload = await agent_api.get_config(current_user=SimpleNamespace(id=1))
    assert payload["capabilities"]["terminal_enabled"] is True
