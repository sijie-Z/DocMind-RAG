import sys
import os
import asyncio
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("worker")

# 添加当前目录到 sys.path，确保能导入 app 模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 导入真正的消费者逻辑
from app.worker.consumer import consume

if __name__ == "__main__":
    logger.info("Starting RAG Worker Process...")
    try:
        # Windows 平台特定的 asyncio 设置
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
        asyncio.run(consume())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
    except Exception as e:
        logger.error(f"Worker failed to start: {e}")
