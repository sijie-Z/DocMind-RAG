"""会话分叉 / seed / 谱系服务（issue #476）。

语义（与 issue 的边界规则逐条对应；也是本模块的判别性回归点）：

1. **边界只能落在已结算位置**：复制前缀必须为空或以 ASSISTANT 消息结尾。
   - ``message_id`` 指向 ASSISTANT：仅当它是该轮次的最后一条 assistant（到下一个
     USER 之前不再出现 ASSISTANT）才合法——assistant 中间消息 409；
   - ``message_id`` 指向 USER：边界在该消息"之前"（该 USER 不复制），前缀仍须为空
     或以 ASSISTANT 结尾，否则 409（更早的开放轮次）；
   - SYSTEM 消息不是合法边界（409）；不存在/不属于源会话的消息 404；
   - 默认边界 = 最后一条已结算消息：跳过尾部未答复的 USER（不报错、不复制）。
2. **活跃任务**：源会话存在非终态任务（PENDING/RUNNING/WAITING_*）时，消息级
   热分叉被禁止；必须显式 ``include_checkpoint`` 且存在可用（已结算）checkpoint，
   否则 409。PAUSED（克隆产物）不算"运行中"，否则子会话永远无法再分叉。
3. **checkpoint 克隆**：最近一个已结算 checkpoint 深拷为新 ``AgentTask``：新 id、
   绑定 child session、状态 ``PAUSED``（可 resume）、``run.fork_origin`` 记录来源
   （避免"伪历史"）；``run.pending_approval`` 或执行中工具（WAITING_TOOL）一律拒绝。
   克隆任务使用全新 task id → replay 命名空间 `agent:replay:<new_id>` 为空，
   母任务 replay 产物不会被复制。
4. **上下文隔离**：消息复制为新 id + 子会话内重新编号的 seq；父会话行/消息不变；
   子会话使用新的 ``session:<id>`` 记忆命名空间，默认不复制母记忆。
5. **seed**：可选的种子文本写入子命名空间（reflective insight，尽力落 Redis），
   供子会话首轮召回。记忆写入失败不使 fork 失败（消息/谱系是硬保证）。
6. **租户**：访问作用域复用 ``app.services.session_service``（org + user 口径，
   超管旁路/历史 NULL 组织一致）；越权/跨组织一律 404（不 403，避免枚举）。
7. **幂等**：``Idempotency-Key`` 以 sha256 存进子会话 settings.fork；重复请求
   返回同一子会话（``idempotent=True``），不再复制。选择 DB 存储而非 Redis：
   幂等事实必须与子会话同事务落库，Redis 抖动不得造成重复分叉。**边界**：
   check-then-insert 只保证顺序重试（客户端超时重试的主形态）；同一瞬间的并发
   双请求可能各建一个子会话（JSON 字段无法建唯一索引），调用方应串行重试。
8. **配额**：单次 fork 复制消息上限 ``FORK_MAX_COPIED_MESSAGES``（2000），
   超限 409 并在 detail 给出 limit/count。

变异点（评审/回归重点）：
- 把 USER 边界当作"包含该消息" → 前缀内容与 fork_point 断言红；
- 删掉 assistant 中间消息检查 → 中间消息用例红；
- 默认边界不跳过尾部 USER → 默认用例红；
- 复制复用源消息 id / seq 不重排 → 唯一约束冲突/断言红；
- seed 写进父命名空间或复制父记忆 → 隔离用例红；
- 访问校验漏掉 org/user → 跨组织用例红（应为 404）；
- 克隆复用源 task id / 不改 session_id / 浅拷贝 → 克隆用例红。
"""

from __future__ import annotations

import copy
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.agent import checkpoint
from app.agent.memory_bridge import AgentMemoryBridge
from app.core.log_safety import log_safe
from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.agent_task import AgentTask, TaskStatus
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType
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

DEFAULT_FORK_REASON = "user_fork"
_TITLE_MAX = 200
_REASON_MAX = 100
# 谱系遍历深度上限：防御环状数据（损坏数据不得让 lineage 无限循环）。
_MAX_LINEAGE_DEPTH = 50
# 克隆时候选的最近任务行数上限（只挑可解析的已结算快照）。
_CHECKPOINT_CANDIDATE_LIMIT = 20

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
    """边界未落在已结算位置（assistant 中间消息 / 开放轮次 / SYSTEM）。"""

    error_code = "FORK_BOUNDARY_INVALID"
    message = "分叉边界非法：只能从已结算轮次处分叉"


class ForkQuotaExceededError(ConflictError):
    """复制消息数超过上限。"""

    error_code = "FORK_QUOTA_EXCEEDED"
    message = "分叉复制的消息数超出上限"


class ForkCheckpointRequiredError(ConflictError):
    """源会话有运行中任务，但请求没有走 checkpoint 克隆。"""

    error_code = "FORK_CHECKPOINT_REQUIRED"
    message = "源会话有运行中的任务，只能从最后一个已提交 checkpoint 分叉"


class ForkCheckpointUnavailableError(ConflictError):
    """没有可用的已结算 checkpoint（不存在/损坏/待审批/执行中工具）。"""

    error_code = "FORK_CHECKPOINT_UNAVAILABLE"
    message = "没有可用的已结算 checkpoint（不存在、待审批或执行中工具）"


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


def _settled_tail(prefix: list[ChatMessage]) -> ChatMessage | None:
    """前缀中最后一条非 SYSTEM 消息（SYSTEM 是带外数据，不构成开放轮次）。"""
    for msg in reversed(prefix):
        if msg.message_type != MessageType.SYSTEM:
            return msg
    return None


def _resolve_prefix(
    messages: list[ChatMessage], message_id: str | None
) -> tuple[list[ChatMessage], ChatMessage | None]:
    """把边界解析为"可复制的已结算前缀"。

    Returns:
        (prefix, fork_point) —— fork_point 是前缀里最后一条**源会话**消息
        （空前缀为 None）；USER 边界本身不进前缀（"在该 USER 之前分叉"）。
    """
    if message_id:
        target_idx = next((i for i, m in enumerate(messages) if m.id == message_id), None)
        if target_idx is None:
            raise NotFoundError("边界消息不存在或不属于该会话")
        target = messages[target_idx]

        if target.message_type == MessageType.USER:
            prefix = messages[:target_idx]
            tail = _settled_tail(prefix)
            if tail is not None and tail.message_type != MessageType.ASSISTANT:
                raise ForkBoundaryInvalidError(
                    "该 USER 消息之前的前缀以未答复的消息结尾，不能作为分叉边界",
                    detail={"message_id": message_id},
                )
            return prefix, (prefix[-1] if prefix else None)

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

        raise ForkBoundaryInvalidError(
            "SYSTEM 消息不能作为分叉边界", detail={"message_id": message_id}
        )

    # 默认边界 = 最后一条已结算消息（跳过尾部未答复的 USER/SYSTEM 尾巴）。
    last_assistant_idx = None
    for idx, msg in enumerate(messages):
        if msg.message_type == MessageType.ASSISTANT:
            last_assistant_idx = idx
    if last_assistant_idx is None:
        return [], None
    return messages[: last_assistant_idx + 1], messages[last_assistant_idx]


async def _load_active_tasks(
    db: AsyncSession, source: ChatSession, user_id: int
) -> list[AgentTask]:
    """源会话的"运行中任务"（含待审批/执行中工具；不含 PAUSED/终态）。

    查询 defer ``context_snapshot``（#278/R1 毒丸防线）：本函数只需要任务行的
    状态/id，损坏的快照列不得把"判定是否有运行中任务"整条链路炸成 500。
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
    return list(rows.scalars().all())


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
    db: AsyncSession, source: ChatSession, user_id: int
) -> tuple[AgentTask, dict[str, Any]] | None:
    """最近的可用 checkpoint（任务行 + 已校验信封）：可解析、无待审批、非执行中工具。

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
        AgentTask.context_snapshot.is_not(None),
        # 先在 SQL 里排除"明显不可克隆"的状态，再按最近顺序取候选：最近 20 个
        # 任务不能因为全是待审批/执行中就把更早的可用 checkpoint 挡掉。
        AgentTask.status.not_in(_UNSETTLED_TASK_STATUSES),
    ]
    if source.organization_id is not None:
        conditions.append(AgentTask.organization_id == source.organization_id)
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


async def _find_idempotent_child(
    db: AsyncSession,
    *,
    source_id: str,
    user_id: int,
    scope: SessionAccessScope,
    key_hash: str,
) -> ChatSession | None:
    """按 (source, user, scope) 找同一幂等键创建过的子会话（DB 持久事实）。"""
    conditions = [
        ChatSession.parent_session_id == source_id,
        ChatSession.user_id == user_id,
        *session_access_org_conditions(scope),
    ]
    rows = await db.execute(
        select(ChatSession).where(*conditions).order_by(ChatSession.created_at.asc())
    )
    for child in rows.scalars().all():
        settings_json = child.settings if isinstance(child.settings, dict) else {}
        fork_meta = settings_json.get("fork")
        if isinstance(fork_meta, dict) and fork_meta.get("idempotency_key") == key_hash:
            return child
    return None


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
    """从 source 会话的已结算边界分叉出子会话。

    Raises:
        NotFoundError: 源会话/边界消息不存在、他人或跨组织（统一 404）。
        ForkBoundaryInvalidError / ForkQuotaExceededError /
        ForkCheckpointRequiredError / ForkCheckpointUnavailableError: 409 语义。
        ValidationError: seed 超过长度上限（400）。
    """
    scope = _resolve_scope(scope, organization_id)
    if seed is not None and len(seed) > MAX_SEED_CHARS:
        raise ValidationError(f"种子内容过长（上限 {MAX_SEED_CHARS} 字符）")

    source = await _load_source(db, source_id, user_id, scope)

    key_hash = _idempotency_hash(idempotency_key) if idempotency_key else None
    if key_hash:
        existing = await _find_idempotent_child(
            db, source_id=source.id, user_id=user_id, scope=scope, key_hash=key_hash
        )
        if existing is not None:
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

    messages = await _load_messages(db, source.id)
    prefix, fork_point = _resolve_prefix(messages, message_id)
    if len(prefix) > FORK_MAX_COPIED_MESSAGES:
        raise ForkQuotaExceededError(
            f"分叉复制的消息数超出上限（{FORK_MAX_COPIED_MESSAGES} 条）",
            detail={"limit": FORK_MAX_COPIED_MESSAGES, "count": len(prefix)},
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
        picked = await _latest_settled_checkpoint(db, source, user_id)
        if picked is None:
            raise ForkCheckpointUnavailableError(
                "没有可用的已结算 checkpoint（不存在、损坏、待审批或执行中工具）"
            )
        checkpoint_task, checkpoint_snapshot = picked

    child_id = str(uuid.uuid4())
    fork_point_id = fork_point.id if fork_point is not None else None
    child_org = source.organization_id if source.organization_id is not None else organization_id
    resolved_title = (
        title if title is not None and title.strip() else f"{source.title or '未命名会话'} (fork)"
    )[:_TITLE_MAX]
    resolved_reason = (reason or DEFAULT_FORK_REASON)[:_REASON_MAX]
    seed_requested = bool(seed and seed.strip())
    # 无组织上下文无法建立记忆命名空间（memory_service fail-closed 拒绝 None/0）：
    # seed 如实标记为未生效，而不是"响应说 seed 了、记忆里没有"。
    is_seeded = seed_requested and child_org is not None
    cloned_task_id = uuid.uuid4().hex[:12] if checkpoint_task is not None else None
    # 响应字段一律用局部变量（提交后不回读 ORM 属性，避免过期实例触发
    # 同步懒加载 → MissingGreenlet）。
    boundary_seq = prefix[-1].seq if prefix else 0
    copied_count = len(prefix)

    parent_settings = source.settings if isinstance(source.settings, dict) else {}
    child_settings = copy.deepcopy(parent_settings)
    if not isinstance(child_settings, dict):
        child_settings = {}
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
        "checkpoint_source_task_id": checkpoint_task.id if checkpoint_task is not None else None,
        "is_seeded": is_seeded,
        "seed_length": len(seed) if seed_requested and seed else 0,
        "seed_skipped_reason": ("no_organization" if seed_requested and not is_seeded else None),
        "fork_reason": resolved_reason,
        "forked_at": now.isoformat() + "Z",
    }
    if key_hash:
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
    )

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
                    status=TaskStatus.PAUSED,
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
    except Exception:
        await db.rollback()
        raise

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
