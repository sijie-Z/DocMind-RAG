"""`tests/integration/` 的目录级配置。

本目录下的测试自动获得 `integration` marker，不必逐个文件加装饰器。
marker 定义见 `pyproject.toml` 的 `[tool.pytest.ini_options].markers`。

由此 `-m integration` / `-m "not integration"` 才真正可用 —— 在此之前 marker
虽然声明了，但没有任何测试使用它（见 issue #86）。
"""

from pathlib import Path

import pytest

_INTEGRATION_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(config, items):
    """给 `tests/integration/` 下收集到的每个用例补上 `integration` marker。

    注意：`pytest_collection_modifyitems` 是 **session 级** hook —— 无论它定义在哪个
    `conftest.py`，收到的都是整个 session 收集到的**完整 items 列表**。运行
    `pytest tests/` 时 `unit/` 与 `behavior/` 的用例同样会传进来，所以必须按路径过滤，
    不能无条件 `add_marker`，否则全仓用例都会被标成 integration。
    """
    for item in items:
        if not item.path.is_relative_to(_INTEGRATION_DIR):
            continue
        if item.get_closest_marker("integration") is None:
            item.add_marker(pytest.mark.integration)
