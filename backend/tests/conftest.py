"""Pytest 配置与公共 fixture。"""

import os
import sys

# Fix sys.path: ensure .venv-test packages take priority over leaked hermes venv
# (hermes venv's pydantic_core is incompatible with this Python version)
_VENV_TEST = os.path.join(os.path.dirname(__file__), "..", ".venv-test", "Lib", "site-packages")
_VENV_TEST = os.path.abspath(_VENV_TEST)
if os.path.isdir(_VENV_TEST):
    for _p in list(sys.path):
        if "hermes-agent" in _p:
            sys.path.remove(_p)
    if _VENV_TEST not in sys.path:
        sys.path.insert(0, _VENV_TEST)

import pytest
from fastapi.testclient import TestClient

# 在导入 app 之前设置测试环境变量，避免 Settings validator 报错
# tiktoken 的 BPE 缓存（issue #164 起随仓库携带，见 backend/docker/tiktoken_cache/）。
# 不指过去的话，tiktoken 首次 get_encoding() 会去公网下载：受限网络下拿不到 →
# 静默降级成启发式计数 → 依赖「精确计数」的用例（如 test_agent_loop_wiring 的
# 超预算压缩）会随环境时红时绿。指到仓库内的缓存后，测试对网络封闭、结果稳定。
_TIKTOKEN_CACHE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "docker", "tiktoken_cache")
)

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
    "ENABLE_SCHEDULER": "false",
    "PROMETHEUS_ENABLED": "true",
    "SANDBOX_MODE": "ast",  # force AST sandbox in tests (no Docker dependency)
}
if os.path.isdir(_TIKTOKEN_CACHE):
    _TEST_ENV["TIKTOKEN_CACHE_DIR"] = _TIKTOKEN_CACHE

# 应用测试环境变量
for k, v in _TEST_ENV.items():
    os.environ.setdefault(k, v)


@pytest.fixture(autouse=True)
def _reset_llm_breaker():
    """隔离全局熔断器（issue #242）：executor 的 `_LLM_BREAKER` 是进程级单例
    （生产正确：一个进程一份、跨请求累积）。但测试进程里，注入失败的用例会把
    它打到 OPEN，污染后续用例的 LLM 调用路径——每个用例前后复位，保证用例间
    行为与接线前一致。"""
    from app.agent import executor as _ex_mod

    _ex_mod._LLM_BREAKER._reset()
    yield
    _ex_mod._LLM_BREAKER._reset()


@pytest.fixture(scope="session")
def client():
    """创建 FastAPI TestClient（session 级别复用）。"""
    from app.main import app

    return TestClient(app)
