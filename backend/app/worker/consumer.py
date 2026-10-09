import asyncio
import json
import logging
import os
import sys

# 确保 backend 目录在 path 中，以便能导入 app 模块
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from aiokafka import AIOKafkaConsumer

from app.core.config import settings
from app.worker.doc_processor import processor
from app.worker.heartbeat import touch_heartbeat

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 空闲时也要周期性回到循环里刷新心跳：getmany 即使没有消息也最多阻塞这么久
POLL_TIMEOUT_MS = 5000

# issue #301 批 D（#30）：文档级失败的消息允许的投递尝试次数（含首次）。
# 失败 → 不提交偏移、停住消费（RedeliveryHalt）→ 容器重启后从上次提交点重投；
# 尝试数用 job.retry_count 记账（_record_failed_attempt），达到上限后终局提交
# （死信语义），避免毒消息把 worker 卡进无限重启循环。
MAX_DELIVERY_ATTEMPTS = 3


class RedeliveryHalt(Exception):
    """文档级处理失败且重试预算未尽：不提交偏移、停住消费等重投。

    为什么不"继续循环 + 不提交"就完事：aIoKafka 的 ``commit()`` 提交的是消费位点
    ``state.position``——``getmany`` 返回批次时位点已经推进过整批消息
    （``consumer/fetcher.py`` 的 ``_update_position``），只要后续任意一条成功消息
    提交，失败消息的偏移就会被一并带过，"不提交"会变成一句空话（这正是修前
    "失败不提交"注释承诺不生效的机制之一）。让本异常穿出消费循环、进程退出，
    由容器重启策略（compose ``restart: unless-stopped``）从**上次已提交偏移**
    重投，才让"不提交"成为真承诺。
    """


async def _record_failed_attempt(job_id: int | None) -> int:
    """为一条失败消息记账并返回当前尝试数（job.retry_count += 1）。

    返回值即决策依据（与 ``MAX_DELIVERY_ATTEMPTS`` 比较）：

    - 正常：已持久化的累计失败尝试数；
    - ``job_id`` 缺失或 job 行不存在：没有账本 → 返回上限值（终局提交，不做
      无限重投——重投需要账目可查，这是本策略的显式边界）；
    - 读写失败（DB 抖动）：返回 0（保守视为"预算未尽"→ 不提交、重投；
      DB 恢复后账目恢复、重试自然收敛到上限）。
    """
    if job_id is None:
        return MAX_DELIVERY_ATTEMPTS
    try:
        from app.core.database import AsyncSessionLocal
        from app.models.knowledge_job import KnowledgeProcessingJob

        async with AsyncSessionLocal() as session:
            job = await session.get(KnowledgeProcessingJob, job_id)
            if job is None:
                return MAX_DELIVERY_ATTEMPTS
            job.retry_count = (job.retry_count or 0) + 1
            await session.commit()
            return int(job.retry_count)
    except Exception as e:
        logger.error("[CONSUMER] failed to record attempt for job %s: %s", job_id, e)
        return 0


async def run_consume_loop(consumer, process=None, touch=None, record_attempt=None):
    """消费循环：每次 Kafka 轮询返回后刷新心跳（issue #156）。

    用 ``while True`` + ``getmany(timeout_ms=...)`` 而不是 ``async for``：
    ``async for`` 在没有消息时会一直挂起，空闲（正常）状态反而刷不了心跳；
    ``getmany`` 即使没有消息也会按超时返回，于是空闲时心跳照常刷新，
    而进程冻结 / 事件循环停摆 / 消费推进被挂住时心跳停止刷新，
    容器健康检查（worker_healthcheck.py）即可判红。

    ``process`` / ``touch`` / ``record_attempt`` 仅用于测试注入，默认真实实现。

    失败处理（issue #301 批 D / #30，``processor.process`` 的返回值契约见
    ``doc_processor.py``）：``False`` 或抛异常 = 文档级失败 → 不提交偏移；
    重试预算未尽时抛 ``RedeliveryHalt`` 停住（重启重投），预算耗尽时响亮记录并
    终局提交（死信语义：文档/任务已 FAILED，手动重试入口 = 知识库重建端点）。
    """
    process_call = process or processor.process
    beat = touch or touch_heartbeat
    record = record_attempt or _record_failed_attempt

    while True:
        batches = await consumer.getmany(timeout_ms=POLL_TIMEOUT_MS)
        beat()
        for records in batches.values():
            for msg in records:
                logger.info(f"Received task: {msg.value}")
                data = msg.value
                if not isinstance(data, dict):
                    # 非 dict 消息直接跳过并提交偏移，避免 AttributeError 杀死循环
                    logger.warning(f"Skipping non-dict message: {type(data).__name__}")
                    await consumer.commit()
                    continue

                doc_id = data.get("document_id")
                job_id = data.get("job_id")

                if doc_id:
                    try:
                        processed = await process_call(doc_id, job_id)
                    except Exception as e:
                        # 未分类异常（防御网；真实 processor 内部已兜底）：按失败处理，
                        # 绝不允许把异常当成"处理过了"提交偏移，也不允许静默吞掉。
                        processed = False
                        logger.error(f"Error processing message: {e}", exc_info=True)

                    if processed is False:
                        # 文档级失败：不提交偏移。重试预算见 _record_failed_attempt。
                        attempts = await record(job_id)
                        if attempts >= MAX_DELIVERY_ATTEMPTS:
                            logger.error(
                                "[CONSUMER] document %s failed %d time(s); giving up on "
                                "this message (terminal commit, dead-letter semantics): "
                                "doc/job already marked FAILED; manual retry: "
                                "POST /api/v1/knowledge/rebuild/%s",
                                doc_id,
                                attempts,
                                doc_id,
                            )
                            await consumer.commit()
                            continue
                        raise RedeliveryHalt(
                            f"document {doc_id} (job {job_id}) processing failed "
                            f"(attempt {attempts}/{MAX_DELIVERY_ATTEMPTS}); offset NOT "
                            f"committed - halting for redelivery after restart"
                        )

                    # True（或其他真值）：消息已了结，可安全提交
                    await consumer.commit()
                else:
                    logger.warning("Received message without document_id")
                    await consumer.commit()


async def consume():
    """Kafka 消费者主循环"""
    logger.info(f"Connecting to Kafka at {settings.KAFKA_BOOTSTRAP_SERVERS}...")

    consumer = AIOKafkaConsumer(
        settings.KAFKA_FILE_PROCESSING_TOPIC,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id="doc_processor_group",
        value_deserializer=lambda m: json.loads(m.decode('utf-8')),
        auto_offset_reset="earliest",
        enable_auto_commit=False,  # 手动提交：处理成功后显式 commit，失败的消息可重新消费
    )

    try:
        # start() 也纳入 try/finally：启动失败时同样执行 stop()，避免连接泄漏
        await consumer.start()
        # 消费者就绪后立即落第一次心跳，健康检查从此刻开始盯存活性
        touch_heartbeat()
        logger.info("Kafka Consumer started. Waiting for messages...")

        await run_consume_loop(
            consumer,
            # 把心跳刷新作为处理阶段回调传入：单篇大文档的解析/向量化可能
            # 远超心跳阈值，阶段级刷新避免把"长耗时处理"误判为"卡死"。
            process=lambda doc_id, job_id: processor.process(
                doc_id, job_id, on_progress=touch_heartbeat
            ),
        )

    except RedeliveryHalt as e:
        # issue #301 批 D（#30）：受限重投——偏移未提交，进程退出让容器重启
        # 从上次提交点重投该消息。显式 re-raise：非零退出 + 完整日志（不做静默退出）。
        logger.error("[CONSUMER] halting for redelivery: %s", e)
        raise
    except Exception as e:
        logger.error(f"Consumer crashed: {e}")
    finally:
        logger.info("Stopping consumer...")
        try:
            await consumer.stop()
        except Exception as e:
            logger.error(f"Error stopping consumer: {e}")

if __name__ == "__main__":
    try:
        # Windows 上 asyncio 的 loop policy 问题
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        asyncio.run(consume())
    except KeyboardInterrupt:
        logger.info("Consumer stopped manually")
