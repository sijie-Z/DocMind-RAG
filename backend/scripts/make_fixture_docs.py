"""从本机语料目录挑选真实文档，生成本地验收 fixture（**私有原文不入库**）。

用法：
    set DOCMIND_FIXTURE_SRC=D:\\Desktop\\python基础和八股文档与数据结构算法
    python -m scripts.make_fixture_docs --dest ../data/fixtures --limit 8

设计约束：
- 只读源目录，不修改原件；
- 输出目录（默认 `backend/data/fixtures`，已在 .gitignore 内）**不进版本库**；
- 源目录缺失时**不报错**，打印一行提示并退出 0 —— 让 CI/新机器无此目录也能跑通；
- 选择规则可测：按扩展名优先级 + 文件大小上限，稳定排序（路径字典序）保证可复现。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

DEFAULT_SOURCE_ENV = "DOCMIND_FIXTURE_SRC"
SUPPORTED = (".md", ".docx", ".pdf")
DEFAULT_LIMIT = 8
DEFAULT_MAX_BYTES = 4 * 1024 * 1024  # 4MB：超过的多为整本书，索引太慢


def select_documents(
    root: Path,
    *,
    limit: int = DEFAULT_LIMIT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    include: tuple[str, ...] = SUPPORTED,
) -> list[Path]:
    """按扩展名优先级挑选文件：.md 优先（解析快、可读），其次 .docx，最后 .pdf。

    同扩展名内按路径字典序，保证多次运行结果一致（可复现的验收语料）。
    空文件与超大文件跳过。
    """
    if not root.is_dir():
        return []
    picked: list[Path] = []
    for ext in include:
        candidates = sorted(
            (p for p in root.rglob(f"*{ext}") if p.is_file()),
            key=lambda p: str(p).lower(),
        )
        for path in candidates:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size <= 0 or size > max_bytes:
                continue
            picked.append(path)
            if len(picked) >= limit:
                return picked
    return picked


def copy_fixtures(paths: list[Path], dest: Path) -> list[Path]:
    """复制到目标目录；重名时加序号前缀避免覆盖。返回实际写入的路径。"""
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for idx, src in enumerate(paths, start=1):
        target = dest / f"{idx:02d}_{src.name}"
        shutil.copy2(src, target)
        written.append(target)
    return written


def resolve_source(explicit: str | None) -> Path | None:
    """优先 CLI 参数，其次环境变量；都没有则返回 None（不是错误）。"""
    raw = explicit or os.environ.get(DEFAULT_SOURCE_ENV, "")
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成本地验收 fixture 文档")
    parser.add_argument("--src", default=None, help=f"源目录（默认取 ${DEFAULT_SOURCE_ENV}）")
    parser.add_argument("--dest", default="data/fixtures", help="输出目录")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    args = parser.parse_args(argv)

    src = resolve_source(args.src)
    if src is None:
        print(
            f"[skip] 未找到源目录（--src 或 ${DEFAULT_SOURCE_ENV}），跳过 fixture 生成。"
            "这不影响 CI：测试会用内置小样本。"
        )
        return 0

    docs = select_documents(src, limit=args.limit, max_bytes=args.max_bytes)
    if not docs:
        print(f"[skip] {src} 下没有可用的 {', '.join(SUPPORTED)} 文件。")
        return 0

    written = copy_fixtures(docs, Path(args.dest))
    print(f"[ok] 复制 {len(written)} 份到 {args.dest}")
    for p in written:
        print(f"  - {p.name} ({p.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
