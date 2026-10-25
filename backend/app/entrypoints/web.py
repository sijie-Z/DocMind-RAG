"""web 入口：进程内 uvicorn + ``run.py`` 的端口探测（issue #479 §5.5）。

端口探测范围与 ``backend/run.py`` 一致（``PORT`` 起 10 个）。reload 场景仍由
``python run.py`` 保留（uvicorn reload 需要子进程模型，进程内启动不支持），
见 ``docs/decisions/ADR-003-profiles-bundles-patch.md``。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import uvicorn

from app.bootstrap import build_app
from app.core.config import settings
from app.core.logging import setup_logging

logger = logging.getLogger(__name__)

MAX_PORT_TRIES = 10
BACKEND_DIR = Path(__file__).resolve().parents[2]


def _port_helpers():
    """复用 run.py 的 ``is_port_free`` / ``write_port_file``（不修改 run.py）。"""
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from run import is_port_free, write_port_file

    return is_port_free, write_port_file


def run(args: argparse.Namespace | None = None, profile: str = "web") -> None:
    setup_logging()
    is_port_free, write_port_file = _port_helpers()

    preferred = int(settings.PORT)
    for offset in range(MAX_PORT_TRIES):
        port = preferred + offset
        if not is_port_free(port):
            continue

        write_port_file(str(BACKEND_DIR), port)
        if port != preferred:
            logger.warning("端口 %s 被占用，已自动切换到 %s", preferred, port)

        # run.py 用 DOCMIND_PORT 把实际端口传给子进程；进程内启动时同样写入，
        # 供 resolve_backend_port 与 .backend-port 语义保持一致。
        os.environ["DOCMIND_PORT"] = str(port)
        try:
            uvicorn.run(
                build_app(profile),
                host=settings.HOST,
                port=port,
                log_level=settings.LOG_LEVEL.lower(),
            )
            return
        except SystemExit as exc:
            if exc.code in (None, 0):
                return
            # uvicorn 的真实启动错误（含退出码）原样透传；只有预检端口占用
            # 或绑定 OSError 才尝试下一端口，不能伪装成范围端口冲突。
            raise
        except OSError as exc:
            logger.warning("端口 %s 绑定失败（%s），尝试下一个端口...", port, exc)

    raise SystemExit(f"无法在 {preferred}-{preferred + MAX_PORT_TRIES - 1} 范围内启动服务")
