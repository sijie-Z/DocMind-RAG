import json
import logging
import asyncio
from typing import Any
from aiokafka import AIOKafkaProducer
from app.core.config import settings

logger = logging.getLogger(__name__)
        
class KafkaProducerClient:
    def __init__(self):
        self.producer = None
        self.bootstrap_servers = settings.KAFKA_BOOTSTRAP_SERVERS

    async def start(self):
        try:
            self.producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode('utf-8')
            )
            await self.producer.start()
            logger.info("Kafka producer started")
        except Exception as e:
            logger.error(f"Failed to start Kafka producer: {e}")
            # Do not raise, allow app to start without Kafka (but features will fail)
            self.producer = None

    async def stop(self):
        if self.producer:
            await self.producer.stop()
            logger.info("Kafka producer stopped")

    async def send_message(self, topic: str, value: Any):
        if not self.producer:
            logger.warning("Kafka producer is not initialized, skipping message send")
            return
        
        try:
            await self.producer.send_and_wait(topic, value)
            logger.info(f"Sent message to topic {topic}: {value}")
        except Exception as e:
            logger.error(f"Error sending message to Kafka: {e}")
            raise

# Global instance
kafka_producer = KafkaProducerClient()
