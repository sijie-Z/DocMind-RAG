"""Config package — split into focused modules for maintainability.

All settings are composed into a single ``Settings`` class via multiple
inheritance so that existing ``from app.core.config import settings``
imports continue to work unchanged.

issue #479：模块级 ``settings`` 改为惰性代理——import 本包不读 ``.env``、
不探测硬件、不构造 ``Settings``；首次属性访问才经
:func:`app.core.config.loader.load_settings` 按 profile/bundle/patch 分层加载。
"""

from app.core.config.ai import AISettings
from app.core.config.base import BaseAppSettings
from app.core.config.database import DatabaseSettings
from app.core.config.proxy import SettingsProxy
from app.core.config.security import SecuritySettings


class Settings(BaseAppSettings, DatabaseSettings, AISettings, SecuritySettings):
    """Combined application settings — inherits all config sections."""

    pass


settings = SettingsProxy()


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


__all__ = [
    "Settings",
    "settings",
    "resolve_embedding_model",
    "BaseAppSettings",
    "DatabaseSettings",
    "AISettings",
    "SecuritySettings",
]
