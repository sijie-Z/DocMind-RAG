"""SSE 单一编码器与 Redis ring buffer（issue #480 §5.2）。

- :func:`format_sse`：``event:`` / ``id:`` / ``data:`` 段的唯一编码入口；
- :class:`SSEStream`：按 ``stream_id`` 维护 ``INCR sse:seq:{id}`` 与
  ``LPUSH sse:buf:{id}`` ring buffer（``LTRIM 0 SSE_BUFFER_MAXLEN-1``，
  TTL ``SSE_BUFFER_TTL_SECONDS``），并提供 ``Last-Event-ID`` 回放；
- :func:`keepalive_stream`：断连保留包装器——客户端断开时不让生成器立即
  终止（pipeline 继续跑并继续写 ring buffer），最长保留
  ``SSE_ORPHAN_TTL_SECONDS`` 秒后取消；开关关闭时保持旧行为（立即取消）。

降级语义：Redis 不可用时事件照常输出但不带 ``id``（无法续传），只记 warning，
绝不因可观测性组件故障打断主流程。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from app.core.config import settings
from app.core.redis import redis_client

logger = logging.getLogger(__name__)

SEQ_KEY = "sse:seq:{stream_id}"
BUF_KEY = "sse:buf:{stream_id}"


def format_sse(event: str, data: dict, event_id: str | None = None) -> str:
    """编码一个 SSE 帧：``event:`` / ``id:``（可选）/ ``data:``（多行逐行前缀）。

    ``data`` 中的换行按 SSE 规范拆成多条 ``data:`` 行，客户端按行拼接回原文。
    """
    lines: list[str] = [f"event: {event}"]
    if event_id is not None:
        lines.append(f"id: {event_id}")
    payload = json.dumps(data, ensure_ascii=False)
    lines.extend(f"data: {line}" for line in payload.splitlines() or [""])
    return "\n".join(lines) + "\n\n"


def _parse_event_id(event_id: str) -> tuple[str, int] | None:
    """``{stream_id}:{seq}`` → (stream_id, seq)；非法格式返回 None。"""
    stream_id, sep, seq_text = event_id.rpartition(":")
    if not sep or not stream_id:
        return None
    try:
        seq = int(seq_text)
    except ValueError:
        return None
    if seq < 0:
        return None
    return stream_id, seq


class SSEStream:
    """单个逻辑流的 SSE 发射器（事件 id = ``{stream_id}:{seq}``）。

    ``stream_id`` 允许首次事件前绑定（聊天入口无 session 时用首个事件的
    ``task_id``，见 issue §5.2 的解析规则）。
    """

    def __init__(self, stream_id: str | None = None) -> None:
        self.stream_id = stream_id

    def bind(self, stream_id: str | None) -> None:
        """首次未绑定且传入非空值时绑定；已绑定不覆盖（同一流只认一个键）。"""
        if self.stream_id is None and stream_id:
            self.stream_id = stream_id

    async def emit(self, event: str, data: dict) -> str:
        """分配序号、写入 ring buffer，返回完整 SSE 帧。"""
        if self.stream_id is None:
            # 尚未绑定流键（极端兜底）：无 id 输出，避免把事件写到共享键。
            return format_sse(event, data)

        stream_id = self.stream_id
        seq_key = SEQ_KEY.format(stream_id=stream_id)
        buf_key = BUF_KEY.format(stream_id=stream_id)
        event_id: str | None = None
        try:
            seq = int(await redis_client.incr(seq_key))
            event_id = f"{stream_id}:{seq}"
            record = json.dumps({"id": event_id, "event": event, "data": data}, ensure_ascii=False)
            await redis_client.lpush(buf_key, record)
            await redis_client.ltrim(buf_key, 0, max(int(settings.SSE_BUFFER_MAXLEN) - 1, 0))
            await redis_client.expire(seq_key, int(settings.SSE_BUFFER_TTL_SECONDS))
            await redis_client.expire(buf_key, int(settings.SSE_BUFFER_TTL_SECONDS))
        except Exception as exc:  # noqa: BLE001 - 可观测性降级，绝不打断主流程
            logger.warning("SSE ring buffer unavailable, event has no id: %s", exc)
            event_id = None
        return format_sse(event, data, event_id)

    async def replay(self, last_event_id: str) -> tuple[list[str], bool]:
        """回放 ``seq > last`` 的缓冲事件。

        Returns:
            (frames, gone)：gone=True 表示缓冲区不连续/流键不匹配/序号已淘汰，
            调用方应发送 ``SSE_RESUME_GONE`` 后关闭（HTTP 层仍为 200）。
        """
        parsed = _parse_event_id(last_event_id)
        if parsed is None:
            return [], True
        resume_stream_id, last_seq = parsed
        if self.stream_id is not None and resume_stream_id != self.stream_id:
            return [], True

        stream_id = resume_stream_id
        seq_key = SEQ_KEY.format(stream_id=stream_id)
        buf_key = BUF_KEY.format(stream_id=stream_id)
        try:
            raw_entries = await redis_client.lrange(buf_key, 0, -1)
            current_seq = int(await redis_client.get(seq_key) or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SSE resume unavailable (redis): %s", exc)
            return [], False

        records: list[tuple[int, dict]] = []
        for raw in raw_entries or []:
            try:
                record = json.loads(raw)
                seq = int(str(record["id"]).rpartition(":")[2])
            except (ValueError, KeyError, TypeError):
                continue
            if seq > last_seq:
                records.append((seq, record))
        records.sort(key=lambda item: item[0])

        if not records:
            # 没有待回放条目：序号已前进但缓冲为空 ⇒ 已被淘汰（GONE）；
            # 否则说明重连点就是最新点，无事可回放。
            return ([], True) if current_seq > last_seq else ([], False)

        first_seq = records[0][0]
        if first_seq > last_seq + 1:
            return [], True
        frames = [
            format_sse(
                str(record.get("event", "message")), record.get("data") or {}, str(record["id"])
            )
            for _, record in records
        ]
        return frames, False


async def keepalive_stream(
    source: AsyncIterator[str],
    *,
    ttl_seconds: int | None,
) -> AsyncIterator[str]:
    """断连保留包装器。

    正常消费时逐帧转发；客户端断开（生成器被 close）时：
    - ``ttl_seconds is None``（开关关闭）→ 立即取消 producer（旧行为）；
    - 否则 producer 继续消费 ``source``（事件仍写入 Redis ring buffer），
      最长 ``ttl_seconds`` 秒后取消。

    producer 的本地队列只服务当前连接；断开后队列积压被丢弃（帧已在
    Redis），不会无限增长。
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=256)

    async def _pump() -> None:
        try:
            async for frame in source:
                try:
                    queue.put_nowait(frame)
                except asyncio.QueueFull:
                    # 慢消费者/已断连：本地丢弃，ring buffer 已保存。
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - producer 异常不应成为未取回任务异常
            logger.warning("SSE producer failed", exc_info=True)
        finally:
            try:
                await queue.put(None)
            except asyncio.CancelledError:
                raise

    task = asyncio.create_task(_pump())

    async def _cancel_later() -> None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=ttl_seconds)
        except TimeoutError:
            logger.info("SSE orphan stream TTL reached, cancelling pipeline (ttl=%ss)", ttl_seconds)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        except Exception:  # noqa: BLE001 - 收尾任务不向事件循环抛异常
            logger.warning("SSE orphan canceller failed", exc_info=True)

    canceller: asyncio.Task | None = None
    try:
        while True:
            frame = await queue.get()
            if frame is None:
                break
            yield frame
    finally:
        if not task.done():
            if ttl_seconds is not None:
                canceller = asyncio.create_task(_cancel_later())
            else:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if canceller is not None:
            # 不 await：保留后台定时器；引用保存在事件循环的任务集合中。
            canceller.add_done_callback(lambda _t: None)
