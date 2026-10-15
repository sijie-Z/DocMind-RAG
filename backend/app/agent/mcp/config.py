"""MCP client 配置层 —— 从 ``mcp_servers.json`` 加载 server 定义。

配置形态（backend/mcp_servers.json，可用 ``DOCMIND_MCP_SERVERS_CONFIG`` 覆盖路径）::

    {
      "servers": {
        "<server 名>": {
          "transport": "stdio" | "http" | "sse",
          "enabled": false,                 // 缺省 false：配置存在 ≠ 主动接入
          "description": "…",
          "timeout": 30,                    // 单次请求秒数，缺省 30
          "command": "npx",                 // stdio 必填
          "args": ["-y", "…"],
          "env": {"KEY": "value"},
          "cwd": "…",
          "url": "https://…",               // http / sse 必填
          "headers": {"Authorization": "Bearer ${TOKEN}"}
        }
      }
    }

字符串值支持 ``${VAR}`` 环境变量展开（缺失留空并 warning）。顶层若没有
``servers`` 键、但整体形如 ``{name: {...}}``，按 server 映射解析（容错）。

本模块只做“配置 → 校验过的 dataclass”这一段，不发起任何连接；
非法条目逐条 warning 跳过，**任何情况都不抛出**——它是启动路径的一部分。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_FILENAME = "mcp_servers.json"
CONFIG_PATH_ENV = "DOCMIND_MCP_SERVERS_CONFIG"

# 传输别名 → 规范名。"streamable-http"/"streamable_http" 是 MCP 规范里的
# 正式名；"http" 是我们配置里的简写。sse 为旧规范（2024-11-05）的
# HTTP+SSE 传输，SDK 仍支持，作为兼容项保留。
_TRANSPORT_ALIASES = {
    "stdio": "stdio",
    "http": "http",
    "streamable-http": "http",
    "streamable_http": "http",
    "sse": "sse",
}

DEFAULT_TIMEOUT = 30.0
MAX_TIMEOUT = 600.0

_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass
class MCPServerConfig:
    """一个 MCP server 的完整定义（已校验、已展开环境变量）。"""

    name: str
    transport: str = "stdio"
    enabled: bool = False
    description: str = ""
    timeout: float = DEFAULT_TIMEOUT

    # transport == "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None

    # transport in ("http", "sse")
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


def default_config_path() -> Path:
    """backend/mcp_servers.json（相对本文件定位，不依赖 cwd）。"""
    # config.py → mcp/ → agent/ → app/ → backend/
    return Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME


def resolve_config_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    from_env = os.environ.get(CONFIG_PATH_ENV)
    if from_env:
        return Path(from_env)
    return default_config_path()


def _expand_env(value: str, *, where: str) -> str:
    """展开字符串里的 ``${VAR}``；var 缺失 → 空串 + warning（不抛）。"""

    def _sub(match: re.Match[str]) -> str:
        var = match.group(1)
        resolved = os.environ.get(var)
        if resolved is None:
            logger.warning("MCP 配置 %s 引用的环境变量 %s 未设置，按空串处理", where, var)
            return ""
        return resolved

    return _ENV_VAR_RE.sub(_sub, value)


def _expand_str(value: Any, *, where: str) -> str:
    return _expand_env(str(value), where=where)


def _expand_mapping(raw: Any, *, where: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        logger.warning("MCP 配置 %s 应为对象，实际 %s，忽略", where, type(raw).__name__)
        return {}
    return {str(k): _expand_str(v, where=f"{where}.{k}") for k, v in raw.items()}


def _coerce_timeout(raw: Any, where: str) -> float:
    if raw is None:
        return DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("MCP 配置 %s 的 timeout 非法（%r），用默认 %s", where, raw, DEFAULT_TIMEOUT)
        return DEFAULT_TIMEOUT
    if value <= 0:
        logger.warning("MCP 配置 %s 的 timeout <= 0（%r），用默认 %s", where, raw, DEFAULT_TIMEOUT)
        return DEFAULT_TIMEOUT
    return min(value, MAX_TIMEOUT)


def _parse_server(name: str, raw: Any) -> MCPServerConfig | None:
    """解析单个 server 配置；非法 → warning + None（跳过）。"""
    where = f"servers.{name}"
    if not isinstance(raw, dict):
        logger.warning("MCP 配置 %s 应为对象，实际 %s，跳过", where, type(raw).__name__)
        return None

    transport_raw = str(raw.get("transport", "stdio")).strip().lower()
    transport = _TRANSPORT_ALIASES.get(transport_raw)
    if transport is None:
        logger.warning("MCP 配置 %s 的 transport 非法（%r），跳过", where, transport_raw)
        return None

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        logger.warning("MCP 配置 %s 的 enabled 应为布尔，实际 %r，按 false 处理", where, enabled)
        enabled = False

    cfg = MCPServerConfig(
        name=name,
        transport=transport,
        enabled=enabled,
        description=str(raw.get("description") or ""),
        timeout=_coerce_timeout(raw.get("timeout"), where),
    )

    if transport == "stdio":
        cfg.command = _expand_str(raw.get("command") or "", where=f"{where}.command")
        args_raw = raw.get("args") or []
        if not isinstance(args_raw, list):
            logger.warning("MCP 配置 %s 的 args 应为数组，忽略", where)
            args_raw = []
        cfg.args = [_expand_str(a, where=f"{where}.args") for a in args_raw]
        cfg.env = _expand_mapping(raw.get("env"), where=f"{where}.env")
        cwd = raw.get("cwd")
        cfg.cwd = _expand_str(cwd, where=f"{where}.cwd") if cwd else None

        if not cfg.command.strip():
            logger.warning("MCP 配置 %s 缺少 command，stdio server 无法启动，跳过", where)
            return None
    else:  # http / sse
        cfg.url = _expand_str(raw.get("url") or "", where=f"{where}.url")
        cfg.headers = _expand_mapping(raw.get("headers"), where=f"{where}.headers")
        if not cfg.url.lower().startswith(("http://", "https://")):
            logger.warning("MCP 配置 %s 的 url 缺失或非法（%r），跳过", where, cfg.url)
            return None

    return cfg


def load_servers_config(path: str | Path | None = None) -> list[MCPServerConfig]:
    """加载并校验 server 配置列表。

    文件缺失 → info + 空列表；JSON 损坏 → warning + 空列表；
    单条非法 → warning + 跳过该条。**绝不抛出**。
    """
    resolved = resolve_config_path(path)
    if not resolved.is_file():
        logger.info("MCP 配置未提供（%s 不存在），MCP 动态工具不启用", resolved)
        return []

    try:
        raw_text = resolved.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("MCP 配置读取失败（%s）：%s", resolved, e)
        return []

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        logger.warning("MCP 配置 JSON 解析失败（%s）：%s", resolved, e)
        return []

    servers_raw: Any = None
    if isinstance(data, dict):
        if isinstance(data.get("servers"), dict):
            servers_raw = data["servers"]
        elif data and all(isinstance(v, dict) for v in data.values()):
            # 容错：顶层直接就是 {name: {...}}
            servers_raw = data
    if servers_raw is None:
        logger.warning("MCP 配置 %s 缺少 'servers' 对象，忽略", resolved)
        return []

    configs: list[MCPServerConfig] = []
    for name, raw in servers_raw.items():
        if not isinstance(name, str) or not name.strip():
            logger.warning("MCP 配置存在非法 server 名 %r，跳过", name)
            continue
        parsed = _parse_server(name.strip(), raw)
        if parsed is not None:
            configs.append(parsed)
    return configs
