"""ACP 会话状态与 ChatSession 持久化助手（issue #481 §5.3 / §5.5）。

- :class:`AcpSessionState`：一个 ACP 会话 = ``ChatSession.id`` + 每会话
  ``asyncio.Lock``（并发 prompt → -32009）+ ``cancel_event`` + 串行 sender
  （同一会话的 ``session/update`` 进队列、单任务按序写出，prompt 结算前
  ``await queue.join()``）；
- :class:`AcpSessionRegistry`：sessionId → 状态（一连接多会话）；
- DB 助手：``create_chat_session`` / ``load_chat_session`` /
  ``list_chat_sessions`` / ``load_history`` / ``update_session_settings``。

取消语义（issue §5.3 / #494）：``cancel()`` 置取消旗、开启 update 抑制并丢弃
队列中未发送的更新，此后 ``enqueue_update`` 在抑制期间一律丢弃——成功取消后
不得再发迟到 update。抑制与取消旗**解耦**：取消诚实失败时 ``resume_updates()``
恢复外发（run 仍在跑，客户端必须能继续看到输出）；终局由 ``session/prompt``
的 ``stopReason`` 表达（取消输掉竞态时如实报 end_turn）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType

logger = logging.getLogger(__name__)

# session/list 单次返回上限（issue §5.2）。
SESSION_LIST_LIMIT = 50
# history 条数上限（沿用 /agent/chat 的口径：最近 20 条）。
HISTORY_LIMIT = 20

UpdateSender = Callable[[dict[str, Any]], Awaitable[None]]


class AcpSessionState:
    """单个 ACP 会话的运行时状态（锁 / 取消旗 / 有序更新队列）。"""

    def __init__(self, session_id: str, send: UpdateSender) -> None:
        self.session_id = session_id
        self._send = send
        self.lock = asyncio.Lock()
        self.cancel_event = asyncio.Event()
        # 取消抑制（issue #494 评审 B5）：与 cancel_event 解耦——取消**诚实失败**
        # 时只复位本旗，run 继续外发 update；cancel_event 保留给终局判定
        # （取消输掉竞态时 prompt 报 end_turn，而不是把完成改写成 cancelled）。
        self.updates_suppressed = False
        # 队列元素：session/update 通知帧；None 是 sender 退出哨兵。
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.plan_entries: dict[str, list[dict[str, Any]]] = {}
        self.pending_permission_tasks: set[asyncio.Task] = set()
        self.last_tool_call_id = ""
        # 本轮 run 的任务 id（由事件盖章捕获；issue #494：取消终态兜底必须
        # 只收敛本轮任务，不能误伤同一会话里其它写入者的活跃任务）。
        self.active_task_id = ""
        # 本轮 prompt 开始前该会话已有的最新任务 id（issue #494 评审 B6）：
        # 取消等待任务行登记时用它区分「本轮新任务」与「上一轮遗留的终态任务」。
        # 语义："" = 明确没有任务；非空 = 基线任务 id；None = 基线未知
        # （启动查询在途/失败）——未知时不得用"最新任意任务"推断本轮终态。
        self.pre_prompt_task_id: str | None = None
        self.update_seq = 0
        self._closed = False
        self.sender_task = asyncio.create_task(self._sender())

    # ── 更新通道 ──────────────────────────────────────────────────────────

    def enqueue_update(self, update: dict[str, Any]) -> bool:
        """入队一条 ``session/update``；取消抑制生效时一律丢弃（返回 False）。"""
        if self._closed or self.updates_suppressed:
            return False
        self.update_seq += 1
        self.queue.put_nowait(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"sessionId": self.session_id, "update": update},
            }
        )
        return True

    async def _sender(self) -> None:
        while True:
            item = await self.queue.get()
            try:
                if item is None:
                    return
                try:
                    await self._send(item)
                except Exception:  # noqa: BLE001 - 单条写出失败不打断会话
                    logger.warning("ACP session/update write failed", exc_info=True)
            finally:
                self.queue.task_done()

    async def barrier(self) -> None:
        """等待队列中所有更新写出（prompt 结算前调用）。"""
        await self.queue.join()

    def drain(self) -> int:
        """丢弃队列中未发送的更新（取消路径）；返回丢弃条数。"""
        dropped = 0
        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            dropped += 1
            self.queue.task_done()
        return dropped

    # ── 取消 / 关闭 ───────────────────────────────────────────────────────

    def cancel_pending_permissions(self) -> None:
        """取消本会话所有在途审批等待（prompt 侧按 cancelled→deny 收敛）。"""
        for task in list(self.pending_permission_tasks):
            task.cancel()

    async def cancel(self) -> int:
        """置取消旗、抑制 update、丢未发送更新、取消待审批；返回丢弃条数。"""
        self.cancel_event.set()
        self.updates_suppressed = True
        self.cancel_pending_permissions()
        return self.drain()

    def resume_updates(self) -> None:
        """取消诚实失败（run 仍在跑）→ 恢复 update 外发（issue #494 评审 B5）。

        只复位抑制，不复位 ``cancel_event``：终局仍要知道"取消来过"，以便在
        取消输掉竞态时如实报 end_turn/错误而不是 cancelled。
        """
        self.updates_suppressed = False

    def reset_for_prompt(self) -> None:
        """新一轮 prompt 的干净起点（上一轮取消不污染本轮）。"""
        self.cancel_event.clear()
        self.updates_suppressed = False
        self.plan_entries.clear()
        self.last_tool_call_id = ""
        self.active_task_id = ""
        self.pre_prompt_task_id = None

    async def close(self) -> None:
        """结束 sender（仅解除连接映射，不动 DB）。"""
        if self._closed:
            return
        self._closed = True
        self.cancel_event.set()
        self.drain()
        self.queue.put_nowait(None)
        try:
            await asyncio.wait_for(self.sender_task, timeout=5.0)
        except TimeoutError:
            self.sender_task.cancel()
        except asyncio.CancelledError:
            # 调用方被取消（断连收尾）：停掉 sender 后如实上抛，绝不吞取消。
            self.sender_task.cancel()
            raise


class AcpSessionRegistry:
    """sessionId → :class:`AcpSessionState`（一连接多会话）。"""

    def __init__(self) -> None:
        self._sessions: dict[str, AcpSessionState] = {}

    def get(self, session_id: str) -> AcpSessionState | None:
        return self._sessions.get(session_id)

    def ensure(self, session_id: str, send: UpdateSender) -> AcpSessionState:
        state = self._sessions.get(session_id)
        if state is None:
            state = AcpSessionState(session_id, send)
            self._sessions[session_id] = state
        return state

    def pop(self, session_id: str) -> AcpSessionState | None:
        return self._sessions.pop(session_id, None)

    def values(self) -> list[AcpSessionState]:
        return list(self._sessions.values())

    def clear(self) -> None:
        self._sessions.clear()


# ── ChatSession 持久化助手 ──────────────────────────────────────────────────


async def create_chat_session(db_factory, *, user_id: int, organization_id: int | None) -> str:
    """``session/new``：创建 ChatSession 并提交，返回 ``ChatSession.id``。

    与 issue §5.5 一致：``get_or_create_session(session, None, user_id,
    "ACP session", org_id)``（无 conversation_id = 新建）。
    """
    from app.services.chat_service import get_or_create_session

    async with db_factory() as db:
        session_id, _ = await get_or_create_session(
            db, None, user_id, "ACP session", organization_id
        )
        await db.commit()
    return session_id


async def load_chat_session(
    db_factory,
    session_id: str,
    *,
    user_id: int,
    organization_id: int | None,
    is_superuser: bool = False,
) -> ChatSession | None:
    """按 id + user + org 读取会话；不存在/他人/跨组织一律 None。"""
    async with db_factory() as db:
        session = await db.get(ChatSession, session_id)
    if session is None or session.user_id != user_id:
        return None
    if (
        not is_superuser
        and session.organization_id is not None
        and session.organization_id != organization_id
    ):
        return None
    return session


async def list_chat_sessions(
    db_factory,
    *,
    user_id: int,
    organization_id: int | None,
    is_superuser: bool = False,
) -> list[ChatSession]:
    """当前用户最多 50 条会话（本组织 + 历史 NULL；按更新时间倒序）。"""
    from app.services.session_service import session_org_condition

    stmt = select(ChatSession).where(
        ChatSession.user_id == user_id,
        ChatSession.status != ChatSessionStatus.ENDED,
    )
    if not is_superuser:
        org_condition = session_org_condition(organization_id)
        if org_condition is not None:
            stmt = stmt.where(org_condition)
    stmt = stmt.order_by(ChatSession.updated_at.desc(), ChatSession.created_at.desc()).limit(
        SESSION_LIST_LIMIT
    )
    async with db_factory() as db:
        rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


async def load_history(db_factory, session_id: str) -> list[dict[str, str]]:
    """**最近** 20 条消息 → ``[{role, content}]``（沿用 /agent/chat 的构造格式）。

    注意：``/agent/chat`` 现有查询是 ``seq ASC LIMIT 20``（取最早 20 条）；
    ACP（issue §5.5）明确要求"最近 20 条"，这里按时间倒序取 20 条后翻转。
    """
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.seq.desc())
        .limit(HISTORY_LIMIT)
    )
    async with db_factory() as db:
        messages = (await db.execute(stmt)).scalars().all()
    return [
        {
            "role": "user" if m.message_type == MessageType.USER else "assistant",
            "content": m.content,
        }
        for m in reversed(messages)
    ]


async def update_session_settings(
    session: ChatSession,
    *,
    option: str,
    value: Any,
) -> None:
    """``session/set_config_option``：把 model / plan_mode 写入 ChatSession.settings。"""
    settings_map = dict(session.settings) if isinstance(session.settings, dict) else {}
    if option == "model":
        settings_map["model"] = value
    elif option == "plan_mode":
        settings_map["plan_approval_mode"] = value
    session.settings = settings_map
    flag_modified(session, "settings")
