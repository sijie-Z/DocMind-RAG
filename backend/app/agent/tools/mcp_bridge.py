"""MCP Bridge Tool — call external tools via the Model Context Protocol.

MCP servers are configured in MCP_SERVERS below (or loaded from env vars).
The registered tool "mcp_call" lets the LLM call any external MCP tool.

Usage:
    mcp_call(server="github", tool="search_repositories", args={"query": "..."})
    mcp_call(server="filesystem", tool="read_file", args={"path": "/tmp/test.txt"})
"""

import logging
import os
from typing import Any

from mcp import StdioServerParameters, stdio_client
from mcp.client.session import ClientSession

from app.agent.registry import register_tool

logger = logging.getLogger(__name__)

# ── MCP Server Registry ───────────────────────────────────────────────
# Add new MCP servers here. Env vars can override via MCP_SERVER_{NAME}_*
MCP_SERVERS: dict[str, dict[str, Any]] = {
    "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_TOKEN": os.getenv("GITHUB_TOKEN", "")},
        "description": "GitHub API — issues, PRs, repos, search, code review",
    },
    "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", os.getenv("MCP_FS_ROOT", "/tmp")],
        "env": {},
        "description": "本地文件系统读写",
    },
}


# 安全加固：npx 子进程只透传白名单环境变量，严禁泄露宿主机全部环境变量
_ENV_ALLOWLIST = (
    "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP",
    "APPDATA", "LOCALAPPDATA", "ComSpec", "PATHEXT",
)


def _build_safe_env(config: dict) -> dict:
    """Build an environment dict for MCP subprocesses: allowlist + explicit config env only."""
    safe_env = {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}
    safe_env.update({k: v for k, v in config.get("env", {}).items() if v is not None})
    return safe_env


def _get_server_config(name: str) -> dict | None:
    """Get MCP server config, supporting env var overrides."""
    config = MCP_SERVERS.get(name)
    if not config:
        return None

    # Allow env overrides: MCP_SERVER_GITHUB_TOKEN, MCP_SERVER_GITHUB_COMMAND, etc.
    prefix = f"MCP_SERVER_{name.upper()}_"
    env_token = os.getenv(f"{prefix}TOKEN") or os.getenv(f"{prefix}KEY")
    if env_token:
        config = {**config}
        config["env"] = {**config.get("env", {}), "TOKEN": env_token, "KEY": env_token}

    return config


async def _call_mcp_tool(server_name: str, tool_name: str, arguments: dict) -> str:
    """Connect to an MCP server via stdio and call a tool."""
    config = _get_server_config(server_name)
    if not config:
        available = list(MCP_SERVERS.keys())
        return (
            f"Error: unknown MCP server '{server_name}'. "
            f"Available: {', '.join(available)}"
        )

    params = StdioServerParameters(
        command=config["command"],
        args=config["args"],
        env=_build_safe_env(config),
    )

    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # Call the tool
                result = await session.call_tool(tool_name, arguments)

                # Format response
                if result.isError:
                    content = result.content[0].text if result.content else "Unknown error"
                    return f"MCP error from {server_name}/{tool_name}: {content}"

                parts = []
                for item in result.content:
                    if hasattr(item, "text") and item.text:
                        parts.append(item.text)
                    elif hasattr(item, "data") and item.data:
                        parts.append(f"[binary data: {len(item.data)} bytes]")
                    elif isinstance(item, dict):
                        parts.append(item.get("text", str(item)))
                    else:
                        parts.append(str(item))

                return "\n".join(parts) if parts else "(empty result)"

    except Exception as e:
        logger.error(f"MCP call failed: server={server_name}, tool={tool_name}: {e}")
        return f"Error calling MCP server '{server_name}/{tool_name}': {type(e).__name__}: {e}"


async def _list_mcp_tools(server_name: str) -> str:
    """List available tools from an MCP server."""
    config = _get_server_config(server_name)
    if not config:
        return f"Unknown MCP server: {server_name}"

    params = StdioServerParameters(
        command=config["command"],
        args=config["args"],
        env=_build_safe_env(config),
    )

    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()

                if not result.tools:
                    return f"MCP server '{server_name}' has no tools."

                lines = [f"MCP Server: {server_name} ({len(result.tools)} tools)"]
                for tool in result.tools:
                    lines.append(f"\n  - {tool.name}")
                    if tool.description:
                        lines.append(f"    {tool.description[:150]}")
                return "\n".join(lines)

    except Exception as e:
        return f"Error listing MCP tools from '{server_name}': {e}"


@register_tool(
    name="mcp_call",
    description=(
        "通过 MCP 协议调用外部工具服务。"
        "支持的服务器: github(GitHub API), filesystem(文件读写)。"
        "使用方式: 指定 server + tool + arguments 来调用外部工具。"
        "如果不确定工具有哪些, 设置 tool='list_tools' 来发现。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "MCP 服务器名称",
                "enum": list(MCP_SERVERS.keys()),
            },
            "tool": {
                "type": "string",
                "description": "要调用的工具名。设为 'list_tools' 可列出该服务器的所有可用工具",
            },
            "arguments": {
                "type": "object",
                "description": "工具参数 (键值对)。留空 {} 则调用无参工具",
                "default": {},
            },
        },
        "required": ["server", "tool"],
    },
    tags=["mcp", "external"],
)
async def mcp_call(
    server: str,
    tool: str,
    arguments: dict | None = None,
    **_: Any,
) -> str:
    """LLM-facing tool: call any MCP server's tool or list available tools."""
    if tool == "list_tools":
        return await _list_mcp_tools(server)

    return await _call_mcp_tool(server, tool, arguments or {})
