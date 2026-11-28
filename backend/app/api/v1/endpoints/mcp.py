"""MCP 端点。

口径：
- 复用 ``mcp_manager.get_status()``（MCP 支线真实状态源）——不新增状态推导、
  不发起任何连接尝试，读不到就如实空态，绝不编造；
- 仅管理员可见：服务器接入属部署级信息（对齐 llm_config 的管理员门控惯例）；
- manager 采用**函数内惰性导入**：即使 mcp SDK 缺失也只影响本端点，
  不破坏"任何 MCP 失败不阻塞主启动"的既有约定。

批次十六：``/server`` 是**入站** MCP Server（Streamable HTTP，只读知识库），
挂在同一 router 下、使用 ``dm_`` API Key 认证（``MCP_SERVER_ENABLED`` 门控）。
它不改变上面 ``/servers``（出站管理面）的认证与语义，也不新增 ROUTER_SPECS 键。
"""

from fastapi import APIRouter, Depends

from app.core.security import get_current_user, is_platform_admin
from app.exceptions import AuthorizationError
from app.mcp_server.server import register_mcp_server_routes
from app.models.user import User

router = APIRouter()


@router.get("/servers")
async def list_mcp_servers(current_user: User = Depends(get_current_user)):
    """MCP 服务器状态列表（平台管理员）。写操作 = 编辑 backend/mcp_servers.json 后重启，无在线启停。"""
    # W0-1：MCP 服务器接入属部署级信息 —— 平台级只认显式 is_superuser。
    if not is_platform_admin(current_user):
        raise AuthorizationError("需要管理员权限")
    from app.agent.mcp.manager import mcp_manager

    return {"success": True, "data": mcp_manager.get_status()}


# 入站 MCP Server：最终 URL /api/v1/mcp/server（无尾斜杠、无 307 重定向）。
register_mcp_server_routes(router)
