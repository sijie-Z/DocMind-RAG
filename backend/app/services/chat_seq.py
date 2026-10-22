"""消息序号（seq）分配 —— issue #476。

``ChatMessage.seq`` 是会话内单调序号（唯一约束 ``(session_id, seq)``），
fork 的边界语义（seq <= 边界）与复制重排都依赖它。**所有新消息写入必须经
本模块分配 seq**：

- MySQL 上先对 ``chat_sessions`` 行做 ``SELECT ... FOR UPDATE``，把同一会话的
  并发写入串行化——否则两个并发请求会算出同一个 ``max(seq)+1``，撞唯一约束
  变成 500；锁父行（而不是 max 查询本身）才能覆盖"会话尚无消息"的空窗。
- SQLite（本地/单测）不支持也无需要行锁：写事务本身串行。
- 会话行尚不存在（调用方准备在同一事务里新建）时，锁查询返回空，seq 从 1 开始；
  待插入行对并发请求不可见，不会争抢。

唯一约束 ``uq_chat_messages_session_seq`` 仍是最后防线：绕过本模块的写路径
会显式失败（IntegrityError），而不是静默写出重复序号。

变异点：把 ``FOR UPDATE`` 去掉后，并发用例在 MySQL 上会出现两个相同 seq
（唯一约束报错）——本模块的单测钉住"分配结果单调递增"，行锁由集成环境兜底。
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage, ChatSession


def _dialect_name(db: AsyncSession) -> str:
    """尽力解析会话绑定的方言；解析失败按"非 MySQL"处理（不加锁）。"""
    try:
        bind = db.get_bind()
    except Exception:
        return ""
    return getattr(getattr(bind, "dialect", None), "name", "") or ""


async def next_message_seq(db: AsyncSession, session_id: str) -> int:
    """返回该会话的下一个 seq（从 1 开始）。

    调用方负责把返回值写进 ``ChatMessage.seq``，并在同一事务内提交；同一事务里
    连续分配两次（如 user + assistant）时，第二次的 ``max`` 查询会经 autoflush
    看到第一条待插入消息，返回 +2。
    """
    if _dialect_name(db) == "mysql":
        # 锁父行：会话级串行化 seq 分配（行不存在时为空操作）。
        await db.execute(
            select(ChatSession.id).where(ChatSession.id == session_id).with_for_update()
        )
    current = (
        await db.execute(
            select(func.coalesce(func.max(ChatMessage.seq), 0)).where(
                ChatMessage.session_id == session_id
            )
        )
    ).scalar() or 0
    return int(current) + 1
