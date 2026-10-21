"""``.backend-port`` 端口解析的单元测试（issue #170）。

回归对象：``app.main.resolve_backend_port()`` —— 它决定写进 ``.backend-port``
的端口值。旧实现是 ``int(os.environ.get("DOCMIND_PORT", settings.PORT))``：
没设 ``DOCMIND_PORT`` 时写入**配置端口**，命令行 ``--port`` 不同就会让
``frontend/vite.config.mts`` 的 ``/api`` 代理打到无人监听的端口
（``ECONNREFUSED``）。另外旧实现遇到 ``DOCMIND_PORT=``（空串）会
``int('')`` 直接崩掉启动。

真正起服务、断言「文件内容 == 实际监听端口」的用例在
``tests/integration/test_backend_port_file.py``。
"""

import sys

import pytest

from app.core.config import settings
from app.main import _parse_cli_port, _port_from_env, resolve_backend_port


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["prog", "app.main:app", "--port", "8123", "--reload"], 8123),
        (["prog", "--port=8123"], 8123),
        (
            ["prog", "--port", "8000", "--host", "0.0.0.0", "--port", "9000"],
            9000,
        ),  # 重复取最后（click 语义）
        (["prog", "app.main:app", "--reload"], None),  # 没传 --port
        (["prog", "--port"], None),  # 缺值
        (["prog", "--port", "abc"], None),  # 非法值
        (["prog", "--port="], None),
        (["prog", "--port", "70000"], None),  # 越界
        ([], None),
    ],
)
def test_parse_cli_port(argv, expected):
    assert _parse_cli_port(argv) == expected


def test_port_from_env(monkeypatch):
    monkeypatch.setenv("WS170_PORT", "8123")
    assert _port_from_env("WS170_PORT") == 8123

    monkeypatch.setenv("WS170_PORT", " 8123 ")
    assert _port_from_env("WS170_PORT") == 8123

    monkeypatch.setenv("WS170_PORT", "")
    assert _port_from_env("WS170_PORT") is None

    monkeypatch.setenv("WS170_PORT", "abc")
    assert _port_from_env("WS170_PORT") is None

    monkeypatch.setenv("WS170_PORT", "70000")
    assert _port_from_env("WS170_PORT") is None

    monkeypatch.delenv("WS170_PORT")
    assert _port_from_env("WS170_PORT") is None


@pytest.fixture
def no_port_env(monkeypatch):
    """清理端口相关环境变量，避免外部环境污染判定。"""
    monkeypatch.delenv("DOCMIND_PORT", raising=False)
    monkeypatch.delenv("UVICORN_PORT", raising=False)


def test_cli_port_wins_over_env_and_config(no_port_env, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["uvicorn", "app.main:app", "--port", "8123", "--reload"])
    monkeypatch.setenv("DOCMIND_PORT", "8010")
    monkeypatch.setenv("UVICORN_PORT", "8000")
    assert resolve_backend_port() == 8123


def test_docmind_port_used_when_no_cli_port(no_port_env, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["uvicorn", "app.main:app"])
    monkeypatch.setenv("DOCMIND_PORT", "8123")
    assert resolve_backend_port() == 8123


def test_uvicorn_port_used_when_no_cli_port_and_no_docmind_port(no_port_env, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["uvicorn", "app.main:app"])
    monkeypatch.setenv("UVICORN_PORT", "9001")
    assert resolve_backend_port() == 9001


def test_falls_back_to_settings_port_when_nothing_else_is_known(no_port_env, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["uvicorn", "app.main:app"])
    assert resolve_backend_port() == settings.PORT


def test_empty_docmind_port_is_skipped_instead_of_crashing(no_port_env, monkeypatch):
    """旧实现遇 DOCMIND_PORT="" 会 int('') 崩溃（应用启动失败），必须跳过该来源。"""
    monkeypatch.setattr(sys, "argv", ["uvicorn", "app.main:app"])
    monkeypatch.setenv("DOCMIND_PORT", "")
    assert resolve_backend_port() == settings.PORT
