"""Redis connection module with lazy-initialized holder pattern.

Uses a proxy object for `redis_client` so that module-level imports
(`from app.core.redis import redis_client`) always resolve to the
live client, even when the import happens before `init_redis()`.
"""

import logging

import redis.asyncio as redis

from app.core.config import settings

logger = logging.getLogger(__name__)


class _RedisHolder:
    """Lifecycle-managed Redis client holder."""

    def __init__(self):
        self._client: redis.Redis | None = None

    @property
    def client(self) -> redis.Redis | None:
        return self._client

    async def initialize(self):
        if self._client is not None:
            return
        try:
            logger.info("Connecting to Redis...")
            self._client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_keepalive=True,
                retry_on_timeout=True,
            )
            await self._client.ping()
            logger.info("Redis connected")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}. Running without Redis.")
            self._client = None

    async def close(self):
        if self._client:
            try:
                await self._client.close()
                logger.info("Redis connection closed")
            except Exception as e:
                logger.error(f"Error closing Redis: {e}")
            finally:
                self._client = None


class _RedisProxy:
    """Proxy that delegates attribute access to the holder's client.

    This solves the Python import-binding problem: code that does
    ``from app.core.redis import redis_client`` at module level gets
    a reference to this proxy object.  Because the proxy delegates
    attribute lookups to ``_holder.client``, it always sees the
    current client (or None before init).
    """

    __slots__ = ("_holder",)

    def __init__(self, holder: _RedisHolder):
        object.__setattr__(self, "_holder", holder)

    def __bool__(self) -> bool:
        return self._holder.client is not None

    def __getattr__(self, name: str):
        client = self._holder.client
        if client is None:
            raise RuntimeError(
                "Redis client not available — call init_redis() first or "
                "check `if redis_client` before use"
            )
        return getattr(client, name)

    def __repr__(self) -> str:
        client = self._holder.client
        return f"<RedisProxy client={client!r}>"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_holder = _RedisHolder()
redis_client = _RedisProxy(_holder)  # safe to import at module level


async def init_redis():
    """Initialize Redis connection (called at app startup)."""
    await _holder.initialize()


async def get_redis() -> redis.Redis:
    """Get Redis client, initializing lazily if needed."""
    if _holder.client is None:
        await _holder.initialize()
    if _holder.client is None:
        raise RuntimeError("Redis client not available")
    return _holder.client


async def close_redis():
    """Close Redis connection (called at app shutdown)."""
    await _holder.close()


# ---------------------------------------------------------------------------
# Redis helper utilities
# ---------------------------------------------------------------------------
class RedisTools:
    """Static convenience wrappers around common Redis operations.

    失败语义（issue #291）：**查询/写入失败一律如实上抛**，不得与
    「键不存在」/「写入成功」同形。此前四个数据面方法把异常吞成
    None/False/空操作，调用方（令牌吊销链）无法区分「Redis 查不了」与
    「令牌不在黑名单」，于是登出恒报 200、已吊销 token 照常放行——三重
    fail-open。降级策略由调用方决定：本模块不替它猜。

    例外：*increment/decrement* 不在吊销链上，维持旧的「失败降级为 0」
    语义——全仓暂无调用方（待接线时需连同降级语义一起改造）。
    """

    @staticmethod
    async def set_cache(key: str, value: str, expire: int = 3600) -> None:
        """写入缓存键；失败上抛（此前静默吞掉，写入方无从知道自己没写成）。"""
        try:
            client = await get_redis()
            await client.setex(key, expire, value)
        except Exception as e:
            logger.warning(f"Redis cache set failed: {e}")
            raise

    @staticmethod
    async def get_cache(key: str) -> str | None:
        """读取缓存键；键不存在返回 None，**查询失败上抛**（不得与「不存在」同形）。"""
        try:
            client = await get_redis()
            return await client.get(key)
        except Exception as e:
            logger.warning(f"Redis cache get failed: {e}")
            raise

    @staticmethod
    async def delete_cache(key: str) -> None:
        """删除缓存键；失败上抛。"""
        try:
            client = await get_redis()
            await client.delete(key)
        except Exception as e:
            logger.warning(f"Redis cache delete failed: {e}")
            raise

    @staticmethod
    async def exists(key: str) -> bool:
        """键是否存在；**查询失败上抛**（不得与「不存在」同形，issue #291）。"""
        try:
            client = await get_redis()
            return await client.exists(key) > 0
        except Exception as e:
            logger.warning(f"Redis exists check failed: {e}")
            raise

    @staticmethod
    async def increment(key: str, amount: int = 1) -> int:
        try:
            client = await get_redis()
            return await client.incrby(key, amount)
        except Exception as e:
            logger.warning(f"Redis increment failed: {e}")
            return 0

    @staticmethod
    async def decrement(key: str, amount: int = 1) -> int:
        try:
            client = await get_redis()
            return await client.decrby(key, amount)
        except Exception as e:
            logger.warning(f"Redis decrement failed: {e}")
            return 0
