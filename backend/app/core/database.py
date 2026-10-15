"""
DocMind AI 知识库系统 - 数据库连接模块
"""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

# 创建基类
Base = declarative_base()

# 项目约定的 MySQL collation。外键要求两端完全一致，且 collation 决定唯一约束
# 与字符串比较的语义（如尾随空格），因此它属于 schema 契约的一部分。
# docker-compose.yml 的 --collation-server 与 SETUP.md 的 CREATE DATABASE 都用这个值，
# CI 也用它启动 MySQL service；三处必须保持一致，见 CHANGELOG。
CANONICAL_MYSQL_COLLATION = "utf8mb4_unicode_ci"

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

async def _warn_if_collation_differs() -> None:
    """MySQL 上若数据库默认 collation 与项目约定不符，打一条明确的告警。

    刻意只告警不抛异常：库仍然可用，但唯一约束与字符串比较的语义会与约定不同
    （例如 tags.name 的尾随空格处理），这类偏差不应该静默发生。
    """
    if _is_sqlite or "mysql" not in settings.DATABASE_URL.lower():
        return
    try:
        async with engine.connect() as conn:
            collation = (await conn.execute(text("SELECT @@collation_database"))).scalar()
        if collation and collation != CANONICAL_MYSQL_COLLATION:
            logger.warning(
                f"数据库默认 collation 为 {collation}，与项目约定的 "
                f"{CANONICAL_MYSQL_COLLATION} 不一致——唯一约束与字符串比较语义可能不同。"
                f"建议: CREATE DATABASE ... COLLATE {CANONICAL_MYSQL_COLLATION}（见 SETUP.md）"
            )
    except Exception as e:
        logger.debug(f"collation 检查跳过: {str(e)}")


async def _database_state(conn) -> tuple[int, int | None]:
    """返回 (库里的表数量, alembic_version 里的 revision 行数)，兼容 MySQL 与 SQLite。

    第二项为 None 表示 alembic_version 表不存在；表存在但 0 行则返回 0。
    两者必须区分（issue #154）：`alembic_version` 表**存在但为空**的库常见于
    create_all 建库后残留的空表，它和「没有这张表」一样都不是迁移建库。
    判据是「表里有没有 revision 行」，不是「表是否存在」。

    取行数用 COUNT(*)，不触碰列名/约束，因此表结构被改过也能安全求值；
    表不存在时不会去查行数，避免抛异常。
    """
    if conn.dialect.name == "mysql":
        table_count = (await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE()"
        ))).scalar() or 0
        has_alembic_version = bool((await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = 'alembic_version'"
        ))).scalar())
    else:
        table_count = (await conn.execute(text(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        ))).scalar() or 0
        has_alembic_version = bool((await conn.execute(text(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'table' AND name = 'alembic_version'"
        ))).scalar())

    if not has_alembic_version:
        return int(table_count), None

    revision_count = (await conn.execute(text(
        "SELECT COUNT(*) FROM alembic_version"
    ))).scalar() or 0
    return int(table_count), int(revision_count)


# 「非迁移建库」告警的修复指引。单独成串，两条告警（无表 / 空表）共用。
_ALEMBIC_ADOPT_HINT = (
    "\n修复步骤：\n"
    "  1. python scripts/check_schema.py           # 先核对库现状与模型的漂移\n"
    "  2. alembic stamp <与库现状一致的 revision>   # 给现有 schema 盖上版本号\n"
    "  3. alembic upgrade head                     # 让迁移补齐剩余变更"
)


async def init_db():
    """初始化数据库 — create_all 只用于**空库引导**，schema 的长期管理走 Alembic。

    设置 ``USE_ALEMBIC=true`` 环境变量可完全跳过 create_all。

    自 issue #150 起，create_all 仅在空库上执行：它不会给已有表补列，在非空库上
    运行只会在缺表时静默造出与迁移路径口径不同的表——这正是 issue #150 schema
    漂移的「另一半」来源。非空库会被跳过：alembic_version 里有 revision 记录
    说明 schema 归迁移管；表缺失**或表存在但 0 行**都视为非迁移建库
    （issue #154：空表恰是 create_all 建库的常见残留形态），打漂移告警。
    """
    try:
        import os
        use_alembic = os.getenv("USE_ALEMBIC", "").lower() in ("1", "true", "yes")

        await _warn_if_collation_differs()

        if use_alembic:
            logger.info("数据库 schema 由 Alembic 管理，跳过 create_all")
            return

        async with engine.connect() as conn:
            table_count, revision_count = await _database_state(conn)

        if table_count > 0:
            if revision_count:
                logger.info(
                    f"数据库非空且 alembic_version 有 {revision_count} 条 revision 记录，"
                    "schema 由迁移管理，跳过 create_all（可用 python scripts/check_schema.py "
                    "核对 revision 与迁移 head 是否一致）"
                )
            elif revision_count == 0:
                logger.warning(
                    "数据库非空，alembic_version 表存在但没有 revision 记录 —— 该库不是迁移"
                    "建库（如 create_all 建库后残留的空表），schema 不由 Alembic 管理。"
                    "create_all 不会给已有表补列/改列，为避免静默造出与迁移路径分叉的 schema，"
                    "已跳过。" + _ALEMBIC_ADOPT_HINT
                )
            else:  # revision_count is None：连 alembic_version 表都没有
                logger.warning(
                    "数据库非空，但没有 alembic_version 表 —— 该库不是迁移建库。"
                    "create_all 不会给已有表补列/改列，为避免静默造出与迁移路径分叉的 schema，"
                    "已跳过。" + _ALEMBIC_ADOPT_HINT
                )
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
