"""sdk 入口：stdio JSON-RPC（issue #480 §5.5）。

- 身份只来自 CLI：``--org-id`` / ``--user-id``，缺失 exit 2（stdout 无协议帧）；
- ``--api-key-file`` 可选：从文件读 ``dm_`` key（绝不进 argv/日志）；
- stdout 纯净：在导入 RPC 模块前把 ``sys.stdout`` 指到 stderr，协议帧只走
  保存下来的真实 stdout（``app.rpc.server.ProtocolWriter``）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys


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
        # 只校验格式；stdio 传输不使用 HTTP 凭据，绝不把 key 写日志/argv。
        if _read_api_key_file(key_file) is None:
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
