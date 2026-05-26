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

__all__ = [
    "Settings",
    "settings",
    "BaseAppSettings",
    "DatabaseSettings",
    "AISettings",
    "SecuritySettings",
]
