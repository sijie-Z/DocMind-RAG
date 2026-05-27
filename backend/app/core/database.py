"""
派聪明AI知识库系统 - 数据库连接模块
"""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

# 创建基类
Base = declarative_base()

# 数据库引擎
_is_sqlite = "sqlite" in settings.DATABASE_URL.lower()

engine = create_async_engine(
    settings.DATABASE_URL,
    **({} if _is_sqlite else {
        "pool_size": settings.DATABASE_POOL_SIZE,
        "max_overflow": settings.DATABASE_MAX_OVERFLOW,
        "pool_pre_ping": True,
        "pool_recycle": 3600,
    }),
    echo=settings.DEBUG
)

# 会话工厂
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def init_db():
    """初始化数据库 — 开发模式使用 create_all，生产模式应使用 Alembic。

    设置 ``USE_ALEMBIC=true`` 环境变量可跳过 create_all，由 Alembic 管理 schema。
    """
    try:
        import os
        use_alembic = os.getenv("USE_ALEMBIC", "").lower() in ("1", "true", "yes")

        if use_alembic:
            logger.info("数据库 schema 由 Alembic 管理，跳过 create_all")
            return

        logger.info("正在初始化数据库 (create_all)...")

        # 必须导入模型，否则 Base.metadata.create_all 不会创建任何表
        import app.models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("数据库初始化完成")

    except Exception as e:
        logger.error(f"数据库初始化失败: {str(e)}")
        raise

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception as e:
            logger.error(f"数据库会话错误: {str(e)}")
            await session.rollback()
            raise
        finally:
            await session.close()

async def close_db():
    """关闭数据库连接"""
    try:
        await engine.dispose()
        logger.info("数据库连接已关闭")
    except Exception as e:
        logger.error(f"关闭数据库连接失败: {str(e)}")

# 同步数据库引擎（用于某些需要同步操作的场景）
sync_engine = create_engine(
    settings.DATABASE_URL.replace("+aiomysql", "+pymysql").replace("+aiosqlite", ""),
    **({} if _is_sqlite else {
        "pool_size": settings.DATABASE_POOL_SIZE,
        "max_overflow": settings.DATABASE_MAX_OVERFLOW,
        "pool_pre_ping": True,
        "pool_recycle": 3600,
    })
)

SyncSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)

def get_sync_db():
    """获取同步数据库会话"""
    db = SyncSessionLocal()
    try:
        yield db
    finally:
        db.close()
