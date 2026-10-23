"""sdk 入口：stdio JSON-RPC（issue #480 §5.5，issue #493 运行时硬化）。

- stdout 纯净：**本函数第一行**调用 ``configure_stdio_stdout()``——早于任何会
  创建 DB engine / 注册日志 handler 的 app import；协议帧只走保存下来的真实
  stdout（``app.rpc.server.ProtocolWriter``，句柄由 ``core.stdio`` 保管）；
- 依赖不静默降级：``run_stdio`` 进入协议循环前严格初始化 Redis 与 DB
  （cancel 旗 / prompt 幂等依赖 Redis），失败 → stderr 说明 + exit 2；
- 身份只来自 CLI：``--org-id`` / ``--user-id``，缺失 exit 2（stdout 无协议帧）；
- ``--api-key-file`` 可选：从文件读 ``dm_`` key（绝不进 argv/日志），启动时
  校验 key 有效且其 ``user_id`` / ``org_id`` 与 CLI 身份一致；任何不匹配
  → stderr 说明 + exit 2。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable


def _read_api_key_file(path: str) -> str | None:
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


# ``--api-key-file`` 身份校验的固定错误码（模块内字面量）。
# CodeQL py/clear-text-logging-sensitive-data：raw_key 参与的校验结果**不得**
# 以字符串形式流到 stderr；_run_entry 只按错误码打印固定文案。
IDENTITY_DEP_UNAVAILABLE = "dep_unavailable"
IDENTITY_INVALID = "invalid"
IDENTITY_USER_MISMATCH = "user_mismatch"
IDENTITY_ORG_MISMATCH = "org_mismatch"


async def _validate_api_key_identity(
    raw_key: str,
    *,
    org_id: int,
    user_id: int,
    db_factory: Callable[[], object] | None = None,
) -> str | None:
    """校验 key 有效且身份与 CLI 一致；返回固定错误码（None = 通过）。

    返回码是模块内字面量常量（``dep_unavailable`` / ``invalid`` /
    ``user_mismatch`` / ``org_mismatch``），**不携带 raw_key 的任何内容**。
    校验使用 ``api_key_service.authenticate``（bcrypt + 过期/吊销/禁用检查），
    成功时回填 ``token_organization_id`` 供组织一致性比较。打印处只输出固定
    文案，绝不透传任何由 raw_key 影响的字符串。
    """
    from app.core.database import AsyncSessionLocal
    from app.services.api_key_service import api_key_service

    factory = db_factory or AsyncSessionLocal
    try:
        async with factory() as db:  # type: ignore[operator]
            user = await api_key_service.authenticate(raw_key, db)
    except Exception:  # noqa: BLE001 - 依赖不可用也按"无法证明身份"拒绝
        return IDENTITY_DEP_UNAVAILABLE

    if user is None:
        return IDENTITY_INVALID
    if int(user.id) != int(user_id):
        return IDENTITY_USER_MISMATCH
    key_org_id = getattr(user, "token_organization_id", None)
    if key_org_id is None or int(key_org_id) != int(org_id):
        return IDENTITY_ORG_MISMATCH
    return None


async def _run_entry(
    *,
    organization_id: int,
    user_id: int,
    is_superuser: bool,
    raw_api_key: str | None,
) -> int:
    """单一事件循环内完成 key 校验 → stdio 运行时（避免跨事件循环复用连接池）。

    ``run_stdio`` 在进入协议循环前严格初始化 Redis/DB（失败打印 stderr 并
    返回 2，见 ``app.rpc.server``）。
    """
    if raw_api_key is not None:
        identity_error = await _validate_api_key_identity(
            raw_api_key,
            org_id=organization_id,
            user_id=user_id,
        )
        if identity_error is not None:
            # 只打印固定字面量文案：任何由 raw_key 影响的字符串都不得进入
            # stderr（CodeQL py/clear-text-logging-sensitive-data）。
            if identity_error == IDENTITY_DEP_UNAVAILABLE:
                print(
                    "--api-key-file 校验失败（数据库/Redis 不可用），拒绝启动",
                    file=sys.stderr,
                )
            elif identity_error == IDENTITY_INVALID:
                print("--api-key-file 的 API Key 无效、已过期或已吊销", file=sys.stderr)
            elif identity_error == IDENTITY_USER_MISMATCH:
                print(
                    "--api-key-file 的 API Key 属于其他用户（user_id 不匹配）",
                    file=sys.stderr,
                )
            elif identity_error == IDENTITY_ORG_MISMATCH:
                print(
                    "--api-key-file 的 API Key 未绑定 --org-id 指定的组织（org_id 不匹配）",
                    file=sys.stderr,
                )
            else:
                print("--api-key-file 校验失败，拒绝启动", file=sys.stderr)
            return 2

    from app.rpc.server import run_stdio

    return await run_stdio(
        organization_id=organization_id,
        user_id=user_id,
        is_superuser=is_superuser,
    )


def run(args: argparse.Namespace | None = None, profile: str = "sdk") -> None:
    # 必须最先执行：此刻只能 import 标准库与 app.core.stdio（import-safe）。
    # 之后任何 app 模块（database / rpc.server / logging 配置）都在重定向之后加载。
    from app.core.stdio import configure_stdio_stdout

    configure_stdio_stdout()

    org_id = getattr(args, "org_id", None)
    user_id = getattr(args, "user_id", None)
    if org_id is None or user_id is None:
        print(
            "sdk 入口需要 --org-id 与 --user-id（stdio 身份只从 CLI 参数读取）",
            file=sys.stderr,
        )
        sys.exit(2)

    raw_key: str | None = None
    key_file = getattr(args, "api_key_file", None)
    if key_file:
        raw_key = _read_api_key_file(key_file)
        if raw_key is None:
            sys.exit(2)

    exit_code = asyncio.run(
        _run_entry(
            organization_id=int(org_id),
            user_id=int(user_id),
            is_superuser=bool(getattr(args, "is_superuser", False)),
            raw_api_key=raw_key,
        )
    )
    sys.exit(exit_code)
