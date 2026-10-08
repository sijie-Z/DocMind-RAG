"""Checkpoint manager — persistent task lifecycle + resume（issue #201 最小版）.

对应设计文档 §3（backend/docs/agent-reliability-design.md）与
`_orchestrate/round2/recon_201_resume.md` 的最小版施工图：

- **快照载体 = MySQL `agent_tasks.context_snapshot`**，不是 Redis
  `agent:replay:`：后者只在 run 末尾写一次（崩溃场景天然缺失），且状态机
  （status / 租约 / retry_count）必须与快照同库才有事务语义可言。
- **触发粒度** = 每个 PlanStep 终态后（after_each_step）+ 每轮规划后
  （after_planning）+ 一轮执行返回（after_execute_turn）+ 收尾/断流兜底
  （on_finish / on_error / on_disconnect）。步内（消息级）恢复属完整版。
- **幂等 = 已完成步骤（completed/skipped）绝不重执行**：由 executor 的
  `get_ready_steps()` 天然只挑 pending 承载；崩溃时在途步骤复位为 pending
  重跑一次（at-least-once，见 loop.run 的 resume 分支）。
- **失败绝不阻塞聊天**：写路径内部兜异常、只记 warning（与 ctx.save() 同风格）。

枚举坑（004/006 勘误）：`agent_tasks.status` 在迁移里按**大写成员名**入库
（"PENDING"…"ABANDONED"）。本模块全程走 ORM（SQLAlchemy Enum 按成员名），
刻意不引入裸 SQL；状态翻转一律经 `AgentTask.transition()`。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from app.core.database import AsyncSessionLocal
from app.core.log_safety import log_safe
from app.core.redis import redis_client
from app.exceptions import ConflictError, NotFoundError
from app.models.agent_task import AgentTask, TaskStatus

logger = logging.getLogger(__name__)

# 快照信封版本：结构变更时递增，旧版本按"无可用快照"安全降级（见 parse_snapshot）。
CHECKPOINT_VERSION = 1

# resume 租约：拿到锁的进程独占恢复该任务；每次 checkpoint 保存时续租。
LEASE_SECONDS = 900

# 任务行的过期时间（只写字段；定期清理作业不在本版）。
TASK_EXPIRES_DAYS = 3

# 快照里"已产出答案"的字符上限（截尾保留；防单条 UPDATE 过大）。
PARTIAL_OUTPUT_CAP = 16_384

# 冻结进快照的历史消息条数上限。
HISTORY_CAP = 50


# ── 领域异常（AppError 子类：Part 2 的 HTTP 面据此映射 404/409）──────────────


class TaskNotFoundError(NotFoundError):
    """任务不存在、或不属于当前用户（对外统一 404，不泄露归属信息）。"""
    error_code = "TASK_NOT_FOUND"
    message = "任务不存在或无权访问"


class TaskNotResumableError(ConflictError):
    """任务已终态（completed/abandoned），不可再恢复。"""
    error_code = "TASK_NOT_RESUMABLE"
    message = "任务已终态，不可恢复"


class TaskBusyError(ConflictError):
    """任务正被另一个 resume 持有（Redis 租约未释放）。"""
    error_code = "TASK_BUSY"
    message = "任务正在被另一个恢复请求处理"


def _lock_key(task_id: str) -> str:
    return f"agent:task:lock:{task_id}"


# ── 快照信封：构建 / 解析 ────────────────────────────────────────────────────


def build_envelope(
    *,
    ctx: Any,
    plan: Any,
    trigger: str,
    partial_output: str = "",
    history: list | None = None,
    config: Any = None,
    run_state: dict | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    """组装 v1 快照信封（纯函数，便于单测往返）。

    各段的序列化入口都是"既有形状的超集"：ctx → to_checkpoint_dict()
    （= to_dict() + artifacts/current_step_id），plan → Plan.to_checkpoint_dict()
    （= _save_plan 的 payload + result/error_context/retry_count）。
    """
    return {
        "v": CHECKPOINT_VERSION,
        "session_id": session_id or "",
        "ctx": ctx.to_checkpoint_dict() if ctx is not None else None,
        "plan": plan.to_checkpoint_dict() if plan is not None else None,
        "history": [dict(m) for m in (history or [])][-HISTORY_CAP:],
        "config": config.to_dict() if config is not None else {},
        "run": dict(run_state or {}),
        "partial_output": (partial_output or "")[:PARTIAL_OUTPUT_CAP],
        "updated_by": trigger,
    }


def parse_snapshot(raw: Any) -> dict[str, Any] | None:
    """校验存量快照；返回可用信封，任何损坏/版本不符 → None（安全降级）。

    调用方（load_for_resume / resume_chat）把 None 当作"无 checkpoint"：
    从规划阶段干净重跑——没有任何已完成步骤被越过，仍然幂等；
    绝不拿损坏数据去重建 Plan/ctx。
    """
    if not isinstance(raw, dict):
        return None
    if raw.get("v") != CHECKPOINT_VERSION:
        return None
    if not isinstance(raw.get("ctx"), dict):
        return None
    plan = raw.get("plan")
    if plan is not None and not isinstance(plan, dict):
        return None
    return raw


# ── 写路径 ───────────────────────────────────────────────────────────────────


async def create_task(
    *,
    user_id: int,
    organization_id: int | None,
    query: str,
) -> str | None:
    """建一条 RUNNING 的任务行（chat 起点）。失败返回 None——聊天绝不因它失败。

    task.id（uuid4 hex 12）同时作为 ctx.task_id 与 `agent:replay:{task_id}`
    的命名空间：三处同名才能指向同一个 run。
    """
    try:
        async with AsyncSessionLocal() as db:
            task = AgentTask(
                id=uuid.uuid4().hex[:12],
                user_id=user_id,
                organization_id=organization_id,
                query=query,
                # 显式给 PENDING（列默认值要到 flush 才生效），再经状态机进 RUNNING：
                # 本版没有排队语义，"PENDING 瞬时"。
                status=TaskStatus.PENDING,
                expires_at=datetime.utcnow() + timedelta(days=TASK_EXPIRES_DAYS),
            )
            task.transition(TaskStatus.RUNNING)
            db.add(task)
            await db.commit()
            return task.id
    except Exception as e:
        logger.warning("checkpoint create_task failed: %s", log_safe(e))
        return None


async def save_checkpoint(
    *,
    task_id: str | None,
    ctx: Any,
    plan: Any,
    trigger: str,
    partial_output: str = "",
    history: list | None = None,
    config: Any = None,
    run_state: dict | None = None,
    session_id: str = "",
) -> bool:
    """单条 UPDATE 落一次快照：context_snapshot + 派生的进度列。

    独立 AsyncSessionLocal（照 endpoints/agent.py 的 _save_session_interaction
    模式）：不借聊天的请求会话——那条链路的生命周期属于 SSE，不能假设它还健在。
    失败返回 False（只记 warning），调用方一律不得因此中断主流程。
    """
    if not task_id:
        return False
    envelope = build_envelope(
        ctx=ctx, plan=plan, trigger=trigger, partial_output=partial_output,
        history=history, config=config, run_state=run_state, session_id=session_id,
    )
    try:
        async with AsyncSessionLocal() as db:
            task = await db.get(AgentTask, task_id)
            if task is None:
                logger.warning("checkpoint save skipped: task %s 不存在", log_safe(task_id))
                return False
            task.context_snapshot = envelope
            if plan is not None:
                task.plan_id = plan.id
                task.total_steps = plan.total_steps
                task.completed_steps = plan.completed_steps
                task.progress = round(plan.progress, 4)
            if ctx is not None:
                task.current_step_id = getattr(ctx, "current_step_id", None)
            await db.commit()
    except Exception as e:
        logger.warning("checkpoint save failed (task=%s, trigger=%s): %s", log_safe(task_id), log_safe(trigger), log_safe(e))
        return False
    await _renew_lease(task_id)
    return True


async def save_from_live(*, task_id: str | None, live: dict | None, trigger: str) -> bool:
    """service 断流/异常兜底专用：从 loop.checkpoint_live 的"活状态"落一次快照。

    loop 生成器被 aclose 的时刻自身已停在 yield 上，无法再执行任何触发点代码；
    最近可落盘的状态由 live 引用带出（见 PERAgentLoop.checkpoint_live）。
    """
    if not task_id or not live or live.get("ctx") is None:
        return False
    return await save_checkpoint(
        task_id=task_id,
        trigger=trigger,
        ctx=live.get("ctx"),
        plan=live.get("plan"),
        partial_output=live.get("partial_output", ""),
        history=live.get("history"),
        config=live.get("config"),
        run_state=live.get("run_state"),
        session_id=live.get("session_id", ""),
    )


async def mark_terminal(
    task_id: str | None,
    to: TaskStatus,
    *,
    error: str | None = None,
    error_type: str | None = None,
) -> bool:
    """把任务推进到终态（经 transition() 校验）；从不抛异常（清理路径专用）。

    - 已是目标状态 → 幂等成功（重复收尾不报错）；
    - 非法转换（如 PENDING→COMPLETED）→ 记 warning、返回 False、状态不变；
    - 终态落定后释放 resume 租约（FAILED 也释放：FAILED→RUNNING 是既有重试边）。
    """
    if not task_id:
        return False
    try:
        async with AsyncSessionLocal() as db:
            task = await db.get(AgentTask, task_id)
            if task is None:
                return False
            if task.status == to:
                return True
            try:
                task.transition(to)
            except ValueError as e:
                logger.warning("mark_terminal 拒绝非法转换 (task=%s): %s", log_safe(task_id), log_safe(e))
                return False
            if error:
                task.last_error = str(error)[:2000]
                task.last_error_type = (error_type or "")[:50] or None
            await db.commit()
    except Exception as e:
        logger.warning("mark_terminal failed (task=%s): %s", log_safe(task_id), log_safe(e))
        return False
    await release_task(task_id)
    return True


# ── 读路径 / resume 认领 ─────────────────────────────────────────────────────


async def load_task(
    *, task_id: str, user_id: int, organization_id: int | None = None
) -> AgentTask:
    """取任务行（状态视图与 resume 共用的归属校验）。

    - 行不存在 / 不属于 user_id → TaskNotFoundError（对外 404，不区分二者以免泄露归属）；
    - organization_id 给定时要求**严格相等**（含"任务 org 为 None"的情形——给定组织
      上下文却拿不到同组织任务，一律 404）。HTTP 端点必须先经 `require_organization`
      解析出非空组织上下文再传进来：无组织用户由端点层 fail-closed（403，与 /chat
      同纪律），不允许把 None 传到这里"跳过检查"（评审 B1）。
    返回 detached 实例：只读其列属性，任何写都必须经本模块函数。
    """
    async with AsyncSessionLocal() as db:
        task = await db.get(AgentTask, task_id)
    if task is None or task.user_id != user_id:
        raise TaskNotFoundError("任务不存在或无权访问")
    if organization_id is not None and task.organization_id != organization_id:
        raise TaskNotFoundError("任务不存在或无权访问")
    return task


def task_to_status_dict(task: AgentTask) -> dict[str, Any]:
    """任务状态视图（GET /tasks/{id} 的 data 段；纯函数，不解快照）。"""
    return {
        "task_id": task.id,
        "state": task.status.value,
        "is_recoverable": task.is_recoverable,
        "progress": task.progress,
        "completed_steps": task.completed_steps,
        "total_steps": task.total_steps,
        "current_step_id": task.current_step_id,
        "last_error": task.last_error,
        "last_error_type": task.last_error_type,
        "retry_count": task.retry_count,
        "query": task.query,
        "created_at": task.created_at.isoformat() if task.created_at else "",
        "updated_at": task.updated_at.isoformat() if task.updated_at else "",
    }


async def load_for_resume(
    *, task_id: str, user_id: int, organization_id: int | None = None
) -> tuple[AgentTask, dict[str, Any] | None]:
    """取任务行 + 校验过的快照信封。

    - 行不存在或不属于 user_id（或组织不一致，给定 organization_id 时）→
      TaskNotFoundError（对外 404，不区分二者）；
    - 快照缺失/损坏/版本不符 → payload=None（安全降级：调用方从规划阶段重跑）；
      终态判定由调用方做（is_terminal → TaskNotResumableError，409 语义）。
    """
    task = await load_task(task_id=task_id, user_id=user_id, organization_id=organization_id)
    payload: dict[str, Any] | None = None
    if task.context_snapshot is not None:
        payload = parse_snapshot(task.context_snapshot)
        if payload is None:
            logger.warning(
                "checkpoint 快照损坏/版本不符（task=%s）：resume 安全降级为从规划阶段重跑",
                log_safe(task_id),
            )
    return task, payload


async def claim_task(task_id: str, lease_seconds: int = LEASE_SECONDS) -> bool:
    """resume 互斥：Redis SET NX EX 租约。拿不到 → False（并发 resume 冲突）。

    Redis 不可用时降级为"仅状态检查"（放行 + warning）：本版是单进程场景，
    双 resume 的保护在无 Redis 时只剩状态机兜底——已知窗口，见设计报告 5.1-E2。
    """
    try:
        if not redis_client:
            logger.warning("Redis 不可用：resume 并发锁降级为仅状态检查（task=%s）", log_safe(task_id))
            return True
        ok = await redis_client.set(_lock_key(task_id), "1", nx=True, ex=lease_seconds)
        return bool(ok)
    except Exception as e:
        logger.warning("claim_task 失败，降级放行（task=%s）：%s", log_safe(task_id), log_safe(e))
        return True


async def release_task(task_id: str) -> None:
    """释放 resume 租约（终态落定或认领失败时调用）。失败只记 debug。"""
    try:
        if redis_client:
            await redis_client.delete(_lock_key(task_id))
    except Exception as e:
        logger.debug("release_task failed (task=%s): %s", log_safe(task_id), log_safe(e))


async def _renew_lease(task_id: str, lease_seconds: int = LEASE_SECONDS) -> None:
    """每次快照保存顺带续租（普通 chat 运行没有锁，EXPIRE 对不存在的 key 是 no-op）。"""
    try:
        if redis_client:
            await redis_client.expire(_lock_key(task_id), lease_seconds)
    except Exception as e:
        logger.debug("renew lease failed (task=%s): %s", log_safe(task_id), log_safe(e))


async def begin_resume(task_id: str, lease_seconds: int = LEASE_SECONDS) -> AgentTask:
    """认领一次 resume：拿租约 → 状态机进 RUNNING（重试边）→ retry_count += 1。

    状态流转（不新增状态机边）：
        RUNNING（陈旧，上次进程中断）→ FAILED → RUNNING
        FAILED → RUNNING（既有 "retry" 边）；PENDING → RUNNING；
        （WAITING_TOOL/HUMAN → RUNNING 保底——本版无写入者）
    终态 → TaskNotResumableError；拿不到租约 → TaskBusyError。
    任何中途失败都会释放租约（让调用方能立即重试）。
    """
    if not await claim_task(task_id, lease_seconds=lease_seconds):
        raise TaskBusyError("任务正在被另一个恢复请求处理")
    try:
        async with AsyncSessionLocal() as db:
            task = await db.get(AgentTask, task_id)
            if task is None:
                raise TaskNotFoundError("任务不存在或无权访问")
            if task.is_terminal:
                raise TaskNotResumableError(
                    f"任务已处于终态 {task.status.value}，不可恢复",
                    detail={"task_id": task_id, "state": task.status.value},
                )
            if task.status == TaskStatus.RUNNING:
                # 陈旧 RUNNING = 上次进程中断的可观测形态；先判"那次尝试已死"再接管。
                task.transition(TaskStatus.FAILED)
            if task.status != TaskStatus.RUNNING:
                task.transition(TaskStatus.RUNNING)
            task.retry_count += 1
            await db.commit()
            return task
    except Exception:
        await release_task(task_id)
        raise
