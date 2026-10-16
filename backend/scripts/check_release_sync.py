"""发布纪律门禁：CHANGELOG 顶部版本标题与 APP_VERSION 必须「同 PR bump」。

缺陷模式（第二轮问题总清单 §三.15 / §7.2⑦）
--------------------------------------------
仓库的发布习惯是「chore(release): x.y.z」提交只改 CHANGELOG.md、忘记同步
`APP_VERSION`（backend/app/core/config/base.py）——1.22.0 与 1.22.1-rc.1 两次
发布，线上 /health 仍报 1.21.0。本脚本把「同 PR bump」变成 PR 门禁：本次改动
只要碰了发布版本（CHANGELOG 顶部版本标题或 APP_VERSION 任一变化），两者就
必须落在同一个版本上。

判据（diff 驱动，避免对存量不一致误红）
--------------------------------------
- base = 目标分支引用（CI 传入，如 origin/develop）；head = 当前工作区。
- top_versioned_heading()：按出现顺序取第一个版本化 `## [x.y.z]` 标题
  （跳过 [Unreleased]），只取方括号内版本号——标题里日期改动不触发。
- 触发：app_changed 或 top_changed；两者皆未变 → 通过（普通 PR 在
  [Unreleased] 下加条目不算发布）。
- 触发时要求 head 两侧 versions_equivalent()。

rc 版本规则（防误红，逐条有测试）
---------------------------------
- R1 写法归一：`1.22.1-rc.1` / `1.22.1-rc1` / `1.22.1_RC_1` / `v1.22.1-rc.1`
  视为同一版本（分隔符、大小写、v 前缀差异无关）。
- R2 日期无关：版本标题只比对版本 token，`- 2026-10-05` 日期变化不触发。
- R3 存量豁免：冻结基线里 APP_VERSION 落后于 CHANGELOG（如 1.21.0 vs
  1.22.1-rc.1）是历史欠账；只要本 PR 不动发布版本就不红——不信「修文档的
  PR 被逼先 bump 版本」。
- R4 预发布同规：顶部是 rc 时照常执行——rc 发布同样必须把 APP_VERSION
  bump 到同一个 rc 值，不允许「只改 CHANGELOG」复发。

退出码：0 = 通过（含「与发布无关」）；1 = 触发且不一致；2 = 工具失败
（base 引用解析不了 / 文件缺失或格式坏 / APP_VERSION 解析不到）。
--base 缺省（本地手跑）→ 打印跳过、退出 0；CI 恒传 --base，取不到即红
（不会 fail-open）。

输出编码：stdout / stderr 统一 UTF-8（issue #157 同款——中文 Windows 管道
默认 locale 编码会把诊断变乱码，测试以子进程按 UTF-8 读取）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

CHANGELOG_PATH = "CHANGELOG.md"
APP_CONFIG_PATH = "backend/app/core/config/base.py"

APP_VERSION_RE = re.compile(r'^\s*APP_VERSION\s*:\s*str\s*=\s*"([^"]+)"', re.MULTILINE)
HEADING_RE = re.compile(r"^##\s+\[([^\]]+)\]", re.MULTILINE)
_VERSIONISH_RE = re.compile(r"^v?\d")
_VERSION_RE = re.compile(
    r"^v?(\d+)\.(\d+)\.(\d+)(?:[-_.]?(?P<stage>rc|alpha|beta|a|b)[-_.]?(?P<num>\d+))?$",
    re.IGNORECASE,
)
_STAGE_RANK = {"alpha": 0, "a": 0, "beta": 1, "b": 1, "rc": 2, "": 3}


def top_versioned_heading(changelog_text: str) -> str | None:
    """取第一个版本化 `## [x.y.z...]` 标题（跳过 [Unreleased]）；没有则 None。"""
    for raw in HEADING_RE.findall(changelog_text):
        value = raw.strip()
        if value.lower() == "unreleased":
            continue
        if _VERSIONISH_RE.match(value):
            return value
    return None


def parse_app_version(source: str) -> str | None:
    """取 base.py 里 APP_VERSION 的字面量；解析不到返回 None。"""
    match = APP_VERSION_RE.search(source)
    return match.group(1).strip() if match else None


def _canonical(version: str) -> tuple[int, int, int, int, int] | None:
    """把版本字符串规范化为可比较元组；非 `x.y.z[-stage[n]]` 形态返回 None。"""
    match = _VERSION_RE.match(version.strip())
    if match is None:
        return None
    stage = (match.group("stage") or "").lower()
    number = int(match.group("num")) if match.group("num") else 0
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        _STAGE_RANK[stage],
        number,
    )


def versions_equivalent(left: str, right: str) -> bool:
    """rc 写法归一等价比较（R1）；无法规范化时退回字符串相等（保守）。"""
    canonical_left = _canonical(left)
    canonical_right = _canonical(right)
    if canonical_left is not None and canonical_right is not None:
        return canonical_left == canonical_right
    return left.strip() == right.strip()


@dataclass(frozen=True)
class Verdict:
    """判据结论：是否通过、是否由本 PR 触发、说明文本。"""

    ok: bool
    triggered: bool
    detail: str


def evaluate(
    base_app: str,
    base_top: str | None,
    head_app: str,
    head_top: str | None,
) -> Verdict:
    """纯函数判据：base / head 两份快照 → 结论（单测直接驱动）。"""
    app_changed = head_app != base_app
    top_changed = head_top != base_top

    if not app_changed and not top_changed:
        return Verdict(
            ok=True,
            triggered=False,
            detail="本 PR 未改动发布版本（APP_VERSION 与 CHANGELOG 顶部版本标题均未变），通过。",
        )

    if head_top is None:
        return Verdict(
            ok=False,
            triggered=True,
            detail=(
                "本 PR 改动了发布版本，但 CHANGELOG 里找不到版本化标题"
                "（形如 `## [x.y.z]`）——无法比对，请补上对应版本标题。"
            ),
        )

    if versions_equivalent(head_app, head_top):
        return Verdict(
            ok=True,
            triggered=True,
            detail=f"发布版本已同 PR 对齐：APP_VERSION = CHANGELOG 顶部标题 = {head_top}。",
        )

    return Verdict(
        ok=False,
        triggered=True,
        detail=(
            f"发布版本未同 PR bump：APP_VERSION = {head_app}，"
            f"CHANGELOG 顶部版本标题 = {head_top}（app_changed={app_changed}, "
            f"top_changed={top_changed}）。请在同一个 PR 里把两者改到一致"
            "（发布提交惯例：chore(release): x.y.z）。"
        ),
    )


def _read_head_file(root: Path, relative: str) -> str | None:
    try:
        return (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _git_show(root: Path, ref: str, relative: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{ref}:{relative}"],
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _base_ref_resolvable(root: Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True,
    )
    return result.returncode == 0


def main(argv: list[str] | None = None) -> int:
    """比对入口。--base 缺省时打印跳过并返回 0（仅供非 PR 场景）。"""
    parser = argparse.ArgumentParser(description="CHANGELOG 与 APP_VERSION 同 PR bump 比对")
    parser.add_argument(
        "--base",
        default=None,
        help="目标分支引用（如 origin/develop）。缺省则跳过检查。",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="仓库根目录（默认按脚本位置推断；测试用）。",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]

    head_changelog = _read_head_file(root, CHANGELOG_PATH)
    head_config_source = _read_head_file(root, APP_CONFIG_PATH)
    if head_changelog is None or head_config_source is None:
        print(
            f"工具失败：读取不到 {CHANGELOG_PATH} 或 {APP_CONFIG_PATH}（root={root}）",
            file=sys.stderr,
        )
        return 2
    head_app = parse_app_version(head_config_source)
    if head_app is None:
        print(f"工具失败：{APP_CONFIG_PATH} 里解析不到 APP_VERSION。", file=sys.stderr)
        return 2
    head_top = top_versioned_heading(head_changelog)

    if not args.base:
        print("跳过：未提供 --base（非 PR 场景）——同 PR bump 比对仅在 PR 上执行。")
        return 0

    if not _base_ref_resolvable(root, args.base):
        print(f"工具失败：base 引用解析不了：{args.base}", file=sys.stderr)
        return 2

    base_changelog = _git_show(root, args.base, CHANGELOG_PATH)
    base_config_source = _git_show(root, args.base, APP_CONFIG_PATH)
    if base_changelog is None or base_config_source is None:
        print(f"工具失败：读不到 {args.base} 上的 CHANGELOG / base.py。", file=sys.stderr)
        return 2
    base_app = parse_app_version(base_config_source)
    if base_app is None:
        print(
            f"工具失败：base 侧 {APP_CONFIG_PATH} 里解析不到 APP_VERSION。",
            file=sys.stderr,
        )
        return 2
    base_top = top_versioned_heading(base_changelog)

    verdict = evaluate(base_app, base_top, head_app, head_top)
    print(f"base={args.base}  head APP_VERSION={head_app}  head CHANGELOG 顶部={head_top}")
    if verdict.ok:
        print(f"检查通过：{verdict.detail}")
        return 0
    print(f"检查失败：{verdict.detail}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
