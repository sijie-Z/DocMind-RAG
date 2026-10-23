"""sdk 入口：stdio JSON-RPC（issue #480 §5.5）。

- 身份只来自 CLI：``--org-id`` / ``--user-id``，缺失 exit 2（stdout 无协议帧）；
- ``--api-key-file`` 可选：从文件读 ``dm_`` key（绝不进 argv/日志），启动时
  校验 key 有效且其 ``user_id`` / ``org_id`` 与 CLI 身份一致；任何不匹配
  → stderr 说明 + exit 2；
- stdout 纯净：在导入 RPC 模块前把 ``sys.stdout`` 指到 stderr，协议帧只走
  保存下来的真实 stdout（``app.rpc.server.ProtocolWriter``）。
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


async def _validate_api_key_identity(
    raw_key: str,
    *,
    org_id: int,
    user_id: int,
    db_factory: Callable[[], object] | None = None,
) -> str | None:
    """校验 key 有效且身份与 CLI 一致；返回错误文案（None = 通过）。

    校验使用 ``api_key_service.authenticate``（bcrypt + 过期/吊销/禁用检查），
    成功时回填 ``token_organization_id`` 供组织一致性比较。错误文案不包含
    key 明文、哈希或数据库细节。
    """
    from app.core.database import AsyncSessionLocal
    from app.services.api_key_service import api_key_service

    factory = db_factory or AsyncSessionLocal
    try:
        async with factory() as db:  # type: ignore[operator]
            user = await api_key_service.authenticate(raw_key, db)
    except Exception:  # noqa: BLE001 - 依赖不可用也按"无法证明身份"拒绝
        return "--api-key-file 校验失败（数据库/Redis 不可用），拒绝启动"

    if user is None:
        return "--api-key-file 的 API Key 无效、已过期或已吊销"
    if int(user.id) != int(user_id):
        return "--api-key-file 的 API Key 属于其他用户（user_id 不匹配）"
    key_org_id = getattr(user, "token_organization_id", None)
    if key_org_id is None or int(key_org_id) != int(org_id):
        return "--api-key-file 的 API Key 未绑定 --org-id 指定的组织（org_id 不匹配）"
    return None


def run(args: argparse.Namespace | None = None, profile: str = "sdk") -> None:
    org_id = getattr(args, "org_id", None)
    user_id = getattr(args, "user_id", None)
    if org_id is None or user_id is None:
        print(
            "sdk 入口需要 --org-id 与 --user-id（stdio 身份只从 CLI 参数读取）",
            file=sys.stderr,
        )
        sys.exit(2)

    key_file = getattr(args, "api_key_file", None)
    if key_file:
        raw_key = _read_api_key_file(key_file)
        if raw_key is None:
            sys.exit(2)
        error = asyncio.run(
            _validate_api_key_identity(
                raw_key,
                org_id=int(org_id),
                user_id=int(user_id),
            )
        )
        if error is not None:
            print(error, file=sys.stderr)
            sys.exit(2)

    from app.rpc.server import run_stdio

    exit_code = asyncio.run(
        run_stdio(
            organization_id=int(org_id),
            user_id=int(user_id),
            is_superuser=bool(getattr(args, "is_superuser", False)),
        )
    )
    sys.exit(exit_code)
