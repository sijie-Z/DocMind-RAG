"""``POST /api/v1/rpc`` 的 HTTP 契约（issue #480 §5.3 / §8）。

- 开关关闭 → 404；
- 无 Authorization → 401；JWT（无 api_key_id）→ 403；缺 rpc scope → 403；
- 有效 API Key：initialize/tools.list/未知方法/批量上限/单个对象返回。
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.security import get_current_user


def _api_user(scopes: list[str] | None = None, api_key_id: str | None = "k-1"):
    class _User:
        pass

    user = _User()
    user.id = 7
    user.organization_id = 1
    user.is_active = True
    user.is_superuser = False
    user.token_organization_id = None
    if api_key_id is not None:
        user.api_key_id = api_key_id
    if scopes is not None:
        user.api_key_scopes = scopes
    return user


@pytest.fixture
async def client(monkeypatch):
    from app.main import app

    monkeypatch.setattr("app.rpc.http.settings.RPC_HTTP_ENABLED", True, raising=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://rpc") as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)


def _use(user) -> None:
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: user


def test_rpc_http_enabled_by_default():
    """issue #493：HTTP RPC 默认开启；关闭时端点仍如实 404（见下）。

    sdk profile 不受影响：该入口只跑 stdio、不构建 FastAPI app，不挂 HTTP。
    """
    from app.core.config.base import BaseAppSettings

    assert BaseAppSettings.model_fields["RPC_HTTP_ENABLED"].default is True


@pytest.mark.asyncio
async def test_disabled_returns_404(client, monkeypatch):
    monkeypatch.setattr("app.rpc.http.settings.RPC_HTTP_ENABLED", False, raising=False)
    r = await client.post(
        "/api/v1/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={"Authorization": "Bearer dm_x"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_missing_auth_is_401(client):
    r = await client.post("/api/v1/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_jwt_without_api_key_is_403(client):
    _use(_api_user(scopes=None, api_key_id=None))
    r = await client.post(
        "/api/v1/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers={"Authorization": "Bearer jwt-token"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_missing_rpc_scope_is_403(client):
    _use(_api_user(scopes=["chat:read"]))
    r = await client.post(
        "/api/v1/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers={"Authorization": "Bearer dm_x"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_initialize_and_tools_and_unknown(client):
    _use(_api_user(scopes=["rpc"]))
    r = await client.post(
        "/api/v1/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={"Authorization": "Bearer dm_x"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == 1
    assert data["result"]["protocolVersion"] == 1
    assert data["result"]["capabilities"] == {"streaming": True, "approvals": True}
    assert "session/prompt" in data["result"]["methods"]

    r2 = await client.post(
        "/api/v1/rpc",
        json={"jsonrpc": "2.0", "id": "t", "method": "tools/list", "params": {}},
        headers={"Authorization": "Bearer dm_x"},
    )
    tools = r2.json()["result"]["tools"]
    assert tools and all("name" in tool for tool in tools)

    r3 = await client.post(
        "/api/v1/rpc",
        json={"jsonrpc": "2.0", "id": 3, "method": "nope", "params": {}},
        headers={"Authorization": "Bearer dm_x"},
    )
    assert r3.json()["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_batch_and_limit(client):
    _use(_api_user(scopes=["rpc"]))
    batch = [{"jsonrpc": "2.0", "id": i, "method": "initialize", "params": {}} for i in range(2)]
    r = await client.post("/api/v1/rpc", json=batch, headers={"Authorization": "Bearer dm_x"})
    assert r.status_code == 200
    responses = r.json()
    assert [item["id"] for item in responses] == [0, 1]

    too_many = [{"jsonrpc": "2.0", "id": i, "method": "initialize"} for i in range(21)]
    r2 = await client.post("/api/v1/rpc", json=too_many, headers={"Authorization": "Bearer dm_x"})
    assert r2.status_code == 200
    assert r2.json()["error"]["code"] == -32600
