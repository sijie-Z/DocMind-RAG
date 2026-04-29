# -*- coding: utf-8 -*-
"""
派聪明AI知识库系统 - Redis连接模块
"""

import logging
import redis.asyncio as redis
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Redis客户端实例
redis_client: Optional[redis.Redis] = None

async def init_redis():
    """初始化Redis连接"""
    global redis_client
    
    try:
        logger.info("正在连接Redis...")
        
        redis_client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_keepalive=True,
            retry_on_timeout=True
        )
        
        # 测试连接
        await redis_client.ping()
        logger.info("Redis连接成功")
        
    except Exception as e:
        logger.warning(f"Redis连接失败: {str(e)}。应用将以无Redis模式启动。")
        redis_client = None

async def get_redis() -> redis.Redis:
    """获取Redis客户端"""
    if redis_client is None:
        raise RuntimeError("Redis客户端未初始化")
    return redis_client

async def close_redis():
    """关闭Redis连接"""
    global redis_client
    
    if redis_client:
        try:
            await redis_client.close()
            logger.info("Redis连接已关闭")
        except Exception as e:
            logger.error(f"关闭Redis连接失败: {str(e)}")
        finally:
            redis_client = None

# Redis工具函数
class RedisTools:
    """Redis工具类"""
    
    @staticmethod
    async def set_cache(key: str, value: str, expire: int = 3600):
        """设置缓存"""
        try:
            client = await get_redis()
            await client.setex(key, expire, value)
        except Exception as e:
            logger.warning(f"Redis缓存设置失败: {e}")
    
    @staticmethod
    async def get_cache(key: str) -> Optional[str]:
        """获取缓存"""
        try:
            client = await get_redis()
            return await client.get(key)
        except Exception as e:
            logger.warning(f"Redis缓存获取失败: {e}")
            return None
    
    @staticmethod
    async def delete_cache(key: str):
        """删除缓存"""
        try:
            client = await get_redis()
            await client.delete(key)
        except Exception as e:
            logger.warning(f"Redis缓存删除失败: {e}")
    
    @staticmethod
    async def exists(key: str) -> bool:
        """检查键是否存在"""
        try:
            client = await get_redis()
            return await client.exists(key) > 0
        except Exception as e:
            logger.warning(f"Redis键检查失败: {e}")
            return False
    
    @staticmethod
    async def increment(key: str, amount: int = 1) -> int:
        """递增计数器"""
        try:
            client = await get_redis()
            return await client.incrby(key, amount)
        except Exception as e:
            logger.warning(f"Redis递增失败: {e}")
            return 0
    
    @staticmethod
    async def decrement(key: str, amount: int = 1) -> int:
        """递减计数器"""
        try:
            client = await get_redis()
            return await client.decrby(key, amount)
        except Exception as e:
            logger.warning(f"Redis递减失败: {e}")
            return 0