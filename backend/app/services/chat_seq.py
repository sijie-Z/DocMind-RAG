"""消息序号（seq）分配 —— issue #476 / #499。

``ChatMessage.seq`` 是会话内单调序号（唯一约束 ``(session_id, seq)``），
fork 的边界语义（seq <= 边界）与复制重排都依赖它。**所有新消息写入必须经
本模块分配 seq**：

- MySQL 上先对 ``chat_sessions`` 行做 ``SELECT ... FOR UPDATE``，把同一会话的
  并发写入串行化，然后在行锁内**递增持久计数器** ``next_message_seq``
  （issue #499；迁移 016 按 MAX(seq) 回填）。
- 计数器取 ``max(next_message_seq, MAX(chat_messages.seq))`` 再 +1：计数器是
  主路径，MAX 兜底覆盖带外写入/测试直插/历史行——既保留 #476 的既有语义
  （同一事务里先写 USER 再分配 ASSISTANT 时能看到待插入消息），又不会在
  计数器落后时分配出重复 seq。
- SQLite（本地/单测）不支持也无需要行锁：写事务本身串行（同一测试引擎）。
- 会话行尚不存在（调用方准备在同一事务里新建）时，锁查询返回空，seq 从 1 开始；
  待插入行对并发请求不可见，不会争抢。

唯一约束 ``uq_chat_messages_session_seq`` 仍是最后防线：绕过本模块的写路径
会显式失败（IntegrityError），而不是静默写出重复序号。

变异点：把计数器递增改成"只读不写"后，``test_next_message_seq_persists_counter``
（断言列值随分配推进）必红；把行锁去掉后，并发用例在 MySQL 上会出现两个相同
seq（唯一约束报错）——本模块的单测钉住"分配结果单调递增 + 落列"，行锁由集成
环境兜底。
"""

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage, ChatSession


def _dialect_name(db: AsyncSession) -> str:
    """尽力解析会话绑定的方言；解析失败按"非 MySQL"处理（不加锁）。"""
    try:
        bind = db.get_bind()
    except Exception:
        return ""
    return getattr(getattr(bind, "dialect", None), "name", "") or ""


def _coerce_seq(value: Any) -> int | None:
    """把 DB / 测试替身返回的计数器值安全转为 int。

    生产列是 Integer，但旧测试的会话 mock 可能返回 ``SimpleNamespace`` 等不可
    转换对象；防御为"无计数器值"（跳过计数字段更新，仍按 MAX 兜底分配），
    绝不把 mock 形态升级成 500。
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def next_message_seq(db: AsyncSession, session_id: str) -> int:
    """返回该会话的下一个 seq（从 1 开始），并把计数器推进到该值。

    调用方负责把返回值写进 ``ChatMessage.seq``，并在同一事务内提交；同一事务里
    连续分配两次（如 user + assistant）时，第二次调用会读到刚才推进的计数器
    （或经 autoflush 看到待插入消息的 MAX），返回 +1。
    """
    counter_stmt = select(ChatSession.next_message_seq).where(ChatSession.id == session_id)
    if _dialect_name(db) == "mysql":
        # 锁父行：会话级串行化 seq 分配（行不存在时为空操作）。
        counter_stmt = counter_stmt.with_for_update()
    counter = _coerce_seq((await db.execute(counter_stmt)).scalar_one_or_none())

    # MAX 兜底：计数器尚未迁移/被带外写路径绕过时不得分配重复 seq。
    max_seq = (
        _coerce_seq(
            (
                await db.execute(
                    select(func.coalesce(func.max(ChatMessage.seq), 0)).where(
                        ChatMessage.session_id == session_id
                    )
                )
            ).scalar()
        )
        or 0
    )
    next_seq = max(counter or 0, max_seq) + 1

    if counter is not None:
        # 行锁内的计数推进：并发事务在 MySQL 上被串行化；SQLite 同连接顺序执行。
        await db.execute(
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(next_message_seq=next_seq)
        )
    return next_seq
