"""会话分叉 / seed / 谱系服务（issue #476 / #499）。

语义（与 issue 的边界规则逐条对应；也是本模块的判别性回归点）：

1. **边界 = seq <= boundary（含边界消息，issue #499）**：
   - 指定 ``message_id`` 时复制到该消息为止（USER/SYSTEM/ASSISTANT 均可）；
     USER 边界不再"在该 USER 之前"分叉——首个 USER 边界复制 1 条，而不是
     copied=0 的歧义；
   - ASSISTANT 边界仍须是该轮次的最后一条 assistant（中间消息 409）；
   - 边界消息 ``meta_data`` 带 ``tool_status=pending`` 或 ``status=pending``
     → 400（未结算的流式/工具占位消息不能作为切点）；
   - 指定消息不存在/不属于源会话 → 404。
   - 默认边界 = 最后一条已结算消息：跳过尾部未答复的 USER（不报错、不复制）。
2. **活跃任务**：源会话存在非终态任务（PENDING/RUNNING/WAITING_*）时，消息级
   热分叉被禁止；必须显式 ``include_checkpoint`` 且存在可用（已结算）checkpoint，
   否则 409。PAUSED（历史 fork 克隆产物）不算"运行中"。
3. **checkpoint 克隆**：最近一个已结算 checkpoint 深拷为新 ``AgentTask``：新 id、
   绑定 child session、状态 ``PENDING``（issue #499 契约；可直接 resume）、
   ``run.fork_origin`` 记录来源（避免"伪历史"）；``run.pending_approval`` 或
   执行中工具（WAITING_TOOL）一律拒绝。克隆任务使用全新 task id → replay
   命名空间 `agent:replay:<new_id>` 为空，母任务 replay 产物不会被复制。
4. **上下文隔离**：消息复制为新 id + 子会话内重新编号的 seq；父会话行/消息不变；
   子会话使用新的 ``session:<id>`` 记忆命名空间，默认不复制母记忆；子会话
   settings 写入 ``history_limit=200``（issue #499：fork 子会话加载 200 条历史）。
5. **seed**：可选的种子文本写入子命名空间（reflective insight，尽力落 Redis），
   供子会话首轮召回。记忆写入失败不使 fork 失败（消息/谱系是硬保证）。
6. **租户**：访问作用域复用 ``app.services.session_service``（org + user 口径，
   超管旁路/历史 NULL 组织一致）；越权/跨组织一律 404（不 403，避免枚举）。
7. **幂等（issue #499）**：body ``idempotency_key`` 优先、``Idempotency-Key`` 头
   兜底。三层防线：
   - DB 唯一约束 ``(user_id, idempotency_key)``（迁移 016）——并发双建的最终
     事实，插入冲突方回滚后读回既有子会话；
   - Redis ``fork:idem:{user_id}:{key}`` ``SET NX EX 86400``（值为子 session_id）
     ——快路径去重；Redis 不可用时降级为 DB 约束（不阻断 fork）；
   - 同一用户 + 同一 key 的重放返回同一子会话（含同一克隆任务 id），不重复复制。
     首个请求尚未落库时，重复请求短暂轮询后 409 ``FORK_IN_PROGRESS``（可重试）。
8. **配额**：单次 fork 复制消息上限 ``FORK_MAX_COPIED_MESSAGES``（2000），
   超限 400 ``VALIDATION_ERROR``（detail 含 messages/limit）。

变异点（评审/回归重点）：
- 把 USER 边界当作"不包含该消息" → USER 边界 copied/seq 断言红；
- 删掉 assistant 中间消息检查 → 中间消息用例红；
- 默认边界不跳过尾部 USER → 默认用例红；
- 复制复用源消息 id / seq 不重排 → 唯一约束冲突/断言红；
- seed 写进父命名空间或复制父记忆 → 隔离用例红；
- 访问校验漏掉 org/user → 跨组织用例红（应为 404）；
- 克隆复用源 task id / 不改 session_id / 浅拷贝 → 克隆用例红；
- 去掉 DB 唯一约束 → 并发同 key 用例建出两个子会话（必红）；
- 克隆任务状态写成 PAUSED → PENDING 契约用例红。
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.agent import checkpoint
from app.agent.memory_bridge import AgentMemoryBridge
from app.core.log_safety import log_safe
from app.core.redis import redis_client
from app.exceptions import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from app.models.agent_task import AgentTask, TaskStatus
from app.models.artifact import AgentArtifact
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType
from app.services import llm_settings_service
from app.services.artifact_service import artifact_summary
from app.services.session_service import (
    SessionAccessScope,
    session_access_conditions,
    session_access_org_conditions,
)

logger = logging.getLogger(__name__)

# 单次 fork 允许复制的消息上限（issue §3 隐含需求：配额与存储增长提示）。
FORK_MAX_COPIED_MESSAGES = 2000
# seed 文本上限（防止把超大文本灌进记忆/设置）。
MAX_SEED_CHARS = 4000
# fork 子会话的历史加载上限（issue #499：普通会话默认 20，fork 子会话 200）。
FORK_CHILD_HISTORY_LIMIT = 200

# fork 幂等（issue #499）：Redis 快路径 + DB 唯一约束兜底。
IDEMPOTENCY_REDIS_PREFIX = "fork:idem:"
IDEMPOTENCY_TTL_SECONDS = 86_400
# 首个同 key 请求尚未落库时，重复请求的最长等待（轮询间隔见下）。
# 超时按 409 FORK_IN_PROGRESS 返回（可重试），绝不并发建第二个子会话。
IDEMPOTENCY_WAIT_SECONDS = 2.0
IDEMPOTENCY_POLL_INTERVAL_SECONDS = 0.05

DEFAULT_FORK_REASON = "user_fork"
_TITLE_MAX = 200
_REASON_MAX = 100
# 谱系遍历深度上限：防御环状数据（损坏数据不得让 lineage 无限循环）。
_MAX_LINEAGE_DEPTH = 50
# 克隆时候选的最近任务行数上限（只挑可解析的已结算快照）。
_CHECKPOINT_CANDIDATE_LIMIT = 20

# ── 会话树导航（批次八）────────────────────────────────────────────────────
# 分支标签长度与 DB/API 校验一致（超长 400，不静默截断）。
BRANCH_LABEL_MAX_CHARS = 100
# 摘要正文上限（fake 抽取式与 LLM 结果都按此截断，避免把巨量文本写进会话行）。
BRANCH_SUMMARY_MAX_CHARS = 2000
# fake 模式抽取上限：足够展示要点，同时保证同一消息序列输出逐字稳定。
BRANCH_SUMMARY_EXTRACT_CHARS = 280
# 摘要支持的模式：fake（确定性抽取，测试/离线）与 llm（不可用必须显式失败）。
BRANCH_SUMMARY_MODES = ("fake", "llm")
# LLM 摘要超时：模型不可用时端点必须快速返回可判定的失败，而不是悬挂。
SUMMARY_LLM_TIMEOUT_SECONDS = 30.0
# 树遍历深度上限：损坏数据（环/超长链）不得让接口无限循环。
_MAX_TREE_DEPTH = 50

MAX_TREE_BRANCHES = 500
"""单次树响应最多返回的分支数；超出时置 ``branches_truncated``。"""

MAX_BRANCH_ARTIFACTS = 200
"""分支上下文最多返回的工件行数；面板只展示计数，超出时置 ``artifacts_truncated``。"""

# "源会话有运行中任务"的判定集合：PAUSED（fork 克隆产物）刻意不在其中；
# FAILED/CANCELLED 等终态也不在——它们不是正在跑的 run。
_ACTIVE_TASK_STATUSES = (
    TaskStatus.PENDING,
    TaskStatus.RUNNING,
    TaskStatus.WAITING_TOOL,
    TaskStatus.WAITING_APPROVAL,
    TaskStatus.WAITING_PLAN_APPROVAL,
    TaskStatus.WAITING_HUMAN,
)
# 不允许作为克隆来源的状态：待审批 / 执行中工具（issue 明文拒绝）。
_UNSETTLED_TASK_STATUSES = (
    TaskStatus.WAITING_TOOL,
    TaskStatus.WAITING_APPROVAL,
    TaskStatus.WAITING_PLAN_APPROVAL,
)


# ── 领域异常（AppError 子类：端点据此映射 404/409）─────────────────────────


class ForkBoundaryInvalidError(ConflictError):
    """ASSISTANT 中间消息不能作为边界（轮次终态校验失败）。"""

    error_code = "FORK_BOUNDARY_INVALID"
    message = "分叉边界非法：ASSISTANT 边界必须是该轮次的最终消息"


class ForkQuotaExceededError(ValidationError):
    """复制消息数超过上限（issue #499：400 VALIDATION_ERROR，detail 带 messages/limit）。"""

    error_code = "VALIDATION_ERROR"
    message = "分叉复制的消息数超出上限"


class ForkCheckpointRequiredError(ConflictError):
    """源会话有运行中任务，但请求没有走 checkpoint 克隆。"""

    error_code = "FORK_CHECKPOINT_REQUIRED"
    message = "源会话有运行中的任务，只能从最后一个已提交 checkpoint 分叉"


class ForkCheckpointUnavailableError(ConflictError):
    """没有可用的已结算 checkpoint（不存在/损坏/待审批/执行中工具）。"""

    error_code = "FORK_CHECKPOINT_UNAVAILABLE"
    message = "没有可用的已结算 checkpoint（不存在、待审批或执行中工具）"


class ForkInProgressError(ConflictError):
    """同幂等键的首个请求尚未落库（短暂等待后仍不可见）。"""

    error_code = "FORK_IN_PROGRESS"
    message = "相同幂等键的分叉请求正在处理中，请稍后重试"


class ForkIdempotencyConflictError(ConflictError):
    """幂等键被其他组织/不可见的会话占用（唯一约束冲突但不可重放）。"""

    error_code = "FORK_IDEMPOTENCY_CONFLICT"
    message = "该幂等键已被其他会话占用，请更换幂等键后重试"


class BranchSummaryUnavailableError(ServiceUnavailableError):
    """请求 LLM 摘要但模型不可用/调用失败（503，绝不降级成 fake 假成功）。"""

    error_code = "SUMMARY_MODEL_UNAVAILABLE"
    message = "摘要模型不可用，未生成摘要"


@dataclass
class ForkResult:
    """一次 fork 的结果（端点据此组响应；idempotent=True 表示重放）。"""

    session: ChatSession
    copied_message_count: int
    boundary_seq: int
    fork_point_message_id: str | None
    checkpoint_task_id: str | None
    is_seeded: bool
    idempotent: bool


# ── 内部辅助 ────────────────────────────────────────────────────────────────


def _resolve_scope(
    scope: SessionAccessScope | None, organization_id: int | None
) -> SessionAccessScope:
    """直接调用（单测/内部）未传 scope 时，按 organization_id 构造同口径作用域。"""
    if scope is not None:
        return scope
    return SessionAccessScope(bypass_org=False, org_id=organization_id)


async def _load_source(
    db: AsyncSession, source_id: str, user_id: int, scope: SessionAccessScope
) -> ChatSession:
    """加载调用者可见的源会话；不存在/他人/跨组织一律 404（不泄露归属）。

    复用 session_service 的 ``session_access_conditions``（id + user + org 口径；
    超管旁路 / 历史 NULL 组织 / 无组织 fail-closed 与 /chat、/agent 完全一致）。
    """
    stmt = select(ChatSession).where(*session_access_conditions(source_id, user_id, scope))
    source = (await db.execute(stmt)).scalar_one_or_none()
    if source is None:
        raise NotFoundError("会话不存在")
    return source


async def _load_messages(db: AsyncSession, session_id: str) -> list[ChatMessage]:
    """按会话内序号加载全部消息（seq 由迁移回填/写入路径保证单调）。"""
    rows = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.seq.asc(), ChatMessage.created_at.asc(), ChatMessage.id.asc())
    )
    return list(rows.scalars().all())


def _message_meta(msg: ChatMessage) -> dict[str, Any]:
    """消息 ``meta_data`` 的安全字典视图。

    仓库自身的写路径既有 JSON dict 也有 ``json.dumps`` 字符串（例如 demo 种子）；
    两种形态都要识别，损坏的字符串按"无元数据"处理（不炸、不误判）。
    """
    meta = msg.meta_data
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (TypeError, ValueError):
            return {}
    return meta if isinstance(meta, dict) else {}


def _ensure_boundary_settled(msg: ChatMessage) -> None:
    """边界消息不得是未结算的占位消息（issue #499：pending → 400）。

    约定：流式/工具执行中的消息在 meta_data 里标记 ``tool_status=pending`` 或
    ``status=pending``；这类切点会复制出"半条轮次"，必须 400 拒绝。
    """
    meta = _message_meta(msg)
    if meta.get("tool_status") == "pending" or meta.get("status") == "pending":
        raise ValidationError(
            "边界消息仍在处理中（pending），不能作为分叉边界",
            detail={
                "message_id": msg.id,
                "tool_status": meta.get("tool_status"),
                "status": meta.get("status"),
            },
        )


def _resolve_prefix(
    messages: list[ChatMessage], message_id: str | None
) -> tuple[list[ChatMessage], ChatMessage | None]:
    """把边界解析为"可复制的消息前缀"（issue #499：``seq <= boundary`` 含边界）。

    Returns:
        (prefix, fork_point) —— prefix 含边界消息本身；空前缀时 fork_point 为 None。
        USER/SYSTEM 边界与 ASSISTANT 边界同等对待（含边界），不再有"在该 USER
        之前分叉"的歧义。ASSISTANT 边界仍须是该轮次最后一条 assistant。
    """
    if message_id:
        target_idx = next((i for i, m in enumerate(messages) if m.id == message_id), None)
        if target_idx is None:
            raise NotFoundError("边界消息不存在或不属于该会话")
        target = messages[target_idx]
        _ensure_boundary_settled(target)

        if target.message_type == MessageType.ASSISTANT:
            # 同一轮内 assistant 后面还有 assistant = 中间消息，不是轮次终态。
            cursor = target_idx + 1
            while cursor < len(messages) and messages[cursor].message_type != MessageType.USER:
                if messages[cursor].message_type == MessageType.ASSISTANT:
                    raise ForkBoundaryInvalidError(
                        "该 ASSISTANT 消息不是轮次最终消息（后面还有 assistant），不能作为分叉边界",
                        detail={"message_id": message_id},
                    )
                cursor += 1
        return messages[: target_idx + 1], target

    # 默认边界 = 最后一条已结算消息（跳过尾部未答复的 USER/SYSTEM 尾巴）。
    last_assistant_idx = None
    for idx, msg in enumerate(messages):
        if msg.message_type == MessageType.ASSISTANT:
            last_assistant_idx = idx
    if last_assistant_idx is None:
        return [], None
    # 默认边界同样要过 pending 校验（尾部的最后一条 assistant 可能就是流式占位）。
    _ensure_boundary_settled(messages[last_assistant_idx])
    return messages[: last_assistant_idx + 1], messages[last_assistant_idx]


async def _load_active_tasks(
    db: AsyncSession, source: ChatSession, user_id: int
) -> list[AgentTask]:
    """源会话的"运行中任务"（含待审批/执行中工具；不含 PAUSED/终态）。

    查询 defer ``context_snapshot``（#278/R1 毒丸防线）：本函数只需要任务行的
    状态/id，损坏的快照列不得把"判定是否有运行中任务"整条链路炸成 500。

    issue #499：fork 克隆任务落 PENDING（契约），但它不是"正在跑的 run"——
    快照 ``run.fork_origin`` 是克隆标记，这类 PENDING 必须排除，否则子会话会
    被克隆产物挡住、无法再普通分叉。真正的排队 PENDING（无克隆标记）仍算活跃。
    克隆标记需要读快照，按毒丸纪律单独容错读取（坏快照按"非克隆"保守处理）。
    """
    conditions = [
        AgentTask.session_id == source.id,
        AgentTask.user_id == user_id,
        AgentTask.status.in_(_ACTIVE_TASK_STATUSES),
    ]
    if source.organization_id is not None:
        # 任务 org 与会话 org 不一致说明是异常数据/跨组织运行；不据此阻止 fork。
        # 历史 session_id 为空的任务本就匹配不到（列不可查询，见 ADR）。
        conditions.append(AgentTask.organization_id == source.organization_id)
    rows = await db.execute(
        select(AgentTask).where(*conditions).options(defer(AgentTask.context_snapshot))
    )
    active: list[AgentTask] = []
    for task in rows.scalars().all():
        if task.status == TaskStatus.PENDING:
            envelope = checkpoint.parse_snapshot(await _load_snapshot_raw(db, task.id))
            run_state = envelope.get("run") if isinstance(envelope, dict) else None
            if isinstance(run_state, dict) and isinstance(run_state.get("fork_origin"), dict):
                continue  # fork 克隆的 PENDING 不是运行中的 run
        active.append(task)
    return active


async def _load_snapshot_raw(db: AsyncSession, task_id: str) -> Any | None:
    """单独容错读取快照列（#278/R1）：反序列化失败按"无快照"降级。"""
    try:
        cursor = await db.execute(select(AgentTask.context_snapshot).where(AgentTask.id == task_id))
        return cursor.scalar_one_or_none()
    except (TypeError, ValueError) as e:
        logger.warning(
            "checkpoint 快照列反序列化失败（task=%s，fork 候选）：按无快照跳过：%s",
            log_safe(task_id),
            log_safe(e),
        )
        return None


async def _latest_settled_checkpoint(
    db: AsyncSession, source: ChatSession, user_id: int, *, child_org: int | None
) -> tuple[AgentTask, dict[str, Any]] | None:
    """最近的可用 checkpoint（任务行 + 已校验信封）：可解析、无待审批、非执行中工具。

    候选任务必须与 child org 精确一致（NULL 也不例外）。母会话的历史 NULL org
    只决定子会话如何继承调用方组织，绝不能放宽 checkpoint 的租户边界。

    不把快照历史映射回消息 seq 的原因：checkpoint 的 history 是"运行时实际消费
    的消息窗口"，没有消息 id/seq；把它当作消息边界会伪造对应关系。克隆的语义是
    "从母 run 的最后已提交断点继续"，与消息前缀各自独立——来源由
    ``run.fork_origin`` 如实记录。

    快照列用 defer + 单独容错读取（#278/R1）；deferred 属性在 async 会话里同步
    访问会触发同步 IO（MissingGreenlet），所以信封必须随返回值一起带出，克隆方
    绝不回读 ORM 属性。
    """
    conditions = [
        AgentTask.session_id == source.id,
        AgentTask.user_id == user_id,
        AgentTask.organization_id == child_org,
        AgentTask.context_snapshot.is_not(None),
        # 先在 SQL 里排除"明显不可克隆"的状态，再按最近顺序取候选：最近 20 个
        # 任务不能因为全是待审批/执行中就把更早的可用 checkpoint 挡掉。
        AgentTask.status.not_in(_UNSETTLED_TASK_STATUSES),
    ]
    rows = await db.execute(
        select(AgentTask)
        .where(*conditions)
        .options(defer(AgentTask.context_snapshot))
        .order_by(AgentTask.updated_at.desc(), AgentTask.created_at.desc())
        .limit(_CHECKPOINT_CANDIDATE_LIMIT)
    )
    for task in rows.scalars().all():
        raw_snapshot = await _load_snapshot_raw(db, task.id)
        envelope = checkpoint.parse_snapshot(raw_snapshot)
        if envelope is None:
            continue
        run_state = envelope.get("run")
        if isinstance(run_state, dict) and isinstance(run_state.get("pending_approval"), dict):
            continue
        # 计划审批同样是"待人类决定"：状态可能已被取消收敛（CANCELLED），
        # 但快照里的 pending_plan_approval 仍是过期审批门，克隆后 resume 会重挂。
        if isinstance(run_state, dict) and isinstance(run_state.get("pending_plan_approval"), dict):
            continue
        return task, envelope
    return None


def _clone_snapshot(
    source_task: AgentTask,
    source_snapshot: dict[str, Any],
    *,
    cloned_task_id: str,
    child_id: str,
    source_id: str,
    fork_point_message_id: str | None,
    user_id: int,
) -> dict[str, Any]:
    """深拷已解析的快照信封并改写会话/任务绑定 + 写入来源。

    绝不与母任务共享嵌套对象；也绝不回读 ``source_task.context_snapshot``
    （deferred 属性在 async 会话里同步访问会触发 MissingGreenlet）。
    """
    snapshot = copy.deepcopy(source_snapshot)
    snapshot["session_id"] = child_id
    # 克隆任务拥有全新的 replay 命名空间：快照里的 ctx.task_id 一并改写为克隆 id，
    # 让快照在首次 resume 落盘前也不会"自称"母任务（诊断/来源不误导）。
    ctx = snapshot.get("ctx")
    if isinstance(ctx, dict):
        ctx["task_id"] = cloned_task_id
        if snapshot.get("answer_contract") == "single_final/v1":
            artifacts = ctx.get("artifacts")
            if isinstance(artifacts, dict):
                artifacts.pop("review", None)
                artifacts.pop("final_synthesis", None)
    if (
        snapshot.get("answer_contract") == "single_final/v1"
        and snapshot.get("final_phase") != "collecting"
    ):
        # Evidence and final input survive a fork; the parent's draft/delivery
        # never does.  The child re-enters synthesis without replaying tools.
        snapshot["final_phase"] = "synthesis_pending"
        snapshot["partial_output"] = ""
        for key in ("final_answer", "final_draft", "final_messages"):
            snapshot.pop(key, None)
    run_state = snapshot.get("run")
    if not isinstance(run_state, dict):
        run_state = {}
    run_state["fork_origin"] = {
        "source_task_id": source_task.id,
        "source_session_id": source_id,
        "fork_point_message_id": fork_point_message_id,
        "forked_by": user_id,
        "forked_at": datetime.utcnow().isoformat() + "Z",
    }
    snapshot["run"] = run_state
    snapshot["updated_by"] = "fork_clone"
    return snapshot


def _idempotency_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _idempotency_redis_key(user_id: int, key: str) -> str:
    return f"{IDEMPOTENCY_REDIS_PREFIX}{user_id}:{key}"


async def _find_idempotent_child(
    db: AsyncSession,
    *,
    user_id: int,
    scope: SessionAccessScope,
    key: str,
) -> ChatSession | None:
    """按 ``(user_id, idempotency_key)`` 找已落库的子会话（DB 唯一事实）。

    唯一约束是用户全局的（与迁移 016 一致），不再按源会话过滤：同一用户复用
    同一幂等键必须拿到同一个结果。组织可见范围仍参与过滤——幂等键不得成为
    跨组织读取的旁路；若键属于其他组织，调用方按"未找到"处理（最终由唯一
    约束拒绝重复创建）。
    """
    conditions = [
        ChatSession.user_id == user_id,
        ChatSession.idempotency_key == key,
        *session_access_org_conditions(scope),
    ]
    rows = await db.execute(
        select(ChatSession)
        .where(*conditions)
        .order_by(ChatSession.created_at.asc(), ChatSession.id.asc())
        .limit(1)
    )
    return rows.scalars().first()


async def _idempotency_key_row_exists(db: AsyncSession, *, user_id: int, key: str) -> bool:
    """幂等键是否已被任何行占用（**不看**组织可见范围）。

    仅用于并发/租户冲突判定：调用方据此决定能否释放 Redis 抢占、以及如何把
    IntegrityError 映射为明确的 409。返回行 id 本身不用于读取/回放数据。
    """
    result = await db.execute(
        select(ChatSession.id)
        .where(
            ChatSession.user_id == user_id,
            ChatSession.idempotency_key == key,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def _replay_result(existing: ChatSession) -> ForkResult:
    """把既有子会话投影为幂等重放结果（字段全部来自落库事实）。"""
    meta = _fork_meta(existing)
    return ForkResult(
        session=existing,
        copied_message_count=int(meta.get("copied_message_count") or 0),
        boundary_seq=int(meta.get("boundary_seq") or 0),
        fork_point_message_id=existing.fork_point_message_id,
        checkpoint_task_id=meta.get("checkpoint_task_id"),
        is_seeded=bool(meta.get("is_seeded")),
        idempotent=True,
    )


async def _claim_idempotency(user_id: int, key: str, child_id: str) -> tuple[bool, str | None]:
    """Redis ``SET NX EX`` 抢占幂等键。

    Returns:
        (True, None)：本请求获得创建权（或 Redis 不可用，降级为 DB 唯一约束兜底）；
        (False, holder)：已有请求持有该键（holder = 其子会话 id，可能尚未落库）。
    """
    try:
        if not redis_client:
            return True, None
    except Exception:  # noqa: BLE001 — 代理层的任何异常都按"Redis 不可用"降级
        return True, None
    try:
        redis_key = _idempotency_redis_key(user_id, key)
        claimed = await redis_client.set(redis_key, child_id, nx=True, ex=IDEMPOTENCY_TTL_SECONDS)
        if claimed:
            return True, None
        holder = await redis_client.get(redis_key)
        if isinstance(holder, bytes):
            holder = holder.decode("utf-8", errors="replace")
        return False, holder if isinstance(holder, str) and holder else None
    except Exception as e:  # noqa: BLE001 — Redis 抖动不得造成重复分叉（DB 唯一约束兜底）
        logger.warning("fork 幂等 Redis 抢占失败，降级为 DB 唯一约束: %s", log_safe(e))
        return True, None


async def _remember_idempotency(user_id: int, key: str, child_id: str) -> None:
    """把幂等键指向最终子会话（Redis 不可用时静默降级，DB 仍是事实源）。"""
    try:
        if not redis_client:
            return
        await redis_client.set(
            _idempotency_redis_key(user_id, key),
            child_id,
            ex=IDEMPOTENCY_TTL_SECONDS,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("fork 幂等键回写 Redis 失败（DB 事实已落库）: %s", log_safe(e))


async def _release_idempotency_claim(user_id: int, key: str, child_id: str) -> None:
    """创建失败时释放本请求的 Redis 抢占（只删自己的值，绝不误删他人）。"""
    try:
        if not redis_client:
            return
        redis_key = _idempotency_redis_key(user_id, key)
        current = await redis_client.get(redis_key)
        if isinstance(current, bytes):
            current = current.decode("utf-8", errors="replace")
        if current == child_id:
            await redis_client.delete(redis_key)
    except Exception as e:  # noqa: BLE001
        logger.debug("fork 幂等键释放失败（TTL 会自清）: %s", log_safe(e))


async def _wait_for_idempotent_child(
    db: AsyncSession,
    *,
    user_id: int,
    scope: SessionAccessScope,
    key: str,
) -> ChatSession | None:
    """短时轮询：等首个同 key 请求提交后读回其子会话（每轮开新事务看最新快照）。"""
    deadline = time.monotonic() + IDEMPOTENCY_WAIT_SECONDS
    while True:
        # 结束上一轮只读事务（SQLite/MySQL 的可重复读快照必须换事务才能看到新提交）。
        await db.rollback()
        existing = await _find_idempotent_child(db, user_id=user_id, scope=scope, key=key)
        if existing is not None:
            return existing
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(IDEMPOTENCY_POLL_INTERVAL_SECONDS)


def _fork_meta(child: ChatSession | dict[str, Any]) -> dict[str, Any]:
    settings_json = child.settings if isinstance(child, ChatSession) else child
    if not isinstance(settings_json, dict):
        return {}
    fork_meta = settings_json.get("fork")
    return fork_meta if isinstance(fork_meta, dict) else {}


async def _write_seed_memory(
    *,
    child_id: str,
    user_id: int,
    organization_id: int | None,
    seed: str,
    source_id: str,
    fork_point_message_id: str | None,
) -> bool:
    """把 seed 写入子会话命名空间（`session:<child_id>`）。

    Returns: True = 进程内记忆已写入（Redis 落盘由 memory_service 尽力而为）；
    False = 写入失败（调用方必须如实下调 is_seeded，不得声称已 seed）。

    组织为 None 时 **不应调用**：``AgentMemoryBridge`` 按 A2 纪律 fail-closed
    拒绝无组织命名空间（调用方在 fork 主流程里已把 seed_applied 置 False）。
    """
    try:
        bridge = AgentMemoryBridge(
            session_id=child_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        await bridge.record_insight(
            seed,
            context={
                "source": "session_fork_seed",
                "parent_session_id": source_id,
                "fork_point_message_id": fork_point_message_id,
            },
        )
        return True
    except Exception as e:  # noqa: BLE001 — 记忆是尽力而为，绝不使 fork 失败
        logger.warning("fork seed 写入记忆失败（fork 已成功）: %s", log_safe(e))
        return False


# ── 主入口：fork ────────────────────────────────────────────────────────────


async def fork_session(
    db: AsyncSession,
    *,
    source_id: str,
    user_id: int,
    organization_id: int | None = None,
    scope: SessionAccessScope | None = None,
    message_id: str | None = None,
    title: str | None = None,
    include_checkpoint: bool = False,
    seed: str | None = None,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> ForkResult:
    """从 source 会话的 ``seq <= boundary`` 前缀分叉出子会话（含边界消息）。

    Raises:
        NotFoundError: 源会话/边界消息不存在、他人或跨组织（统一 404）。
        ValidationError: seed 超长 / 边界消息 pending / 复制数超配额（400）。
        ForkBoundaryInvalidError / ForkCheckpointRequiredError /
        ForkCheckpointUnavailableError / ForkInProgressError: 409 语义。
    """
    scope = _resolve_scope(scope, organization_id)
    if seed is not None and len(seed) > MAX_SEED_CHARS:
        raise ValidationError(f"种子内容过长（上限 {MAX_SEED_CHARS} 字符）")
    idem_key = idempotency_key.strip() if isinstance(idempotency_key, str) else None
    idem_key = idem_key or None
    if idem_key is not None and len(idem_key) > 128:
        raise ValidationError("幂等键过长（上限 128 字符）")

    source = await _load_source(db, source_id, user_id, scope)
    child_org = source.organization_id if source.organization_id is not None else organization_id

    key_hash = _idempotency_hash(idem_key) if idem_key else None
    if idem_key:
        # 顺序重试的快路径：DB 是幂等事实源（Redis 只是加速/并发闸门）。
        existing = await _find_idempotent_child(db, user_id=user_id, scope=scope, key=idem_key)
        if existing is not None:
            return _replay_result(existing)

    child_id = str(uuid.uuid4())
    if idem_key:
        claimed, holder = await _claim_idempotency(user_id, idem_key, child_id)
        if not claimed:
            # 首个同 key 请求尚未落库：短暂轮询后返回同一结果，超时 409（可重试）。
            existing = await _wait_for_idempotent_child(
                db, user_id=user_id, scope=scope, key=idem_key
            )
            if existing is not None:
                await _remember_idempotency(user_id, idem_key, existing.id)
                return _replay_result(existing)
            # 超时仍不可见：释放**本次观察到的**抢占值，避免首个请求崩溃/超时后
            # 幂等键被锁死 24h（DB 唯一约束仍是并发双建的事实防线）。
            # 但若该键已被某个不可见（其他组织）的既有行占用，不得删除它的有效
            # 抢占值，也不该谎报"稍后重试"（它是永久冲突，重试不会成功）。
            if holder:
                await db.rollback()
                if await _idempotency_key_row_exists(db, user_id=user_id, key=idem_key):
                    raise ForkIdempotencyConflictError(
                        "该幂等键已被其他会话占用，请更换幂等键后重试",
                        detail={"idempotency_key": idem_key},
                    )
                await _release_idempotency_claim(user_id, idem_key, holder)
            raise ForkInProgressError(
                "相同幂等键的分叉请求正在处理中，请稍后重试",
                detail={
                    "idempotency_key": idem_key,
                    "holder_session_id": holder,
                    "retry_after_seconds": int(IDEMPOTENCY_WAIT_SECONDS) or 1,
                },
            )

    try:
        messages = await _load_messages(db, source.id)
        prefix, fork_point = _resolve_prefix(messages, message_id)
        if len(prefix) > FORK_MAX_COPIED_MESSAGES:
            raise ForkQuotaExceededError(
                f"分叉复制的消息数超出上限（{FORK_MAX_COPIED_MESSAGES} 条）",
                detail={"messages": len(prefix), "limit": FORK_MAX_COPIED_MESSAGES},
            )

        active_tasks = await _load_active_tasks(db, source, user_id)
        if active_tasks and not include_checkpoint:
            raise ForkCheckpointRequiredError(
                "源会话有运行中的任务，只能从最后一个已提交 checkpoint 分叉"
                "（请传 include_checkpoint=true）",
                detail={"active_task_ids": [t.id for t in active_tasks]},
            )

        checkpoint_task: AgentTask | None = None
        checkpoint_snapshot: dict[str, Any] | None = None
        if include_checkpoint:
            picked = await _latest_settled_checkpoint(db, source, user_id, child_org=child_org)
            if picked is None:
                raise ForkCheckpointUnavailableError(
                    "没有可用的已结算 checkpoint（不存在、损坏、待审批或执行中工具）"
                )
            checkpoint_task, checkpoint_snapshot = picked
            # 候选查询之外，插入克隆前再核对实际源任务，禁止任何内部旁路重标 org。
            if checkpoint_task.organization_id != child_org:
                raise ForkCheckpointUnavailableError(
                    "没有可用的已结算 checkpoint（不存在、损坏、待审批或执行中工具）"
                )

        fork_point_id = fork_point.id if fork_point is not None else None
        resolved_title = (
            title
            if title is not None and title.strip()
            else f"{source.title or '未命名会话'} (fork)"
        )[:_TITLE_MAX]
        resolved_reason = (reason or DEFAULT_FORK_REASON)[:_REASON_MAX]
        seed_requested = bool(seed and seed.strip())
        # 无组织上下文无法建立记忆命名空间（memory_service fail-closed 拒绝 None/0）：
        # seed 如实标记为未生效，而不是"响应说 seed 了、记忆里没有"。
        is_seeded = seed_requested and child_org is not None
        cloned_task_id = uuid.uuid4().hex[:12] if checkpoint_task is not None else None
        # 响应字段一律用局部变量（提交后不回读 ORM 属性，避免过期实例触发
        # 同步懒加载 → MissingGreenlet）。issue #499：boundary_seq 即边界消息 seq。
        boundary_seq = fork_point.seq if fork_point is not None else 0
        copied_count = len(prefix)

        parent_settings = source.settings if isinstance(source.settings, dict) else {}
        child_settings = copy.deepcopy(parent_settings)
        if not isinstance(child_settings, dict):
            child_settings = {}
        # issue #499：fork 子会话加载 200 条历史（普通会话默认 20）。
        child_settings["history_limit"] = FORK_CHILD_HISTORY_LIMIT
        now = datetime.utcnow()
        child_settings["fork"] = {
            "parent_session_id": source.id,
            "root_session_id": source.root_session_id or source.id,
            "boundary_message_id": message_id,
            "fork_point_message_id": fork_point_id,
            "boundary_seq": boundary_seq,
            "copied_message_count": copied_count,
            "checkpoint_cloned": checkpoint_task is not None,
            # 幂等重放必须返回**克隆任务** id（不是母任务 id）：否则重试拿到的响应
            # 会让客户端去 resume 母 run，把收尾答案写回母会话（伪历史）。
            "checkpoint_task_id": cloned_task_id,
            "checkpoint_source_task_id": (
                checkpoint_task.id if checkpoint_task is not None else None
            ),
            "is_seeded": is_seeded,
            "seed_length": len(seed) if seed_requested and seed else 0,
            "seed_skipped_reason": (
                "no_organization" if seed_requested and not is_seeded else None
            ),
            "fork_reason": resolved_reason,
            "forked_at": now.isoformat() + "Z",
        }
        if key_hash:
            # 兼容/审计：settings 里保留 sha256（不落明文幂等键）。
            child_settings["fork"]["idempotency_key"] = key_hash

        # seed 在**提交前**写入（记忆写入不依赖 DB 事务）：
        # - 失败时把降级标记并进同一次 commit，彻底消除"第二次 commit 失败 →
        #   rollback 过期 ORM → MissingGreenlet → 已提交的 fork 变 500"的双故障路径；
        # - 若随后 DB commit 失败，最坏只留下一个孤儿记忆命名空间（全新 child id，
        #   永不复用），不会污染任何会话。
        if is_seeded and seed:
            if not await _write_seed_memory(
                child_id=child_id,
                user_id=user_id,
                organization_id=child_org,
                seed=seed,
                source_id=source.id,
                fork_point_message_id=fork_point_id,
            ):
                # 记忆写入失败：如实下调 is_seeded（响应与落库都不能声称已 seed）。
                is_seeded = False
                child_settings["fork"]["is_seeded"] = False
                child_settings["fork"]["seed_write_failed"] = True

        child = ChatSession(
            id=child_id,
            user_id=user_id,
            organization_id=child_org,
            title=resolved_title,
            status=ChatSessionStatus.ACTIVE,
            message_count=copied_count,
            last_message_at=prefix[-1].created_at if prefix else None,
            settings=child_settings,
            parent_session_id=source.id,
            root_session_id=source.root_session_id or source.id,
            fork_point_message_id=fork_point_id,
            fork_reason=resolved_reason,
            # issue #499：复制消息的 seq 重排为 1..N，计数器同步推进。
            next_message_seq=copied_count,
            idempotency_key=idem_key,
        )
    except Exception:
        # 抢占成功后的任何失败（404/400/409、DB 读错误等）都必须释放幂等键，
        # 否则同一键的重试会被一个未落库的抢占值挡住。
        if idem_key:
            await _release_idempotency_claim(user_id, idem_key, child_id)
        raise

    try:
        db.add(child)
        for new_seq, src_msg in enumerate(prefix, 1):
            copy_kwargs: dict[str, Any] = {}
            if src_msg.created_at is not None:
                copy_kwargs["created_at"] = src_msg.created_at
            db.add(
                ChatMessage(
                    id=str(uuid.uuid4()),
                    session_id=child_id,
                    seq=new_seq,
                    content=src_msg.content,
                    message_type=src_msg.message_type,
                    meta_data=copy.deepcopy(src_msg.meta_data),
                    feedback=src_msg.feedback or 0,
                    feedback_note=src_msg.feedback_note,
                    **copy_kwargs,
                )
            )

        if checkpoint_task is not None:
            db.add(
                AgentTask(
                    id=cloned_task_id,
                    user_id=user_id,
                    organization_id=child_org,
                    session_id=child_id,
                    # issue #499 契约：克隆任务是 PENDING（可直接 load_for_resume 续跑）。
                    status=TaskStatus.PENDING,
                    query=checkpoint_task.query,
                    plan_id=checkpoint_task.plan_id,
                    current_step_id=checkpoint_task.current_step_id,
                    context_snapshot=_clone_snapshot(
                        checkpoint_task,
                        checkpoint_snapshot,
                        cloned_task_id=cloned_task_id,
                        child_id=child_id,
                        source_id=source.id,
                        fork_point_message_id=fork_point_id,
                        user_id=user_id,
                    ),
                    completed_steps=checkpoint_task.completed_steps or 0,
                    total_steps=checkpoint_task.total_steps,
                    progress=checkpoint_task.progress or 0.0,
                    retry_count=0,
                    expires_at=datetime.utcnow() + timedelta(days=checkpoint.TASK_EXPIRES_DAYS),
                )
            )
        await db.commit()
    except IntegrityError:
        # 并发双建的最终防线：唯一约束 (user_id, idempotency_key) 让后到者失败。
        # 回滚后读回先到者并按其重放——绝不返回 500，也绝不建出第二个子会话。
        await db.rollback()
        existing = (
            await _find_idempotent_child(db, user_id=user_id, scope=scope, key=idem_key)
            if idem_key
            else None
        )
        if existing is None:
            if idem_key:
                # 唯一键可能被其他组织/当前作用域不可见的会话占用：不回放其数据
                # （租户边界），也不把 IntegrityError 漏成 500。
                invisible = await _idempotency_key_row_exists(db, user_id=user_id, key=idem_key)
                await _release_idempotency_claim(user_id, idem_key, child_id)
                if invisible:
                    raise ForkIdempotencyConflictError(
                        "该幂等键已被其他会话占用，请更换幂等键后重试",
                        detail={"idempotency_key": idem_key},
                    ) from None
            raise
        if idem_key:
            await _remember_idempotency(user_id, idem_key, existing.id)
        return _replay_result(existing)
    except Exception:
        await db.rollback()
        if idem_key:
            await _release_idempotency_claim(user_id, idem_key, child_id)
        raise

    if idem_key:
        # Redis 抢占时写入的 child_id 已是最终事实；Redis 降级时这里补记（尽力）。
        await _remember_idempotency(user_id, idem_key, child_id)

    return ForkResult(
        session=child,
        copied_message_count=copied_count,
        boundary_seq=boundary_seq,
        fork_point_message_id=fork_point_id,
        checkpoint_task_id=cloned_task_id,
        is_seeded=is_seeded,
        idempotent=False,
    )


# ── 谱系查询 ────────────────────────────────────────────────────────────────


def _lineage_node(session_obj: ChatSession) -> dict[str, Any]:
    return {
        "id": session_obj.id,
        "title": session_obj.title,
        "parent_session_id": session_obj.parent_session_id,
        "fork_point_message_id": session_obj.fork_point_message_id,
        "fork_reason": session_obj.fork_reason,
        "is_fork": session_obj.parent_session_id is not None,
        "created_at": session_obj.created_at.isoformat() if session_obj.created_at else "",
    }


async def get_lineage(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: int,
    organization_id: int | None = None,
    scope: SessionAccessScope | None = None,
) -> dict[str, Any]:
    """会话谱系：祖先链（根→父）+ 直接子级 + 谱系元数据。

    父会话被删除/归档后仍可查：祖先链在缺失节点处停止并如实标记
    ``parent_missing``/``lineage_truncated``，子会话行上的 parent/root/fork_point
    字段不依赖父行存在。
    """
    scope = _resolve_scope(scope, organization_id)
    session = await _load_source(db, session_id, user_id, scope)

    ancestors: list[dict[str, Any]] = []
    seen = {session.id}
    current = session
    parent_missing = False
    lineage_truncated = False
    for _ in range(_MAX_LINEAGE_DEPTH):
        parent_id = current.parent_session_id
        if not parent_id:
            break
        if parent_id in seen:
            lineage_truncated = True
            break
        parent = (
            await db.execute(
                select(ChatSession).where(*session_access_conditions(parent_id, user_id, scope))
            )
        ).scalar_one_or_none()
        if parent is None:
            if current.id == session.id:
                parent_missing = True
            lineage_truncated = True
            break
        ancestors.append(_lineage_node(parent))
        seen.add(parent_id)
        current = parent
    else:
        lineage_truncated = True
    ancestors.reverse()

    children_conditions = [
        ChatSession.parent_session_id == session.id,
        ChatSession.user_id == user_id,
        *session_access_org_conditions(scope),
    ]
    child_rows = list(
        (
            await db.execute(
                select(ChatSession)
                .where(*children_conditions)
                .order_by(ChatSession.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    child_counts: dict[str, int] = {}
    if child_rows:
        count_rows = await db.execute(
            select(ChatMessage.session_id, func.count(ChatMessage.id))
            .where(ChatMessage.session_id.in_([c.id for c in child_rows]))
            .group_by(ChatMessage.session_id)
        )
        child_counts = {sid: int(count) for sid, count in count_rows.all()}

    children = [
        {
            **_lineage_node(c),
            "message_count": child_counts.get(c.id, 0),
            "is_seeded": bool(_fork_meta(c).get("is_seeded")),
        }
        for c in child_rows
    ]

    fork_meta = _fork_meta(session)
    return {
        "session_id": session.id,
        "title": session.title,
        "root_session_id": session.root_session_id or session.id,
        "parent_session_id": session.parent_session_id,
        "fork_point_message_id": session.fork_point_message_id,
        "fork_reason": session.fork_reason,
        "is_fork": session.parent_session_id is not None,
        "is_seeded": bool(fork_meta.get("is_seeded")),
        "depth": len(ancestors),
        "ancestors": ancestors,
        "children": children,
        "parent_missing": parent_missing,
        "lineage_truncated": lineage_truncated,
    }


# ── 会话树导航（批次八）────────────────────────────────────────────────────


def _iso(value: datetime | None) -> str | None:
    """时间戳的统一序列化（None 保持 None）。"""
    return value.isoformat() if value is not None else None


def _tree_depth(
    row: ChatSession, by_id: dict[str, ChatSession], *, max_depth: int = _MAX_TREE_DEPTH
) -> int:
    """当前行在**可见节点集合**内的深度（根=0）；环/缺失父节点按截断处理。"""
    depth = 0
    seen = {row.id}
    cursor = row
    while cursor.parent_session_id and depth < max_depth:
        parent_id = cursor.parent_session_id
        if parent_id in seen:
            break  # 环状损坏数据：停止，不伪造深度
        seen.add(parent_id)
        parent = by_id.get(parent_id)
        if parent is None:
            break  # 父节点不可见/已删除：当前可见子树的深度从此处起算
        depth += 1
        cursor = parent
    return depth


async def _artifact_stats(
    db: AsyncSession, *, session_ids: list[str], user_id: int
) -> dict[str, tuple[int, int, int]]:
    """按会话聚合工件行数 / 最新版本号 / 版本号总和。

    只按 ``conversation_id`` + ``user_id`` 限定：会话可见性已由 tree 查询的
    org+user 口径决定；工件自身可能沿用调用者当前组织（历史 NULL 组织会话），
    再加组织条件反而会把本人历史工件藏掉。孤儿/他人会话的工件不会进入统计。
    """
    if not session_ids:
        return {}
    rows = await db.execute(
        select(
            AgentArtifact.conversation_id,
            func.count(AgentArtifact.id),
            func.max(AgentArtifact.version_no),
            func.sum(AgentArtifact.version_no),
        )
        .where(
            AgentArtifact.conversation_id.in_(session_ids),
            AgentArtifact.user_id == user_id,
            AgentArtifact.status == "ready",
        )
        .group_by(AgentArtifact.conversation_id)
    )
    return {
        conversation_id: (int(count or 0), int(latest or 0), int(total or 0))
        for conversation_id, count, latest, total in rows.all()
    }


def _branch_payload(
    row: ChatSession,
    *,
    current_id: str,
    root_id: str,
    depth: int,
    message_count: int,
    artifact_stats: tuple[int, int, int],
    fork_point_preview: str | None,
    parent_visible: bool,
) -> dict[str, Any]:
    count, latest, total = artifact_stats
    return {
        "session_id": row.id,
        "title": row.title,
        "parent_session_id": row.parent_session_id,
        "root_session_id": row.root_session_id or row.id,
        "fork_point_message_id": row.fork_point_message_id,
        "fork_point_preview": fork_point_preview,
        "fork_reason": row.fork_reason,
        "branch_label": row.branch_label,
        "branch_summary": row.branch_summary,
        "summary_model": row.summary_model,
        "summarized_at": _iso(row.summarized_at),
        "created_at": _iso(row.created_at) or "",
        "depth": depth,
        "is_root": row.id == root_id,
        "is_current": row.id == current_id,
        "parent_visible": parent_visible,
        "message_count": message_count,
        "artifact_count": count,
        "artifact_latest_version": latest,
        "artifact_version_total": total,
    }


async def get_session_tree(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: int,
    organization_id: int | None = None,
    scope: SessionAccessScope | None = None,
) -> dict[str, Any]:
    """返回会话所在分支树（根 + 全部可见后代），按 depth/创建时间稳定排序。

    - 访问口径复用 ``_load_source``（org+user；越权/跨组织 404，不泄露存在性）；
    - 树 = ``id == root`` 或 ``root_session_id == root`` 且调用者可见的会话；
    - 每行带 ``fork_point_message_id``、分支标签/摘要与**按分支**的工件统计
      （``agent_artifact`` 按 ``conversation_id`` 聚合，绝不跨分支串数）；
    - 根会话被删除/不可见时返回当前可见子树并置 ``tree_truncated=True``，
      绝不把不可见行伪装成树节点；
    - 只读：不写库、不创建会话、不触发任何 fork。
    """
    scope = _resolve_scope(scope, organization_id)
    current = await _load_source(db, session_id, user_id, scope)
    root_id = current.root_session_id or current.id

    conditions = [
        ChatSession.user_id == user_id,
        or_(ChatSession.id == root_id, ChatSession.root_session_id == root_id),
        *session_access_org_conditions(scope),
    ]
    rows = list(
        (
            await db.execute(
                select(ChatSession)
                .where(*conditions)
                .order_by(ChatSession.created_at.asc(), ChatSession.id.asc())
                .limit(MAX_TREE_BRANCHES + 1)
            )
        )
        .scalars()
        .all()
    )
    branches_truncated = len(rows) > MAX_TREE_BRANCHES
    if branches_truncated:
        rows = rows[:MAX_TREE_BRANCHES]
    # 当前会话必须可见且已被上面的条件命中；防御性兜底，避免空树。
    if not any(row.id == current.id for row in rows):
        if branches_truncated and rows:
            rows[-1] = current
        else:
            rows.append(current)

    by_id = {row.id: row for row in rows}
    ids = list(by_id)
    message_counts: dict[str, int] = {}
    if ids:
        count_rows = await db.execute(
            select(ChatMessage.session_id, func.count(ChatMessage.id))
            .where(ChatMessage.session_id.in_(ids))
            .group_by(ChatMessage.session_id)
        )
        message_counts = {sid: int(count) for sid, count in count_rows.all()}

    fork_message_ids = [row.fork_point_message_id for row in rows if row.fork_point_message_id]
    fork_previews: dict[str, str] = {}
    if fork_message_ids:
        # 只取前 120 字符（SQL 侧截断），并 join 会话表做 org/user 作用域，
        # 避免把不可见父会话的正文读进内存。
        preview_rows = await db.execute(
            select(ChatMessage.id, func.substr(ChatMessage.content, 1, 120))
            .join(ChatSession, ChatSession.id == ChatMessage.session_id)
            .where(
                ChatMessage.id.in_(fork_message_ids),
                ChatSession.user_id == user_id,
                *session_access_org_conditions(scope),
            )
        )
        for message_id, content in preview_rows.all():
            text = " ".join((content or "").split())
            if text:
                fork_previews[message_id] = text[:120]

    stats = await _artifact_stats(db, session_ids=ids, user_id=user_id)

    branches = [
        _branch_payload(
            row,
            current_id=current.id,
            root_id=root_id,
            depth=_tree_depth(row, by_id),
            message_count=message_counts.get(row.id, 0),
            artifact_stats=stats.get(row.id, (0, 0, 0)),
            fork_point_preview=fork_previews.get(row.fork_point_message_id or ""),
            parent_visible=(row.parent_session_id is None or row.parent_session_id in by_id),
        )
        for row in rows
    ]
    branches.sort(key=lambda node: (node["depth"], node["created_at"], node["session_id"]))

    root_present = root_id in by_id
    tree_truncated = (not root_present) or any(
        node["parent_session_id"] and not node["parent_visible"] for node in branches
    )
    return {
        "root_session_id": root_id,
        "current_session_id": current.id,
        "root_present": root_present,
        "branch_count": len(branches),
        "branches_truncated": branches_truncated,
        "tree_truncated": tree_truncated,
        "branches": branches,
    }


def _message_payload(msg: ChatMessage) -> dict[str, Any]:
    """分支消息投影：与 chat 详情同键（camelCase），前端原位加载可直接复用。"""
    meta = _message_meta(msg)
    sources = meta.get("sources")
    return {
        "id": msg.id,
        "seq": msg.seq,
        "content": msg.content,
        "messageType": msg.message_type.value,
        "createdAt": msg.created_at.isoformat() if msg.created_at else "",
        "sources": sources if isinstance(sources, list) else [],
        "feedback": msg.feedback or 0,
        "feedbackNote": msg.feedback_note,
    }


async def get_branch_context(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: int,
    organization_id: int | None = None,
    scope: SessionAccessScope | None = None,
) -> dict[str, Any]:
    """读取选中分支的消息 + 摘要 + 工件上下文，**不做任何写入**。

    前端「点击分支原位切换」依赖本函数：切换视图不等于 fork，不得创建新会话、
    不得复制消息、不得改动母/子会话行。越权/跨组织一律 404。
    """
    scope = _resolve_scope(scope, organization_id)
    session = await _load_source(db, session_id, user_id, scope)

    messages = await _load_messages(db, session.id)
    artifacts = list(
        (
            await db.execute(
                select(AgentArtifact)
                .where(
                    AgentArtifact.conversation_id == session.id,
                    AgentArtifact.user_id == user_id,
                    AgentArtifact.status == "ready",
                )
                .order_by(AgentArtifact.created_at.desc(), AgentArtifact.version_no.desc())
                .limit(MAX_BRANCH_ARTIFACTS + 1)
            )
        )
        .scalars()
        .all()
    )
    artifacts_truncated = len(artifacts) > MAX_BRANCH_ARTIFACTS
    if artifacts_truncated:
        artifacts = artifacts[:MAX_BRANCH_ARTIFACTS]
    return {
        "session_id": session.id,
        "title": session.title,
        "root_session_id": session.root_session_id or session.id,
        "parent_session_id": session.parent_session_id,
        "fork_point_message_id": session.fork_point_message_id,
        "fork_reason": session.fork_reason,
        "branch_label": session.branch_label,
        "branch_summary": session.branch_summary,
        "summary_model": session.summary_model,
        "summarized_at": _iso(session.summarized_at),
        # 原位切换需要的最小会话元数据（与 chat 详情同口径；不新增写路径）。
        "created_at": _iso(session.created_at) or "",
        "updated_at": _iso(session.updated_at),
        "settings": session.settings if isinstance(session.settings, dict) else None,
        "message_count": len(messages),
        "artifact_count": len(artifacts),
        "messages": [_message_payload(msg) for msg in messages],
        "artifacts_truncated": artifacts_truncated,
        "artifacts": [artifact_summary(row) for row in artifacts],
    }


async def set_branch_label(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: int,
    label: str | None,
    organization_id: int | None = None,
    scope: SessionAccessScope | None = None,
) -> dict[str, Any]:
    """设置/清除分支标签（空白视为清除）；只写 ``branch_label`` 一列。"""
    scope = _resolve_scope(scope, organization_id)
    session = await _load_source(db, session_id, user_id, scope)
    clean = label.strip() if isinstance(label, str) else None
    clean = clean or None
    if clean is not None and len(clean) > BRANCH_LABEL_MAX_CHARS:
        raise ValidationError(
            f"分支标签过长（上限 {BRANCH_LABEL_MAX_CHARS} 字符）",
            detail={"limit": BRANCH_LABEL_MAX_CHARS, "length": len(clean)},
        )
    session.branch_label = clean
    await db.commit()
    return {
        "session_id": session.id,
        "branch_label": clean,
        "updated": True,
    }


def _extractive_summary(messages: list[ChatMessage]) -> str | None:
    """确定性抽取式摘要：按消息顺序取要点，同一输入逐字稳定（fake 模式）。

    这不是「假装 LLM 成功」：``fake`` 是显式模式，返回内容完全来自消息原文，
    模型字段如实记 ``fake``；生产端点的默认模式是 ``llm``，模型不可用时
    显式 503，绝不回落到这里冒充模型输出。
    """
    parts: list[str] = []
    for msg in messages:
        text = " ".join((msg.content or "").split())
        if not text:
            continue
        if msg.message_type == MessageType.USER:
            speaker = "用户"
        elif msg.message_type == MessageType.ASSISTANT:
            speaker = "助手"
        else:
            speaker = "系统"
        parts.append(f"{speaker}: {text}")
        if len("\n".join(parts)) >= BRANCH_SUMMARY_EXTRACT_CHARS:
            break
    joined = "\n".join(parts).strip()
    if not joined:
        return None
    return joined[:BRANCH_SUMMARY_EXTRACT_CHARS].strip() or None


def _summary_prompt(messages: list[ChatMessage]) -> str:
    """把消息压成 LLM 输入（尾部 40 条、总长 8000 字符），避免超长请求。"""
    lines: list[str] = []
    for msg in messages[-40:]:
        text = (msg.content or "").strip()
        if not text:
            continue
        lines.append(f"{msg.message_type.value}: {text[:2000]}")
    return "\n".join(lines)[:8000]


async def _llm_summary(
    db: AsyncSession, *, user_id: int, messages: list[ChatMessage]
) -> tuple[str, str]:
    """调用已配置的 LLM 生成摘要；不可用/失败一律显式抛 503，绝不伪造成功。

    Returns:
        (summary, model_name)
    """
    try:
        resolution = await llm_settings_service.resolve_for_user(db, user_id=user_id)
    except Exception as exc:  # noqa: BLE001 - 解析失败等价于模型不可用
        raise BranchSummaryUnavailableError(
            detail={"reason": "resolve_failed", "error": type(exc).__name__}
        ) from exc
    if resolution.client is None:
        raise BranchSummaryUnavailableError(detail={"reason": "no_client"})
    try:
        response = await asyncio.wait_for(
            resolution.client.chat.completions.create(
                model=resolution.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是会话分支摘要器。只根据给定消息，用简洁中文总结该分支的"
                            "讨论要点与结论；不得编造消息之外的事实。"
                        ),
                    },
                    {"role": "user", "content": _summary_prompt(messages)},
                ],
                temperature=0,
                max_tokens=300,
            ),
            timeout=SUMMARY_LLM_TIMEOUT_SECONDS,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 - 上游失败必须可判定，不得静默降级
        raise BranchSummaryUnavailableError(
            detail={"reason": "llm_call_failed", "error": type(exc).__name__}
        ) from exc
    if not content:
        raise BranchSummaryUnavailableError(detail={"reason": "empty_completion"})
    return content[:BRANCH_SUMMARY_MAX_CHARS], resolution.model


async def _clear_branch_summary(db: AsyncSession, session: ChatSession) -> None:
    """清空分支摘要三列（空分支/全空正文时调用）。"""
    session.branch_summary = None
    session.summary_model = None
    session.summarized_at = None
    await db.commit()


async def summarize_branch(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: int,
    mode: str,
    organization_id: int | None = None,
    scope: SessionAccessScope | None = None,
) -> dict[str, Any]:
    """生成并持久化分支摘要；无消息时如实返回「无摘要」，不写占位文本。

    - ``fake``：确定性抽取式（同一消息序列输出逐字稳定），``summary_model="fake"``；
    - ``llm``：调用已配置模型；无客户端/调用失败/空输出 → 503
      ``SUMMARY_MODEL_UNAVAILABLE``，且**不落任何摘要**（绝不 fake 成功）。

    只写 ``branch_summary`` / ``summary_model`` / ``summarized_at`` 三列；
    不改消息、不建会话、不触发 fork。
    """
    normalized = (mode or "").strip().lower()
    if normalized not in BRANCH_SUMMARY_MODES:
        raise ValidationError(
            "摘要模式非法（仅支持 fake/llm）",
            detail={"mode": mode, "allowed": list(BRANCH_SUMMARY_MODES)},
        )
    scope = _resolve_scope(scope, organization_id)
    session = await _load_source(db, session_id, user_id, scope)
    messages = await _load_messages(db, session.id)
    if not messages:
        # 空分支：如实返回「没有可摘要内容」，并清掉旧摘要，避免树继续显示
        # 与本次响应矛盾的陈旧内容。
        await _clear_branch_summary(db, session)
        return {
            "session_id": session.id,
            "generated": False,
            "summary": None,
            "summary_model": None,
            "summarized_at": None,
            "message_count": 0,
        }

    if normalized == "fake":
        summary = _extractive_summary(messages)
        model_name = "fake"
    else:
        summary, model_name = await _llm_summary(db, user_id=user_id, messages=messages)

    if not summary:
        # 全部消息正文为空：与「无消息」同口径，清掉旧摘要、不落占位摘要。
        await _clear_branch_summary(db, session)
        return {
            "session_id": session.id,
            "generated": False,
            "summary": None,
            "summary_model": None,
            "summarized_at": None,
            "message_count": len(messages),
        }

    now = datetime.now(UTC)
    session.branch_summary = summary
    session.summary_model = model_name
    session.summarized_at = now
    await db.commit()
    return {
        "session_id": session.id,
        "generated": True,
        "summary": summary,
        "summary_model": model_name,
        "summarized_at": now.isoformat(),
        "message_count": len(messages),
    }
