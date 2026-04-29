import asyncio
import json
import logging
import sys
import os

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
        auto_offset_reset="earliest"
    )
    
    await consumer.start()
    logger.info("Kafka Consumer started. Waiting for messages...")
    
    try:
        async for msg in consumer:
            logger.info(f"Received task: {msg.value}")
            data = msg.value
            doc_id = data.get("document_id")
            
            if doc_id:
                try:
                    await processor.process(doc_id)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
            else:
                logger.warning("Received message without document_id")
                
    except Exception as e:
        logger.error(f"Consumer crashed: {e}")
    finally:
        logger.info("Stopping consumer...")
        await consumer.stop()

if __name__ == "__main__":
    try:
        # Windows 上 asyncio 的 loop policy 问题
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
        asyncio.run(consume())
    except KeyboardInterrupt:
        logger.info("Consumer stopped manually")
