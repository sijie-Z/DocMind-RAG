"""统一 CLI：``python -m app --profile ...``（issue #479 §5.6）。

执行顺序（不得调换）：

1. ``argparse`` 解析——此时不 import ``app.main``，避免业务模块抢先加载配置；
2. ``load_settings(profile, patches)``；非法配置 → stderr 单行 JSON、exit 2；
3. ``--dump-config`` → stdout 排序 JSON（secret 打码）、exit 0；
4. 按 entrypoint 动态 import ``app.entrypoints.<entrypoint>`` 并运行。
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys

from app.core.config.schema import ConfigError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app",
        description="DocMind 统一入口：按 profile 合并配置并启动对应 entrypoint。",
    )
    parser.add_argument(
        "--profile",
        default=None,
        metavar="NAME",
        help="web|worker|headless|sdk|acp（默认 web；base 非法）",
    )
    parser.add_argument(
        "--patch",
        action="append",
        default=None,
        metavar="FILE",
        help="按顺序应用的 patch 文件（可重复）",
    )
    parser.add_argument(
        "--dump-config",
        action="store_true",
        help="打印排序 JSON 配置（secret 打码）后退出",
    )
    parser.add_argument("--query", default=None, help="headless：要执行的问题")
    parser.add_argument("--user-id", type=int, default=None, help="headless/sdk：用户 id")
    parser.add_argument("--org-id", type=int, default=None, help="headless/sdk：组织 id")
    parser.add_argument(
        "--api-key-file",
        default=None,
        metavar="FILE",
        help="sdk：从文件读取 dm_ API Key（不放 argv；缺失则仅用 CLI 身份）",
    )
    parser.add_argument("--json", action="store_true", help="headless：显式声明单行 JSON 输出")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="headless：只校验参数与配置，不调用 LLM、不启动 HTTP",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from app.core.config.loader import (
        config_hash,
        get_active_bundles,
        get_active_entrypoint,
        get_active_profile,
        load_settings,
        mask_secrets,
    )

    try:
        settings_obj = load_settings(args.profile, args.patch)
    except ConfigError as exc:
        print(
            json.dumps(
                {"error": "CONFIG_INVALID", "path": exc.path, "message": exc.message},
                ensure_ascii=True,
            ),
            file=sys.stderr,
        )
        return 2

    # 启动可观测性：每次加载打印一行（stderr，避免污染 --dump-config 的 stdout）。
    # 通过 app.main / uvicorn 启动时，同一行由 loader 的 logger 输出。
    masked_settings = mask_secrets(settings_obj.model_dump())
    assembly = {
        "profile": get_active_profile(),
        "bundles": get_active_bundles(),
        "settings": masked_settings,
    }
    print(
        f"config profile={assembly['profile']} "
        f"bundles={','.join(assembly['bundles'])} "
        f"config_hash={config_hash(assembly)}",
        file=sys.stderr,
    )

    if args.dump_config:
        payload = {
            **assembly,
            "entrypoint": get_active_entrypoint(),
        }
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2))
        return 0

    entrypoint = get_active_entrypoint()
    module = importlib.import_module(f"app.entrypoints.{entrypoint}")
    result = module.run(args)
    return int(result) if result is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
