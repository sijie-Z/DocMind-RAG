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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.agent import steering, tool_ledger
from app.agent.tool_pairs import validate_tool_pairs
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

# 快照里"已产出答案"的字符上限（截尾=只保头部；防单条 UPDATE 过大）。
# 语义边界（issue #278/m4，评审 R5）：崩溃点产出 >16KB 时恢复答案会缺中段
# （无校验和、无中段保留）——最小版的有意取舍，完整版再议；现有用例只钉
# cap 长度语义（test_checkpoint_roundtrip::test_envelope_carries_session_and_caps_partial_and_history）。
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


class TaskNotWaitingApprovalError(ConflictError):
    """任务不在 waiting_approval（或没有待审批请求），审批应答无处安放。"""
    error_code = "TASK_NOT_WAITING_APPROVAL"
    message = "任务当前没有待审批的请求"


class TaskNotCancellableError(ConflictError):
    """任务已终态（completed/abandoned/cancelled），取消无意义。"""
    error_code = "TASK_NOT_CANCELLABLE"
    message = "任务已处于终态，无法取消"


class TaskNotWaitingPlanApprovalError(ConflictError):
    """任务不在 waiting_plan_approval（或没有待审批计划），计划审批应答无处安放。"""
    error_code = "TASK_NOT_WAITING_PLAN_APPROVAL"
    message = "任务当前没有待审批的计划"


class TaskWaitingPlanApprovalError(ConflictError):
    """任务正在等待计划审批：直连 resume 被拒——必须经 plan-approval 端点应答
    （否则计划会在未获人类批准的情况下被执行，审批门形同虚设）。"""
    error_code = "TASK_WAITING_PLAN_APPROVAL"
    message = "任务正在等待计划审批，请先批准/编辑/拒绝该计划"


def _lock_key(task_id: str) -> str:
    return f"agent:task:lock:{task_id}"


# ── HITL：协作式取消旗（approval.py / 端点 / loop 检查点共用）────────────────

# 取消旗 TTL：够穿越"用户点取消 → 活跃 run 在检查点观察到"的窗口；留 24h 让
# 陈旧旗还能在 resume 前收敛终态（见 begin_resume）。
CANCEL_FLAG_TTL_SECONDS = 86_400


def _cancel_key(task_id: str) -> str:
    return f"agent:task:cancel:{task_id}"


async def request_cancel(task_id: str) -> bool:
    """置协作式取消旗（Redis SETEX）。False = 取消通道不可用。

    诚实口径：通道不可用时端点**绝不**假装已受理；活跃 run 会在步间/工具前
    检查点观察到旗并收敛为 run_cancelled → CANCELLED。
    """
    try:
        if not redis_client:
            return False
        await redis_client.setex(_cancel_key(task_id), CANCEL_FLAG_TTL_SECONDS, "1")
        return True
    except Exception as e:
        logger.warning("request_cancel failed (task=%s): %s", log_safe(task_id), log_safe(e))
        return False


async def is_cancel_requested(task_id: str) -> bool:
    """查询取消旗；通道故障按"未取消"处理（打不坏正常执行）。"""
    try:
        if not redis_client:
            return False
        return bool(await redis_client.get(_cancel_key(task_id)))
    except Exception as e:
        logger.debug("is_cancel_requested failed (task=%s): %s", log_safe(task_id), log_safe(e))
        return False


async def clear_cancel_flag(task_id: str) -> None:
    """清除取消旗（测试/收敛收尾用；生产 TTL 自清）。"""
    try:
        if redis_client:
            await redis_client.delete(_cancel_key(task_id))
    except Exception as e:
        logger.debug("clear_cancel_flag failed (task=%s): %s", log_safe(task_id), log_safe(e))


# ── 任务行读取（快照列容错，issue #278/R1）────────────────────────────────────


async def _load_task_row(db: AsyncSession, task_id: str) -> AgentTask | None:
    """读任务行，**不加载快照列**（defer），与 ``db.get`` 同语义。

    背景：``db.get`` 默认加载全部列，快照列若是截断/半写的非法 JSON（生产
    MySQL 的 JSON 列有服务端校验拦得住，sqlite / 带外写库拦不住），读行时
    的 ``json.loads`` 失败会把"读任务行"整个炸掉——该行此后每次读写都炸
    （毒丸）。认领 / 落盘 / 收尾都不需要旧快照值：defer 后该列不进 SELECT，
    UPDATE 只写被改动的列（落盘覆盖坏值也不受影响）；需要快照值的
    ``load_for_resume`` 在行读取完成后单独容错读取该列。
    """
    return await db.get(AgentTask, task_id, options=[defer(AgentTask.context_snapshot)])


# ── 快照信封：构建 / 解析 ────────────────────────────────────────────────────


def _cap_history(history: list | None) -> list[dict]:
    """HISTORY_CAP 截断（C3a）：切点不得拆散 tool_call/tool_result 配对。

    ``[-HISTORY_CAP:]`` 是盲切：若截断点恰好落在 result 上（其 call 在窗口
    之外），冻结的历史会以孤儿 result 的形式被 resume 消费。这里把窗口头部
    的孤儿 result 摘掉（其 call 本就不在窗口内），并在返回前再验一次配对
    （损坏的历史显式报错，save_checkpoint 侧 fail-open 并留 warning）。
    """
    raw = [dict(m) for m in (history or [])]
    window = raw[max(0, len(raw) - HISTORY_CAP):]
    while window and window[0].get("role") == "tool":
        window.pop(0)
    validate_tool_pairs(window)
    return window


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
        "history": _cap_history(history),
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
            task = await _load_task_row(db, task_id)
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
    - **写入失败**（DB 抖动等，issue #301 批 A #10）→ 重试一次（新会话）；
      仍失败 → logger.error 响亮留痕（含后果：任务行将停留原状态、可经
      resume 收敛）并返回 False。刻意不向上抛：本函数也被 _checkpoint_error
      的清理路径与断流兜底调用，在那些路径上抛异常会顶掉原始异常；成功路径
      调用方（service）必须检查返回值并如实上报（见 service._finalize_task_terminal）。
    - 终态落定后释放 resume 租约（FAILED 也释放：FAILED→RUNNING 是既有重试边）。
    """
    if not task_id:
        return False
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            async with AsyncSessionLocal() as db:
                task = await _load_task_row(db, task_id)
                if task is None:
                    return False
                if task.status == to:
                    # 幂等成功同样保证"终态 ⇒ 缓冲空/账目闭合"（防带外写入的迟到指导；
                    # C3a：上次收尾若在 sweep 前失败，这里补一次——synthesize 幂等）。
                    await tool_ledger.synthesize_terminal(
                        task_id,
                        reason="cancel" if to == TaskStatus.CANCELLED else "interrupt",
                    )
                    await steering.clear_steers(task_id)
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
            last_exc = e
            if attempt == 0:
                logger.warning(
                    "mark_terminal 第 1 次写入失败，重试一次 (task=%s, to=%s): %s",
                    log_safe(task_id), to.value, log_safe(e),
                )
                continue
            break
        # C3a：终态落定后先收敛未完成工具调用账目，再释放租约——避免新 resume
        # 在"已释放、未收敛"的窗口里认领任务。取消按取消语义（登记未派发
        # → ABORTED_BEFORE_DISPATCH），其余终态按中断语义（→ TOOL_NOT_STARTED /
        # TOOL_OUTCOME_UNKNOWN），绝不留悬空 call。失败只记 warning（幂等、可重试）。
        await tool_ledger.synthesize_terminal(
            task_id, reason="cancel" if to == TaskStatus.CANCELLED else "interrupt",
        )
        await release_task(task_id)
        # A（steering）：终态清转向缓冲——指导不再有任何消费点（下一步边界
        # 永不到来），留在队列里只会误导后续诊断；清失败不影响终态正确性。
        await steering.clear_steers(task_id)
        return True
    logger.error(
        "mark_terminal 写入失败（已重试一次）：任务 %s 未能落到 %s，"
        "将停留原状态（可经 resume 收敛）: %s",
        log_safe(task_id), to.value, log_safe(last_exc),
    )
    return False


# ── 断流收敛兜底（第四波实机缺陷修复）──────────────────────────────────────

# 断流收敛的合法起始态：只有"正在跑"系才视为"流死 = 运行死"。等待人工决策的
# 挂起态（WAITING_APPROVAL / WAITING_PLAN_APPROVAL / WAITING_HUMAN）不在此列
# ——那是"人回来续跑"的合法入口，断流 ≠ 放弃（HITL 语义）。
_DISCONNECT_CONVERGE_FROM = frozenset({TaskStatus.RUNNING, TaskStatus.WAITING_TOOL})


async def converge_disconnected(
    task_id: str | None,
    *,
    error: str = "客户端断流：运行在完成前中断（可经恢复重试）",
    error_type: str = "client_disconnect",
) -> bool:
    """断流收敛兜底：流已死且无人续跑时，把停在"正在跑"系的任务收敛为 FAILED。

    背景（第四波实机，复验于 e5b5e5b）：断流此前只落快照、状态永停 RUNNING
    （设计报告 2.3 的"可恢复"意图），但产品面无 resume 入口、也无看门狗——
    实测任务列表永远"进行中、无回放"（id=0a7e5e8ea580 现场）。本兜底只补
    收敛观感，不牺牲恢复链：

    - FAILED 是既有"可重试"非终态（FAILED→RUNNING 是既有 retry 边，见
      begin_resume），resume 语义完整保留；绝不写 COMPLETED（不伪装完成）；
    - 等待审批类挂起态不动（见 _DISCONNECT_CONVERGE_FROM 注释）；
    - 不触碰租约：chat 从未持有；resume 断流的归还由 service 侧同一受保护
      单元里的 release_lease 分支负责（避免误删并发 resume 的锁）；
    - 不触碰转向缓冲：FAILED 可恢复，缓冲留给 resume 起点 (drain) 消费。
    失败只记 warning（best-effort，与断流快照同级纪律），返回 False。
    """
    if not task_id:
        return False
    try:
        async with AsyncSessionLocal() as db:
            task = await _load_task_row(db, task_id)
            if task is None:
                return False
            if task.status not in _DISCONNECT_CONVERGE_FROM:
                return False
            task.transition(TaskStatus.FAILED)
            task.last_error = str(error)[:2000]
            task.last_error_type = error_type[:50]
            await db.commit()
            # C3a：断流 = 运行中断——未完成 call 补写合成终态（可经 resume 收敛）。
            await tool_ledger.synthesize_terminal(task_id, reason="interrupt")
            return True
    except Exception as e:
        logger.warning(
            "断流收敛失败（task=%s）：%s", log_safe(task_id), log_safe(e),
        )
        return False


# ── HITL：审批暂停写路径（approval.py 的暂停回调调用）──────────────────────


async def mark_waiting_approval(task_id: str | None) -> bool:
    """把任务推进到 waiting_approval（RUNNING→…，经状态机）；幂等。

    成功即**释放 resume 租约**：暂停的 run 已把状态所有权交还给"人类应答"
    （run 的生成器已结束）——审批端点的 resume 认领不能被 900s 租约卡住
    （与 mark_terminal 同款释放纪律）。任何失败只记 warning、返回 False：
    调用方（loop 的暂停回调）据此走"审批不可用"降级，绝不悬空。
    """
    if not task_id:
        return False
    try:
        async with AsyncSessionLocal() as db:
            task = await _load_task_row(db, task_id)
            if task is None:
                return False
            if task.status == TaskStatus.WAITING_APPROVAL:
                # 幂等（重入同一暂停点）；顺带补一次账目收敛（上次收尾若在
                # sweep 前失败，这里兜底——synthesize 幂等）。
                await tool_ledger.synthesize_terminal(task_id, reason="interrupt")
                return True
            if task.is_terminal:
                logger.warning(
                    "mark_waiting_approval 拒绝：任务 %s 已终态 %s",
                    log_safe(task_id), task.status.value,
                )
                return False
            try:
                task.transition(TaskStatus.WAITING_APPROVAL)
            except ValueError as e:
                logger.warning(
                    "mark_waiting_approval 拒绝非法转换 (task=%s): %s",
                    log_safe(task_id), log_safe(e),
                )
                return False
            await db.commit()
    except Exception as e:
        logger.warning("mark_waiting_approval 写入失败 (task=%s): %s", log_safe(task_id), log_safe(e))
        return False
    # C3a：本 run 在审批点结束——本回合已登记、未派发的调用补写 TOOL_NOT_STARTED
    # （resume 后模型会以新的 call_id 重新登记；旧账不得悬空）。
    await tool_ledger.synthesize_terminal(task_id, reason="interrupt")
    await release_task(task_id)
    return True


async def get_pending_approval(task_id: str) -> dict[str, Any] | None:
    """只读取快照 run_state.pending_approval（GET 状态视图/恢复现场用）。

    容错同 load_for_resume：快照缺失/损坏/无 pending → None（安全降级）。
    """
    raw_snapshot: Any | None = None
    try:
        async with AsyncSessionLocal() as db:
            cursor = await db.execute(
                select(AgentTask.context_snapshot).where(AgentTask.id == task_id)
            )
            raw_snapshot = cursor.scalar_one_or_none()
    except (TypeError, ValueError) as e:
        logger.warning(
            "checkpoint 快照列反序列化失败（task=%s，读 pending_approval）：%s",
            log_safe(task_id), log_safe(e),
        )
        return None
    except Exception as e:
        logger.debug("get_pending_approval 读取失败 (task=%s): %s", log_safe(task_id), log_safe(e))
        return None
    envelope = parse_snapshot(raw_snapshot)
    if envelope is None:
        return None
    run_state = envelope.get("run")
    pending = run_state.get("pending_approval") if isinstance(run_state, dict) else None
    return pending if isinstance(pending, dict) else None


async def apply_approval_decision(
    *,
    task_id: str,
    user_id: int,
    organization_id: int | None = None,
    decision: str,
    modified_instruction: str | None = None,
    note: str | None = None,
) -> tuple[AgentTask, dict[str, Any] | None, dict[str, Any]]:
    """审批应答的同步预检 + 记账 + 认领（端点在开流之前调用）。

    单写者顺序：拿租约 → 读行/快照 → 校验（waiting_approval + 有 pending）
    → 写决定账本（approve + modified_instruction 同时追加 steering 指令）
    → WAITING_APPROVAL→RUNNING（retry_count += 1）→ 返回 (task, envelope, record)。
    任何失败路径都释放租约（让调用方拿到 409 后可立即重试）。

    409 语义（AppError 子类）：
      - 行终态 → TaskNotResumableError（state=…）；已 cancelled 的任务应答被拒，
        不再"复活"；
      - 非 waiting_approval / 无 pending / 快照损坏 → TaskNotWaitingApprovalError；
      - 并发（租约被持有）→ TaskBusyError；
      - 审批等待超时（approval.py：expires_at 已过）→ 如实置 FAILED +
        error_type="approval_timeout" 后按 409 上报（**绝不**伪装成"被拒绝"）。
    """
    if decision not in ("approve", "deny"):
        raise ValueError(f"非法审批决定: {decision!r}")
    if not await claim_task(task_id):
        raise TaskBusyError("任务正在被另一个恢复请求处理")
    try:
        expired_pending: dict[str, Any] | None = None
        async with AsyncSessionLocal() as db:
            task = await _load_task_row(db, task_id)
            _check_task_access(task, user_id=user_id, organization_id=organization_id)
            if task.is_terminal:
                raise TaskNotResumableError(
                    f"任务已处于终态 {task.status.value}，无待审批请求可应答",
                    detail={"task_id": task_id, "state": task.status.value},
                )
            if task.status != TaskStatus.WAITING_APPROVAL:
                raise TaskNotWaitingApprovalError(
                    f"任务当前状态为 {task.status.value}，没有待审批的请求",
                    detail={"task_id": task_id, "state": task.status.value},
                )
            raw_snapshot: Any | None = None
            try:
                cursor = await db.execute(
                    select(AgentTask.context_snapshot).where(AgentTask.id == task_id)
                )
                raw_snapshot = cursor.scalar_one_or_none()
            except (TypeError, ValueError) as e:
                logger.warning(
                    "checkpoint 快照列反序列化失败（task=%s，审批记账）：%s",
                    log_safe(task_id), log_safe(e),
                )
            envelope = parse_snapshot(raw_snapshot)
            if envelope is None:
                raise TaskNotWaitingApprovalError(
                    "任务快照缺失/损坏，无法关联待审批请求",
                    detail={"task_id": task_id, "state": task.status.value},
                )
            run_state = envelope.get("run")
            if not isinstance(run_state, dict) or not isinstance(
                run_state.get("pending_approval"), dict
            ):
                raise TaskNotWaitingApprovalError(
                    "没有待审批的请求（可能已被应答或撤回）",
                    detail={"task_id": task_id, "state": task.status.value},
                )

            from app.agent import approval as _approval

            pending = run_state["pending_approval"]
            if _approval.approval_expired(pending):
                # 超时：不写任何决定，退出会话后如实置 FAILED(approval_timeout)。
                expired_pending = pending
            else:
                record: dict[str, Any] = {
                    "tool_name": pending.get("tool_name"),
                    "args_hash": pending.get("args_hash"),
                    "tool_call_id": pending.get("tool_call_id"),
                    "decision": decision,
                    "modified_instruction": (
                        str(modified_instruction).strip()[:2000]
                        if decision == "approve" and modified_instruction
                        else None
                    ),
                    "note": (str(note).strip()[:500] or None) if note else None,
                    "decided_by": user_id,
                    "decided_at": datetime.utcnow().isoformat() + "Z",
                }
                decisions = run_state.get("approval_decisions")
                if not isinstance(decisions, list):
                    decisions = []
                decisions.append(record)
                run_state["approval_decisions"] = decisions
                if record["modified_instruction"]:
                    # 改指令续跑：指令入 run_state.steering，resume 时注入步骤
                    # 提示词（见 loop.run 的 human_guidance 提取）。
                    steering = run_state.get("steering")
                    if not isinstance(steering, list):
                        steering = []
                    steering.append(record["modified_instruction"])
                    run_state["steering"] = steering
                run_state.pop("pending_approval", None)
                envelope["run"] = run_state
                envelope["updated_by"] = f"approval_{decision}"
                task.context_snapshot = envelope
                task.transition(TaskStatus.RUNNING)
                task.retry_count += 1
                await db.commit()
                return task, envelope, record

        # ── 超时分支（会话已退出，避免嵌套会话写）──
        logger.warning(
            "审批等待超时（task=%s, tool=%s）：如实置 FAILED(approval_timeout)",
            log_safe(task_id), log_safe(str((expired_pending or {}).get("tool_name"))),
        )
        await mark_terminal(
            task_id,
            TaskStatus.FAILED,
            error="审批等待超时（approval_timeout）：等待期间未收到人类应答",
            error_type="approval_timeout",
        )
        raise TaskNotWaitingApprovalError(
            "审批等待已超时：任务已如实置为 failed（approval_timeout），"
            "可经 resume 重新发起审批",
            detail={"task_id": task_id, "state": "failed", "error_type": "approval_timeout"},
        )
    except Exception:
        await release_task(task_id)
        raise


# ── B：计划审批（plan_approval_mode=always 的暂停/决策写路径）────────────────
#
# 语义（与 approval 门正交：审批门拦"工具行动"，计划审批拦"整版计划"）：
# - 暂停：loop 在 planner 产出计划后调 mark_waiting_plan_approval，任务态
#   RUNNING→WAITING_PLAN_APPROVAL，等人类决策；
# - approve：照计划执行（resume 复用既有链）；
# - edit：校验后的步骤**整体替换**快照计划的步骤再执行（白名单字段：
#   id/description/dependencies/tool_hint；非法输入在端点层即 422）；
# - reject：**带理由重规划一次**——写 run_state.plan_reject_replan 标记，
#   resume 的 loop 放弃被拒计划、把理由作为 replan_feedback 重新规划；
#   第二次拒绝 → 终态 REJECTED（重规划只给一次机会，见 request 语义；
#   "再拒即终态"，不再无限重规划）。
MAX_PLAN_APPROVAL_REJECTIONS = 1


def _build_edited_steps(edited_steps: list | None) -> list[dict[str, Any]]:
    """编辑步骤的服务端独立校验 + 快照形状重建（白名单字段）。

    与 PlanStep.from_checkpoint_dict 的读取形状全等：编辑 = 用人类给定的
    步骤**整体替换**计划步骤（未提供字段取安全默认：pending / auto_retry /
    low）。端点层 pydantic 已做同一组结构校验（422）；这里是端点豁免路径
    （服务层直调）的第二道闸门——绝不把未校验的人类输入写进快照。
    """
    steps: list[dict[str, Any]] = []
    for item in edited_steps or []:
        if not isinstance(item, dict):
            raise ValueError("edited_steps 的每个元素必须是对象")
        sid = str(item.get("id") or "").strip()
        desc = str(item.get("description") or "").strip()
        if not sid:
            raise ValueError("每个步骤必须携带非空 id")
        if not desc:
            raise ValueError(f"步骤 {sid} 必须携带非空 description")
        deps = []
        for d in item.get("dependencies") or []:
            d_str = str(d).strip()
            if d_str:
                deps.append(d_str)
        tool_hint = item.get("tool_hint")
        steps.append({
            "id": sid[:36],
            "description": desc[:2000],
            "dependencies": deps,
            "tool_hint": (str(tool_hint).strip()[:100] or None) if tool_hint else None,
            "status": "pending",
            "result": None,
            "error_context": None,
            "retry_count": 0,
            "parallel_group": None,
            "risk_level": "low",
            "retry_strategy": "auto_retry",
            "fallback_tool": None,
        })
    if not steps:
        raise ValueError("edited_steps 不能为空（至少 1 个步骤）")
    ids = [s["id"] for s in steps]
    if len(set(ids)) != len(ids):
        raise ValueError("edited_steps 的 id 必须唯一")
    idset = set(ids)
    for s in steps:
        unknown = [d for d in s["dependencies"] if d not in idset]
        if unknown:
            raise ValueError(f"步骤 {s['id']} 的 dependencies 引用了不存在的步骤: {unknown}")
        if s["id"] in s["dependencies"]:
            raise ValueError(f"步骤 {s['id']} 不能依赖自身")
    return steps


async def mark_waiting_plan_approval(task_id: str | None) -> bool:
    """把任务推进到 waiting_plan_approval（RUNNING→…，经状态机）；幂等。

    成功即释放 resume 租约（暂停的 run 已把状态所有权交还给人类决策——与
    mark_waiting_approval 同款释放纪律）。失败只记 warning、返回 False：
    调用方（loop 的暂停点）据此走"无法持久化 → 不制造假暂停"降级。
    """
    if not task_id:
        return False
    try:
        async with AsyncSessionLocal() as db:
            task = await _load_task_row(db, task_id)
            if task is None:
                return False
            if task.status == TaskStatus.WAITING_PLAN_APPROVAL:
                return True  # 幂等（重入同一暂停点）
            if task.is_terminal:
                logger.warning(
                    "mark_waiting_plan_approval 拒绝：任务 %s 已终态 %s",
                    log_safe(task_id), task.status.value,
                )
                return False
            try:
                task.transition(TaskStatus.WAITING_PLAN_APPROVAL)
            except ValueError as e:
                logger.warning(
                    "mark_waiting_plan_approval 拒绝非法转换 (task=%s): %s",
                    log_safe(task_id), log_safe(e),
                )
                return False
            await db.commit()
    except Exception as e:
        logger.warning("mark_waiting_plan_approval 写入失败 (task=%s): %s", log_safe(task_id), log_safe(e))
        return False
    await release_task(task_id)
    return True


async def get_pending_plan_approval(task_id: str) -> dict[str, Any] | None:
    """只读取快照 run_state.pending_plan_approval（GET 状态视图用）。

    容错同 get_pending_approval：快照缺失/损坏/无 pending → None（安全降级）。
    """
    raw_snapshot: Any | None = None
    try:
        async with AsyncSessionLocal() as db:
            cursor = await db.execute(
                select(AgentTask.context_snapshot).where(AgentTask.id == task_id)
            )
            raw_snapshot = cursor.scalar_one_or_none()
    except (TypeError, ValueError) as e:
        logger.warning(
            "checkpoint 快照列反序列化失败（task=%s，读 pending_plan_approval）：%s",
            log_safe(task_id), log_safe(e),
        )
        return None
    except Exception as e:
        logger.debug("get_pending_plan_approval 读取失败 (task=%s): %s", log_safe(task_id), log_safe(e))
        return None
    envelope = parse_snapshot(raw_snapshot)
    if envelope is None:
        return None
    run_state = envelope.get("run")
    pending = run_state.get("pending_plan_approval") if isinstance(run_state, dict) else None
    return pending if isinstance(pending, dict) else None


async def apply_plan_approval_decision(
    *,
    task_id: str,
    user_id: int,
    organization_id: int | None = None,
    decision: str,
    edited_steps: list | None = None,
    note: str | None = None,
) -> tuple[AgentTask, dict[str, Any] | None, dict[str, Any]]:
    """计划审批应答的同步预检 + 记账 + 认领（端点在开流之前调用）。

    单写者顺序：拿租约 → 读行/快照 → 校验（waiting_plan_approval + 有 pending）
    → 记账 → WAITING_PLAN_APPROVAL→RUNNING（retry_count += 1）→ 返回
    (task, envelope, record)。任何失败路径都释放租约。

    - approve：弹掉 pending（= 计划获准，resume 直接执行）；
    - edit：_build_edited_steps 校验后整体替换快照 plan.steps（弹掉 pending）；
    - reject#1：弹掉 pending + run_state.plan_reject_replan=理由 → 回 RUNNING，
      调用方续跑 resume 流（loop 放弃被拒计划、带理由重规划）；
    - reject#2（重规划额度已用尽）：终态 REJECTED + last_error 记拒绝理由
      （诚实的失败原因：不是 failed、不是 cancelled——是"人类看过，不要"）；
      record["terminal"]=True，**无流可续**，调用方回 JSON 而非 SSE。

    409 语义（AppError 子类）：终态 → TaskNotResumableError；非
    waiting_plan_approval / 无 pending / 快照损坏 → TaskNotWaitingPlanApprovalError；
    并发（租约被持有）→ TaskBusyError。
    """
    if decision not in ("approve", "edit", "reject"):
        raise ValueError(f"非法计划审批决定: {decision!r}")
    if not await claim_task(task_id):
        raise TaskBusyError("任务正在被另一个恢复请求处理")
    try:
        async with AsyncSessionLocal() as db:
            task = await _load_task_row(db, task_id)
            _check_task_access(task, user_id=user_id, organization_id=organization_id)
            if task.is_terminal:
                raise TaskNotResumableError(
                    f"任务已处于终态 {task.status.value}，无待审批计划可应答",
                    detail={"task_id": task_id, "state": task.status.value},
                )
            if task.status != TaskStatus.WAITING_PLAN_APPROVAL:
                raise TaskNotWaitingPlanApprovalError(
                    f"任务当前状态为 {task.status.value}，没有待审批的计划",
                    detail={"task_id": task_id, "state": task.status.value},
                )
            raw_snapshot: Any | None = None
            try:
                cursor = await db.execute(
                    select(AgentTask.context_snapshot).where(AgentTask.id == task_id)
                )
                raw_snapshot = cursor.scalar_one_or_none()
            except (TypeError, ValueError) as e:
                logger.warning(
                    "checkpoint 快照列反序列化失败（task=%s，计划审批记账）：%s",
                    log_safe(task_id), log_safe(e),
                )
            envelope = parse_snapshot(raw_snapshot)
            if envelope is None:
                raise TaskNotWaitingPlanApprovalError(
                    "任务快照缺失/损坏，无法关联待审批计划",
                    detail={"task_id": task_id, "state": task.status.value},
                )
            run_state = envelope.get("run")
            pending = (
                run_state.get("pending_plan_approval")
                if isinstance(run_state, dict) else None
            )
            if not isinstance(pending, dict):
                raise TaskNotWaitingPlanApprovalError(
                    "没有待审批的计划（可能已被应答）",
                    detail={"task_id": task_id, "state": task.status.value},
                )
            plan_data = envelope.get("plan")
            if not isinstance(plan_data, dict):
                raise TaskNotWaitingPlanApprovalError(
                    "任务快照缺少计划，无法应答",
                    detail={"task_id": task_id, "state": task.status.value},
                )

            now_iso = datetime.utcnow().isoformat() + "Z"
            record: dict[str, Any] = {
                "decision": decision,
                "plan_id": pending.get("plan_id") or plan_data.get("id"),
                "step_count": len(pending.get("steps") or []),
                "note": (str(note).strip()[:500] or None) if note else None,
                "decided_by": user_id,
                "decided_at": now_iso,
            }

            if decision in ("approve", "edit"):
                if decision == "edit":
                    new_steps = _build_edited_steps(edited_steps)
                    replaced_plan = dict(plan_data)
                    replaced_plan["steps"] = new_steps
                    envelope["plan"] = replaced_plan
                    record["edited_step_count"] = len(new_steps)
                run_state.pop("pending_plan_approval", None)
                envelope["run"] = run_state
                envelope["updated_by"] = f"plan_{decision}"
                task.context_snapshot = envelope
                task.transition(TaskStatus.RUNNING)
                task.retry_count += 1
                await db.commit()
                return task, envelope, record

            # ── reject ──
            rejections = int(run_state.get("plan_approval_rejections") or 0) + 1
            run_state["plan_approval_rejections"] = rejections
            run_state.pop("pending_plan_approval", None)
            envelope["run"] = run_state
            if rejections > MAX_PLAN_APPROVAL_REJECTIONS:
                # 第二次拒绝：终态 REJECTED（重规划机会已用尽——绝不无限重规划）。
                envelope["updated_by"] = "plan_reject_terminal"
                task.context_snapshot = envelope
                task.transition(TaskStatus.REJECTED)
                reason = record["note"] or "（未附理由）"
                task.last_error = (
                    f"计划被用户拒绝（第 {rejections} 次，重规划上限 "
                    f"{MAX_PLAN_APPROVAL_REJECTIONS} 次）：{reason}"
                )[:2000]
                task.last_error_type = "plan_rejected"
                await db.commit()
                record.update({
                    "terminal": True,
                    "rejections": rejections,
                    "message": "计划已再次被拒绝，重规划机会已用尽，任务终止（rejected）",
                })
                # 终态收尾纪律与 mark_terminal 一致：释放租约 + 清转向缓冲。
                await release_task(task_id)
                await steering.clear_steers(task_id)
                return task, envelope, record
            # 第一次拒绝：带理由重规划一次（run_state 标记 → resume 的 loop 消费）。
            run_state["plan_reject_replan"] = {
                "feedback": record["note"] or (
                    "用户拒绝了上一个计划，但未附理由；请重新规划：保持目标不变，"
                    "换一种步骤分解思路，避免与上一版雷同"
                ),
                "at": now_iso,
            }
            envelope["updated_by"] = "plan_reject_replan"
            task.context_snapshot = envelope
            task.transition(TaskStatus.RUNNING)
            task.retry_count += 1
            await db.commit()
            record.update({"rejections": rejections, "replan": True})
            return task, envelope, record
    except Exception:
        await release_task(task_id)
        raise


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
    - 读行经 `_load_task_row`（defer 快照列，issue #278/R1 毒丸防线）：快照列若是
      截断/半写 JSON，"读任务行"不得被它整个炸掉。
    返回 detached 实例：只读其列属性（**不含** context_snapshot——需要它的调用方
    走 load_for_resume 的单独容错读取），任何写都必须经本模块函数。
    """
    async with AsyncSessionLocal() as db:
        task = await _load_task_row(db, task_id)
    _check_task_access(task, user_id=user_id, organization_id=organization_id)
    return task


def _check_task_access(
    task: AgentTask | None, *, user_id: int, organization_id: int | None
) -> None:
    """归属 / 组织校验（load_task 与 load_for_resume 共用，避免两处守卫漂移）。

    - 行缺失或非本人 → TaskNotFoundError（对外 404，不区分二者以免泄露归属）；
    - organization_id 给定 ⇒ 严格相等（含任务 org=None → 404；评审 B1）。
    """
    if task is None or task.user_id != user_id:
        raise TaskNotFoundError("任务不存在或无权访问")
    if organization_id is not None and task.organization_id != organization_id:
        raise TaskNotFoundError("任务不存在或无权访问")


async def load_task_row(task_id: str) -> AgentTask | None:
    """按 id 裸读任务行（无归属校验），供内部旁路（终态通知）使用。

    返回 detached 实例、不加载快照列（同 _load_task_row 的毒丸防线）；
    行缺失 / 任何读取故障 → None（诚实降级）。**HTTP 面绝不使用本函数**——
    对外一律走 load_task 的归属闸门。
    """
    if not task_id:
        return None
    try:
        async with AsyncSessionLocal() as db:
            return await _load_task_row(db, task_id)
    except Exception as e:
        logger.warning("load_task_row failed (task=%s): %s", log_safe(task_id), log_safe(e))
        return None


async def list_tasks(
    *, user_id: int, organization_id: int | None, offset: int, limit: int
) -> tuple[int, list[AgentTask]]:
    """任务列表分页（GET /agent/tasks 用）。

    归属过滤与 load_task 同纪律（严格同组织，含 org=None 的精确匹配）；
    行读取 defer 快照列（#278/R1 毒丸防线——列表绝不因某行快照坏 JSON 而炸）。
    """
    conditions = (
        AgentTask.user_id == user_id,
        AgentTask.organization_id == organization_id,
    )
    async with AsyncSessionLocal() as db:
        total = (
            await db.execute(select(func.count(AgentTask.id)).where(*conditions))
        ).scalar() or 0
        rows = (
            await db.execute(
                select(AgentTask)
                .where(*conditions)
                .options(defer(AgentTask.context_snapshot))
                .order_by(AgentTask.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
    return int(total), list(rows)


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
    - 快照列反序列化失败（截断/半写 JSON，issue #278/R1）同样按"无快照"降级：
      行本身照常返回（其余列完好），毒丸行不得拖垮每次 resume。
    终态判定由调用方做（is_terminal → TaskNotResumableError，409 语义）。
    """
    async with AsyncSessionLocal() as db:
        # 行与快照列分开读：整行 get 会把快照列的 json.loads 失败扩散成
        # "整个任务行读不出来"（毒丸——此后每次 resume/落盘/收尾都炸）。
        task = await _load_task_row(db, task_id)
        _check_task_access(task, user_id=user_id, organization_id=organization_id)
        raw_snapshot: Any | None = None
        try:
            cursor = await db.execute(
                select(AgentTask.context_snapshot).where(AgentTask.id == task_id)
            )
            raw_snapshot = cursor.scalar_one_or_none()
        except (TypeError, ValueError) as e:
            # json.JSONDecodeError 是 ValueError 子类（截断/半写 JSON 的实测形态）。
            logger.warning(
                "checkpoint 快照列反序列化失败（task=%s）：按无快照安全降级：%s",
                log_safe(task_id),
                log_safe(e),
            )
    payload: dict[str, Any] | None = None
    if raw_snapshot is not None:
        payload = parse_snapshot(raw_snapshot)
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
        WAITING_APPROVAL/WAITING_TOOL/WAITING_HUMAN → RUNNING（恢复执行）
    终态 → TaskNotResumableError；拿不到租约 → TaskBusyError。
    HITL 取消旗：观察到旗（= 取消请求在 run 死前未被消费）→ 先把任务如实收敛
    到 CANCELLED（终态），再以 TaskNotResumableError(state=cancelled) 拒绝本次
    resume——取消过的任务绝不被 resume 复活。
    任何中途失败都会释放租约（让调用方能立即重试）。
    """
    if not await claim_task(task_id, lease_seconds=lease_seconds):
        raise TaskBusyError("任务正在被另一个恢复请求处理")
    try:
        async with AsyncSessionLocal() as db:
            task = await _load_task_row(db, task_id)
            if task is None:
                raise TaskNotFoundError("任务不存在或无权访问")
            if task.is_terminal:
                raise TaskNotResumableError(
                    f"任务已处于终态 {task.status.value}，不可恢复",
                    detail={"task_id": task_id, "state": task.status.value},
                )
            if await is_cancel_requested(task_id):
                # 取消旗的终态收敛（协作式取消闭环的兜底边）：活跃 run 已死、
                # 旗无人消费时，下一次 resume 尝试负责如实收敛，绝不静默放跑。
                try:
                    task.transition(TaskStatus.CANCELLED)
                    task.last_error = "用户取消（resume 前观察到取消旗）"
                    task.last_error_type = "user_cancelled"
                    await db.commit()
                    # C3a：取消收敛——未完成 call 按取消语义补写合成终态。
                    await tool_ledger.synthesize_terminal(task_id, reason="cancel")
                except ValueError:
                    # 当前状态不可转 cancelled（理论不可达：上面的非终态都已放开边）
                    pass
                await release_task(task_id)
                # 终态收敛同样清转向缓冲（本路径绕过 mark_terminal 的清理）。
                await steering.clear_steers(task_id)
                raise TaskNotResumableError(
                    "任务已被取消，不可恢复",
                    detail={"task_id": task_id, "state": task.status.value},
                )
            if task.status == TaskStatus.RUNNING:
                # 陈旧 RUNNING = 上次进程中断的可观测形态；先判"那次尝试已死"再接管。
                task.transition(TaskStatus.FAILED)
            if task.status != TaskStatus.RUNNING:
                task.transition(TaskStatus.RUNNING)
            task.retry_count += 1
            await db.commit()
            # C3a：resume 前收敛上一个 run 的遗留账目——已派发无结果 →
            # TOOL_OUTCOME_UNKNOWN（执行中失联）；已登记未派发 → TOOL_NOT_STARTED。
            await tool_ledger.synthesize_terminal(task_id, reason="interrupt")
            return task
    except Exception:
        await release_task(task_id)
        raise
