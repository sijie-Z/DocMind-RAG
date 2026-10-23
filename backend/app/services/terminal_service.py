"""Terminal service —— 进程级单例（issue #477）。

职责分工：

* :class:`app.agent.terminal.manager.TerminalManager` —— 纯内存生命周期/配额/缓冲；
* 本模块 —— 把管理器接上生产后端（Docker exec PTY），并承载多 worker 所需的
  Redis 状态（owner 记录、worker 心跳、一次性票据）与后台回收循环。

Redis 键：

* ``terminal:owner:{terminal_id}``  —— 该终端的 owner worker id（TTL = 最大寿命）；
* ``terminal:worker:{worker_id}``   —— worker 心跳（TTL 90s，回收循环每 30s 刷新）；
* ``terminal:ticket:{ticket}``      —— 一次性 WS 票据，值 ``"{user_id}:{terminal_id}"``；
* ``terminal:ticket:by_terminal:{terminal_id}`` —— 反向索引，签发新票据时删除旧票据。

安全语义：**Redis 不可用 = fail-closed**（签票/验票/owner 记录无法落库时返回 503
``redis_unavailable``），绝不退化成「无票据/无归属也可连接」。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import secrets
import socket
import time
from datetime import UTC, datetime
from typing import Any

from app.agent.terminal.docker_backend import (
    LABEL_TERMINAL,
    DockerTerminalBackend,
    docker_available,
    list_terminal_containers,
    remove_terminal_container,
)
from app.agent.terminal.manager import (
    TerminalConfig,
    TerminalDisabled,
    TerminalDockerUnavailable,
    TerminalError,
    TerminalManager,
    TerminalNotFound,
    TerminalOwnedElsewhere,
    TerminalRedisUnavailable,
    TerminalSession,
)

logger = logging.getLogger(__name__)

_OWNER_KEY = "terminal:owner:{terminal_id}"
_WORKER_KEY = "terminal:worker:{worker_id}"
_TICKET_KEY = "terminal:ticket:{ticket}"
_TICKET_BY_TERMINAL_KEY = "terminal:ticket:by_terminal:{terminal_id}"

_DOCKER_PROBE_TTL_SECONDS = 5.0
_WORKER_HEARTBEAT_TTL_SECONDS = 90
# 心跳缺失后的宽限期（issue #477 对抗评审 M3）：Redis 中断超过心跳 TTL 后恢复时，
# 存活 worker 可能还没到下一次心跳 tick；此窗口内不把「心跳缺失」当作 owner 已死。
_OWNER_HEARTBEAT_GRACE_SECONDS = 120.0
REAPER_INTERVAL_SECONDS = 30.0

_ISO_RE = re.compile(
    r"^(?P<base>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(?P<frac>\d+))?(?P<tz>Z|[+-]\d{2}:\d{2})?$"
)


def default_worker_id() -> str:
    """worker 标识：``WORKER_ID`` 环境变量优先，否则 ``hostname:pid``。"""
    return os.environ.get("WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}"


def _parse_created(raw: str) -> float | None:
    """解析 Docker 的 ``Created`` 时间戳（纳秒精度）为 epoch 秒。"""
    if not raw:
        return None
    match = _ISO_RE.match(raw.strip())
    if not match:
        return None
    frac = (match.group("frac") or "")[:6].ljust(6, "0")
    tz = match.group("tz") or "+00:00"
    if tz == "Z":
        tz = "+00:00"
    try:
        parsed = datetime.fromisoformat(f"{match.group('base')}.{frac}{tz}")
    except ValueError:
        return None
    return parsed.astimezone(UTC).timestamp()


_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# 单次输入最多审计的行数：防止一条 64KB 帧里塞数万短行导致逐行 DB 写阻塞输入。
_MAX_AUDITED_LINES_PER_INPUT = 20


def _summarize_line(line: str) -> str | None:
    """单行命令摘要：剥离 ANSI 转义、处理退格、脱敏控制字符、截断 200。

    前端 xterm 逐键发送（``\\r`` 单独一帧），所以调用方必须先按换行缓冲/切分；
    这里不再要求输入自带换行。
    """
    line = _ANSI_CSI_RE.sub("", line)
    out: list[str] = []
    for ch in line:
        if ch in ("\x7f", "\b"):
            if out:
                out.pop()
            continue
        if ord(ch) < 32 or ord(ch) == 127:
            continue
        out.append(ch)
    cleaned = "".join(out).strip()
    if not cleaned:
        return None
    return cleaned[:200]


class TerminalService:
    """终端服务单例：管理器 + Redis 状态 + 回收循环。"""

    def __init__(self) -> None:
        self.worker_id = default_worker_id()
        self._manager: TerminalManager | None = None
        self._reaper_task: asyncio.Task[None] | None = None
        self._docker_probe: tuple[float, bool] | None = None
        # owner 心跳缺失的首次观察时间（container_id → wall time）。只有连续
        # 缺失 ≥ 宽限期才判死——按「缺失时长」而非容器年龄（评审 M3）。
        self._dead_owner_candidates: dict[str, float] = {}

    # ── 配置 / 开关 ──
    @staticmethod
    def _settings():
        from app.core.config import settings

        return settings

    def enabled(self) -> bool:
        return bool(getattr(self._settings(), "TERMINAL_ENABLED", False))

    def _require_enabled(self) -> None:
        if not self.enabled():
            raise TerminalDisabled("终端功能未启用（TERMINAL_ENABLED=false）")

    async def docker_available(self) -> bool:
        """探测 Docker（5 秒缓存，避免每个请求都起子线程）。"""
        now = time.monotonic()
        if self._docker_probe is not None:
            probed_at, value = self._docker_probe
            if now - probed_at < _DOCKER_PROBE_TTL_SECONDS:
                return value
        value = await asyncio.to_thread(docker_available)
        self._docker_probe = (now, value)
        return value

    def reset_docker_probe(self) -> None:
        """测试用：清空探测缓存。"""
        self._docker_probe = None

    async def ensure_available(self) -> None:
        """终端创建前的统一门禁：开关 → 沙箱模式 → Docker 可达。"""
        self._require_enabled()
        if getattr(self._settings(), "SANDBOX_MODE", "auto") == "ast":
            raise TerminalDockerUnavailable(
                "SANDBOX_MODE=ast 不允许持久终端（需要 docker 沙箱模式）"
            )
        if not await self.docker_available():
            raise TerminalDockerUnavailable("Docker daemon 不可用")

    # ── 管理器 ──
    @property
    def manager(self) -> TerminalManager:
        if self._manager is None:
            self._manager = TerminalManager(
                config=TerminalConfig.from_settings(),
                backend_factory=self._make_backend,
                worker_id=self.worker_id,
            )
        return self._manager

    def _make_backend(self, *, terminal_id: str, user_id: int, **_: Any) -> Any:
        settings = self._settings()
        image = settings.TERMINAL_CONTAINER_IMAGE or settings.SANDBOX_DOCKER_IMAGE
        return DockerTerminalBackend(terminal_id=terminal_id, user_id=user_id, image=image)

    # ── Redis ──
    @staticmethod
    def _redis():
        from app.core.redis import redis_client

        if not redis_client:
            raise TerminalRedisUnavailable("Redis 不可用：终端票据/归属记录无法落库")
        return redis_client

    async def _set_owner(self, terminal_id: str, user_id: int) -> None:
        """owner 值 = ``"{user_id}:{worker_id}"``——跨 worker 访问时先判归属人，
        非本人一律按「不存在」处理（防存在性枚举，issue §5.4 非本人 → 404）。"""
        try:
            await self._redis().set(
                _OWNER_KEY.format(terminal_id=terminal_id),
                f"{user_id}:{self.worker_id}",
                ex=int(self._settings().TERMINAL_MAX_LIFETIME_SECONDS),
            )
        except TerminalRedisUnavailable:
            raise
        except Exception as e:
            raise TerminalRedisUnavailable(f"写入终端归属记录失败: {type(e).__name__}") from e

    async def get_owner(self, terminal_id: str) -> tuple[int, str] | None:
        """返回 ``(user_id, worker_id)``；不存在或格式非法返回 None。"""
        try:
            raw = await self._redis().get(_OWNER_KEY.format(terminal_id=terminal_id))
        except TerminalRedisUnavailable:
            raise
        except Exception as e:
            raise TerminalRedisUnavailable(f"读取终端归属记录失败: {type(e).__name__}") from e
        if not raw:
            return None
        try:
            user_id, worker_id = str(raw).split(":", 1)
            return int(user_id), worker_id
        except (ValueError, AttributeError):
            return None

    async def clear_owner(self, terminal_id: str) -> None:
        """尽力删除 owner（回收/kill 收尾用，失败只记 debug）。"""
        with contextlib.suppress(Exception):
            await self._redis().delete(_OWNER_KEY.format(terminal_id=terminal_id))

    async def register_worker(self) -> None:
        """写入/刷新本 worker 心跳（回收循环据此判断 owner 是否已死）。"""
        try:
            await self._redis().set(
                _WORKER_KEY.format(worker_id=self.worker_id),
                "1",
                ex=_WORKER_HEARTBEAT_TTL_SECONDS,
            )
        except TerminalRedisUnavailable:
            raise
        except Exception as e:
            raise TerminalRedisUnavailable(f"写入 worker 心跳失败: {type(e).__name__}") from e

    async def worker_alive(self, worker_id: str) -> bool:
        try:
            return bool(await self._redis().exists(_WORKER_KEY.format(worker_id=worker_id)))
        except TerminalRedisUnavailable:
            raise
        except Exception as e:
            raise TerminalRedisUnavailable(f"读取 worker 心跳失败: {type(e).__name__}") from e

    # ── 票据 ──
    @property
    def ticket_ttl(self) -> int:
        return int(self._settings().TERMINAL_TICKET_TTL_SECONDS)

    async def issue_ticket(self, *, user_id: int, terminal_id: str) -> str:
        """签发一次性 WS 票据；同一终端的新票据立即让旧票据失效。"""
        redis = self._redis()
        ttl = self.ticket_ttl
        ticket = secrets.token_urlsafe(32)
        try:
            old = await redis.get(_TICKET_BY_TERMINAL_KEY.format(terminal_id=terminal_id))
            if old:
                await redis.delete(_TICKET_KEY.format(ticket=old))
            await redis.set(
                _TICKET_KEY.format(ticket=ticket),
                f"{user_id}:{terminal_id}",
                ex=ttl,
            )
            await redis.set(_TICKET_BY_TERMINAL_KEY.format(terminal_id=terminal_id), ticket, ex=ttl)
        except Exception as e:
            raise TerminalRedisUnavailable(f"签发终端票据失败: {type(e).__name__}") from e
        return ticket

    async def consume_ticket(self, ticket: str) -> tuple[int, str] | None:
        """GETDEL 一次性消费；票据不存在/过期/格式非法返回 None。"""
        redis = self._redis()
        try:
            raw = await redis.getdel(_TICKET_KEY.format(ticket=ticket))
        except Exception as e:
            raise TerminalRedisUnavailable(f"校验终端票据失败: {type(e).__name__}") from e
        if not raw:
            return None
        try:
            user_id, terminal_id = str(raw).split(":", 1)
            return int(user_id), terminal_id
        except (ValueError, AttributeError):
            return None

    # ── 归属解析 ──
    async def resolve_local(self, terminal_id: str, *, user_id: int) -> TerminalSession:
        """取本地会话。

        - 本地存在且属于本人 → 返回；
        - owner 记录属于**其他用户** → NotFound（不泄露存在性，issue §5.4）；
        - owner 记录属于本人但在其他 worker → TerminalOwnedElsewhere（409/1008）；
        - 其余 → NotFound。
        """
        session = self.manager.get(terminal_id, user_id=user_id)
        if session is not None:
            return session
        owner = await self.get_owner(terminal_id)
        if owner is not None:
            owner_user_id, owner_worker = owner
            if owner_user_id != user_id:
                raise TerminalNotFound("终端不存在或无权访问")
            if owner_worker != self.worker_id:
                raise TerminalOwnedElsewhere("终端归属其他 worker 进程（生产环境需 sticky 路由）")
        raise TerminalNotFound("终端不存在或无权访问")

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
        await self.ensure_available()
        session = await self.manager.create(
            user_id=user_id,
            org_id=org_id,
            session_id=session_id,
            cols=cols,
            rows=rows,
        )
        try:
            await self._set_owner(session.terminal_id, user_id)
        except TerminalError:
            # 半成品绝不留存（issue #477 §8）。
            await self.manager.kill(session.terminal_id, user_id=user_id)
            raise
        await self._audit(
            user_id,
            "terminal_create",
            target_id=session.terminal_id,
            detail=f"cols={cols} rows={rows} session={session_id or '-'}",
        )
        return session

    async def kill(self, terminal_id: str, *, user_id: int) -> bool:
        killed = await self.manager.kill(terminal_id, user_id=user_id)
        if killed:
            await self.clear_owner(terminal_id)
            await self._audit(user_id, "terminal_close", target_id=terminal_id)
        return killed

    # ── 审计（issue #477 §3：会话创建/关闭/命令摘要）────────────────────────
    async def _audit(
        self,
        user_id: int,
        action: str,
        *,
        target_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        """写用户活动审计。审计失败绝不影响终端数据面（异常自吞 + 2s 上限）。"""
        try:
            from app.services.audit_service import audit_service

            await asyncio.wait_for(
                audit_service.log_activity(
                    user_id=user_id,
                    action=action,
                    target_type="terminal",
                    target_id=target_id,
                    detail=detail,
                ),
                timeout=2.0,
            )
        except Exception:  # pragma: no cover — 审计实现自兜，双保险
            logger.debug("terminal audit failed: %s", action, exc_info=True)

    async def audit_line(self, *, user_id: int, terminal_id: str, line: str) -> None:
        """记录一条**完整命令行**的摘要（由调用方按换行切分/缓冲）。"""
        summary = _summarize_line(line)
        if not summary:
            return
        await self._audit(user_id, "terminal_command", target_id=terminal_id, detail=summary)

    async def audit_completed_lines(self, *, user_id: int, terminal_id: str, data: str) -> None:
        """把一段输入按换行切分，审计其中**已完成**的每一行。

        单次输入最多审计 ``_MAX_AUDITED_LINES_PER_INPUT`` 行，其余合并为一条
        计数摘要——避免超多行输入把输入循环拖成 N 次 DB 写。
        """
        parts = re.split(r"[\r\n]", data)
        lines = parts[:-1]
        for line in lines[:_MAX_AUDITED_LINES_PER_INPUT]:
            await self.audit_line(user_id=user_id, terminal_id=terminal_id, line=line)
        extra = len(lines) - _MAX_AUDITED_LINES_PER_INPUT
        if extra > 0:
            await self._audit(
                user_id,
                "terminal_command",
                target_id=terminal_id,
                detail=f"[truncated: {extra} more lines skipped]",
            )

    # ── 回收 ──
    async def sweep(self) -> int:
        """一次回收：本地空闲/超寿命会话 + 孤儿容器。返回回收的本地会话数。"""
        if not self.enabled():
            return 0
        reclaimed = await self.manager.sweep()
        for session, reason in reclaimed:
            await self.clear_owner(session.terminal_id)
            await self._audit(
                session.user_id,
                "terminal_close",
                target_id=session.terminal_id,
                detail=f"reason={reason}",
            )
        await self.cleanup_orphan_containers()
        return len(reclaimed)

    async def cleanup_orphan_containers(self, *, now: float | None = None) -> int:
        """清理 ``docmind.terminal`` 标签容器中无主（owner 已死/已超寿命）的那些。

        owner 心跳缺失采用**连续观察**：首次发现只登记候选，缺失持续 ≥ 宽限期
        （``_OWNER_HEARTBEAT_GRACE_SECONDS``）才删除——按缺失时长而非容器年龄
        判定，覆盖「Redis 中断恢复后、存活 worker 尚未刷新心跳」的竞态（评审 M3）。
        """
        settings = self._settings()
        if not await self.docker_available():
            logger.debug("terminal reaper: Docker 不可用，跳过孤儿容器清理")
            return 0
        try:
            containers = await asyncio.to_thread(list_terminal_containers)
        except Exception as e:
            logger.debug("terminal reaper: 列出容器失败（跳过本轮）: %s", type(e).__name__)
            return 0

        max_lifetime = float(settings.TERMINAL_MAX_LIFETIME_SECONDS)
        current = time.time() if now is None else now
        removed = 0
        seen_ids: set[str] = set()
        for info in list(containers):
            terminal_id = (info.get("labels") or {}).get(LABEL_TERMINAL)
            container_id = str(info.get("id") or "")
            if not terminal_id:
                continue
            if container_id:
                seen_ids.add(container_id)
            if self.manager.has_local(terminal_id):
                continue
            created = _parse_created(str(info.get("created") or ""))
            age = (current - created) if created is not None else None
            try:
                owner = await self.get_owner(terminal_id)
                if owner is None:
                    owner_alive = False
                elif owner[1] == self.worker_id:
                    owner_alive = True
                else:
                    owner_alive = await self.worker_alive(owner[1])
            except TerminalError:
                # Redis 不可用：无法区分活/死 owner —— 本轮不删任何容器。
                logger.debug("terminal reaper: Redis 不可用，跳过孤儿判定")
                return removed
            if owner is not None and owner[1] == self.worker_id:
                # 本 worker 已无该会话（如进程重启、WORKER_ID 固定）→ 孤儿。
                orphan = True
            elif owner is not None and owner_alive:
                self._dead_owner_candidates.pop(container_id, None)
                orphan = False
            elif owner is not None and not owner_alive:
                first_seen = self._dead_owner_candidates.get(container_id)
                if first_seen is None:
                    self._dead_owner_candidates[container_id] = current
                    orphan = False
                else:
                    orphan = (current - first_seen) >= _OWNER_HEARTBEAT_GRACE_SECONDS
            else:
                # owner 键不存在：仅在超过最大寿命后清理；Redis 丢数据时宁可
                # 多留一个容器，也不误杀其他 worker 的活会话。
                orphan = age is not None and age >= max_lifetime
            if not orphan:
                continue
            self._dead_owner_candidates.pop(container_id, None)
            ok = await asyncio.to_thread(remove_terminal_container, info["id"])
            if ok:
                removed += 1
                logger.info(
                    "terminal reaper: 已清理孤儿容器 id=%s terminal=%s",
                    info["id"],
                    terminal_id,
                )
        # 已消失的容器不再占用候选表。
        self._dead_owner_candidates = {
            cid: ts for cid, ts in self._dead_owner_candidates.items() if cid in seen_ids
        }
        return removed

    async def reaper_loop(self, interval: float = REAPER_INTERVAL_SECONDS) -> None:
        """后台回收循环：刷新 worker 心跳 → sweep → sleep。"""
        while True:
            try:
                with contextlib.suppress(TerminalError):
                    await self.register_worker()
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("terminal reaper tick failed: %s", e)
            await asyncio.sleep(interval)

    async def startup(self) -> None:
        """lifespan 启动：注册心跳、立即清理孤儿、启动循环（默认关闭时 no-op）。"""
        if not self.enabled():
            logger.info("TERMINAL_ENABLED=false，终端回收循环不启动")
            return
        with contextlib.suppress(TerminalError):
            await self.register_worker()
        with contextlib.suppress(Exception):
            await self.cleanup_orphan_containers()
        self._reaper_task = asyncio.create_task(self.reaper_loop(), name="terminal-reaper")
        logger.info("终端回收循环已启动（每 %.0fs 一次）", REAPER_INTERVAL_SECONDS)

    async def stop(self) -> None:
        """lifespan 关闭：停循环并关闭本地全部会话。"""
        task, self._reaper_task = self._reaper_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        if self._manager is not None:
            await self._manager.shutdown()


# 进程级单例（端点 / 工具共用）。
terminal_service = TerminalService()
