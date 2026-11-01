"""check_schema._sync_url 的驱动映射（Batch 11）。

check_schema.py 用同步引擎做 schema 校验；DATABASE_URL 是异步驱动 URL，
必须映射到可用的同步驱动，否则本地/CI 会直接 create_engine 失败。
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_schema.py"


def _load_check_schema():
    spec = importlib.util.spec_from_file_location("check_schema_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sync_url_maps_async_drivers():
    mod = _load_check_schema()
    assert mod._sync_url("mysql+aiomysql://u:p@h:3306/db") == "mysql+pymysql://u:p@h:3306/db"
    assert mod._sync_url("mysql+asyncmy://u:p@h:3306/db") == "mysql+pymysql://u:p@h:3306/db"
    assert mod._sync_url("sqlite+aiosqlite:///tmp/x.db") == "sqlite:///tmp/x.db"


def test_sync_url_keeps_already_sync_urls():
    mod = _load_check_schema()
    assert mod._sync_url("mysql+pymysql://u:p@h:3306/db") == "mysql+pymysql://u:p@h:3306/db"
