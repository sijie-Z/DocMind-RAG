"""Pytest 配置与公共 fixture。"""
import os

import pytest
from fastapi.testclient import TestClient

# 在导入 app 之前设置测试环境变量，避免 Settings validator 报错
_TEST_ENV = {
    "SECRET_KEY": "test-secret-key-for-pytest-only-32chars!",
    "JWT_SECRET_KEY": "test-jwt-secret-key-for-pytest-only!",
    "DATABASE_URL": "mysql+aiomysql://root:root@localhost:3306/test_db",
    "REDIS_HOST": "localhost",
    "REDIS_PORT": "6379",
    "ELASTICSEARCH_HOSTS": '["http://localhost:9200"]',
    "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
    "MINIO_ENDPOINT": "localhost:9000",
    "DEEPSEEK_API_KEY": "test-key",
    "EMBEDDING_API_KEY": "test-key",
    "ENABLE_RATE_LIMIT": "false",
    "ENABLE_MONITORING": "false",
}

# 应用测试环境变量
for k, v in _TEST_ENV.items():
    os.environ.setdefault(k, v)


@pytest.fixture(scope="session")
def client():
    """创建 FastAPI TestClient（session 级别复用）。"""
    from app.main import app
    return TestClient(app)
