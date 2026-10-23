"""Persistent terminal（issue #477）——容器持久 PTY 的编排层。

模块划分：

* :mod:`app.agent.terminal.manager` —— 与后端无关的生命周期/配额/回收/环形缓冲；
* :mod:`app.agent.terminal.docker_backend` —— Docker exec PTY 的生产后端；
* :mod:`app.services.terminal_service` —— 进程级单例（Redis owner/一次性票据/
  回收循环），供 HTTP/WS 端点与 Agent 工具共用。
"""

from app.agent.terminal.manager import (
    FakeBackend,
    TerminalBackend,
    TerminalBusy,
    TerminalConfig,
    TerminalDisabled,
    TerminalDockerUnavailable,
    TerminalError,
    TerminalInputTooLarge,
    TerminalManager,
    TerminalNotFound,
    TerminalQuotaExceeded,
    TerminalSession,
    TerminalTimeout,
)

__all__ = [
    "FakeBackend",
    "TerminalBackend",
    "TerminalBusy",
    "TerminalConfig",
    "TerminalDisabled",
    "TerminalDockerUnavailable",
    "TerminalError",
    "TerminalInputTooLarge",
    "TerminalManager",
    "TerminalNotFound",
    "TerminalQuotaExceeded",
    "TerminalSession",
    "TerminalTimeout",
]
