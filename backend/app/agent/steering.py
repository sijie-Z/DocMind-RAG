"""运行中转向（mid-run steering）——任务级指导消息的有界队列。

需求语义（"人在环"从检查站升级为方向盘的第一半）：

- ``POST /agent/tasks/{id}/steer``：任务 **running / waiting_approval** 时接收
  一条人类指导（其它状态 409 诚实文案）；缓冲有界（``MAX_STEER_QUEUE``），
  满了如实拒绝（429），**绝不**静默丢弃任何一条。
- 消费点：loop 在 **run 起点**（resume 并入等待期间暂存的指导）与 **步边界**
  （步骤终态事件之后）drain 队列 → 并入 ``human_guidance`` 注入下一步提示词
  （与 HITL"改指令续跑"同一注入机制，见 executor._execute_step_once 的
  "## 人工指导"段）→ 追加进快照 run_state["steering"]（活引用，随下一次
  落盘持久化）→ 发 SSE ``steer_applied``（含消息摘要）。
- waiting_approval 期间：无活跃 run 消费，消息留在队列里；审批恢复（resume）
  的 run 起点 drain 时并入引导——与既有 modified_instruction 的
  run_state["steering"] 通道合流。
- 终态：mark_terminal 清缓冲（指导不再有任何消费点，留在队列里只会误导）。

Redis 键与取消旗同址（``agent:task:*:{task_id}``），TTL 同窗口（24h）：
协调存储不可达时 push 返回 None（端点 503 如实拒绝，绝不假装受理）；
drain 通道故障按"无新消息"处理（打不坏正常执行，最坏后果 = 本轮少注入一条
指导，消息仍在队列里等下一次 drain）。

有界语义（无 Lua/eval——与既有 _FakeRedis 测试替身的能力面保持兼容）：

- push：先 LLEN 快检（满 → -1，零写入）；RPUSH 后复查长度，超界则用
  ``LREM key -1 message`` 撤回**本次**入队（尾端匹配；并发下极端互为重复
  文本时不产生信息差）并返回 -1。上界在任意交错下都不会被突破，且不存在
  静默丢弃——被拒消息明确回报调用方。
- drain：``LPOP key count``（Redis 6.2+；原子弹出，避免 LRANGE+DEL 之间
  新入队消息被误删的窗口）。
"""

from __future__ import annotations

import logging

from app.core.log_safety import log_safe
from app.core.redis import redis_client
from app.exceptions import ConflictError, RateLimitError

logger = logging.getLogger(__name__)

# 缓冲上限（条）。5 的理由：人类转向的真实节奏是"想到一句发一句"，5 条
# 足够覆盖一个慢步骤内的连续补充，又小到让"满"成为对异常发送节奏的诚实
# 背压信号（429 提示等消费后再发），而不是无限囤积。
MAX_STEER_QUEUE = 5

# 队列 TTL（秒）：与取消旗同窗口（记不住 24h 的指导本就该在 resume 前
# 重新确认；任务行 expires_at 是 3 天，此处取两者中较保守者）。
STEER_TTL_SECONDS = 86_400

# 注入引导的字符上限（与 executor 提示词侧的 2000 截断同口径）。
GUIDANCE_CAP_CHARS = 2_000

# 快照 run_state["steering"] 保留的历史条数（运行期注入用全量 compose；
# 快照只为崩溃/resume 保真——20 条远超现实节奏，防无限膨胀）。
STEERING_HISTORY_CAP = 20


class TaskNotSteerableError(ConflictError):
    """任务不在可接收转向的状态（仅 running / waiting_approval）。"""

    error_code = "TASK_NOT_STEERABLE"
    message = "任务当前状态不能接收运行中指导"


class SteerBufferFullError(RateLimitError):
    """转向缓冲已满（等 agent 消费后再发；绝不静默丢弃/顶掉旧消息）。"""

    error_code = "STEER_BUFFER_FULL"
    message = "转向缓冲已满，请等待 agent 消费后再发送"


def _steer_key(task_id: str) -> str:
    return f"agent:task:steer:{task_id}"


def compose_guidance(messages: list[str], cap: int = GUIDANCE_CAP_CHARS) -> str:
    """按时间顺序合成注入文本；超 cap 时**保最新**（旧的整条舍弃）。

    单条超 cap：头截断到 cap（与 loop resume 既有 ``[:2000]`` 口径一致）。
    整条保留避免半句截断造成的语义歧义；"保最新"是驾向语义的必然选择——
    转的是方向，最近一句才是方向盘。
    """
    kept: list[str] = []
    budget = cap
    for msg in reversed(messages):
        text = msg[:cap] if len(msg) > cap else msg
        cost = len(text) + (1 if kept else 0)
        if cost > budget:
            break
        kept.append(text)
        budget -= cost
    kept.reverse()
    return "\n".join(kept)


async def push_steer(task_id: str, message: str) -> int | None:
    """入队一条指导。返回新队列长度；-1 = 缓冲已满；None = 通道不可用。

    None 绝不与"已受理"同形：端点据此 503（与取消旗的诚实口径一致）。
    空白消息（strip 后为空）是**编程错误**——端点层 pydantic 已 422 拦下
    （评审 MINOR-A：判词 strip 后为空不得入库成空条目）；这里用 ValueError
    守住模块边界，绝不静默收下会变成"内容为空幻影 steer_applied"的空串。
    """
    if not task_id:
        return None
    text = (message or "").strip()
    if not text:
        raise ValueError("steer 消息不能为空或仅空白")
    try:
        if not redis_client:
            return None
        key = _steer_key(task_id)
        pending = await redis_client.llen(key)
        if pending >= MAX_STEER_QUEUE:
            return -1
        await redis_client.rpush(key, text)
        await redis_client.expire(key, STEER_TTL_SECONDS)
        post = await redis_client.llen(key)
        if post > MAX_STEER_QUEUE:
            # 快检与入队之间被并发入队挤爆：撤回本次（尾端匹配）并如实拒绝。
            await redis_client.lrem(key, -1, text)
            return -1
        return post
    except Exception as e:
        logger.warning("push_steer failed (task=%s): %s", log_safe(task_id), log_safe(e))
        return None


async def drain_steers(task_id: str) -> list[str] | None:
    """原子取走全部待消费指导（保序）。None = 通道不可用（调用方视为无消息）。"""
    if not task_id:
        return None
    try:
        if not redis_client:
            return None
        # count 取 MAX_STEER_QUEUE（上界即队列容量；并发软上界至多再多几条，
        # 剩下的留给下一次 drain——原子弹出绝无 LRANGE+DEL 的误删窗口）。
        raw = await redis_client.lpop(_steer_key(task_id), MAX_STEER_QUEUE)
        if raw is None:
            return []
        if isinstance(raw, str):
            entries = [raw]
        else:
            entries = [s for s in raw if isinstance(s, str)]
        # 防御（评审 MINOR-A 同族）：空白条目丢弃——旧写入者/带外写入的产物
        # 绝不变成内容为空的幻影 steer_applied（空条目对注入与展示都无意义）。
        return [s for s in entries if s.strip()]
    except Exception as e:
        logger.warning("drain_steers failed (task=%s): %s", log_safe(task_id), log_safe(e))
        return None


async def clear_steers(task_id: str | None) -> None:
    """清空任务转向缓冲（终态收敛/测试用）。失败只记 debug——清理不改变正确性。"""
    if not task_id:
        return
    try:
        if redis_client:
            await redis_client.delete(_steer_key(task_id))
    except Exception as e:
        logger.debug("clear_steers failed (task=%s): %s", log_safe(task_id), log_safe(e))
