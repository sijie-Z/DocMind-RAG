"""依赖审计报告解析器（pip-audit）。

## 为什么是仓库内脚本，而不是 workflow 里的 `python -c`

这段逻辑原本内联在 `.github/workflows/ci-nightly.yml` 里，并写死了「pip-audit 的 JSON 是
顶层数组」这个假设。pip-audit 2.x 实际输出的是 `{"dependencies": [...], "fixes": [...]}`，
于是一份**结构完全正常、也确实扫出了漏洞的报告**被判成"工具失败"。结果是 nightly 长期红着，
却指向一个错误的原因——这比不做校验更糟，因为它消耗掉了排查者的信任。

搬进仓库脚本后，格式假设由 `tests/unit/test_audit_report.py` 用**真实输出样本**钉住。

## 用法

    python scripts/audit_report.py --summary report.json   # 校验结构 + 输出 Markdown 摘要
    python scripts/audit_report.py --check   report.json   # 只校验结构

退出码：`0` = 报告结构完整；`2` = 结构不完整（**工具失败**，不是发现漏洞）。

注意：**"发现了漏洞"不是错误。** pip-audit 发现漏洞时会自己以 1 退出，但那仍是一份有效报告；
本脚本只判断"报告能不能读"，与"读到了什么"分开——这正是原内联版本没做到的事。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

EXIT_OK = 0
EXIT_MALFORMED = 2


class ReportFormatError(Exception):
    """报告不符合任何一种已知的 pip-audit JSON 形态。"""


def iter_dependencies(report: Any) -> list[dict]:
    """从 pip-audit 报告里取出依赖列表，兼容已知的两种顶层形态。

    - pip-audit 2.x：``{"dependencies": [...], "fixes": [...]}``
    - pip-audit 1.x：``[{...}, ...]``

    未知形态一律抛 :class:`ReportFormatError`（调用方据此判定为工具失败），
    绝不"尽力而为"地返回空列表——静默的空结果等于把工具故障伪装成"没有漏洞"。
    """
    if isinstance(report, list):
        deps = report
    elif isinstance(report, dict):
        if "dependencies" not in report:
            raise ReportFormatError(
                f"顶层是 dict 但没有 'dependencies' 键（实际键：{sorted(report)}）"
            )
        deps = report["dependencies"]
    else:
        raise ReportFormatError(f"顶层应为 list 或 dict，实际是 {type(report).__name__}")

    if not isinstance(deps, list):
        raise ReportFormatError(f"'dependencies' 应为 list，实际是 {type(deps).__name__}")
    if not deps:
        # `-r requirements.txt` 必然解析出依赖。空列表意味着解析环节没生效，
        # 而不是"这个项目没有依赖"，所以按工具失败处理。
        raise ReportFormatError("依赖列表为空——requirements 解析未生效，按工具失败处理")

    for index, dep in enumerate(deps):
        if not isinstance(dep, dict):
            raise ReportFormatError(f"dependencies[{index}] 应为 dict，实际是 {type(dep).__name__}")
        for key in ("name", "version"):
            if key not in dep:
                raise ReportFormatError(
                    f"dependencies[{index}] 缺少 '{key}'（实际键：{sorted(dep)}）"
                )
        if "vulns" in dep and not isinstance(dep["vulns"], list):
            raise ReportFormatError(
                f"dependencies[{index}].vulns 应为 list，实际是 {type(dep['vulns']).__name__}"
            )

    return deps


def _unique_ids(vulns: list[dict]) -> str:
    """按出现顺序去重后的 advisory ID 列表。

    pip-audit 会把同一条 advisory 重复列出（实测：`requests` 2.19.0 的 PYSEC-2018-28 出现两次，
    `urllib3` 1.23 的 22 条里有大量重复），直接渲染会让摘要比实际漏洞数长得多。
    """
    seen: dict[str, None] = {}
    for vuln in vulns:
        seen.setdefault(vuln.get("id", "?"), None)
    return "|".join(seen)


def render_summary(dependencies: list[dict]) -> str:
    """把依赖列表渲染成可直接追加到 `$GITHUB_STEP_SUMMARY` 的 Markdown。"""
    vulnerable = [
        (dep["name"], dep["version"], _unique_ids(dep.get("vulns", [])))
        for dep in dependencies
        if dep.get("vulns")
    ]
    if not vulnerable:
        return "未发现已知漏洞。\n"
    lines = [f"发现 {len(vulnerable)} 个存在漏洞的依赖："]
    lines += [f"- {name} {version} — {ids}" for name, version, ids in vulnerable]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    # 摘要会被追加进 `$GITHUB_STEP_SUMMARY`，该文件要求 UTF-8；而 Python 在 stdout 被重定向时
    # 默认用 locale 编码（中文 Windows 上是 cp936）。不强制就会在非 UTF-8 环境下写出乱码。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="解析 pip-audit JSON 报告")
    parser.add_argument("report", help="pip-audit --format json 的输出文件")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="只校验结构")
    mode.add_argument(
        "--summary", action="store_true", help="校验结构并把 Markdown 摘要写入 stdout"
    )
    args = parser.parse_args(argv)

    try:
        with open(args.report, encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        # 走 stderr：CI 里 stdout 会被重定向进摘要文件，诊断信息必须留在 job 日志里。
        print(f"无法读取或解析报告 {args.report}：{exc}", file=sys.stderr)
        return EXIT_MALFORMED

    try:
        dependencies = iter_dependencies(report)
    except ReportFormatError as exc:
        print(f"报告结构不符合已知的 pip-audit 格式：{exc}", file=sys.stderr)
        return EXIT_MALFORMED

    if args.summary:
        sys.stdout.write(render_summary(dependencies))
    else:
        print(f"报告结构完整：共 {len(dependencies)} 个依赖", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
