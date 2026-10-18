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

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.security import require_organization
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType
from app.models.user import User

logger = logging.getLogger(__name__)


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
    if org_id is not None:
        conditions.append(
            or_(
                ChatSession.organization_id == org_id,
                ChatSession.organization_id.is_(None),
            )
        )
    return conditions


async def save_session_interaction(
    session_id: str,
    user_id: int,
    query: str,
    organization_id: int | None,
    assistant_response: str = "",
    *,
    session_factory=None,
) -> str | None:
    """Save the user query and assistant response as messages in the session.

    S6 安全加固：会话归属按 user_id 校验（与 _load_session_messages 一致），
    绝不允许把消息写进其他用户的会话。

    ``session_factory`` 仅作测试缝：默认使用 ``AsyncSessionLocal``；
    agent 端点会显式传入其模块级同名工厂，保持既有测试的 monkeypatch 兼容。

    Returns: the assistant message ID；None 表示"没有需要写的助手文本（空回复）"
    或"会话不属于调用者（显式拒绝，日志已记录）"。

    Raises: 落库失败（DB 抖动等）时**原样上抛**，由调用方显式处理——issue #301
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
                )
                session.add(new_session)
            elif existing.organization_id is None:
                # issue #277（评审 or1268 R2 收账）：历史会话自愈——本修复前以 raw
                # org 落库的会话（token 回退 / 无组织超管用户当时记 None）在本次以
                # resolved org 运行后收敛为运行 org，落库与运行解析保持对称；
                # 只补空值，绝不改写已记录的非空组织。
                existing.organization_id = organization_id

            user_msg = ChatMessage(
                id=str(uuid.uuid4()),
                session_id=session_id,
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
                    content=assistant_response,
                    message_type=MessageType.ASSISTANT,
                )
                session.add(assistant_msg)

            # Update session title from first message
            if existing is not None and (not existing.title or existing.title == "New Agent Session"):
                existing.title = query[:80]
                existing.updated_at = func.now()

            await session.commit()
            return assistant_msg_id
    except Exception as e:
        # issue #301 批 D（#22）：记录后**原样上抛**（不再吞成 None）。调用方
        # （/chat、/resume）拿到异常后负责显式告知：warning 事件 + done.saved=false
        # + ERROR 日志——落库失败绝不静默。
        logger.error(f"Failed to save interaction: {e}")
        raise
