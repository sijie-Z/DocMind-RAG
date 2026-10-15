"""MCP 服务器状态端点（只读管理面）。

口径：
- 复用 ``mcp_manager.get_status()``（MCP 支线真实状态源）——不新增状态推导、
  不发起任何连接尝试，读不到就如实空态，绝不编造；
- 仅管理员可见：服务器接入属部署级信息（对齐 llm_config 的管理员门控惯例）；
- manager 采用**函数内惰性导入**：即使 mcp SDK 缺失也只影响本端点，
  不破坏"任何 MCP 失败不阻塞主启动"的既有约定。
"""

from fastapi import APIRouter, Depends

from app.core.security import get_current_user
from app.exceptions import AuthorizationError
from app.models.user import User

router = APIRouter()


@router.get("/servers")
async def list_mcp_servers(current_user: User = Depends(get_current_user)):
    """MCP 服务器状态列表（管理员）。写操作 = 编辑 backend/mcp_servers.json 后重启，无在线启停。"""
    if not current_user.is_superuser and current_user.role != "admin":
        raise AuthorizationError("需要管理员权限")
    from app.agent.mcp.manager import mcp_manager

    return {"success": True, "data": mcp_manager.get_status()}
