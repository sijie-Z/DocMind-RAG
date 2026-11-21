"""竞品对标 P1：提示词版本 / 实验标签的判别性验收。

变异点：
1. writer 的可选字段合并漏掉任一字段 → 写入用例红；
2. reader 白名单去掉任一字段 → 读回键集用例红；
3. resolver 改读 template.name/content（自由文本）→ 正文不泄漏用例红；
4. resolver 用 truthiness 代替 is not None → version=0 用例红；
5. RunReport 无条件写出四键 → 旧报告键集用例红。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.exec_context import ExecutionContext
from app.agent.llm_call_log import build_llm_call_record, resolve_prompt_metadata
from app.agent.llm_call_reader import read_llm_calls
from app.agent.run_report import build_run_report, render_run_report_text
from app.agent.usage import record_llm_call_error, record_llm_usage
from app.core.config import settings

BASE = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
PROMPT_FIELDS = ("prompt_id", "prompt_version", "prompt_label", "experiment_id")
LEGACY_FIELDS = {
    "ts",
    "task_id",
    "request_id",
    "model",
    "tokens_in",
    "tokens_out",
    "latency_ms",
    "prompt_hash",
    "tool_calls",
    "error",
}
REPORT_BODY = "SECRET_PROMPT_BODY_MUST_NOT_APPEAR"
FULL_FIELDS = LEGACY_FIELDS | set(PROMPT_FIELDS)


@pytest.fixture()
def log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "llm_calls.jsonl"
    monkeypatch.setattr(settings, "LLM_CALL_LOG_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CALL_LOG_FILE", str(path))
    monkeypatch.setattr(settings, "LLM_CALL_LOG_PER_RUN", False)
    return path


def _row(**extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ts": BASE.isoformat(timespec="milliseconds"),
        "task_id": "t",
        "request_id": "r",
        "model": "m",
        "tokens_in": 1,
        "tokens_out": 1,
        "latency_ms": 1.0,
        "prompt_hash": "sha256:abc",
        "tool_calls": 0,
        "error": None,
    }
    row.update(extra)
    return row


def _response() -> SimpleNamespace:
    return SimpleNamespace(
        model="m",
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        choices=[],
    )


# ── 写入：旧 10 键兼容 + 新字段可选 ──────────────────────────────────────


def test_legacy_and_15_field_key_sets_unchanged():
    """旧 10 键 + 层级 trace 5 键，不传新字段时都不涨。"""
    legacy = build_llm_call_record(task_id="t", request_id="r")
    assert set(legacy) == LEGACY_FIELDS
    traced = build_llm_call_record(
        task_id="t",
        request_id="r",
        span_id="s",
        parent_span_id=None,
        span_type="llm",
        start_time="2025-01-01T12:00:00+00:00",
        end_time="2025-01-01T12:00:01+00:00",
    )
    assert set(traced) == LEGACY_FIELDS | {
        "span_id",
        "span_type",
        "start_time",
        "end_time",
    }


def test_all_four_prompt_fields_written_only_when_supplied():
    """四字段齐全 → 14 键；None / 空串视为未提供，不得写出。"""
    rich = build_llm_call_record(
        task_id="t",
        request_id="r",
        prompt_id=42,
        prompt_version="7",
        prompt_label="production",
        experiment_id="exp-A",
    )
    assert set(rich) == FULL_FIELDS
    assert rich["prompt_id"] == "42", "与 prompt_version 的 String 列同口径"
    assert rich["prompt_version"] == "7"
    assert rich["prompt_label"] == "production"
    assert rich["experiment_id"] == "exp-A"

    partial = build_llm_call_record(
        task_id="t", request_id="r", prompt_id=0, prompt_label="", experiment_id=None
    )
    assert partial["prompt_id"] == "0", "0 是有效标识，不能因 falsy 被丢掉"
    assert "prompt_label" not in partial
    assert "experiment_id" not in partial
    assert "prompt_version" not in partial


def test_prompt_fields_flow_through_usage_and_context(log_file: Path):
    """run 级 ctx 字段经单一入口进入成功与失败两种 JSONL 行。"""
    ctx = ExecutionContext(task_id="task-p", request_id="req-p")
    ctx.prompt_id = "12"
    ctx.prompt_version = "3"
    ctx.prompt_label = "canary"
    ctx.experiment_id = "exp-42"

    assert record_llm_usage(_response(), ctx) == 2
    record_llm_call_error(ctx, error="boom")

    rows = [json.loads(ln) for ln in log_file.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert all(
        {k: r[k] for k in PROMPT_FIELDS}
        == {
            "prompt_id": "12",
            "prompt_version": "3",
            "prompt_label": "canary",
            "experiment_id": "exp-42",
        }
        for r in rows
    )
    assert rows[0]["error"] is None and rows[1]["error"] == "boom"

    # 本次调用显式覆盖 run 级值（可选 per-call override）。
    assert record_llm_usage(_response(), ctx, prompt_metadata={"prompt_version": "4"}) == 2
    last = json.loads(log_file.read_text(encoding="utf-8").splitlines()[-1])
    assert last["prompt_version"] == "4"
    assert last["prompt_id"] == "12"


def test_legacy_usage_path_does_not_grow_keys(log_file: Path):
    """未设置 run 级字段时，usage → JSONL 仍是原 10 键。"""
    record_llm_usage(_response(), ExecutionContext(task_id="t", request_id="r"))
    row = json.loads(log_file.read_text(encoding="utf-8").splitlines()[0])
    assert set(row) == LEGACY_FIELDS


# ── 读取：白名单同步 + 旧行留空 ──────────────────────────────────────────


def test_reader_projects_prompt_fields_and_keeps_legacy_rows(log_file: Path):
    """新字段进白名单；旧行缺字段时投影不多出键；正文仍被丢弃。"""
    rich = _row(**{k: f"v-{k}" for k in PROMPT_FIELDS})
    rich["prompt"] = REPORT_BODY
    rich["completion"] = REPORT_BODY
    log_file.write_text(json.dumps(_row()) + "\n" + json.dumps(rich) + "\n", encoding="utf-8")

    rows = read_llm_calls()
    assert len(rows) == 2
    assert set(rows[0]) == LEGACY_FIELDS, "旧行不得多出四个新键"
    assert set(rows[1]) == FULL_FIELDS, "缺一字段即红"
    assert REPORT_BODY not in json.dumps(rows)


# ── 纯函数 resolver：不读正文、容错 ──────────────────────────────────────


def test_resolve_prompt_metadata_reads_only_scalar_identifiers():
    """只取 id/version；name/content/description 绝不进入结果。"""
    template = SimpleNamespace(
        id=7,
        version=0,  # 0 是有效版本
        name=REPORT_BODY,
        description=REPORT_BODY,
        content=REPORT_BODY,
    )
    meta = resolve_prompt_metadata(template, prompt_label="stable", experiment_id="e1")
    assert meta == {
        "prompt_id": "7",
        "prompt_version": "0",
        "prompt_label": "stable",
        "experiment_id": "e1",
    }
    assert REPORT_BODY not in json.dumps(meta)


def test_resolve_prompt_metadata_is_safe_for_missing_and_bad_values():
    """None / 坏对象 / 属性抛错 / bool / 集合都按缺失降级，不抛异常。"""

    class Exploding:
        @property
        def id(self):
            raise RuntimeError("detached ORM instance")

    assert resolve_prompt_metadata() == {}
    assert resolve_prompt_metadata(None, prompt_label="", experiment_id=None) == {}
    assert resolve_prompt_metadata(Exploding()) == {}
    assert (
        resolve_prompt_metadata(
            SimpleNamespace(id=True, version="v1", name="n"),
            prompt_label={"x": 1},
            experiment_id=["e"],
        )
        == {}
    )

    meta = resolve_prompt_metadata(SimpleNamespace(id=9, version=2))
    assert meta == {"prompt_id": "9", "prompt_version": "2"}


# ── RunReport：可选键 + 文本不泄漏正文 ────────────────────────────────────


def test_run_report_only_emits_prompt_metadata_when_set():
    """旧 run 报告键集不变；设置后四键写入且文本渲染含标签。"""
    ctx = ExecutionContext(task_id="t", query="q")
    old = build_run_report(ctx)
    assert not set(PROMPT_FIELDS) & set(old)
    assert "Prompt:" not in render_run_report_text(old)

    ctx.prompt_id = "8"
    ctx.prompt_version = "2"
    ctx.prompt_label = "stable"
    ctx.experiment_id = "cost-A"
    report = build_run_report(ctx, final_output=REPORT_BODY)
    assert all(report[k] for k in PROMPT_FIELDS)
    text = render_run_report_text(report)
    assert "prompt_id=8" in text and "experiment_id=cost-A" in text

    # resolver 的产物可直接作为标识设置到 ctx，且 resolver 从不读正文。
    template = SimpleNamespace(id=8, version=2, content=REPORT_BODY, name=REPORT_BODY)
    resolved = resolve_prompt_metadata(template)
    assert resolved == {"prompt_id": "8", "prompt_version": "2"}
    assert REPORT_BODY not in render_run_report_text(build_run_report(ctx))
