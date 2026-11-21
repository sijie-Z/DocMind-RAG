"""竞品对标 P1：统一脱敏 + 只读留存筛选的判别性测试。

覆盖：敏感模式（PII/凭据）不可逆替换、超长截断、Unicode 控制字符转义、
RunReport 导出字段（SSE dict 与渲染文本）不携带原文、纯函数不写日志，
以及留存筛选按天/边界/None 的语义与零删除副作用。
"""

from __future__ import annotations

import ast
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.agent.exec_context import ExecutionContext
from app.agent.llm_call_reader import select_llm_calls_for_retention
from app.agent.observability_masking import (
    mask_observability_text,
    mask_run_report_preview,
)
from app.agent.run_report import build_run_report, render_run_report_text
from app.core.config import Settings

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


def _row(when: datetime, **extra: object) -> dict[str, object]:
    return {
        "ts": when.isoformat(timespec="milliseconds"),
        "task_id": "task-1",
        "request_id": "req-1",
        "span_id": "span-1",
        "prompt": "raw body must never leak",
        **extra,
    }


class TestFieldMasking:
    @pytest.mark.parametrize(
        ("raw", "needle"),
        [
            ("联系 user@example.com 获取", "user@example.com"),
            ("手机 13812345678 请回电", "13812345678"),
            ("Bearer AbCdEfGhIjKlMnOpQrSt", "AbCdEfGhIjKlMnOpQrSt"),
            ("sk-abcdefghijklmnopqrstuv", "sk-abcdefghijklmnopqrstuv"),
            ("api_key: supersecretvalue", "supersecretvalue"),
            ("110101199003071234", "110101199003071234"),
            ("4111111111111111", "4111111111111111"),
            ("来自 192.168.1.100 的请求", "192.168.1.100"),
            ("password=SuperSecret123", "SuperSecret123"),
            ("token=ghp_ABCDEFGHIJKLMNOPQRST", "ghp_ABCDEFGHIJKLMNOPQRST"),
            ("Authorization: Bearer ghp_ABCDEFGHIJKLMNOPQRST", "ghp_ABCDEFGHIJKLMNOPQRST"),
            ("github_pat_ABCDEFGHIJKLMNOPQRSTUVWXYZ", "github_pat_ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
            ("xoxb-123456789012-abcdefghijkl", "xoxb-123456789012-abcdefghijkl"),
            ("来自 2001:db8::1 的请求", "2001:db8::1"),
            ("SSN 123-45-6789", "123-45-6789"),
        ],
    )
    def test_sensitive_patterns_are_irreversibly_masked(self, raw: str, needle: str):
        masked = mask_run_report_preview(raw)
        assert needle not in masked
        assert "REDACTED" in masked

    def test_long_value_is_capped(self):
        masked = mask_run_report_preview("x" * 5000)
        assert len(masked) <= 400 + len("...[truncated]")
        assert masked.endswith("...[truncated]")

    def test_unicode_controls_and_newlines_are_escaped(self):
        masked = mask_run_report_preview("a b\x85c​d\te\nf")
        assert "\n" not in masked and "\t" not in masked and "\r" not in masked
        assert " " not in masked and "\x85" not in masked and "​" not in masked
        assert "\\u2028" in masked and "\\u0085" in masked and "\\u200b" in masked
        assert "\\t" in masked and "\\n" in masked

    def test_normal_chinese_text_is_not_false_positive(self):
        normal = "请核验合同第三条第（一）款，重点检查付款期限与违约责任。"
        assert mask_run_report_preview(normal) == normal

    def test_key_value_masking_does_not_double_wrap(self):
        """token 前缀规则先命中时，键值规则不得再套一层括号（修前 [..]]）。"""
        assert mask_run_report_preview("token=ghp_ABCDEFGHIJKLMNOPQRST") == (
            "token=[SECRET_REDACTED]"
        )
        assert mask_run_report_preview("authorization: Bearer abcdefghijklmnop") == (
            "authorization: [SECRET_REDACTED]"
        )

    @pytest.mark.parametrize("address", ["::1", "::", "fe80::1ff:fe23:4567:890a"])
    def test_compressed_ipv6_forms_are_masked(self, address: str):
        assert mask_run_report_preview(address) == "[PII_REDACTED]"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ('password: "quoted secret value"', "password: [SECRET_REDACTED]"),
            ("api_key: 'x y'", "api_key: [SECRET_REDACTED]"),
            ('password: "a b"', "password: [SECRET_REDACTED]"),
            ('token="abc def"', "token=[SECRET_REDACTED]"),
            ('password: "unterminated secret', "password: [SECRET_REDACTED]"),
        ],
    )
    def test_quoted_key_value_consumes_whole_value(self, raw: str, expected: str):
        """引号内有空格时，闭引号与后续片段都必须被整体消费（修前残留 ` secret value"`）。"""
        assert mask_run_report_preview(raw) == expected

    def test_quoted_secret_absent_from_report_dict_and_text(self):
        dq, sq = "a b", "x y"
        ctx = ExecutionContext(
            query=f'password: "{dq}"',
            goal=f"api_key: '{sq}'",
        )
        report = build_run_report(ctx)
        for field in ("query", "goal"):
            assert dq not in report[field] and sq not in report[field]
        text = render_run_report_text(report)
        for residue in (dq, sq, f'{dq}"', f"{sq}'", f" {dq}", f" {sq}"):
            assert residue not in text
        assert "password: [SECRET_REDACTED]" in text
        assert "api_key: [SECRET_REDACTED]" in text

    def test_masker_is_pure_and_writes_no_log_body(self, caplog):
        raw = "邮件 user@example.com"
        with caplog.at_level(logging.DEBUG):
            masked = mask_observability_text(raw)
        assert "user@example.com" not in masked
        assert caplog.records == []
        assert raw == "邮件 user@example.com"  # 入参未被修改

    def test_run_report_dict_and_rendered_text_are_masked(self):
        sentinels = (
            "SuperSecret123",
            "ghp_ABCDEFGHIJKLMNOPQRST",
            "2001:db8::1",
            "123-45-6789",
        )
        ctx = ExecutionContext(
            query=f"找一下 password={sentinels[0]} 的记录",
            goal=f"读取 token={sentinels[1]} 的配置",
        )
        ctx.record_decision("plan", "plan", f"联系 13812345678，SSN {sentinels[3]}")
        report = build_run_report(ctx, final_output=f"结果来自 {sentinels[2]}")

        for field in ("query", "goal", "final_output_preview"):
            for needle in sentinels:
                assert needle not in report[field]
        assert "13812345678" not in report["plan_reasoning"][0]
        text = render_run_report_text(report)
        for needle in (*sentinels, "user@example.com", "13812345678", "192.168.1.100"):
            assert needle not in text


class TestRetentionSelection:
    def test_retention_days_none_selects_nothing(self):
        rows = [_row(NOW - timedelta(days=365))]
        assert select_llm_calls_for_retention(rows, None, now=NOW) == []

    def test_selection_by_days_and_boundary(self):
        cutoff = NOW - timedelta(days=7)
        rows = [
            _row(cutoff - timedelta(milliseconds=1)),  # 过期
            _row(cutoff, task_id="edge"),  # 边界：保留
            _row(NOW, task_id="fresh"),  # 新鲜：保留
            _row(NOW - timedelta(days=30), task_id="old"),
            "bad-record",  # 非 mapping：跳过
            {"ts": "not-a-date", "task_id": "bad-ts"},  # 坏时间戳：跳过
        ]
        selected = select_llm_calls_for_retention(rows, 7, now=NOW)
        assert [r["task_id"] for r in selected] == ["task-1", "old"]
        assert all("prompt" not in r for r in selected)

    def test_negative_or_zero_retention_is_rejected(self):
        with pytest.raises(ValueError):
            select_llm_calls_for_retention([], 0, now=NOW)
        with pytest.raises(ValueError):
            select_llm_calls_for_retention([], -1, now=NOW)

    def test_selector_never_deletes(self, monkeypatch):
        def _forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("留存筛选不得删除文件")

        monkeypatch.setattr(Path, "unlink", _forbidden)
        monkeypatch.setattr("os.remove", _forbidden)
        monkeypatch.setattr("os.unlink", _forbidden)
        rows = [_row(NOW - timedelta(days=10))]
        assert len(select_llm_calls_for_retention(rows, 7, now=NOW)) == 1

    def test_retention_source_has_no_delete_calls(self):
        reader = Path(__file__).resolve().parents[2] / "app" / "agent" / "llm_call_reader.py"
        tree = ast.parse(reader.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"unlink", "remove", "truncate", "rmdir"}

    def test_retention_setting_defaults_to_none(self):
        assert Settings.model_fields["LLM_CALL_LOG_RETENTION_DAYS"].default is None
