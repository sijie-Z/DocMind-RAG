# -*- coding: utf-8 -*-
"""Settings 配置验证测试。"""
import pytest
import os
from unittest.mock import patch


class TestSettingsValidation:
    """测试 Settings 的 model_validator 能正确拒绝弱配置。"""

    def test_missing_secret_key_raises(self):
        """SECRET_KEY 为空时应抛出 ValueError。"""
        env = {
            "SECRET_KEY": "",
            "JWT_SECRET_KEY": "some-jwt-key-that-is-long-enough",
            "DATABASE_URL": "mysql+aiomysql://root:root@localhost:3306/test",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(ValueError, match="SECRET_KEY must be set"):
                from app.core.config import Settings
                Settings()

    def test_missing_jwt_secret_key_raises(self):
        """JWT_SECRET_KEY 为空时应抛出 ValueError。"""
        env = {
            "SECRET_KEY": "some-secret-key-that-is-long-enough",
            "JWT_SECRET_KEY": "",
            "DATABASE_URL": "mysql+aiomysql://root:root@localhost:3306/test",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(ValueError, match="JWT_SECRET_KEY must be set"):
                from app.core.config import Settings
                Settings()

    def test_valid_settings_succeed(self):
        """两个密钥都设置时应正常创建。"""
        env = {
            "SECRET_KEY": "a" * 32,
            "JWT_SECRET_KEY": "b" * 32,
            "DATABASE_URL": "mysql+aiomysql://root:root@localhost:3306/test",
        }
        with patch.dict(os.environ, env, clear=False):
            from app.core.config import Settings
            s = Settings()
            assert s.SECRET_KEY == "a" * 32
            assert s.JWT_SECRET_KEY == "b" * 32

    def test_default_pool_size_is_reasonable(self):
        """连接池大小不应过大。"""
        env = {
            "SECRET_KEY": "a" * 32,
            "JWT_SECRET_KEY": "b" * 32,
            "DATABASE_URL": "mysql+aiomysql://root:root@localhost:3306/test",
        }
        with patch.dict(os.environ, env, clear=False):
            from app.core.config import Settings
            s = Settings()
            assert s.DATABASE_POOL_SIZE <= 50
            assert s.DATABASE_MAX_OVERFLOW <= 30

    def test_rate_limit_auth_not_excluded(self):
        """认证路径不应被排除在限流之外。"""
        env = {
            "SECRET_KEY": "a" * 32,
            "JWT_SECRET_KEY": "b" * 32,
            "DATABASE_URL": "mysql+aiomysql://root:root@localhost:3306/test",
        }
        with patch.dict(os.environ, env, clear=False):
            from app.core.config import Settings
            s = Settings()
            for path in s.RATE_LIMIT_EXCLUDE_PATHS:
                assert "auth" not in path.lower()
