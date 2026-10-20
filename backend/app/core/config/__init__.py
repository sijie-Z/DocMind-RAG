"""Config package — split into focused modules for maintainability.

All settings are composed into a single ``Settings`` class via multiple
inheritance so that existing ``from app.core.config import settings``
imports continue to work unchanged.
"""

from app.core.config.ai import AISettings
from app.core.config.base import BaseAppSettings
from app.core.config.database import DatabaseSettings
from app.core.config.security import SecuritySettings


class Settings(BaseAppSettings, DatabaseSettings, AISettings, SecuritySettings):
    """Combined application settings — inherits all config sections."""
    pass


settings = Settings()


def resolve_embedding_model() -> str:
    """当前生效的 embedding 模型名 —— 唯一解析规则（issue #196）。

    本地模式用 ``LOCAL_EMBEDDING_MODEL``，在线模式用 ``EMBEDDING_MODEL``。
    索引侧（``app/core/elasticsearch.py`` 的 ``_meta``）与 embedding 客户端
    （``app/services/embedding_service.py``）都必须经由此函数取模型名；
    不要直接读 ``settings.EMBEDDING_MODEL``——本地模式下那是另一个模型，
    正是 #196 模型漂移（索引 nomic / 查询 bge-m3）的结构性来源。

    放在 config 包里是刻意的：纯函数、无客户端构造副作用，可被 ES 模块
    安全导入（不会因缺少 API key 而在 import 期炸掉只读工具）。
    """
    if settings.ENABLE_LOCAL_EMBEDDING:
        return settings.LOCAL_EMBEDDING_MODEL
    return settings.EMBEDDING_MODEL


# ── Auto-detect hardware tier (GPU/CPU) and apply model defaults ──────
# Runs once at import time; env vars take precedence over detection.
try:
    from app.core.config.ai import _auto_tier_defaults

    for _k, _v in _auto_tier_defaults(
        explicitly_set=settings.model_fields_set,
        local_embedding=bool(settings.ENABLE_LOCAL_EMBEDDING),
    ).items():
        if hasattr(settings, _k):
            object.__setattr__(settings, _k, _v)
except Exception:
    pass

__all__ = [
    "Settings",
    "settings",
    "resolve_embedding_model",
    "BaseAppSettings",
    "DatabaseSettings",
    "AISettings",
    "SecuritySettings",
]
