"""CI 门禁：禁止生产代码把「组织上下文」硬编码默认到 1 号组织。

历史缺陷：多处以 `organization_id: int = 1` 作为默认参数，调用方漏传时
请求会静默落到 1 号组织，形成跨租户读写（issue #268 家族）。A2 已把全部
默认值改为 `None` + 入口 fail-closed；本门禁防止回流。

扫描范围：``backend/app``（生产代码）。``tests/`` 不扫描——测试夹具显式构造
组织上下文是合法用法，不参与生产路径。脚本零依赖（仅标准库），可在
CI hygiene job 用 ``--noconftest`` 直接跑。

用法::

    python scripts/check_org_defaults.py            # 扫描 backend/app
    python scripts/check_org_defaults.py --root DIR # 扫描指定目录（测试用）

退出码：0 = 通过；1 = 命中违规；2 = 扫描根不存在。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = BACKEND_DIR / "app"

# 容忍空格：organization_id: int = 1 / organization_id:int=1
# `(?![0-9])` 排除 10、11 等（只拦真正等于 1 的默认值）。
VIOLATION_RE = re.compile(r"organization_id\s*:\s*int\s*=\s*1(?![0-9])")


def find_violations(root: Path) -> list[tuple[Path, int, str]]:
    hits: list[tuple[Path, int, str]] = []
    for path in sorted(root.rglob("*.py")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(lines, 1):
            if VIOLATION_RE.search(line):
                hits.append((path, lineno, line.strip()))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)

    root: Path = args.root
    if not root.is_dir():
        print(f"ERROR: 扫描根不存在: {root}", file=sys.stderr)
        return 2

    hits = find_violations(root)
    if hits:
        print(
            "发现 organization_id 硬编码默认组织（必须改为 None + 入口 fail-closed）:",
            file=sys.stderr,
        )
        for path, lineno, text in hits:
            print(f"  {path}:{lineno}: {text}", file=sys.stderr)
        return 1

    print(f"OK: {root} 中不存在 organization_id 硬编码默认组织")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
