"""stdio NDJSON 循环（issue #480 §5.5，issue #493 运行时硬化）。

约束：
- stdout **只**允许协议帧：真实 stdout 交给 :class:`ProtocolWriter`，
  ``sys.stdout`` 指向 stderr（第三方 ``print`` 不会污染协议）；
- 进入循环前严格初始化 Redis/DB：不可用 → stderr 说明 + 退出码 2，不静默降级；
- 单行上限 ``RPC_LINE_MAX_BYTES``（1MB），超限行丢弃并回 -32600；
- stdin EOF / SIGTERM / SIGINT / ``shutdown`` 都走同一条优雅收尾：
  取消运行中任务 → 拒绝 pending 服务端请求 → 关闭 DB/Redis → 回收 stdin 读线程。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import threading
from typing import Any, TextIO

from app.core.config import settings
from app.core.error_mapping import JSONRPC_INVALID_REQUEST, JSONRPC_PARSE_ERROR
from app.core.log_safety import log_safe
from app.core.stdio import (
    configure_stdio_stdout,
    get_protocol_stdout,
    migrate_stdout_handlers,
)
from app.rpc.dispatcher import RPCService

logger = logging.getLogger(__name__)


class ProtocolWriter:
    """线程/异步安全的 NDJSON 写端（唯一允许写真实 stdout 的路径）。"""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._lock = asyncio.Lock()

    async def send(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with self._lock:
            self._stream.write(line)
            self._stream.flush()

    def flush(self) -> None:
        """同步 flush 真实 stdout（收尾兜底；send 已逐帧 flush）。"""
        try:
            self._stream.flush()
        except Exception:  # noqa: BLE001 - 收尾 flush 失败不影响退出码
            logger.warning("Protocol stdout flush failed", exc_info=True)


async def initialize_stdio_dependencies() -> None:
    """stdio 运行时依赖：Redis 严格初始化 + 数据库初始化（issue #493）。

    cancel 旗与 prompt 幂等都依赖 Redis；不可用时必须 fail-fast（由
    :func:`run_stdio` 转成 stderr 说明 + 退出码 2），不得静默降级为"无 Redis 继续跑"。
    """
    from app.core.database import init_db
    from app.core.redis import init_redis_strict

    await init_redis_strict()
    await init_db()


def _release_stdin_reader(reader: threading.Thread) -> None:
    """尽力让 stdin 读线程退出（issue #493：收尾不留读 stdin 的线程）。

    关闭 stdin 的底层 fd 可让 Windows 上阻塞在 ``readline`` 的线程立即失败；
    POSIX 的 close 不保证唤醒已阻塞的 read，此时线程保持 daemon（进程退出由
    解释器回收）。join 带上限，收尾绝不被卡死。仅当 ``sys.stdin`` 仍是进程
    原始 stdin 时才动 fd，避免误伤嵌入了 ``run_stdio`` 的宿主进程。
    """
    if sys.stdin is sys.__stdin__:
        try:
            os.close(sys.stdin.fileno())
        except (OSError, ValueError, AttributeError):
            pass
    reader.join(timeout=0.5)
    if reader.is_alive():
        logger.debug("stdin reader still blocked at exit; daemon thread reclaimed on exit")


def _stderr_only_stdout(requested_stdout: TextIO) -> None:
    """把 ``sys.stdout`` 重定向到 stderr，并迁移已挂在真实 stdout 的日志 handler。

    薄封装 :func:`app.core.stdio.configure_stdio_stdout`（issue #493）：重定向与
    handler 迁移逻辑集中在 import-safe 的 ``core.stdio``（root 与 sqlalchemy /
    uvicorn / httpx 等具名 logger 一并迁移）。``requested_stdout`` 是调用方保存的
    协议写端；正常情形下它就是当前 ``sys.stdout``，二次迁移为 no-op。
    """
    configure_stdio_stdout()
    migrate_stdout_handlers(requested_stdout)


def _parse_error() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": JSONRPC_PARSE_ERROR, "message": "Parse error"},
    }


def _line_too_long() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": JSONRPC_INVALID_REQUEST, "message": "请求行超过 1MB 上限"},
    }


async def run_stdio(
    *,
    organization_id: int,
    user_id: int,
    is_superuser: bool = False,
    stdout: TextIO | None = None,
) -> int:
    """运行 stdio JSON-RPC 循环，返回进程退出码。

    进入循环前严格初始化 Redis/DB（issue #493）：失败打印 stderr 并返回 2，
    绝不静默降级。``stdout`` 缺省时使用 ``configure_stdio_stdout()`` 保存的
    协议写端（入口已重定向的情形）。
    """
    real_stdout = stdout or get_protocol_stdout() or sys.stdout
    _stderr_only_stdout(real_stdout)
    writer = ProtocolWriter(real_stdout)

    try:
        await initialize_stdio_dependencies()
    except Exception as exc:  # noqa: BLE001 - 依赖初始化失败必须如实退出 2
        logger.error("stdio 运行时依赖初始化失败: %s", log_safe(exc))
        print(f"sdk 启动依赖初始化失败：{log_safe(exc)}", file=sys.stderr)
        await _close_dependencies()
        return 2

    service = RPCService(
        organization_id=organization_id,
        user_id=user_id,
        is_superuser=is_superuser,
    )
    dispatcher = service.build_dispatcher(send=writer.send)

    loop = asyncio.get_running_loop()
    line_queue: asyncio.Queue[bytes] = asyncio.Queue()
    stop = asyncio.Event()

    def _pump_stdin() -> None:
        """后台线程逐行读 stdin；EOF 推空字节作为哨兵。"""
        max_bytes = int(settings.RPC_LINE_MAX_BYTES)
        while True:
            # 限制单次读入大小，超长行不整行进内存（多读 1 字节用于判定超限）。
            try:
                raw = sys.stdin.buffer.readline(max_bytes + 1)
            except (OSError, ValueError):
                # 收尾时 fd 被关闭 / stdin 不可读：按 EOF 处理。
                raw = b""
            if raw is None:
                raw = b""
            try:
                loop.call_soon_threadsafe(line_queue.put_nowait, raw)
            except RuntimeError:
                # 事件循环已关闭（收尾）：静默退出，绝不在线程里抛栈。
                return
            if not raw:
                return

    stdin_thread = threading.Thread(target=_pump_stdin, name="rpc-stdin", daemon=True)
    stdin_thread.start()

    def _request_stop(*_args: Any) -> None:
        loop.call_soon_threadsafe(stop.set)

    installed: list[signal.Signals] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop)
            installed.append(sig)
        except (NotImplementedError, RuntimeError, ValueError):
            # Windows 不支持 loop.add_signal_handler；退回 signal.signal。
            try:
                signal.signal(sig, _request_stop)
            except (ValueError, OSError):  # pragma: no cover - 非主线程/受限环境
                pass

    max_line = int(settings.RPC_LINE_MAX_BYTES)
    try:
        while not stop.is_set() and not service.shutdown_requested:
            try:
                raw = await asyncio.wait_for(line_queue.get(), timeout=0.2)
            except TimeoutError:
                continue
            if raw == b"":
                break
            if len(raw) > max_line:
                logger.warning("RPC line exceeds %s bytes, dropped", max_line)
                await writer.send(_line_too_long())
                continue
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                await writer.send(_parse_error())
                continue
            dispatcher.dispatch(message)

        # shutdown / 信号：等在途请求写出响应，再取消运行中任务。
        await dispatcher.wait_idle(timeout=10.0)
    finally:
        for sig in installed:
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
        try:
            await service.on_disconnect()
        finally:
            await dispatcher.close()
            writer.flush()
            await _close_dependencies()
            _release_stdin_reader(stdin_thread)
    return 0


async def _close_dependencies() -> None:
    """关闭 Redis 与数据库连接池（失败只记日志，不影响退出码）。"""
    try:
        from app.core.redis import close_redis

        await close_redis()
    except Exception:  # noqa: BLE001
        logger.warning("close_redis failed", exc_info=True)
    try:
        from app.core.database import engine

        await engine.dispose()
    except Exception:  # noqa: BLE001
        logger.warning("Database engine dispose failed", exc_info=True)
