"""`scripts/audit_report.py` 的单元测试。

这些测试存在的唯一理由，是 2026-09-20 的那次事故：校验器把 pip-audit **真实输出**判成
"工具失败"，nightly 因此长期红着却指向错误的原因。样本数据取自 pip-audit 2.10.1 的
真实输出（`pip-audit -r requirements.txt --format json`），不是照文档臆造的。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.audit_report import (
    EXIT_MALFORMED,
    EXIT_OK,
    ReportFormatError,
    iter_dependencies,
    main,
    render_summary,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "audit_report.py"

# --- 真实样本 -------------------------------------------------------------

# pip-audit 2.10.1 的实际顶层形态。注意：**不是**数组。
# 修复前，下面这份报告会被判成 malformed——这正是事故本身。
REAL_2X_REPORT = {
    "dependencies": [
        {
            "name": "requests",
            "version": "2.19.0",
            "vulns": [
                {"id": "PYSEC-2018-28", "fix_versions": ["2.20.0"], "aliases": []},
                {"id": "PYSEC-2023-74", "fix_versions": ["2.31.0"], "aliases": []},
            ],
        },
        {"name": "urllib3", "version": "2.8.0", "vulns": []},
    ],
    "fixes": [],
}

# 同一次运行的另一种合法结果：扫到了依赖，但一个漏洞都没有。
REAL_2X_CLEAN = {
    "dependencies": [{"name": "urllib3", "version": "2.8.0", "vulns": []}],
    "fixes": [],
}


def _write(tmp_path, payload, name: str = "report.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


# --- 顶层形态 -------------------------------------------------------------


def test_accepts_pip_audit_2x_shape():
    """回归测试：CI 事故里那份被误判的报告，必须被接受。"""
    deps = iter_dependencies(REAL_2X_REPORT)

    assert [d["name"] for d in deps] == ["requests", "urllib3"]


def test_accepts_legacy_list_shape():
    """pip-audit 1.x 的顶层形态是数组，仍要兼容。"""
    deps = iter_dependencies(REAL_2X_REPORT["dependencies"])

    assert len(deps) == 2


@pytest.mark.parametrize(
    "report",
    [
        pytest.param({"fixes": []}, id="dict-without-dependencies"),
        pytest.param({"dependencies": {"a": 1}}, id="dependencies-not-a-list"),
        pytest.param([], id="empty-list"),
        pytest.param({"dependencies": []}, id="empty-dependencies"),
        pytest.param("not json at all", id="top-level-string"),
        pytest.param(42, id="top-level-number"),
        pytest.param(None, id="top-level-null"),
    ],
)
def test_rejects_unrecognised_shapes(report):
    """无法识别的形态一律拒绝——绝不静默返回空列表。

    静默的空结果会把"工具坏了"伪装成"没有漏洞"，那正是这套校验要防的事。
    """
    with pytest.raises(ReportFormatError):
        iter_dependencies(report)


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param({"version": "1.0"}, id="missing-name"),
        pytest.param({"name": "pkg"}, id="missing-version"),
        pytest.param({"name": "pkg", "version": "1.0", "vulns": "oops"}, id="vulns-not-a-list"),
        pytest.param("pkg==1.0", id="entry-not-a-dict"),
    ],
)
def test_rejects_malformed_entries(entry):
    with pytest.raises(ReportFormatError):
        iter_dependencies({"dependencies": [entry]})


# --- 摘要渲染 -------------------------------------------------------------


def test_summary_lists_vulnerable_packages():
    summary = render_summary(iter_dependencies(REAL_2X_REPORT))

    assert "发现 1 个存在漏洞的依赖" in summary
    assert "- requests 2.19.0 — PYSEC-2018-28|PYSEC-2023-74" in summary
    # 无漏洞的依赖不该出现在清单里
    assert "urllib3" not in summary


def test_summary_reports_clean_run():
    summary = render_summary(iter_dependencies(REAL_2X_CLEAN))

    assert "未发现已知漏洞" in summary


def test_summary_deduplicates_repeated_advisory_ids():
    """真实输出里同一条 advisory 会重复出现，摘要应去重后按原顺序列出。"""
    report = {
        "dependencies": [
            {
                "name": "requests",
                "version": "2.19.0",
                "vulns": [
                    {"id": "PYSEC-2018-28"},
                    {"id": "PYSEC-2023-74"},
                    {"id": "PYSEC-2018-28"},
                ],
            }
        ],
        "fixes": [],
    }

    summary = render_summary(iter_dependencies(report))

    assert "- requests 2.19.0 — PYSEC-2018-28|PYSEC-2023-74" in summary


# --- CLI 契约 -------------------------------------------------------------


def test_cli_exit_ok_and_summary_on_stdout(tmp_path, capsys):
    exit_code = main(["--summary", _write(tmp_path, REAL_2X_REPORT)])

    assert exit_code == EXIT_OK
    assert "requests 2.19.0" in capsys.readouterr().out


def test_cli_exit_malformed_and_diagnostic_on_stderr(tmp_path, capsys):
    exit_code = main(["--summary", _write(tmp_path, {"fixes": []})])

    captured = capsys.readouterr()
    assert exit_code == EXIT_MALFORMED
    # 诊断必须在 stderr：CI 里 stdout 被重定向进摘要文件，只有 stderr 留在 job 日志。
    assert "dependencies" in captured.err
    assert captured.out == ""


def test_cli_exit_malformed_on_missing_file(tmp_path, capsys):
    exit_code = main(["--check", str(tmp_path / "does-not-exist.json")])

    assert exit_code == EXIT_MALFORMED
    assert capsys.readouterr().err


def test_cli_writes_utf8_even_under_non_utf8_locale(tmp_path):
    """`$GITHUB_STEP_SUMMARY` 要求 UTF-8，但 stdout 被重定向时 Python 默认用 locale 编码。

    这条用子进程跑：进程内的 `capsys` 替换了 stdout，测不出真实的重定向行为。
    """
    report = _write(tmp_path, REAL_2X_REPORT)
    out_path = tmp_path / "summary.md"
    env = {**os.environ, "PYTHONIOENCODING": "cp936"}

    with open(out_path, "wb") as handle:
        subprocess.run(
            [sys.executable, str(SCRIPT), "--summary", report],
            stdout=handle,
            env=env,
            check=True,
        )

    # 若脚本没强制 UTF-8，这里会抛 UnicodeDecodeError。
    assert "requests 2.19.0" in out_path.read_text(encoding="utf-8")
