"""issue #510 只读 JSONL 解析器的判别性验收。

覆盖：task_id / request_id / 时间范围过滤、坏行跳过、物理行数上限、
正文零泄漏、配置路径复用（含开关关闭与 per-run 分文件）。

变异点（至少三条独立红）：
1. 去掉坏行 `try/except` 跳过 → 坏行让整个解析抛异常；
2. 去掉 `max_lines` 上限 → 行数上限用例红；
3. 任一过滤条件写错（task / request / 时间）→ 对应过滤用例红。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.agent.llm_call_log import resolve_log_path
from app.agent.llm_call_reader import read_llm_calls
from app.core.config import settings

BASE = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
PROMPT_SECRET = "PROMPT_BODY_MUST_NOT_BE_RETURNED"
COMPLETION_SECRET = "COMPLETION_BODY_MUST_NOT_BE_RETURNED"


def _row(*, task: str, request: str, when: datetime, model: str = "m") -> dict:
    return {
        "ts": when.isoformat(timespec="milliseconds"),
        "task_id": task,
        "request_id": request,
        "model": model,
        "tokens_in": 10,
        "tokens_out": 2,
        "latency_ms": 12.5,
        "prompt_hash": "sha256:abc",
        "tool_calls": 0,
        "error": None,
    }


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # codeql[py/clear-text-storage-sensitive-data] 测试夹具：硬编码哨兵写入 tmp_path，无真实凭据
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture()
def log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "llm_calls.jsonl"
    monkeypatch.setattr(settings, "LLM_CALL_LOG_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CALL_LOG_FILE", str(path))
    monkeypatch.setattr(settings, "LLM_CALL_LOG_PER_RUN", False)
    return path


def test_filters_by_task_and_request_and_skips_bad_lines(log_file: Path):
    """过滤准确 + 坏行跳过；变异点 1/3。"""
    good_a = _row(task="task-A", request="req-1", when=BASE)
    other_task = _row(task="task-B", request="req-1", when=BASE + timedelta(seconds=1))
    other_request = _row(task="task-A", request="req-2", when=BASE + timedelta(seconds=2))
    leaked = dict(good_a)
    leaked["model"] = "with-body"
    leaked["prompt"] = PROMPT_SECRET
    leaked["completion"] = COMPLETION_SECRET
    _write(
        log_file,
        [
            json.dumps(good_a),
            "{not valid json",
            json.dumps(other_task),
            "[]",  # 合法 JSON 但不是对象
            json.dumps(other_request),
            json.dumps(leaked, ensure_ascii=False),
            "",  # 空行
            json.dumps({"ts": "bad-ts", "task_id": "task-A", "request_id": "req-1"}),
        ],
    )

    rows = read_llm_calls(task_id="task-A", request_id="req-1", since=BASE - timedelta(seconds=1))
    assert [r["model"] for r in rows] == ["m", "with-body"], "坏行/无关行/坏时间戳必须跳过"
    assert all(r["task_id"] == "task-A" and r["request_id"] == "req-1" for r in rows)
    assert set(rows[0]) == {
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
    }, "正文/额外字段绝不能泄漏到返回值"
    assert PROMPT_SECRET not in json.dumps(rows)
    assert COMPLETION_SECRET not in json.dumps(rows)


def test_time_range_filter_inclusive_and_timezone_aware(log_file: Path):
    """时间过滤闭区间；写端 UTC +00:00，查询 naive 按 UTC 解释；变异点 3。"""
    early = _row(task="t", request="r", when=BASE - timedelta(seconds=1))
    boundary = _row(task="t", request="r", when=BASE)
    late = _row(task="t", request="r", when=BASE + timedelta(seconds=1))
    after = _row(task="t", request="r", when=BASE + timedelta(seconds=2))
    _write(log_file, [json.dumps(r) for r in (early, boundary, late, after)])

    naive_since = BASE.replace(tzinfo=None)
    naive_until = (BASE + timedelta(seconds=1)).replace(tzinfo=None)
    rows = read_llm_calls(since=naive_since, until=naive_until)
    assert [r["ts"] for r in rows] == [boundary["ts"], late["ts"]]

    # aware 边界按真实偏移比较（+08:00 的 20:00 等价 UTC 12:00）
    tz8_boundary = BASE.astimezone(timezone(timedelta(hours=8)))
    rows = read_llm_calls(since=tz8_boundary)
    assert [r["ts"] for r in rows] == [boundary["ts"], late["ts"], after["ts"]]


def test_max_lines_caps_physical_scan_not_matches(log_file: Path):
    """行数上限生效且统计坏行/无关行；变异点 2。"""
    target = _row(task="keep", request="r", when=BASE)
    lines = ["{broken"] + [json.dumps(_row(task="noise", request="r", when=BASE)) for _ in range(9)]
    lines.append(json.dumps(target))
    lines.append(json.dumps(target))
    _write(log_file, lines)

    rows = read_llm_calls(task_id="keep", max_lines=11)  # 恰好读到第一行 target
    assert [r["task_id"] for r in rows] == ["keep"], "超过上限的行不得被读取"
    rows_all = read_llm_calls(task_id="keep", max_lines=20)
    assert [r["task_id"] for r in rows_all] == ["keep", "keep"]


def test_missing_file_returns_empty_and_reader_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    missing = tmp_path / "does-not-exist.jsonl"
    monkeypatch.setattr(settings, "LLM_CALL_LOG_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CALL_LOG_FILE", str(missing))
    monkeypatch.setattr(settings, "LLM_CALL_LOG_PER_RUN", False)
    assert read_llm_calls() == []
    assert not missing.exists(), "只读解析器不得创建文件"


def test_disabled_switch_and_per_run_path_reuse(log_file: Path, monkeypatch: pytest.MonkeyPatch):
    assert resolve_log_path("task-A") == log_file
    monkeypatch.setattr(settings, "LLM_CALL_LOG_ENABLED", False)
    assert resolve_log_path("task-A") is None
    assert read_llm_calls(task_id="task-A") == []
    assert not log_file.exists(), "关闭开关后不得读取/创建日志"

    monkeypatch.setattr(settings, "LLM_CALL_LOG_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CALL_LOG_PER_RUN", True)
    per_run = resolve_log_path("task-A")
    assert per_run is not None and per_run != log_file
    _write(per_run, [json.dumps(_row(task="task-A", request="req-1", when=BASE))])
    assert [r["task_id"] for r in read_llm_calls(task_id="task-A")] == ["task-A"]


def test_invalid_max_lines_rejected(log_file: Path):
    with pytest.raises(ValueError):
        read_llm_calls(max_lines=0)


def test_bad_timestamp_is_skipped_without_time_filters(log_file: Path):
    """无时间过滤时也必须跳过坏时间戳，输出时间戳保证可解析。"""
    good = _row(task="t", request="r", when=BASE)
    bad_ts = dict(good, ts="not-a-timestamp")
    missing_ts = dict(good)
    missing_ts.pop("ts")
    _write(log_file, [json.dumps(bad_ts), json.dumps(good), json.dumps(missing_ts)])

    rows = read_llm_calls(task_id="t")
    assert [r["ts"] for r in rows] == [good["ts"]]


def test_oversized_line_is_skipped_without_corrupting_next_line(
    log_file: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    """单行超限时跳过整行并告警，剩余部分不得污染下一行。"""
    import app.agent.llm_call_reader as reader

    monkeypatch.setattr(reader, "MAX_LINE_BYTES", 512)
    good = _row(task="t", request="r", when=BASE)
    oversized = json.dumps({"ts": good["ts"], "task_id": "t", "padding": "x" * 2048})
    _write(log_file, [oversized, json.dumps(good)])

    with caplog.at_level("WARNING", logger=reader.logger.name):
        rows = read_llm_calls(task_id="t")

    assert [r["task_id"] for r in rows] == ["t"]
    assert rows[0]["model"] == good["model"]
    assert "超过上限" in caplog.text
