"""DocMind MCP client 包 —— 配置驱动的外部工具动态接入。

分层：
    config.py   — mcp_servers.json 加载/校验（纯配置，不连接）
    client.py   — 单 server 常驻会话（owner-task 模式；stdio / streamable-http / sse）
    mapping.py  — MCP 结果/异常 → ToolResult（诚实映射）
    manager.py  — 多 server 编排：注册进 ToolRegistry、list_changed 刷新、生命周期

入口：``mcp_manager``（app/main.py lifespan 中 initialize / shutdown）。
注意：import 本包会连带 import 官方 mcp SDK；main.py 里只在 lifespan 内
惰性 import，避免改动应用模块加载面。
"""

from app.agent.mcp.manager import MCPManager, mcp_manager, namespace_tool_name

__all__ = ["MCPManager", "mcp_manager", "namespace_tool_name"]
