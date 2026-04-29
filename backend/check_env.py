import asyncio
import sys
import os
from minio import Minio
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

# Add current dir to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.core.config import settings

async def check_mysql():
    print(f"Checking MySQL ({settings.DATABASE_URL})...", end=" ")
    try:
        engine = create_async_engine(settings.DATABASE_URL)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        print("OK")
        return True
    except Exception as e:
        print(f"FAILED: {e}")
        return False

def check_minio():
    print(f"Checking MinIO ({settings.MINIO_ENDPOINT})...", end=" ")
    try:
        client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE
        )
        if not client.bucket_exists(settings.MINIO_BUCKET_NAME):
            print(f"Bucket '{settings.MINIO_BUCKET_NAME}' not found (will be created by app).", end=" ")
        print("OK")
        return True
    except Exception as e:
        print(f"FAILED: {e}")
        return False

async def check_kafka():
    print(f"Checking Kafka ({settings.KAFKA_BOOTSTRAP_SERVERS})...", end=" ")
    try:
        producer = AIOKafkaProducer(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS)
        await producer.start()
        await producer.stop()
        print("OK")
        return True
    except Exception as e:
        print(f"FAILED: {e}")
        return False

async def main():
    print("Environment Check Tool")
    print("----------------------")
    
    mysql_ok = await check_mysql()
    minio_ok = check_minio()
    kafka_ok = await check_kafka()
    
    print("\nSummary:")
    if mysql_ok and minio_ok and kafka_ok:
        print("✅ All systems go! You can run the backend and worker.")
    else:
        print("❌ Some services are not available. Please check docker-compose.")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
