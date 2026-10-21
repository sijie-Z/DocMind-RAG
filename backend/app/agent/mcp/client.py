"""MCP client 传输层 —— 常驻会话 + 崩溃检测 + 惰性重连。

设计（对 SDK 2.x 的 anyio 生命周期约束做了针对性处理）：

1. **owner-task 模式**：每个 server 连接由一个专属 asyncio Task 持有
   ``stdio_client``/``ClientSession`` 的上下文进出。anyio 的 cancel scope
   要求“同一任务进出”，若在 A 任务 enter、在 B 任务 exit 会抛
   ``RuntimeError: Attempted to exit cancel scope in a different task`` 甚至
   楔死进程（本轮 spike 实测）。把生命周期收敛到 owner task 后，任何调用方
   （lifespan、请求处理器）都只是跨任务调用 ``ClientSession`` 对象方法，
   关闭/重连永远发生在 owner task 内部。
2. **状态机**：``connecting → connected / dead → closed``。
   - 调用抛 ``MCPError`` 且 code=-32000（Connection closed，SDK 合成）→
     标记 dead；下次调用经 ``connect()`` 惰性重连。
   - code=-32001（SDK 合成超时）→ 超时，会话存活。
   - 其它 ``MCPError``（服务器真实响应）→ 服务器活着，不标记。
   - 其它异常（传输层断裂等）→ 保守标记 dead，靠惰性重连恢复。
3. **异常绝不逃逸**：owner task 内捕获一切（含启动失败），经 ready 事件
   传给 ``connect()`` 调用方；关闭路径吞掉所有清理异常，防止损坏的
   cleanup 把上层拖死。

错误码常量对齐 ``mcp.shared.jsonrpc_dispatcher``（CONNECTION_CLOSED /
REQUEST_TIMEOUT）；为防 SDK 内部模块改名，这里写死数值并保留注释。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, NoReturn

from mcp import StdioServerParameters, stdio_client
from mcp import types as mcp_types
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.shared.exceptions import MCPError

from app.agent.mcp.config import MCPServerConfig

logger = logging.getLogger(__name__)

# mcp.shared.jsonrpc_dispatcher.CONNECTION_CLOSED / REQUEST_TIMEOUT
_CODE_CONNECTION_CLOSED = -32000
_CODE_REQUEST_TIMEOUT = -32001

# 关闭/取消路径上等待 owner task 收敛的宽限（秒）
_CLOSE_GRACE = 5.0
_CLOSE_FORCE_WAIT = 3.0

# tools/list 分页防护
_MAX_TOOL_PAGES = 20


class MCPClientError(Exception):
    """MCP client 层错误基类。"""


class MCPConnectError(MCPClientError):
    """连接/初始化（含握手超时）失败。"""


class MCPNotConnectedError(MCPClientError):
    """连接不可用（未连接 / 已死且未重连）。"""


class MCPTimeoutError(MCPClientError):
    """单次请求超时（会话本身仍可用）。"""


class MCPTransportError(MCPClientError):
    """传输层断裂/连接关闭——已将连接标记为 dead。"""


class MCPProtocolError(MCPClientError):
    """响应不符合 MCP 协议结构。"""


class MCPRemoteError(MCPClientError):
    """服务器以 JSON-RPC error 响应（服务器活着）。"""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


@dataclass
class MCPToolDescriptor:
    """tools/list 单条工具描述（已归一化为纯 Python 结构）。"""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None


@dataclass
class MCPCallResult:
    """tools/call 结果的协议级表示（未做业务映射）。"""

    is_error: bool
    content: list[dict[str, Any]] = field(default_factory=list)
    structured: Any = None


class MCPServerConnection:
    """单个 MCP server 的常驻连接。

    任务安全说明：``connect`` / ``close`` 由 ``self._lock`` 串行化；
    ``call_tool`` / ``list_tools`` / ``ping`` 可并发调用（SDK 会话本身支持
    并发请求，跨任务使用已实测）。
    """

    def __init__(
        self,
        config: MCPServerConfig,
        http_client_factory: Callable[[], Any] | None = None,
    ):
        self.config = config
        self._http_client_factory = http_client_factory

        self._lock = asyncio.Lock()
        self._session: ClientSession | None = None
        self._owner: asyncio.Task | None = None
        self._stop_evt = asyncio.Event()
        self._ready_evt = asyncio.Event()
        self._startup_error: BaseException | None = None
        self._state = "init"

        # 由 manager 注入：收到 notifications/tools/list_changed 时回调（同步、快速返回）
        self.on_tools_changed: Callable[[], None] | None = None

    # ── 状态 ──────────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state == "connected" and self._session is not None

    # ── 生命周期 ──────────────────────────────────────────────────────────

    async def connect(self, timeout: float = 10.0) -> None:
        """建立连接并完成 initialize 握手；失败抛 ``MCPConnectError``。"""
        async with self._lock:
            if self.is_connected:
                return
            # 清掉可能残留的旧 owner（例如崩溃后未回收）
            await self._close_locked(grace=1.0)

            self._startup_error = None
            self._stop_evt = asyncio.Event()
            self._ready_evt = asyncio.Event()
            self._state = "connecting"
            self._owner = asyncio.create_task(
                self._owner_main(), name=f"mcp-owner-{self.config.name}"
            )

            try:
                await asyncio.wait_for(self._ready_evt.wait(), timeout=max(0.1, timeout))
            except TimeoutError as e:
                self._state = "dead"
                await self._abort_owner()
                raise MCPConnectError(
                    f"MCP server '{self.config.name}' 连接超时（{timeout}s 内未完成 initialize）"
                ) from e

            err = self._startup_error
            self._startup_error = None
            if err is not None or not self.is_connected:
                self._state = "dead"
                owner = self._owner
                if owner is not None and owner.done():
                    self._owner = None
                detail = (
                    f"{type(err).__name__}: {err}"
                    if err is not None
                    else "connection ended during startup"
                )
                raise MCPConnectError(
                    f"MCP server '{self.config.name}' 初始化失败: {detail}"
                ) from (err if isinstance(err, BaseException) else None)

    async def _owner_main(self) -> None:
        """owner task：进入/退出 SDK 上下文；只在自身任务内操作。"""
        stop_evt = self._stop_evt
        try:
            async with self._open_transport() as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(
                    read, write, message_handler=self._handle_incoming
                ) as session:
                    await session.initialize()
                    self._session = session
                    self._state = "connected"
                    self._ready_evt.set()
                    await stop_evt.wait()
        except asyncio.CancelledError:
            self._state = "closed"
            raise
        except BaseException as e:  # noqa: BLE001 —— owner 生命周期任务：任何异常都收敛为状态
            self._startup_error = e
            self._state = "dead"
            logger.warning(
                "MCP server '%s' 连接异常退出: %s: %s", self.config.name, type(e).__name__, e
            )
        finally:
            self._session = None
            self._ready_evt.set()
            if self._state == "connected":
                self._state = "closed"

    def _open_transport(self):
        """按配置返回 SDK 传输上下文管理器（进入/退出都将在 owner task 内）。"""
        cfg = self.config
        if cfg.transport == "stdio":
            params = StdioServerParameters(
                command=cfg.command,
                args=list(cfg.args),
                env=dict(cfg.env) or None,
                cwd=cfg.cwd,
                # MCP 规范要求 UTF-8；但现实里 Windows 上的 server 可能按本地编码
                # （GBK 等）写 stdout。strict 会让解码失败直接打断读取流、整个会话
                # 以“无响应超时”的形态暴死（本机实测）；replace 则最多把个别字符
                # 变成 �，协议帧本身（ASCII 结构字符）不受影响。
                encoding_error_handler="replace",
            )
            return stdio_client(params)

        if cfg.transport == "sse":
            return sse_client(cfg.url, headers=dict(cfg.headers) or None)

        # streamable http（现代规范）
        if self._http_client_factory is not None:
            client = self._http_client_factory()
        else:
            client = create_mcp_http_client(headers=dict(cfg.headers) or None)
        return _HTTPTransport(client, cfg.url)

    async def close(self, grace: float = _CLOSE_GRACE) -> None:
        """停止 owner task 并释放资源；幂等，绝不抛出。"""
        async with self._lock:
            await self._close_locked(grace=grace)

    async def _close_locked(self, grace: float) -> None:
        owner = self._owner
        if owner is not None and not owner.done():
            self._stop_evt.set()  # 正常路径：owner 从 stop.wait() 醒来，在同一任务内退出上下文
            try:
                await asyncio.wait_for(asyncio.shield(owner), timeout=max(0.05, grace))
            except TimeoutError:
                # owner 卡在 enter/initialize 之类的位置，stop 信号无效 → 取消（仍在 owner 任务内展开）
                logger.warning("MCP server '%s' 关闭超时，取消 owner task", self.config.name)
                owner.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(owner), timeout=_CLOSE_FORCE_WAIT)
                except BaseException:  # noqa: BLE001 —— 取消/收尾期异常一律吞掉
                    pass
            except BaseException:  # noqa: BLE001 —— owner 带着异常结束（含 CancelledError）
                pass
        self._owner = None
        self._session = None
        self._startup_error = None
        self._state = "closed"

    async def _abort_owner(self) -> None:
        """连接超时路径：owner 大概率卡在 initialize，直接取消并等待收敛。"""
        owner = self._owner
        if owner is not None and not owner.done():
            owner.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(owner), timeout=_CLOSE_FORCE_WAIT)
            except BaseException:  # noqa: BLE001
                pass
        self._owner = None
        self._session = None

    # ── 请求 ─────────────────────────────────────────────────────────────

    def _require_session(self) -> ClientSession:
        if not self.is_connected or self._session is None:
            raise MCPNotConnectedError(
                f"MCP server '{self.config.name}' 未连接（state={self._state}）"
            )
        return self._session

    def _classify_mcp_error(self, e: MCPError) -> NoReturn:
        """把 SDK 的 MCPError 分类为我们的错误体系；必要时标记 dead。总是抛出。"""
        code = getattr(e, "code", None)
        message = getattr(e, "message", None) or str(e)
        if code == _CODE_CONNECTION_CLOSED:
            self._state = "dead"
            raise MCPTransportError(
                f"MCP server '{self.config.name}' 连接已关闭（服务器进程退出或会话失效）"
            ) from e
        if code == _CODE_REQUEST_TIMEOUT:
            raise MCPTimeoutError(f"MCP server '{self.config.name}' 请求超时") from e
        raise MCPRemoteError(int(code) if code is not None else 0, str(message))

    async def list_tools(self, timeout: float | None = None) -> list[MCPToolDescriptor]:
        """拉取工具列表（自动翻页；最多 _MAX_TOOL_PAGES 页防死循环）。"""
        session = self._require_session()
        effective = float(timeout or self.config.timeout)
        descriptors: list[MCPToolDescriptor] = []
        cursor: str | None = None
        try:
            for _ in range(_MAX_TOOL_PAGES):
                params = mcp_types.PaginatedRequestParams(cursor=cursor) if cursor else None
                raw = await asyncio.wait_for(session.list_tools(params=params), timeout=effective)
                for tool in raw.tools:
                    descriptors.append(self._to_descriptor(tool))
                cursor = getattr(raw, "next_cursor", None)
                if not cursor:
                    break
            return descriptors
        except TimeoutError as e:
            raise MCPTimeoutError(f"MCP server '{self.config.name}' tools/list 超时") from e
        except MCPError as e:
            self._classify_mcp_error(e)
        except MCPClientError:
            raise
        except Exception as e:  # noqa: BLE001
            self._state = "dead"
            raise MCPTransportError(
                f"MCP server '{self.config.name}' tools/list 失败: {type(e).__name__}: {e}"
            ) from e

    @staticmethod
    def _to_descriptor(tool: Any) -> MCPToolDescriptor:
        schema = getattr(tool, "input_schema", None)
        output_schema = getattr(tool, "output_schema", None)
        return MCPToolDescriptor(
            name=str(tool.name),
            description=str(
                getattr(tool, "description", None) or getattr(tool, "title", None) or ""
            ),
            input_schema=dict(schema) if isinstance(schema, dict) else {},
            output_schema=dict(output_schema) if isinstance(output_schema, dict) else None,
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> MCPCallResult:
        """调用远端工具；协议级错误按类型抛出（见模块 docstring）。"""
        session = self._require_session()
        effective = float(timeout or self.config.timeout)
        try:
            raw = await asyncio.wait_for(
                session.call_tool(name, arguments=arguments or {}), timeout=effective
            )
        except TimeoutError as e:
            # 取消已由 SDK 转为对服务器的 courtesy cancel；会话本身可继续使用（已实测）
            raise MCPTimeoutError(
                f"MCP server '{self.config.name}' 工具 '{name}' 调用超时（{effective}s）"
            ) from e
        except MCPError as e:
            self._classify_mcp_error(e)
        except MCPClientError:
            raise
        except Exception as e:  # noqa: BLE001
            self._state = "dead"
            raise MCPTransportError(
                f"MCP server '{self.config.name}' 工具 '{name}' 调用失败: {type(e).__name__}: {e}"
            ) from e

        return self._normalize_call_result(raw)

    def _normalize_call_result(self, raw: Any) -> MCPCallResult:
        if not hasattr(raw, "content"):
            raise MCPProtocolError(
                f"MCP server '{self.config.name}' 返回了非工具结果类型: {type(raw).__name__}"
            )
        content: list[dict[str, Any]] = []
        for block in raw.content or []:
            if hasattr(block, "model_dump"):
                dumped = block.model_dump(by_alias=True, exclude_none=True)
                content.append(
                    dumped if isinstance(dumped, dict) else {"type": "unknown", "raw": str(dumped)}
                )
            elif isinstance(block, dict):
                content.append(block)
            else:
                content.append({"type": "unknown", "raw": str(block)})
        return MCPCallResult(
            is_error=bool(getattr(raw, "is_error", False)),
            content=content,
            structured=getattr(raw, "structured_content", None),
        )

    async def ping(self, timeout: float = 5.0) -> bool:
        """轻量健康检查。返回 False 表示连接不可用（含被标记 dead）。"""
        if not self.is_connected or self._session is None:
            return False
        try:
            await asyncio.wait_for(self._session.send_ping(), timeout=timeout)
            return True
        except TimeoutError:
            return False
        except MCPError as e:
            try:
                self._classify_mcp_error(e)
            except MCPClientError:
                return False
            return False
        except Exception:  # noqa: BLE001
            self._state = "dead"
            return False

    # ── 通知 ─────────────────────────────────────────────────────────────

    async def _handle_incoming(self, message: Any) -> None:
        """SDK message_handler：接收服务器通知与传输层异常。

        必须快速返回（在 dispatcher 读取循环里被 await）。这里只做同步回调，
        重活（重新 list_tools / 重注册）由 manager 调度到独立 task。
        """
        if isinstance(message, Exception):
            logger.debug("MCP server '%s' 传输层异常通知: %s", self.config.name, message)
            return
        if isinstance(message, mcp_types.ToolListChangedNotification):
            cb = self.on_tools_changed
            if cb is None:
                return
            try:
                cb()
            except Exception as e:  # noqa: BLE001 —— 回调异常绝不能打进 dispatcher
                logger.warning(
                    "MCP server '%s' tools/list_changed 回调失败: %s", self.config.name, e
                )


class _HTTPTransport:
    """把「自建 httpx2 client + streamable_http_client + 关闭」包成一个上下文。"""

    def __init__(self, client: Any, url: str):
        self._client = client
        self._url = url
        self._cm = None

    async def __aenter__(self):
        await self._client.__aenter__()
        self._cm = streamable_http_client(
            self._url, http_client=self._client, terminate_on_close=True
        )
        return await self._cm.__aenter__()

    async def __aexit__(self, *exc_info):
        try:
            if self._cm is not None:
                await self._cm.__aexit__(*exc_info)
        finally:
            await self._client.__aexit__(None, None, None)
