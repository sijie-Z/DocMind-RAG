"""工具调用配对持久化账本（C3a，独立最小版——不依赖事件日志）。

现状问题：tool_call / tool_result 配对只存在于内存环形缓冲
（``app/agent/tracing.py`` 的 TraceStore）与 SSE 事件流里；进程崩溃 / 取消 /
resume 之后，已经派发却永远没有返回的 tool_call 没有账可核（悬空 call）。

本模块把每次工具调用落成两行账（模型见 ``app/models/agent_tool_call.py``）：

    agent_tool_calls    —— 登记（REGISTERED）→ 派发（DISPATCHED）→ 终态；
    agent_tool_results  —— 工具返回（append-only；一次调用至多一条，DB 唯一约束）。

``source_event_seq``：本模块按 task 维度发放的单调序号（跨进程重启从库里
MAX 续号）。call 与 result 各占一个序号，"result 序号 > call 序号"即配对
顺序的可核证据；唯一约束同时把"同一事件重复落账"挡在 DB 层。

多副本部署（同一 task 可能在多个 API 进程间轮转 resume）下，进程内序号缓存
可能落后于库内 MAX：本模块在唯一冲突时刷新缓存并重试一次，且
``record_result`` 会区分"同 call 已有 result（真重复）"与"序号撞车（刷新重试）"，
绝不把真实结果误判成重复而丢弃。

合成终态（崩溃/取消/resume 时对未完成 call 的补写；写入规则）：

    REGISTERED + interrupt → TOOL_NOT_STARTED         （已登记未派发：进程死了）
    DISPATCHED + interrupt → TOOL_OUTCOME_UNKNOWN     （执行中失联）
    REGISTERED + cancel    → ABORTED_BEFORE_DISPATCH  （取消前）
    DISPATCHED + cancel    → TOOL_OUTCOME_UNKNOWN     （取消时正在执行）

``TOOL_NOT_STARTED`` 是唯一可"重开"的合成态：resume 后若同一 call_id 被重放，
``mark_dispatched`` / ``record_result`` 允许把它重新推进（登记与派发是同一
调用的两次账目，不是重复入账）；其余终态/合成态幂等拒绝，绝不覆盖首条账目。

写入纪律：热路径（executor 直接调用）全部 fail-open——DB 抖动只记 warning，
绝不打断聊天；但"孤儿 result（无对应 call）"是逻辑错误，显式抛
``OrphanToolResultError``（执行器侧捕获并 logger.error，不静默）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal
from app.core.log_safety import log_safe
from app.models.agent_tool_call import AgentToolCall, AgentToolResult, ToolCallStatus

logger = logging.getLogger(__name__)

# 参数 / 结果入库截断（防单行过大；审计用途无需全量正文）。
ARGS_CAP = 2_000
RESULT_CAP = 500

STATUS_REGISTERED = ToolCallStatus.REGISTERED
STATUS_DISPATCHED = ToolCallStatus.DISPATCHED
STATUS_COMPLETED = ToolCallStatus.COMPLETED
STATUS_FAILED = ToolCallStatus.FAILED
STATUS_TOOL_OUTCOME_UNKNOWN = ToolCallStatus.TOOL_OUTCOME_UNKNOWN
STATUS_TOOL_NOT_STARTED = ToolCallStatus.TOOL_NOT_STARTED
STATUS_ABORTED_BEFORE_DISPATCH = ToolCallStatus.ABORTED_BEFORE_DISPATCH

# 已落定、不可再被 result 覆盖的状态（重复 result 的幂等拦截面）。
_TERMINAL_ACCOUNTED = frozenset({
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_TOOL_OUTCOME_UNKNOWN,
    STATUS_ABORTED_BEFORE_DISPATCH,
})

# 可派发/可落账的"未完成系"状态：TOOL_NOT_STARTED 允许被 resume 后的重放重开。
_OPEN_STATES = (STATUS_REGISTERED, STATUS_TOOL_NOT_STARTED)


class OrphanToolResultError(Exception):
    """result 找不到对应的 call 记录（无 call 的孤儿结果，绝不静默入账）。"""

    error_code = "ORPHAN_TOOL_RESULT"


# ── 事件序号发放（按 task；跨进程重启从库内 MAX 续号）──────────────────────

_seq_counters: dict[str, int] = {}
_seq_lock = asyncio.Lock()


async def _load_db_max_seq(db: Any, task_id: str) -> int:
    max_call = (await db.execute(
        select(func.max(AgentToolCall.source_event_seq))
        .where(AgentToolCall.task_id == task_id)
    )).scalar()
    max_result = (await db.execute(
        select(func.max(AgentToolResult.source_event_seq))
        .where(AgentToolResult.task_id == task_id)
    )).scalar()
    return max(int(max_call or 0), int(max_result or 0))


async def _resync_seq(db: Any, task_id: str) -> int:
    """把进程内序号缓存刷新到库内 MAX（唯一冲突后调用：缓存可能已过期）。"""
    async with _seq_lock:
        _seq_counters[task_id] = await _load_db_max_seq(db, task_id)
        return _seq_counters[task_id]


async def _next_seq(db: Any, task_id: str) -> int:
    async with _seq_lock:
        if task_id not in _seq_counters:
            _seq_counters[task_id] = await _load_db_max_seq(db, task_id)
        _seq_counters[task_id] += 1
        return _seq_counters[task_id]


def _dump_args(args: dict[str, Any] | None) -> str:
    try:
        text = json.dumps(args or {}, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:  # pragma: no cover — default=str 已兜底，纯防御
        text = str(args)
    return text[:ARGS_CAP]


def _clip_result(result: Any) -> str:
    return str(result or "")[:RESULT_CAP]


# ── 写路径 ───────────────────────────────────────────────────────────────────


async def register_calls(
    task_id: str | None,
    calls: Iterable[tuple[str, str, dict[str, Any] | None]],
) -> int:
    """登记本回合**将实际派发**的调用（先落库，后执行）。

    ``calls`` = ``(call_id, tool_name, args)`` 序列。同一 task 内 call_id 已存在
    → 幂等跳过（resume 重放不会二次登记）。返回新登记行数；任何 DB 故障
    fail-open（返回 0，只记 warning）。

    多副本：进程内序号缓存可能落后于库内 MAX → 唯一冲突时刷新缓存重试一次
    （整批回滚后按库里已有 call_id 去重，不会二次登记）。
    """
    specs = list(calls)
    if not task_id or not specs:
        return 0
    for attempt in range(2):
        try:
            async with AsyncSessionLocal() as db:
                wanted = [c[0] for c in specs]
                existing = set((await db.execute(
                    select(AgentToolCall.call_id)
                    .where(AgentToolCall.task_id == task_id)
                    .where(AgentToolCall.call_id.in_(wanted))
                )).scalars().all())
                inserted = 0
                seen: set[str] = set()
                for call_id, tool_name, args in specs:
                    if not call_id or call_id in existing or call_id in seen:
                        continue
                    seen.add(call_id)
                    seq = await _next_seq(db, task_id)
                    db.add(AgentToolCall(
                        task_id=task_id,
                        call_id=call_id,
                        tool_name=tool_name,
                        arguments=_dump_args(args),
                        source_event_seq=seq,
                        status=STATUS_REGISTERED,
                    ))
                    inserted += 1
                if inserted:
                    await db.commit()
                return inserted
        except IntegrityError as e:
            logger.warning(
                "tool ledger register 唯一冲突，刷新序号后重试（task=%s, attempt=%d）: %s",
                log_safe(task_id), attempt + 1, log_safe(e),
            )
            try:
                async with AsyncSessionLocal() as db:
                    await _resync_seq(db, task_id)
            except Exception as e2:  # noqa: BLE001 — 刷新失败即放弃本批（fail-open）
                logger.warning(
                    "tool ledger register 序号刷新失败（task=%s）: %s",
                    log_safe(task_id), log_safe(e2),
                )
                return 0
            continue
        except Exception as e:  # noqa: BLE001 — 账本故障不得打断执行
            logger.warning(
                "tool ledger register failed (task=%s): %s", log_safe(task_id), log_safe(e),
            )
            return 0
    return 0


async def mark_dispatched(task_id: str | None, call_id: str) -> bool:
    """标记"已派发"：REGISTERED / TOOL_NOT_STARTED → DISPATCHED（条件更新）。"""
    if not task_id or not call_id:
        return False
    try:
        async with AsyncSessionLocal() as db:
            outcome = await db.execute(
                update(AgentToolCall)
                .where(AgentToolCall.task_id == task_id)
                .where(AgentToolCall.call_id == call_id)
                .where(AgentToolCall.status.in_(_OPEN_STATES))
                .values(status=STATUS_DISPATCHED, updated_at=datetime.utcnow())
            )
            await db.commit()
            return bool(outcome.rowcount)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "tool ledger dispatch marker failed (task=%s, call=%s): %s",
            log_safe(task_id), log_safe(call_id), log_safe(e),
        )
        return False


async def record_result(
    task_id: str | None,
    call_id: str,
    *,
    success: bool,
    result: Any = "",
    error_type: str | None = None,
) -> bool:
    """结果落账：写一条 ``agent_tool_results`` 并把 call 推进到 COMPLETED/FAILED。

    - 无对应 call → ``OrphanToolResultError``（显式报错，不静默丢账）；
    - 已落定（COMPLETED/FAILED/合成终态）→ 幂等拦截，返回 False，不覆盖首条账目；
    - 唯一约束 (task_id, call_id) / (task_id, source_event_seq) 兜底防重复入账。
      冲突时先判"同 call 是否已有 result"：有 → 真重复；无 → 序号缓存过期，
      刷新 MAX 后重试一次（多副本 resume 的既有部署形态）。
    """
    if not task_id or not call_id:
        return False
    for attempt in range(2):
        try:
            async with AsyncSessionLocal() as db:
                call = (await db.execute(
                    select(AgentToolCall)
                    .where(AgentToolCall.task_id == task_id)
                    .where(AgentToolCall.call_id == call_id)
                )).scalars().first()
                if call is None:
                    raise OrphanToolResultError(
                        f"task={task_id} call_id={call_id} 没有对应的 call 记录"
                    )
                if call.status in _TERMINAL_ACCOUNTED:
                    logger.warning(
                        "重复 tool result 已拦截（task=%s, call=%s, status=%s）",
                        log_safe(task_id), log_safe(call_id), call.status,
                    )
                    return False
                seq = await _next_seq(db, task_id)
                db.add(AgentToolResult(
                    task_id=task_id,
                    call_id=call_id,
                    tool_call_pk=call.id,
                    source_event_seq=seq,
                    status="success" if success else "error",
                    result_summary=_clip_result(result),
                    error_type=(error_type or None),
                ))
                call.status = STATUS_COMPLETED if success else STATUS_FAILED
                call.updated_at = datetime.utcnow()
                try:
                    await db.commit()
                except IntegrityError as e:
                    await db.rollback()
                    duplicate = (await db.execute(
                        select(AgentToolResult.id)
                        .where(AgentToolResult.task_id == task_id)
                        .where(AgentToolResult.call_id == call_id)
                    )).scalars().first()
                    if duplicate is not None:
                        logger.warning(
                            "重复 tool result 命中唯一约束（task=%s, call=%s）: %s",
                            log_safe(task_id), log_safe(call_id), log_safe(e),
                        )
                        return False
                    logger.warning(
                        "tool ledger result 序号冲突，刷新后重试（task=%s, call=%s, attempt=%d）: %s",
                        log_safe(task_id), log_safe(call_id), attempt + 1, log_safe(e),
                    )
                    await _resync_seq(db, task_id)
                    continue
                return True
        except OrphanToolResultError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "tool ledger result failed (task=%s, call=%s): %s",
                log_safe(task_id), log_safe(call_id), log_safe(e),
            )
            return False
    return False


# ── 合成终态 ─────────────────────────────────────────────────────────────────

_CANCEL_SWEEP = {
    STATUS_REGISTERED: STATUS_ABORTED_BEFORE_DISPATCH,
    STATUS_DISPATCHED: STATUS_TOOL_OUTCOME_UNKNOWN,
}
_INTERRUPT_SWEEP = {
    STATUS_REGISTERED: STATUS_TOOL_NOT_STARTED,
    STATUS_DISPATCHED: STATUS_TOOL_OUTCOME_UNKNOWN,
}


async def synthesize_terminal(task_id: str | None, *, reason: str) -> dict[str, int]:
    """对未完成 call 补写合成终态；规则见模块头。幂等，可重复调用。

    ``reason``：
    - ``"interrupt"`` —— 崩溃 / 断流 / resume 前的旧 run 遗留；
    - ``"cancel"``    —— 协作式取消收敛（CANCELLED 终态）。
    返回 ``{终态名: 本次补写行数}``；DB 故障 fail-open（返回已统计部分）。
    """
    if reason not in ("interrupt", "cancel"):
        raise ValueError(f"unknown synthesis reason: {reason!r}")
    counts: dict[str, int] = {}
    if not task_id:
        return counts
    mapping = _CANCEL_SWEEP if reason == "cancel" else _INTERRUPT_SWEEP
    try:
        async with AsyncSessionLocal() as db:
            for source, target in mapping.items():
                outcome = await db.execute(
                    update(AgentToolCall)
                    .where(AgentToolCall.task_id == task_id)
                    .where(AgentToolCall.status == source)
                    .values(status=target, updated_at=datetime.utcnow())
                )
                changed = int(outcome.rowcount or 0)
                if changed:
                    counts[target] = counts.get(target, 0) + changed
            await db.commit()
            # 收敛后释放进程内序号缓存：任务可能跨进程继续 resume，下一次从
            # 库内 MAX 重新续号（顺带避免长跑进程的缓存条目只增不减）。
            async with _seq_lock:
                _seq_counters.pop(task_id, None)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "tool ledger synthesis failed (task=%s, reason=%s): %s",
            log_safe(task_id), reason, log_safe(e),
        )
    return counts
