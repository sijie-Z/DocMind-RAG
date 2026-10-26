"""统一会话服务（B2 / 决策 D3「先合入口与可见范围」）。

`/chat` 与 `/agent` 两个会话入口在此共享同一套语义：

- **可见范围**（`resolve_session_scope` + `session_visibility_conditions`）：
  一律按 org + user 过滤，复用 A2 口径——非超管 fail-closed（无组织 403），
  超管保留旁路（不按组织过滤，但仍限本人）；历史 `organization_id IS NULL`
  的本人会话对当前组织可见（迁移前遗留，chat 侧既有行为）。
- **落库**（`save_session_interaction`）：/agent/chat 的既有实现原样提升为
  公共服务（issue #301 批 D 的诚实失败语义、#277 的 org 自愈均保留），
  chat 的 Agent 模式走完 PER 后复用同一实现，刷新后本轮问答不丢。

数据模型合并（Conversation/Run）留到 C3，本模块不引入新表。
"""

import logging
import uuid
from typing import NamedTuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.log_safety import log_safe
from app.core.security import require_organization
from app.exceptions import AuthorizationError
from app.models.agent_task import AgentTask, TaskStatus
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType
from app.models.user import User
from app.services.chat_seq import next_message_seq

logger = logging.getLogger(__name__)


class SessionAccessScope(NamedTuple):
    """「按 id 访问会话」的组织作用域（列表口径的复用载体）。

    - ``bypass_org=True``：超管旁路——只按 user_id 过滤（与
      ``session_visibility_conditions`` 的既有 bypass 一致）；
    - ``bypass_org=False, org_id=<id>``：本组织 + 历史 NULL 组织会话；
    - ``bypass_org=False, org_id=None``：调用者无组织上下文（非超管）——
      只允许本人历史 NULL 组织会话（fail-closed：不触达任何带组织标记的行；
      历史会话本身仍可读，保留 chat 详情既有语义）。
    """

    bypass_org: bool
    org_id: int | None


async def resolve_session_scope(current_user: User, db: AsyncSession) -> int | None:
    """解析会话列表的组织作用域（A2 `_resolve_prompt_org` 同口径）。

    Returns:
        组织 id；``None`` 表示超管旁路（不加组织条件）。

    Raises:
        AuthorizationError: 非超管且无组织（fail-closed，HTTP 403）。
    """
    if getattr(current_user, "is_superuser", False):
        return None
    return await require_organization(current_user, db)


def session_org_condition(org_id: int | None):
    """会话组织可见条件：本组织 + 历史 ``organization_id IS NULL``。

    两个列表入口与所有按 id 访问路径共用同一口径（历史 NULL 行属本人，
    只对本人可见，不构成跨租户读取）。``org_id is None`` 表示超管旁路，
    返回 None——调用方不附加任何组织条件。
    """
    if org_id is None:
        return None
    return or_(
        ChatSession.organization_id == org_id,
        ChatSession.organization_id.is_(None),
    )


async def resolve_session_access_scope(current_user: User, db: AsyncSession) -> SessionAccessScope:
    """解析「按 id 访问会话」的组织作用域。

    与列表共用 ``require_organization`` 的有效组织解析（含 token 组织回退与
    无组织超管回源），但无组织非超管不 403：降级为「仅历史 NULL 组织会话」，
    保留 chat 详情既有语义（本人历史会话本身可读，只是不触达带组织的数据）。
    """
    if getattr(current_user, "is_superuser", False):
        return SessionAccessScope(bypass_org=True, org_id=None)
    try:
        org_id = await require_organization(current_user, db)
    except AuthorizationError:
        return SessionAccessScope(bypass_org=False, org_id=None)
    return SessionAccessScope(bypass_org=False, org_id=org_id)


def session_access_org_conditions(scope: SessionAccessScope) -> list:
    """``SessionAccessScope`` 的组织条件部分（不含 id / user）。

    硬性前置条件：调用方的查询**必须已 JOIN ``ChatSession``**——本助手只返回
    ``ChatSession`` 列上的条件；未 join 时 SQLAlchemy 会把它变成隐式笛卡尔积。

    供 join 型按 id 访问复用同一可见范围口径——例如消息反馈：
    ``select(ChatMessage).join(ChatSession)`` 无法直接套
    ``session_access_conditions``（那需要先知道 session_id），但同样必须
    限定在「本组织 + 历史 NULL（超管旁路）」内。
    """
    if scope.bypass_org:
        return []
    if scope.org_id is None:
        # 无组织上下文（非超管）：fail-closed 到本人历史 NULL 组织会话
        return [ChatSession.organization_id.is_(None)]
    return [session_org_condition(scope.org_id)]


def session_access_conditions(
    session_id: str,
    user_id: int,
    scope: SessionAccessScope,
) -> list:
    """按 id 访问会话的 WHERE 条件（id + user + org）。

    不包含列表的 ``status != ENDED`` 条件——详情/更新/删除对历史会话保持既有
    可访问语义（本任务不改历史会话状态语义）。
    """
    return [
        ChatSession.id == session_id,
        ChatSession.user_id == user_id,
        *session_access_org_conditions(scope),
    ]


def session_matches_scope(session_obj: ChatSession, scope: SessionAccessScope) -> bool:
    """``session_access_conditions`` 的 Python 侧等价判断。

    供需要区分「不存在」与「跨组织」的端点复用（后者应显式 404/403，
    而不是静默当作不存在）。
    """
    if scope.bypass_org:
        return True
    if session_obj.organization_id is None:
        return True
    return scope.org_id is not None and session_obj.organization_id == scope.org_id


async def load_accessible_session(
    db: AsyncSession,
    session_id: str,
    current_user: User,
    scope: SessionAccessScope,
) -> ChatSession | None:
    """按 id 加载调用者可见的会话；不存在 / 他人 / 跨组织一律 None。"""
    stmt = select(ChatSession).where(*session_access_conditions(session_id, current_user.id, scope))
    return (await db.execute(stmt)).scalar_one_or_none()


def session_visibility_conditions(current_user: User, org_id: int | None) -> list:
    """两个会话列表入口共用的 WHERE 条件（org + user）。

    - ``user_id``：会话是个人数据，永远限本人；
    - ``status != ENDED``：与两个入口既有口径一致；
    - 组织（非超管）：本组织 + 历史 NULL（迁移前遗留，属本人）。
      历史 NULL 行只对本人可见，不构成跨租户读取。
    """
    conditions = [
        ChatSession.user_id == current_user.id,
        ChatSession.status != ChatSessionStatus.ENDED,
    ]
    org_condition = session_org_condition(org_id)
    if org_condition is not None:
        conditions.append(org_condition)
    return conditions


async def save_session_interaction(
    session_id: str,
    user_id: int,
    query: str,
    organization_id: int | None,
    assistant_response: str = "",
    *,
    session_factory=None,
    bypass_org: bool = False,
) -> str | None:
    """Save the user query and assistant response as messages in the session.

    S6 安全加固：会话归属按 user_id 校验（与 _load_session_messages 一致），
    绝不允许把消息写进其他用户的会话。

    T4 组织口径：目标会话已归属非 NULL 组织且与本次有效组织不一致时，
    **明确拒绝**（``AuthorizationError``，调用方如实告知），绝不静默写入——
    多组织用户切到 B 组织后不得把消息落进 A 组织的会话行。``bypass_org``
    仅供超管旁路（与 ``session_visibility_conditions`` 的 is_superuser 先例
    一致：超管可见的会话必须同样可写，仍限本人）。

    ``session_factory`` 仅作测试缝：默认使用 ``AsyncSessionLocal``；
    agent 端点会显式传入其模块级同名工厂，保持既有测试的 monkeypatch 兼容。

    Returns: the assistant message ID；None 表示"没有需要写的助手文本（空回复）"
    或"会话不属于调用者（显式拒绝，日志已记录）"。

    Raises:
        AuthorizationError: 目标会话属于其他组织（明确拒绝，不写任何消息）。
        落库失败（DB 抖动等）时**原样上抛**，由调用方显式处理——issue #301
        批 D（#22）：修前这里把异常吞成 None，与"空回复/越权拒绝"无法区分，调用方
        done 照发且无任何提示，用户刷新后本轮问答凭空消失。
    """
    factory = session_factory or AsyncSessionLocal
    try:
        async with factory() as session:
            # S6 安全加固：按 id + user_id 校验会话归属（与 _load_session_messages 一致），
            # 绝不允许把消息写进其他用户的会话
            stmt = select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id,
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing is None:
                # id 可能已被其他用户的会话占用：确认后拒绝写入（也避免主键冲突）
                occupied = (
                    await session.execute(
                        select(ChatSession.id).where(ChatSession.id == session_id)
                    )
                ).scalar_one_or_none()
                if occupied is not None:
                    logger.warning(
                        f"Refusing to save interaction into session {session_id} "
                        f"not owned by user {user_id}"
                    )
                    return None
                new_session = ChatSession(
                    id=session_id,
                    user_id=user_id,
                    title=query[:80],
                    status=ChatSessionStatus.ACTIVE,
                    organization_id=organization_id,
                    # issue #476：根会话 = 自身（fork 子会话才指向最初会话）。
                    root_session_id=session_id,
                )
                session.add(new_session)
            elif (
                not bypass_org
                and existing.organization_id is not None
                and existing.organization_id != organization_id
            ):
                # T4：目标会话已归属其他组织（含本次有效组织无法解析为 None 的
                # 情形）——明确拒绝，绝不静默写入。
                logger.warning(
                    f"Refusing to save interaction into session {session_id} of "
                    f"organization {log_safe(existing.organization_id)} "
                    f"from organization {log_safe(organization_id)} (user={user_id})"
                )
                raise AuthorizationError("会话不属于当前组织，拒绝写入")
            elif existing.organization_id is None:
                # issue #277（评审 or1268 R2 收账）：历史会话自愈——本修复前以 raw
                # org 落库的会话（token 回退 / 无组织超管用户当时记 None）在本次以
                # resolved org 运行后收敛为运行 org，落库与运行解析保持对称；
                # 只补空值，绝不改写已记录的非空组织。
                existing.organization_id = organization_id

            # issue #476：会话内单调 seq（唯一约束 (session_id, seq)）。先分配
            # user 的序号；assistant 再分配一次——issue #499 起计数器落在
            # chat_sessions.next_message_seq 上，两次分配让计数器与实际最大 seq
            # 保持一致（不再用 base_seq + 1 手工推算）。
            base_seq = await next_message_seq(session, session_id)
            user_msg = ChatMessage(
                id=str(uuid.uuid4()),
                session_id=session_id,
                seq=base_seq,
                content=query,
                message_type=MessageType.USER,
            )
            session.add(user_msg)

            assistant_msg_id = None
            if assistant_response:
                assistant_msg_id = str(uuid.uuid4())
                assistant_msg = ChatMessage(
                    id=assistant_msg_id,
                    session_id=session_id,
                    seq=await next_message_seq(session, session_id),
                    content=assistant_response,
                    message_type=MessageType.ASSISTANT,
                )
                session.add(assistant_msg)

            # Update session title from first message
            if existing is not None and (
                not existing.title or existing.title == "New Agent Session"
            ):
                existing.title = query[:80]
                existing.updated_at = func.now()

            await session.commit()
            return assistant_msg_id
    except AuthorizationError:
        # T4：跨组织写入的明确拒绝（已记 warning），不按落库失败重复记 ERROR。
        raise
    except Exception as e:
        # issue #301 批 D（#22）：记录后**原样上抛**（不再吞成 None）。调用方
        # （/chat、/resume）拿到异常后负责显式告知：warning 事件 + done.saved=false
        # + ERROR 日志——落库失败绝不静默。
        logger.error(f"Failed to save interaction: {e}")
        raise


# ── issue #480 §5.4.1：session ↔ task 映射（JSON-RPC 方法依赖）───────────────
# ``agent_tasks.session_id`` 由 #476 交付；本模块只提供只读解析，不重复加列。

_TERMINAL_TASK_STATUSES = (
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.REJECTED,
    TaskStatus.ABANDONED,
)


async def get_latest_task_id(db: AsyncSession, session_id: str) -> str | None:
    """会话 → 最新**非终态**任务 id；没有则 None（调用方映射 -32004）。"""
    stmt = (
        select(AgentTask.id)
        .where(
            AgentTask.session_id == session_id,
            AgentTask.status.not_in(_TERMINAL_TASK_STATUSES),
        )
        .order_by(AgentTask.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def resolve_session_id(db: AsyncSession, task_id: str) -> str | None:
    """任务 → 会话 id（历史任务行可为空，返回 None）。"""
    result = await db.execute(select(AgentTask.session_id).where(AgentTask.id == task_id))
    return result.scalar_one_or_none()
