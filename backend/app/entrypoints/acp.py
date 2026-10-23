"""acp 入口：Zed Agent Client Protocol v1 over stdio（issue #481）。

- 身份只来自 CLI：``--org-id`` / ``--user-id`` 必填，缺失/非法 exit 2，
  stdout **0 字节**（不启动协议）；
- ``--api-key-file`` 可选：文件读 ``dm_`` key（不进 argv/日志），必须与
  ``--org-id/--user-id`` 一致；
- 身份校验（用户存在 / ``is_active`` / 属于该组织）在启动协议前完成，
  失败原因只写 stderr，exit 2；
- stdout 纯净：先保存真实 stdout 给协议 writer，``sys.stdout`` 指向 stderr
  （第三方 ``print`` 不会污染协议帧）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.log_safety import log_safe


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


async def _run_acp(real_stdout, *, organization_id: int, user_id: int, api_key: str | None) -> int:
    from app.acp.server import AcpServer, IdentityError, validate_identity
    from app.core.database import AsyncSessionLocal

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

    server = AcpServer(
        organization_id=organization_id,
        user_id=user_id,
        stdout=real_stdout,
        redirect_stdout=False,  # 入口已重定向（此处再重定向会覆盖保存的 stdout）
    )
    return await server.run()


def run(args: argparse.Namespace | None = None, profile: str = "acp") -> None:
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

    # stdout 纯净：先保存真实 stdout，再把 sys.stdout 指向 stderr。之后任何
    # 第三方 print / 日志 handler 都进 stderr。
    from app.rpc.server import _stderr_only_stdout

    real_stdout = sys.stdout
    _stderr_only_stdout(real_stdout)

    exit_code = asyncio.run(
        _run_acp(
            real_stdout,
            organization_id=int(org_id),
            user_id=int(user_id),
            api_key=api_key,
        )
    )
    sys.exit(exit_code)
