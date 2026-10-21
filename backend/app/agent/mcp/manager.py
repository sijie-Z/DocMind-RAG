"""MCP manager —— 配置驱动的 server 接入、工具注册表集成与会话生命周期。

职责链：
    mcp_servers.json → MCPServerConfig[] → MCPServerConnection（常驻会话）
    → tools/list → 命名空间化注册（``mcp__<server>__<tool>``）进 ToolRegistry
    → tools/call → ToolResult（如实映射，见 mapping.py）

健壮性原则（与 lifespan 约定一致）：
- ``initialize()`` 绝不抛出：单 server 失败 → warning + 跳过，不阻塞主启动；
- 服务器起不来 → 其工具不注册；调用时服务器已死 → 结构化失败 + 下次调用惰性重连；
- 命名冲突（重复 '' 合并键 / 与既有工具撞名）→ 首个注册者保留，后来者 warning 跳过；
- list_changed 通知 → 防抖后重拉 tools/list，增量增删注册。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any

from app.agent.mcp.client import (
    MCPCallResult,
    MCPClientError,
    MCPConnectError,
    MCPServerConnection,
    MCPToolDescriptor,
)
from app.agent.mcp.config import MCPServerConfig, load_servers_config
from app.agent.mcp.mapping import call_result_to_tool_result, error_to_tool_result
from app.agent.registry import ToolResult, tool_registry
from app.agent.schema_validation import validate_against_schema

logger = logging.getLogger(__name__)

TOOL_PREFIX = "mcp__"
_NAME_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_-]")
_MAX_TOOL_NAME_LEN = 64  # OpenAI function-calling 名称上限；超出用哈希尾缀收敛

# 启动接入的连接超时上限（配置 timeout 是“单次请求”概念，不能拖垮启动）
_CONNECT_TIMEOUT_MAX = 10.0

# 状态视图里错误原文的收敛上限（与 ToolError.raw 截断口径一致）
_ERROR_MAX_LEN = 500


def _bounded_error(exc: BaseException) -> str:
    """错误原文 → 有界的 ``类型: 消息`` 单行文本（进状态视图，不另造格式）。"""
    return f"{type(exc).__name__}: {exc}"[:_ERROR_MAX_LEN]


def sanitize_segment(segment: str) -> str:
    return _NAME_UNSAFE_RE.sub("_", segment)


def namespace_tool_name(server: str, tool: str) -> str:
    """``mcp__<server>__<tool>``；非法字符归一为 ``_``，超长用 sha1 尾缀收敛。"""
    final = f"{TOOL_PREFIX}{sanitize_segment(server)}__{sanitize_segment(tool)}"
    if len(final) > _MAX_TOOL_NAME_LEN:
        # 仅作名称收敛尾缀（去重标识），非密码学用途——显式标注以免被静态
        # 扫描当作弱哈希（与 app/rag/retriever.py 的 chunk_id 哈希同款写法）。
        digest = hashlib.sha1(final.encode("utf-8"), usedforsecurity=False).hexdigest()[:6]
        head = final[: _MAX_TOOL_NAME_LEN - len(digest) - 1]
        final = f"{head}_{digest}"
    return final


class MCPManager:
    """MCP server 集合的管理器（单例 ``mcp_manager``；测试可自由实例化）。"""

    def __init__(
        self,
        configs: list[MCPServerConfig] | None = None,
        *,
        config_path: str | None = None,
        http_client_factory: Any = None,
    ):
        # None = 尚未加载（initialize 时从 config_path / 默认路径加载）；[] = 显式“无配置”
        self._configs: list[MCPServerConfig] | None = list(configs) if configs is not None else None
        self._config_path = config_path
        self._http_client_factory = http_client_factory
        self._connections: dict[str, MCPServerConnection] = {}
        # server → {final_tool_name: 原始工具名}
        self._registered: dict[str, dict[str, str]] = {}
        # server → 最近一次接入/刷新失败原文（连接成功即清除；只进状态视图）
        self._last_errors: dict[str, str] = {}
        self._init_lock = asyncio.Lock()
        self._refresh_locks: dict[str, asyncio.Lock] = {}
        self._refresh_tasks: set[asyncio.Task] = set()
        self._started = False

    # ── 启动 / 关闭 ───────────────────────────────────────────────────────

    async def initialize(self) -> dict[str, Any]:
        """连接全部 enabled server 并注册其工具；绝不抛出。返回状态摘要。"""
        async with self._init_lock:
            if self._started:
                return self.get_status()

            configs = self._configs
            if configs is None:
                configs = load_servers_config(self._config_path)
                self._configs = configs

            enabled = [c for c in configs if c.enabled]
            if not enabled:
                logger.info("MCP：无 enabled server（配置 %d 条），动态工具不接入", len(configs))
                self._started = True
                return self.get_status()

            results = await asyncio.gather(
                *(self._connect_and_register(c) for c in enabled), return_exceptions=True
            )
            for cfg, result in zip(enabled, results, strict=False):
                if isinstance(result, BaseException):  # gather 兜底（_connect_and_register 已自兜）
                    logger.warning(
                        "MCP server '%s' 接入异常: %s: %s", cfg.name, type(result).__name__, result
                    )

            self._started = True
            status = self.get_status()
            logger.info(
                "MCP：%d 个 enabled server，%d 已连接，注册动态工具 %d 个",
                len(enabled),
                len(self._connections),
                status["registered_tools"],
            )
            return status

    async def _connect_and_register(self, cfg: MCPServerConfig) -> None:
        conn = MCPServerConnection(cfg, self._http_client_factory)
        conn.on_tools_changed = lambda: self._schedule_refresh(cfg.name)
        try:
            await conn.connect(timeout=min(cfg.timeout, _CONNECT_TIMEOUT_MAX))
            tools = await conn.list_tools()
        except asyncio.CancelledError:
            await conn.close(grace=1.0)
            raise
        except Exception as e:  # noqa: BLE001 —— 单 server 失败绝不外溢
            logger.warning(
                "MCP server '%s' 接入失败，跳过（不阻塞启动）: %s: %s",
                cfg.name,
                type(e).__name__,
                e,
            )
            self._last_errors[cfg.name] = _bounded_error(e)
            await conn.close(grace=1.0)
            return

        self._connections[cfg.name] = conn
        self._last_errors.pop(cfg.name, None)
        count = self._register_tools(cfg.name, conn, tools)
        logger.info("MCP server '%s' 已连接（%s），注册工具 %d 个", cfg.name, cfg.transport, count)

    async def shutdown(self) -> None:
        """摘除全部动态注册并关闭连接；幂等，绝不抛出。"""
        async with self._init_lock:
            for task in list(self._refresh_tasks):
                task.cancel()
            for task in list(self._refresh_tasks):
                try:
                    await task
                except BaseException:  # noqa: BLE001
                    pass
            self._refresh_tasks.clear()

            for own in self._registered.values():
                for final in list(own):
                    tool_registry.unregister(final)
                own.clear()
            self._registered.clear()

            conns = list(self._connections.values())
            self._connections.clear()
            self._last_errors.clear()
            if conns:
                await asyncio.gather(*(c.close() for c in conns), return_exceptions=True)
            self._started = False
            logger.info("MCP：动态工具全部下线，连接已关闭")

    # ── 注册 / 注销 ───────────────────────────────────────────────────────

    def _tool_owner(self, final_name: str, exclude_server: str) -> str | None:
        """找出已占用 final_name 的其它 server 名（没有则 None）。"""
        for server, own in self._registered.items():
            if server == exclude_server:
                continue
            if final_name in own:
                return server
        return None

    def _register_tools(
        self, server: str, conn: MCPServerConnection, tools: list[MCPToolDescriptor]
    ) -> int:
        """把一批工具描述注册进全局注册表；返回本次在场的工具总数。

        冲突规则：同一 (server, tool) 重复注册（刷新）→ 覆盖更新；
        被其它 server / 既有静态工具占用 → warning + 跳过（首个注册者保留）。
        """
        own = self._registered.setdefault(server, {})
        cfg = conn.config
        for desc in tools:
            tool_name = desc.name.strip()
            if not tool_name:
                logger.warning("MCP server '%s' 返回空工具名条目，跳过", server)
                continue
            final = namespace_tool_name(server, tool_name)

            owner = self._tool_owner(final, exclude_server=server)
            if owner is not None:
                logger.warning(
                    "MCP 工具命名冲突：'%s'（%s/%s）已被 server '%s' 注册，跳过",
                    final,
                    server,
                    tool_name,
                    owner,
                )
                continue
            if final not in own and tool_registry.get(final) is not None:
                logger.warning(
                    "MCP 工具命名冲突：'%s'（%s/%s）与既有注册表条目同名，跳过",
                    final,
                    server,
                    tool_name,
                )
                continue

            tool_registry.register(
                name=final,
                description=f"[MCP:{server}] {desc.description or tool_name}",
                parameters=desc.input_schema or {"type": "object", "properties": {}},
                handler=self._make_handler(conn, server, desc),
                tags=["mcp", "external", f"mcp:{server}"],
                # 注册表 timeout 是兜底闸：client 内部先按 cfg.timeout 触发，
                # 这里 +5s 避免两者同时开火导致分类不确定
                timeout=cfg.timeout + 5.0,
                category="mcp",
                semantic={
                    # 外部工具语义未知 → 保守声明：不并行、不自动重试
                    "type": "mutating",
                    "idempotent": False,
                    "retry_safe": False,
                    "consistency": "eventual",
                    "parallel_safe": False,
                },
            )
            own[final] = tool_name
        return len(own)

    def _make_handler(self, conn: MCPServerConnection, server: str, desc: MCPToolDescriptor):
        schema = desc.input_schema or {}
        cfg = conn.config

        async def mcp_tool_handler(**kwargs: Any) -> ToolResult:
            args = dict(kwargs)
            if schema:
                ok, reason = validate_against_schema(args, schema)
                if not ok:
                    return ToolResult.fail(
                        code="validation_error",
                        message=f"参数不符合 MCP 工具 {server}/{desc.name} 的 inputSchema: {reason}",
                    )
            if not conn.is_connected:
                try:
                    await conn.connect(timeout=min(cfg.timeout, _CONNECT_TIMEOUT_MAX))
                except MCPConnectError as e:
                    return error_to_tool_result(e, server, desc.name)
            try:
                result: MCPCallResult = await conn.call_tool(desc.name, args, timeout=cfg.timeout)
            except MCPClientError as e:
                return error_to_tool_result(e, server, desc.name)
            return call_result_to_tool_result(result, server, desc.name)

        mcp_tool_handler.__name__ = f"mcp_{sanitize_segment(server)}_{sanitize_segment(desc.name)}"
        return mcp_tool_handler

    # ── list_changed 刷新 ─────────────────────────────────────────────────

    def _schedule_refresh(self, server: str) -> None:
        """通知回调（在会话 dispatcher 里被同步调用）：只调度，不做重活。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._refresh_server(server), name=f"mcp-refresh-{server}")
        self._refresh_tasks.add(task)
        task.add_done_callback(self._refresh_tasks.discard)

    async def _refresh_server(self, server: str) -> None:
        lock = self._refresh_locks.setdefault(server, asyncio.Lock())
        async with lock:
            await asyncio.sleep(0.05)  # 防抖：短时间多次通知合并为一次重拉
            conn = self._connections.get(server)
            if conn is None or not conn.is_connected:
                return
            try:
                tools = await conn.list_tools()
            except MCPClientError as e:
                logger.warning("MCP server '%s' list_changed 刷新失败，保留现有注册: %s", server, e)
                self._last_errors[server] = _bounded_error(e)
                return
            self._last_errors.pop(server, None)

            new_finals = {
                namespace_tool_name(server, d.name.strip()) for d in tools if d.name.strip()
            }
            own = self._registered.setdefault(server, {})
            removed = 0
            for final in list(own):
                if final not in new_finals:
                    tool_registry.unregister(final)
                    del own[final]
                    removed += 1
            total = self._register_tools(server, conn, tools)
            logger.info(
                "MCP server '%s' tools/list_changed：现注册 %d 个（移除 %d）",
                server,
                total,
                removed,
            )

    # ── 状态 ─────────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """只读状态视图（管理面/日志共用；不发起任何连接尝试）。

        每 server 字段：name / enabled / transport / description / state /
        tools（已注册计数）/ tool_names（原始工具名，server 侧命名）/
        last_error（最近一次接入或刷新失败原文；成功后清除）。
        """
        servers: list[dict[str, Any]] = []
        for cfg in self._configs or []:
            conn = self._connections.get(cfg.name)
            if conn is not None:
                state = conn.state
            else:
                state = "disabled" if not cfg.enabled else "not_connected"
            own = self._registered.get(cfg.name, {})
            servers.append(
                {
                    "name": cfg.name,
                    "enabled": cfg.enabled,
                    "transport": cfg.transport,
                    "description": cfg.description,
                    "state": state,
                    "tools": len(own),
                    "tool_names": sorted(own.values()),
                    "last_error": self._last_errors.get(cfg.name),
                }
            )
        return {
            "started": self._started,
            "servers": servers,
            "registered_tools": sum(len(m) for m in self._registered.values()),
        }


# 全局单例（lifespan 接线用）
mcp_manager = MCPManager()
