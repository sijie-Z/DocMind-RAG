"""acp 入口：Zed Agent Client Protocol v1 over stdio（issue #481 / #494）。

- stdout 纯净（issue #494）：``run()`` **第一行**调用
  :func:`app.core.stdio.configure_stdio_stdout`——早于任何会创建 DB engine /
  注册日志 handler 的 app import（``app.rpc.server`` 会 chain-import
  ``app.core.database``，SQLAlchemy ``echo`` handler 默认绑 stdout）；本模块
  顶层只 import 标准库。协议帧只走保存下来的真实 stdout；
- 身份只来自 CLI：``--org-id`` / ``--user-id`` 必填，缺失/非法 exit 2，
  stdout **0 字节**（不启动协议）；
- ``--api-key-file`` 可选：文件读 ``dm_`` key（不进 argv/日志），必须与
  ``--org-id/--user-id`` 一致；
- 身份校验（用户存在 / ``is_active`` / 属于该组织）在启动协议前完成，
  失败原因只写 stderr，exit 2；
- Redis 软初始化（issue #494）：cancel 旗依赖 Redis。连接不可用时只记 stderr
  警告并继续启动（身份/会话/协议仍可用）；``session/cancel`` 会如实返回
  -32603，绝不假报取消成功。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

# 启动期 Redis 软初始化的等待上限（秒）：协议进程不得为不可达的 Redis 推迟
# 握手；超过即视为取消通道不可用（session/cancel 如实 -32603）。
REDIS_INIT_TIMEOUT_SECONDS = 2.0


def _read_api_key_file(path: str) -> str | None:
    """读取 ``--api-key-file``；不可读或格式不符返回 None（原因写 stderr）。"""
    try:
        with open(path, encoding="utf-8") as handle:
            key = handle.read().strip()
    except OSError as exc:
        print(f"无法读取 --api-key-file: {exc}", file=sys.stderr)
        return None
    if not key.startswith("dm_"):
        print("--api-key-file 内容不是 dm_ 开头的 API Key", file=sys.stderr)
        return None
    return key


async def _init_cancel_channel() -> None:
    """软初始化 Redis（cancel 旗通道）；超时/不可用只警告，不阻塞启动。

    ``init_redis`` 自身吞掉连接失败（web 进程同语义）；这里再包一层有界等待：
    redis-py 8 默认对连接错误重试 10 次（指数退避），端口不应答时可拖 ~26s，
    协议进程不能为此推迟握手。超时后显式关闭 holder 里半初始化的客户端，
    否则 ``request_cancel`` 会在坏客户端上重复同样的重试。真正需要取消时若
    通道仍不可用，``session/cancel`` 会按 -32603 如实失败。
    """
    import logging

    logger = logging.getLogger(__name__)
    try:
        from app.core.redis import close_redis, init_redis, redis_client

        try:
            await asyncio.wait_for(init_redis(), timeout=REDIS_INIT_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning(
                "ACP Redis 初始化超时（%.1fs），取消通道不可用",
                REDIS_INIT_TIMEOUT_SECONDS,
            )
            try:
                await asyncio.wait_for(close_redis(), timeout=1.0)
            except Exception:  # noqa: BLE001 - 复位失败不阻塞启动
                logger.debug("ACP Redis holder 复位失败", exc_info=True)
            return
        if not redis_client:
            logger.warning("ACP Redis 不可用，取消通道不可用（session/cancel 将如实返回 -32603）")
    except Exception:  # noqa: BLE001 - 启动期软依赖，绝不阻塞协议循环
        logger.warning("ACP Redis 初始化失败，取消通道不可用", exc_info=True)


async def _run_acp(real_stdout, *, organization_id: int, user_id: int, api_key: str | None) -> int:
    # 本函数内才 import app 模块：此时 stdout 已重定向（run() 第一行），
    # SQLAlchemy echo / 第三方 print 只会落 stderr。
    from app.acp.server import AcpServer, IdentityError, validate_identity
    from app.core.database import AsyncSessionLocal
    from app.core.log_safety import log_safe

    try:
        await validate_identity(
            AsyncSessionLocal,
            organization_id=organization_id,
            user_id=user_id,
            api_key=api_key,
        )
    except IdentityError as exc:
        print(f"ACP 身份校验失败: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - 启动期基础设施失败：如实报错
        print(f"ACP 启动失败（身份校验无法完成）: {log_safe(exc)}", file=sys.stderr)
        return 1

    await _init_cancel_channel()

    server = AcpServer(
        organization_id=organization_id,
        user_id=user_id,
        stdout=real_stdout,
        redirect_stdout=False,  # 入口已重定向（此处再重定向会覆盖保存的 stdout）
    )
    return await server.run()


def run(args: argparse.Namespace | None = None, profile: str = "acp") -> None:
    # 必须最先执行：此刻只能 import 标准库与 app.core.stdio（import-safe）。
    # 之后任何 app 模块（rpc.server → database / logging 配置）都在重定向之后加载。
    from app.core.stdio import configure_stdio_stdout, get_protocol_stdout

    configure_stdio_stdout()
    # 入口首次调用 configure 时保存的就是真实 stdout；幂等场景（已被宿主重定向）
    # 回退到已保存的协议写端。
    real_stdout = get_protocol_stdout() or sys.stdout

    org_id = getattr(args, "org_id", None)
    user_id = getattr(args, "user_id", None)
    if org_id is None or user_id is None:
        print(
            "acp 入口需要 --org-id 与 --user-id（stdio 身份只从 CLI 参数读取）",
            file=sys.stderr,
        )
        sys.exit(2)
    if org_id <= 0 or user_id <= 0:
        print("--user-id / --org-id 必须是正整数", file=sys.stderr)
        sys.exit(2)

    api_key: str | None = None
    key_file = getattr(args, "api_key_file", None)
    if key_file:
        api_key = _read_api_key_file(key_file)
        if api_key is None:
            sys.exit(2)

    exit_code = asyncio.run(
        _run_acp(
            real_stdout,
            organization_id=int(org_id),
            user_id=int(user_id),
            api_key=api_key,
        )
    )
    sys.exit(exit_code)
