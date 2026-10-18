"""CI 门禁：禁止生产代码把「组织上下文」硬编码默认到 1 号组织。

历史缺陷：多处以 `organization_id: int = 1` 作为默认参数，调用方漏传时
请求会静默落到 1 号组织，形成跨租户读写（issue #268 家族）。A2 已把全部
默认值改为 `None` + 入口 fail-closed；本门禁防止回流。

覆盖形态（均为「静默落到 1 号组织」的同义写法）：
1. 注解/赋值默认：``organization_id: int = 1``、``organization_id: int | None = 1``、
   ``organization_id: Optional[int] = 1``、``organization_id=1``；
2. 参数默认：``organization_id: int = Field(default=1)`` / ``Query(default=1)``；
3. 运行时兜底：``current_user.organization_id or 1``。

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
import io
import re
import sys
import tokenize
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = BACKEND_DIR / "app"

# 1) 注解/赋值默认值：容忍空格与 Optional[int] / int | None 写法；
# `(?![0-9])` 排除 10、11 等（只拦真正等于 1 的默认值）。
ASSIGN_DEFAULT_RE = re.compile(r"organization_id\s*(?::[^=\n]*)?=\s*1(?![0-9])")
# 2) Field(default=1) / Query(default=1) 等参数默认（同行需出现 organization_id）。
DEFAULT_KW_RE = re.compile(r"\bdefault\s*=\s*1(?![0-9])")
# 3) `organization_id or 1` 运行时兜底。
OR_ONE_RE = re.compile(r"\bor\s+1(?![0-9])")


def _strip_comment(line: str) -> str:
    """去掉行尾注释：注释里描述历史缺陷（`organization_id or 1`）不算违规。"""
    return line.split("#", 1)[0]


def _code_only(source: str) -> list[str]:
    """返回「只保留代码 token」的行文本：注释与字符串/文档字符串被置空。

    说明文字里描述历史缺陷（例如 docstring 中的 ``organization_id or 1``）
    不是可执行默认值，不得误报。tokenize 失败（语法不完整的样例）时退化为
    仅去 `#` 注释。
    """
    lines = source.splitlines()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return [_strip_comment(line) for line in lines]

    masked = list(lines)
    for tok in tokens:
        if tok.type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        (start_row, start_col), (end_row, end_col) = tok.start, tok.end
        for row in range(start_row, end_row + 1):
            idx = row - 1
            if not (0 <= idx < len(masked)):
                continue
            line = masked[idx]
            begin = start_col if row == start_row else 0
            end = end_col if row == end_row else len(line)
            masked[idx] = line[:begin] + " " * max(0, end - begin) + line[end:]
    return masked


def find_violations(root: Path) -> list[tuple[Path, int, str]]:
    hits: list[tuple[Path, int, str]] = []
    for path in sorted(root.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        original_lines = source.splitlines()
        for lineno, code in enumerate(_code_only(source), 1):
            if "organization_id" not in code:
                continue
            if (
                ASSIGN_DEFAULT_RE.search(code)
                or DEFAULT_KW_RE.search(code)
                or OR_ONE_RE.search(code)
            ):
                shown = (
                    original_lines[lineno - 1].strip()
                    if lineno - 1 < len(original_lines)
                    else code.strip()
                )
                hits.append((path, lineno, shown))
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
