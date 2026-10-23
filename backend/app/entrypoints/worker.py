"""worker 入口：Kafka 消费循环，无 HTTP、不启动进程内调度器（issue #479 §0.3）。

调度器只在 web 入口启动：``ENABLE_SCHEDULER`` 默认 true，若 worker 也启动，
标准 compose 部署（backend + worker）会双实例重复执行定时任务。此偏离见
``docs/decisions/ADR-003-profiles-bundles-patch.md``。
"""

from __future__ import annotations

import argparse
import asyncio

from app.core.logging import setup_logging


def run(args: argparse.Namespace | None = None, profile: str = "worker") -> None:
    setup_logging()
    from app.worker.consumer import consume

    asyncio.run(consume())
