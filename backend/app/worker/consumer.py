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

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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
        logger.info("Kafka Consumer started. Waiting for messages...")

        async for msg in consumer:
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
                    await processor.process(doc_id, job_id)
                    # 处理成功后显式提交偏移；失败的消息不提交，重启后可重新消费
                    await consumer.commit()
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
            else:
                logger.warning("Received message without document_id")
                await consumer.commit()

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
