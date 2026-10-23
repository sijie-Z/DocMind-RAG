"""stdio 进程的 stdout 净化（issue #493）。

stdio 协议（JSON-RPC NDJSON / ACP）要求 **stdout 只承载协议帧**。Python 的
第三方库与日志 handler 经常在 import / 构造期直接绑定 ``sys.stdout``——
最典型的是 SQLAlchemy ``echo=True``（``InstanceLogger`` 会往
``sqlalchemy.engine.Engine`` 上挂一个 ``StreamHandler(sys.stdout)``），以及
uvicorn / httpx 等具名 logger 的 console handler。

本模块提供 :func:`configure_stdio_stdout`：

- 保存真实 stdout 作为协议写端，把 ``sys.stdout`` 指向 ``sys.stderr``；
- 把**已经挂在真实 stdout 上**的 ``StreamHandler``（root 与全部具名 logger，
  如 ``sqlalchemy.engine.Engine`` / ``uvicorn.error`` / ``httpx``）迁移到
  stderr——只改 root 会漏掉 SQLAlchemy 这类自带 handler 的 logger；
- 返回真实 stdout 供 ``ProtocolWriter`` 使用。

**必须在 import 任何会创建 DB engine / 注册日志 handler 的 app 模块之前调用**
（例如 ``entrypoints/sdk.py`` 的第一行）。本模块只依赖标准库，import 无副作用。
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from typing import TextIO


def _iter_loggers() -> Iterator[logging.Logger]:
    """遍历 root 与 ``logging.Logger.manager.loggerDict`` 中的全部具名 logger。"""
    yield logging.getLogger()
    for candidate in list(logging.Logger.manager.loggerDict.values()):
        # loggerDict 里的占位符是 logging.PlaceHolder，只处理真实 Logger。
        if isinstance(candidate, logging.Logger):
            yield candidate


def migrate_stdout_handlers(real_stdout: TextIO) -> list[str]:
    """把绑定在 ``real_stdout`` 上的日志 handler 迁到 ``sys.stderr``。

    返回被迁移的 logger 名（root 记为空串），便于测试与排障。只迁移
    ``StreamHandler`` 且 ``handler.stream is real_stdout`` 的对象：文件 handler
    或已指向 stderr 的 handler 不受影响；``FileHandler`` 虽是子类，但它的
    stream 不是 stdout，同样不会命中。
    """
    migrated: list[str] = []
    for logger in _iter_loggers():
        for handler in list(logger.handlers):
            if isinstance(handler, logging.StreamHandler) and handler.stream is real_stdout:
                handler.setStream(sys.stderr)
                migrated.append(logger.name)
    return migrated


_protocol_stdout: TextIO | None = None


def get_protocol_stdout() -> TextIO | None:
    """返回 :func:`configure_stdio_stdout` 保存的协议写端（尚未调用则 None）。

    ``run_stdio`` 以它作为 ``ProtocolWriter`` 的写端：入口已重定向时
    ``sys.stdout`` 是 stderr，不能再当作协议流。
    """
    return _protocol_stdout


def configure_stdio_stdout() -> TextIO:
    """保存真实 stdout 为协议写端，并把 ``sys.stdout`` 指向 stderr。

    同时把已挂在真实 stdout 上的日志 handler（root 与 sqlalchemy/uvicorn/
    httpx/sqlalchemy.engine 等具名 logger）迁移到 stderr。必须在 import 任何
    app 模块之前调用；返回保存下来的真实 stdout。

    幂等：若 ``sys.stdout`` 已是 stderr（重复调用），直接返回 stderr，不重复迁移。
    """
    global _protocol_stdout
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    if real_stdout is not sys.stderr:
        _protocol_stdout = real_stdout
        migrate_stdout_handlers(real_stdout)
    return real_stdout
