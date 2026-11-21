"""层级 trace MVP 的判别性验收：可选字段、只读投影、树组装。

变异点：
1.  reader 白名单去掉任一可选字段 → 新字段记录读回丢键，本文件红；
2.  build_trace_tree 忽略 parent_span_id → 层级/孤儿用例红；
3.  树节点从原始记录复制而非白名单 → 正文泄漏用例红。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.agent.llm_call_log import build_llm_call_record
from app.agent.llm_call_reader import build_trace_tree, read_llm_calls
from app.core.config import settings

BASE = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
TRACE_FIELDS = ("span_id", "parent_span_id", "span_type", "start_time", "end_time")
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
PROMPT_SECRET = "PROMPT_BODY_MUST_NOT_APPEAR_IN_TREE"
COMPLETION_SECRET = "COMPLETION_BODY_MUST_NOT_APPEAR_IN_TREE"


@pytest.fixture()
def log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "llm_calls.jsonl"
    monkeypatch.setattr(settings, "LLM_CALL_LOG_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CALL_LOG_FILE", str(path))
    monkeypatch.setattr(settings, "LLM_CALL_LOG_PER_RUN", False)
    return path


def _row(
    *,
    task: str = "t",
    request: str = "r",
    when: datetime = BASE,
    **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ts": when.isoformat(timespec="milliseconds"),
        "task_id": task,
        "request_id": request,
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


def test_legacy_record_key_set_unchanged():
    """旧调用点不传 span → 记录键集仍是原来的 10 个。"""
    record = build_llm_call_record(task_id="t", request_id="r")
    assert set(record) == LEGACY_FIELDS
    for key in TRACE_FIELDS:
        assert key not in record


def test_optional_trace_fields_only_when_supplied():
    """显式传入的层级字段才写出；None 视为未提供。"""
    record = build_llm_call_record(
        task_id="t",
        request_id="r",
        span_id="s-1",
        parent_span_id=None,
        span_type="tool",
        start_time="2025-01-01T12:00:00.000+00:00",
        end_time="2025-01-01T12:00:01.000+00:00",
    )
    assert record["span_id"] == "s-1"
    assert record["span_type"] == "tool"
    assert record["start_time"].endswith("+00:00")
    assert record["end_time"].endswith("+00:00")
    assert "parent_span_id" not in record, "None 的字段不得写出"


def test_legacy_rows_keep_exact_output_key_set(log_file: Path):
    """旧 JSONL 行读回键集不涨（既有解析契约）。"""
    log_file.write_text(json.dumps(_row()) + "\n", encoding="utf-8")
    rows = read_llm_calls()
    assert len(rows) == 1
    assert set(rows[0]) == LEGACY_FIELDS


def test_readers_projects_new_fields_and_discards_body(log_file: Path):
    """新字段进白名单；混入的原始正文字段不进读结果。"""
    rich = _row(
        span_id="s-1",
        parent_span_id=None,
        span_type="agent",
        start_time="2025-01-01T12:00:00.000+00:00",
        end_time="2025-01-01T12:00:01.000+00:00",
    )
    rich["prompt"] = PROMPT_SECRET
    rich["completion"] = COMPLETION_SECRET
    log_file.write_text(json.dumps(rich, ensure_ascii=False) + "\n", encoding="utf-8")

    rows = read_llm_calls()
    assert len(rows) == 1
    assert set(rows[0]) == LEGACY_FIELDS | set(TRACE_FIELDS), "可选字段缺一即红"
    assert PROMPT_SECRET not in json.dumps(rows)
    assert COMPLETION_SECRET not in json.dumps(rows)


def test_build_trace_tree_nests_by_parent_and_preserves_input():
    """session → run → step → tool → llm call 的层级关系正确。"""
    session = _row(span_id="session", span_type="session")
    run = _row(span_id="run", parent_span_id="session", span_type="run")
    step = _row(span_id="step", parent_span_id="run", span_type="step")
    tool = _row(span_id="tool", parent_span_id="step", span_type="tool")
    llm = _row(span_id="llm", parent_span_id="tool", span_type="llm")
    sibling = _row(span_id="llm2", parent_span_id="tool", span_type="llm")
    records = [llm, run, session, tool, sibling, step]  # 故意乱序
    snapshot = [dict(r) for r in records]

    roots = build_trace_tree(records)
    assert records == snapshot, "纯函数不得修改输入"
    assert [r["span_id"] for r in roots] == ["session"]
    node = roots[0]
    chain = [node]
    for expected in ("run", "step", "tool"):
        assert len(node["children"]) == 1
        node = node["children"][0]
        assert node["span_id"] == expected
        chain.append(node)
    assert sorted(c["span_id"] for c in node["children"]) == ["llm", "llm2"]
    assert all("children" not in r for r in records), "不得把 children 写回原记录"
    assert len(chain) == 4


def test_orphans_duplicate_ids_and_cycles_do_not_hang():
    """孤儿挂 root；自环/互环/重复 span_id 不死循环。"""
    records = [
        _row(span_id="orphan", parent_span_id="missing"),
        _row(span_id="self", parent_span_id="self"),
        _row(span_id="a", parent_span_id="b"),
        _row(span_id="b", parent_span_id="a", span_type="cycle"),
        _row(span_id="dupe"),
        _row(span_id="dupe", parent_span_id="a"),
        _row(parent_span_id="a"),  # 缺 span_id 的记录
    ]
    roots = build_trace_tree(records)
    assert len(roots) == len(records), "无有效 parent 的记录都应成为 root"

    def walk(nodes: list[dict[str, Any]], seen: set[int]) -> None:
        for node in nodes:
            marker = id(node)
            assert marker not in seen, "树中不得有引用环"
            seen.add(marker)
            walk(node["children"], seen)

    walk(roots, set())
    assert all(len(r["children"]) >= 0 for r in roots)


def test_orphan_root_still_owns_its_descendants():
    """孤儿自身挂 root，但其子孙仍按 parent 挂到该孤儿。"""
    orphan = _row(span_id="orphan", parent_span_id="missing")
    child = _row(span_id="child", parent_span_id="orphan")
    grandchild = _row(span_id="grandchild", parent_span_id="child")

    roots = build_trace_tree([grandchild, child, orphan])
    assert [r["span_id"] for r in roots] == ["orphan"]
    assert roots[0]["children"][0]["span_id"] == "child"
    assert roots[0]["children"][0]["children"][0]["span_id"] == "grandchild"


def test_tree_never_exposes_body_even_when_raw_rows_contain_it():
    """树节点只含白名单 + children；原始正文不泄漏。"""
    records = [
        _row(span_id="root", prompt=PROMPT_SECRET, completion=COMPLETION_SECRET),
        _row(span_id="child", parent_span_id="root", extra_secret=PROMPT_SECRET),
    ]
    roots = build_trace_tree(records)
    dumped = json.dumps(roots)
    assert PROMPT_SECRET not in dumped
    assert COMPLETION_SECRET not in dumped
    assert set(roots[0]) == LEGACY_FIELDS | {"span_id", "children"}, "只含白名单 + children"
