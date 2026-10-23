"""Persistent terminal session manager (issue #477).

与后端无关的终端生命周期管理：配额（每用户/每组织）、空闲/寿命回收、
ring buffer（丢最旧字节并累计 ``dropped_total``）、单 writer 约束。

设计要点：

- **一个终端一个 session**，真实 I/O 由 :class:`TerminalBackend` 协议抽象：
  生产实现是 ``docker_backend.DockerTerminalBackend``（exec PTY），单测用
  :class:`FakeBackend`。
- **ring buffer 是全链路唯一输出缓冲**：后台 reader 任务把后端输出写进
  定长字节环；WS 重连补发（``snapshot``/``snapshot_since``）与
  ``terminal_read`` 工具（``read_pending``）各用独立游标，互不消费对方数据。
- **同一终端同一时刻只允许一个 writer**：``send`` 在已有写入者时立即抛
  :class:`TerminalBusy`，不排队——排队会让两次输入的先后顺序不可控。
- **时间**：生命周期判定用单调时钟（``monotonic``），API 展示用墙上时钟；
  测试可显式传入 ``now`` 驱动 ``sweep``，不靠 sleep。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Literal, Protocol

logger = logging.getLogger(__name__)

TerminalStatus = Literal["running", "exited", "error", "closed"]

# 生产后端读取片大小：reader 任务每次最多向后端要 64KiB，写入 ring buffer。
_READ_CHUNK_BYTES = 65536


# ── 结构化错误（API / 工具层按 ``code`` 映射状态码）────────────────────────


class TerminalError(RuntimeError):
    """终端操作失败。``code`` 是稳定的机器可读错误码。"""

    code = "terminal_error"

    def __init__(self, message: str = "终端操作失败"):
        super().__init__(message)


class TerminalDisabled(TerminalError):
    """功能未启用（TERMINAL_ENABLED=false / 非 docker 沙箱）。"""

    code = "terminal_disabled"


class TerminalDockerUnavailable(TerminalError):
    """Docker daemon 不可达（或 SDK 未安装）——绝不创建半成品资源。"""

    code = "docker_unavailable"


class TerminalRedisUnavailable(TerminalError):
    """Redis 不可用：一次性票据 / owner 记录无法落库（fail-closed）。"""

    code = "redis_unavailable"


class TerminalNotFound(TerminalError):
    """终端不存在 / 不属于当前用户（越权与不存在同形，防枚举）。"""

    code = "terminal_not_found"


class TerminalQuotaExceeded(TerminalError):
    """并发终端数超过每用户 / 每组织上限。"""

    code = "terminal_quota_exceeded"


class TerminalOwnedElsewhere(TerminalError):
    """终端归属另一个 worker 进程（多 worker 部署需 sticky 路由）。"""

    code = "terminal_owned_elsewhere"


class TerminalBusy(TerminalError):
    """同一终端已有 writer 在写入。"""

    code = "terminal_busy"


class TerminalInputTooLarge(TerminalError):
    """单次输入超过 ``TERMINAL_MAX_INPUT_BYTES``。"""

    code = "terminal_input_too_large"


class TerminalExited(TerminalError):
    """终端进程已退出，不能再写入。"""

    code = "terminal_exited"


class TerminalTimeout(TerminalError):
    """等待输出超时。"""

    code = "terminal_timeout"


# ── 配置 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TerminalConfig:
    """管理器配置（默认值与 issue #477 §5.3 一致）。"""

    idle_timeout_seconds: float = 900.0
    max_lifetime_seconds: float = 3600.0
    max_per_user: int = 3
    max_per_org: int = 10
    ring_buffer_bytes: int = 262144
    max_read_bytes: int = 65536
    max_input_bytes: int = 65536
    # 进程已退出但尚未被显式 kill 的会话保留窗口：足够最后一次读取尾部输出，
    # 又不会长时间占着用户/组织配额。
    exit_retention_seconds: float = 60.0
    max_cols: int = 1000
    max_rows: int = 500

    @classmethod
    def from_settings(cls) -> TerminalConfig:
        """从惰性 settings 代理构造（只在真正使用时读取）。"""
        from app.core.config import settings

        return cls(
            idle_timeout_seconds=float(settings.TERMINAL_IDLE_TIMEOUT_SECONDS),
            max_lifetime_seconds=float(settings.TERMINAL_MAX_LIFETIME_SECONDS),
            max_per_user=int(settings.TERMINAL_MAX_PER_USER),
            max_per_org=int(settings.TERMINAL_MAX_PER_ORG),
            ring_buffer_bytes=int(settings.TERMINAL_RING_BUFFER_BYTES),
            max_read_bytes=int(settings.TERMINAL_MAX_READ_BYTES),
            max_input_bytes=int(settings.TERMINAL_MAX_INPUT_BYTES),
        )


# ── 后端协议 ─────────────────────────────────────────────────────────────────


class TerminalBackend(Protocol):
    """PTY 后端协议：spawn / send / read / resize / signal / close。

    ``read`` 阻塞直到有数据，EOF 返回 ``b""``；``exit_code`` 只在 EOF 后有意义。
    """

    async def spawn(self, cols: int, rows: int) -> None: ...

    async def send(self, data: bytes) -> int: ...

    async def read(self, max_bytes: int) -> bytes: ...

    async def resize(self, cols: int, rows: int) -> None: ...

    async def signal(self, sig: str) -> None: ...

    async def close(self) -> None: ...

    async def exit_code(self) -> int | None: ...


class FakeBackend:
    """确定性内存后端（单测用）。

    ``feed()`` 注入输出；``emit_eof()`` 模拟进程退出；``send_delay`` 让并发
    写入用例能观察到 writer 锁。
    """

    def __init__(
        self,
        *,
        terminal_id: str = "",
        user_id: int = 0,
        cols: int = 80,
        rows: int = 24,
        echo: bool = False,
        send_delay: float = 0.0,
        **_: Any,
    ) -> None:
        self.terminal_id = terminal_id
        self.user_id = user_id
        self.echo = echo
        self.send_delay = send_delay
        self.spawned = False
        self.closed = False
        self.spawn_args: tuple[int, int] | None = None
        self.sent: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self.signals: list[str] = []
        self._pending = bytearray()
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._exit_code: int | None = None

    async def spawn(self, cols: int, rows: int) -> None:
        self.spawned = True
        self.spawn_args = (cols, rows)

    async def send(self, data: bytes) -> int:
        if self.closed:
            raise TerminalError("后端已关闭")
        if self.send_delay:
            await asyncio.sleep(self.send_delay)
        self.sent.append(bytes(data))
        if self.echo:
            self.feed(data)
        return len(data)

    def feed(self, data: bytes) -> None:
        """测试注入：模拟终端输出。"""
        self._queue.put_nowait(bytes(data))

    def emit_eof(self, code: int = 0) -> None:
        """测试注入：模拟进程退出（reader 收到 EOF 与退出码）。"""
        self._exit_code = code
        self._queue.put_nowait(None)

    async def read(self, max_bytes: int) -> bytes:
        while not self._pending:
            item = await self._queue.get()
            if item is None:
                return b""
            self._pending.extend(item)
        take = min(max_bytes, len(self._pending))
        data = bytes(self._pending[:take])
        del self._pending[:take]
        return data

    async def resize(self, cols: int, rows: int) -> None:
        self.resizes.append((cols, rows))

    async def signal(self, sig: str) -> None:
        self.signals.append(sig)
        if normalize_signal(sig) == "KILL":
            self.emit_eof(-9)

    async def close(self) -> None:
        self.closed = True
        # 唤醒可能阻塞在 read() 的 reader 任务。
        self._queue.put_nowait(None)

    async def exit_code(self) -> int | None:
        return self._exit_code


# ── 会话 ─────────────────────────────────────────────────────────────────────


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def normalize_signal(sig: str) -> str:
    """规范化信号名：``SIGINT`` / ``int`` → ``INT``。

    注意不能用 ``str.lstrip("SIG")``：lstrip 删的是**字符集合**，``"INT"`` 会被
    削成 ``"NT"``（issue #477 单测抓到的真实缺陷）。
    """
    normalized = str(sig).strip().upper()
    if normalized.startswith("SIG"):
        normalized = normalized[3:]
    return normalized


class TerminalSession:
    """一个终端会话的内存状态（不直接暴露给端点层序列化）。"""

    def __init__(
        self,
        *,
        terminal_id: str,
        user_id: int,
        org_id: int | None,
        session_id: str | None,
        backend: TerminalBackend,
        owner_worker: str,
        ring_buffer_bytes: int,
        now_mono: float,
        now_wall: float,
    ) -> None:
        self.terminal_id = terminal_id
        self.user_id = user_id
        self.org_id = org_id
        self.session_id = session_id
        self.backend = backend
        self.owner_worker = owner_worker
        self.status: TerminalStatus = "running"
        self.exit_code: int | None = None
        self.error: str | None = None
        self.close_reason: str | None = None

        self.created_mono = now_mono
        self.last_activity_mono = now_mono
        self.created_at = now_wall
        self.last_activity_at = now_wall

        self._ring_limit = ring_buffer_bytes
        self._buffer = bytearray()
        self._total_written = 0
        self._dropped_total = 0
        self._read_cursor = 0
        self._buffer_lock = asyncio.Lock()

        # 单 writer 锁：send 持有期间其他 writer 直接失败（TerminalBusy）。
        self.send_lock = asyncio.Lock()
        self._data_event = asyncio.Event()
        self.exit_event = asyncio.Event()

        self.reader_task: asyncio.Task[None] | None = None

    # ── 展示 ──
    @property
    def is_terminal(self) -> bool:
        return self.status in ("exited", "error", "closed")

    @property
    def total_written(self) -> int:
        return self._total_written

    @property
    def dropped_total(self) -> int:
        return self._dropped_total

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "terminal_id": self.terminal_id,
            "session_id": self.session_id,
            "created_at": _iso(self.created_at),
            "last_activity_at": _iso(self.last_activity_at),
            "status": self.status,
        }

    def touch(self) -> None:
        self.last_activity_mono = time.monotonic()
        self.last_activity_at = time.time()

    # ── ring buffer ──
    async def append_output(self, data: bytes) -> None:
        """把后端输出写进环形缓冲；溢出丢最旧字节并累计 dropped_total。"""
        if not data:
            return
        async with self._buffer_lock:
            self._buffer.extend(data)
            self._total_written += len(data)
            overflow = len(self._buffer) - self._ring_limit
            if overflow > 0:
                del self._buffer[:overflow]
                self._dropped_total += overflow
        self.touch()
        self._data_event.set()
        _metric_bytes("output", len(data))

    async def snapshot(self) -> tuple[bytes, int, int]:
        """WS 重连补发：当前缓冲全量 + 下一游标 + 累计丢弃字节。"""
        async with self._buffer_lock:
            return bytes(self._buffer), self._total_written, self._dropped_total

    async def snapshot_since(self, cursor: int) -> tuple[bytes, int, int]:
        """返回 ``cursor`` 之后的新输出；已被环覆盖的部分计入 ``lost``。"""
        async with self._buffer_lock:
            if cursor > self._total_written:
                cursor = self._total_written
            oldest = self._total_written - len(self._buffer)
            lost = 0
            if cursor < oldest:
                lost = oldest - cursor
                cursor = oldest
            start_idx = len(self._buffer) - (self._total_written - cursor)
            return bytes(self._buffer[start_idx:]), self._total_written, lost

    async def read_pending(self, max_bytes: int) -> tuple[bytes, bool, int]:
        """``terminal_read`` 消费：未读数据（至多 ``max_bytes``）、是否有剩余、
        累计丢弃字节。"""
        async with self._buffer_lock:
            oldest = self._total_written - len(self._buffer)
            start = max(self._read_cursor, oldest)
            end = self._total_written
            take = min(max_bytes, end - start)
            start_idx = len(self._buffer) - (end - start)
            data = bytes(self._buffer[start_idx : start_idx + take])
            self._read_cursor = start + take
            return data, self._read_cursor < self._total_written, self._dropped_total

    def has_unread(self) -> bool:
        return self._total_written > self._read_cursor

    # ── 等待 ──
    async def wait_for_readable(self, timeout: float) -> bool:
        """等待出现未读输出；clear-then-recheck 消除丢唤醒窗口。"""
        if self.has_unread() or self.is_terminal:
            return True
        self._data_event.clear()
        if self.has_unread() or self.is_terminal:
            return True
        try:
            await asyncio.wait_for(self._data_event.wait(), timeout)
            return True
        except TimeoutError:
            return False

    async def wait_for_since(self, cursor: int, timeout: float) -> None:
        """等待 ``cursor`` 之后出现新输出 / 会话进入终态（或超时）。"""
        if self._total_written > cursor or self.is_terminal:
            return
        self._data_event.clear()
        if self._total_written > cursor or self.is_terminal:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._data_event.wait(), timeout)

    # ── 状态迁移 ──
    def mark_exited(self, code: int | None) -> None:
        if self.status != "running":
            return
        self.status = "exited"
        self.exit_code = code
        self.touch()
        self.exit_event.set()
        self._data_event.set()

    def mark_error(self, message: str) -> None:
        if self.status != "running":
            return
        self.status = "error"
        self.error = message
        self.touch()
        self.exit_event.set()
        self._data_event.set()


# ── 指标（no-op 安全；延迟导入避免测试受 prometheus 影响）─────────────────


def _metric_bytes(direction: str, amount: int) -> None:
    try:
        from app.core.prometheus import TERMINAL_BYTES_TOTAL

        TERMINAL_BYTES_TOTAL.labels(direction=direction).inc(amount)
    except Exception:  # pragma: no cover — 指标失败绝不影响数据面
        pass


def _metric_session_started() -> None:
    try:
        from app.core.prometheus import TERMINAL_SESSIONS_ACTIVE

        TERMINAL_SESSIONS_ACTIVE.inc()
    except Exception:  # pragma: no cover
        pass


def _metric_session_closed(session: TerminalSession) -> None:
    try:
        from app.core.prometheus import (
            TERMINAL_ABNORMAL_EXITS_TOTAL,
            TERMINAL_EXITS_TOTAL,
            TERMINAL_SESSION_LIFETIME,
            TERMINAL_SESSIONS_ACTIVE,
        )

        TERMINAL_SESSIONS_ACTIVE.dec()
        TERMINAL_SESSION_LIFETIME.observe(max(0.0, time.monotonic() - session.created_mono))
        TERMINAL_EXITS_TOTAL.labels(reason=session.close_reason or "unknown").inc()
        abnormal = session.status == "error" or (
            session.exit_code is not None and session.exit_code != 0
        )
        if abnormal:
            TERMINAL_ABNORMAL_EXITS_TOTAL.inc()
    except Exception:  # pragma: no cover
        pass


# ── 管理器 ───────────────────────────────────────────────────────────────────

BackendFactory = Callable[..., TerminalBackend]


class TerminalManager:
    """进程内终端会话管理器（生命周期 / 配额 / 回收 / 缓冲）。"""

    def __init__(
        self,
        *,
        config: TerminalConfig | None = None,
        backend_factory: BackendFactory,
        worker_id: str = "",
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config or TerminalConfig()
        self._backend_factory = backend_factory
        self.worker_id = worker_id
        self._clock = clock
        self._wall_clock = wall_clock
        self._sessions: dict[str, TerminalSession] = {}
        # 配额检查与注册串行化：避免并发 create 绕过配额（短暂持锁，可接受）。
        self._create_lock = asyncio.Lock()

    # ── 查询 ──
    def get_local(self, terminal_id: str) -> TerminalSession | None:
        """按 id 取本地会话（不做归属校验；内部使用）。"""
        return self._sessions.get(terminal_id)

    def has_local(self, terminal_id: str) -> bool:
        return terminal_id in self._sessions

    def get(self, terminal_id: str, *, user_id: int) -> TerminalSession | None:
        """取当前用户的会话；不存在与越权同形（None）。"""
        session = self._sessions.get(terminal_id)
        if session is None or session.user_id != user_id:
            return None
        return session

    def list_for_user(self, user_id: int) -> list[TerminalSession]:
        return [s for s in self._sessions.values() if s.user_id == user_id]

    def _count_user(self, user_id: int) -> int:
        return sum(1 for s in self._sessions.values() if s.user_id == user_id)

    def _count_org(self, org_id: int | None) -> int:
        if org_id is None:
            return 0
        return sum(1 for s in self._sessions.values() if s.org_id == org_id)

    # ── 创建 / 关闭 ──
    async def create(
        self,
        *,
        user_id: int,
        org_id: int | None,
        session_id: str | None = None,
        cols: int = 80,
        rows: int = 24,
    ) -> TerminalSession:
        """创建终端：配额检查 → spawn 后端 → 启动 reader 任务。"""
        if not 1 <= cols <= self.config.max_cols:
            raise TerminalError(f"cols 超出范围（1-{self.config.max_cols}）")
        if not 1 <= rows <= self.config.max_rows:
            raise TerminalError(f"rows 超出范围（1-{self.config.max_rows}）")

        async with self._create_lock:
            if self._count_user(user_id) >= self.config.max_per_user:
                raise TerminalQuotaExceeded(f"每个用户最多 {self.config.max_per_user} 个并发终端")
            if org_id is not None and self._count_org(org_id) >= self.config.max_per_org:
                raise TerminalQuotaExceeded(f"每个组织最多 {self.config.max_per_org} 个并发终端")

            terminal_id = str(uuid.uuid4())
            backend = self._backend_factory(
                terminal_id=terminal_id, user_id=user_id, cols=cols, rows=rows
            )
            await backend.spawn(cols, rows)

            session = TerminalSession(
                terminal_id=terminal_id,
                user_id=user_id,
                org_id=org_id,
                session_id=session_id,
                backend=backend,
                owner_worker=self.worker_id,
                ring_buffer_bytes=self.config.ring_buffer_bytes,
                now_mono=self._clock(),
                now_wall=self._wall_clock(),
            )
            self._sessions[terminal_id] = session
            session.reader_task = asyncio.create_task(
                self._reader_loop(session), name=f"terminal-reader-{terminal_id}"
            )
        _metric_session_started()
        logger.info(
            "terminal session created id=%s user=%s org=%s cols=%s rows=%s",
            terminal_id,
            user_id,
            org_id,
            cols,
            rows,
        )
        return session

    async def kill(self, terminal_id: str, *, user_id: int) -> bool:
        """显式关闭；不存在/越权返回 False。"""
        session = self.get(terminal_id, user_id=user_id)
        if session is None:
            return False
        await self._close_session(session, reason="killed")
        return True

    async def _close_session(self, session: TerminalSession, *, reason: str) -> None:
        if session.status == "closed":
            return
        session.close_reason = reason
        session.status = "closed"
        task = session.reader_task
        current = asyncio.current_task()
        if task is not None and task is not current:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        try:
            await asyncio.wait_for(session.backend.close(), timeout=10)
        except Exception as e:
            # 清理失败只记日志（issue #477 §5.2）——不影响会话移除。
            logger.warning("terminal backend close failed id=%s: %s", session.terminal_id, e)
        self._sessions.pop(session.terminal_id, None)
        _metric_session_closed(session)
        logger.info(
            "terminal session closed id=%s user=%s reason=%s exit=%s",
            session.terminal_id,
            session.user_id,
            reason,
            session.exit_code,
        )

    # ── I/O ──
    async def send(self, terminal_id: str, *, user_id: int, data: bytes) -> int:
        session = self.get(terminal_id, user_id=user_id)
        if session is None:
            raise TerminalNotFound("终端不存在或无权访问")
        if session.status != "running":
            raise TerminalExited("终端进程已退出")
        if len(data) > self.config.max_input_bytes:
            raise TerminalInputTooLarge(f"单次输入超过上限 {self.config.max_input_bytes} 字节")
        if session.send_lock.locked():
            raise TerminalBusy("终端已有写入者，请稍后重试")
        # check 与 acquire 之间无 await 点（未锁时 acquire 不挂起），事件循环内原子。
        await session.send_lock.acquire()
        try:
            written = await session.backend.send(data)
        finally:
            session.send_lock.release()
        session.touch()
        _metric_bytes("input", len(data))
        return written

    async def read(
        self,
        terminal_id: str,
        *,
        user_id: int,
        max_bytes: int | None = None,
        timeout: float = 0.0,
    ) -> tuple[bytes, bool, int]:
        """读取未消费输出（至多 ``max_bytes``）；``timeout>0`` 时等待新输出。

        返回 ``(data, truncated, dropped_total)``；等待超时且无数据也返回空
        ``data``（由调用方决定是否当作 terminal_timeout）。
        """
        session = self.get(terminal_id, user_id=user_id)
        if session is None:
            raise TerminalNotFound("终端不存在或无权访问")
        limit = self.config.max_read_bytes
        if max_bytes is not None:
            limit = max(1, min(int(max_bytes), self.config.max_read_bytes))
        if timeout > 0 and not session.has_unread():
            await session.wait_for_readable(timeout)
        return await session.read_pending(limit)

    async def wait_until_readable(self, terminal_id: str, *, user_id: int, timeout: float) -> bool:
        session = self.get(terminal_id, user_id=user_id)
        if session is None:
            raise TerminalNotFound("终端不存在或无权访问")
        return await session.wait_for_readable(timeout)

    async def resize(self, terminal_id: str, *, user_id: int, cols: int, rows: int) -> None:
        session = self.get(terminal_id, user_id=user_id)
        if session is None:
            raise TerminalNotFound("终端不存在或无权访问")
        if not 1 <= cols <= self.config.max_cols or not 1 <= rows <= self.config.max_rows:
            raise TerminalError("cols/rows 超出范围")
        if session.status != "running":
            raise TerminalExited("终端进程已退出")
        await session.backend.resize(cols, rows)

    async def signal(self, terminal_id: str, *, user_id: int, sig: str) -> None:
        session = self.get(terminal_id, user_id=user_id)
        if session is None:
            raise TerminalNotFound("终端不存在或无权访问")
        normalized = normalize_signal(sig)
        if normalized not in ("INT", "TERM", "KILL"):
            raise TerminalError("不支持的信号（仅 INT/TERM/KILL）")
        await session.backend.signal(normalized)

    # ── 回收 ──
    async def sweep(self, *, now_mono: float | None = None) -> list[tuple[str, str]]:
        """回收超寿命 / 空闲 / 已退出超过保留窗口的会话。返回 (id, reason)。"""
        now = self._clock() if now_mono is None else now_mono
        reclaimed: list[tuple[str, str]] = []
        for session in list(self._sessions.values()):
            if session.status == "closed":
                continue
            reason: str | None = None
            if now - session.created_mono >= self.config.max_lifetime_seconds:
                reason = "max_lifetime"
            elif now - session.last_activity_mono >= self.config.idle_timeout_seconds:
                reason = "idle_timeout"
            elif session.is_terminal and (
                now - session.last_activity_mono >= self.config.exit_retention_seconds
            ):
                reason = "exited_cleanup"
            if reason is None:
                continue
            await self._close_session(session, reason=reason)
            reclaimed.append((session.terminal_id, reason))
        return reclaimed

    async def shutdown(self) -> None:
        """进程关停：关闭全部会话（尽力而为）。"""
        for session in list(self._sessions.values()):
            with contextlib.suppress(BaseException):
                await self._close_session(session, reason="shutdown")

    # ── reader ──
    async def _reader_loop(self, session: TerminalSession) -> None:
        """持续把后端输出泵进 ring buffer；EOF 时记录退出码。"""
        try:
            while True:
                data = await session.backend.read(_READ_CHUNK_BYTES)
                if not data:
                    break
                await session.append_output(data)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # 不记录输出内容（issue #477 红线：终端输出不得进服务端日志）。
            logger.warning(
                "terminal reader failed id=%s: %s",
                session.terminal_id,
                type(e).__name__,
            )
            session.mark_error(f"{type(e).__name__}")
            return
        code: int | None = None
        try:
            code = await session.backend.exit_code()
        except Exception:
            code = None
        session.mark_exited(code)
