"""B1 · issue #180 收尾：批量重索引/一致性核对运维脚本。

背景（分诊结论）：主链路已由 #214/#230/#316/#421 修复（INDEXED 回读校验、
/health 能发现「声称已索引但 ES 全空」）。遗留的是**部署数据态**：缺少一个
把 ES 索引计数与 documents 标记逐文档对账、并能触发重索引的最小运维脚本。

脚本契约（钉住）：
- **对账**：以 documents 表 INDEXED 记录为基准，逐文档比较 ES 实际 chunk 数
  与 `chunk_count`；额外报告 ES 里存在但 DB 未标记的孤儿文档。
- **重索引只走统一处理链**：修复动作必须复用 `DocumentProcessor.process()`
  （自带文档锁、回读校验、缓存失效），**不得**直接扫 MySQL 重写 ES——
  那是另一条会再造假索引的旁路（分诊推荐 #2 的明确要求）。
- **判定**：missing（ES 0 条）/ partial（少于 chunk_count）/ ok / extra；只有
  missing/partial 触发重索引。
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "reconcile_knowledge_index.py"
)


def _load_script_module():
    assert SCRIPT_PATH.exists(), f"缺少运维脚本：{SCRIPT_PATH.name}（#180 收尾）"
    spec = importlib.util.spec_from_file_location(
        "reconcile_knowledge_index", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reconcile_classifies_missing_partial_ok_and_orphans():
    module = _load_script_module()
    rows = [
        ("d1", "a.txt", 1, 5),
        ("d2", "b.txt", 1, 3),
        ("d3", "c.txt", 2, 2),
    ]
    counts = {"d1": 0, "d2": 2, "d3": 2, "orphan-doc": 7}

    report = module.build_report(rows, counts)
    by_id = {state.document_id: state for state in report.states}

    assert by_id["d1"].status == "missing" and by_id["d1"].needs_reindex
    assert by_id["d2"].status == "partial" and by_id["d2"].needs_reindex
    assert by_id["d3"].status == "ok" and not by_id["d3"].needs_reindex
    assert report.orphan_document_ids == ["orphan-doc"]
    assert report.has_issues is True
    assert report.exit_code == module.EXIT_MISMATCH


def test_reconcile_clean_report_exits_ok():
    module = _load_script_module()
    report = module.build_report(
        [("d1", "a.txt", 1, 2), ("d2", "b.txt", 1, 1)],
        {"d1": 2, "d2": 1},
    )
    assert report.has_issues is False
    assert report.exit_code == module.EXIT_OK
    assert report.orphan_document_ids == []


@pytest.mark.asyncio
async def test_reindex_only_touches_broken_documents_via_processor_pipeline():
    module = _load_script_module()
    rows = [("d1", "a.txt", 1, 5), ("d2", "b.txt", 1, 3), ("d3", "c.txt", 1, 2)]
    report = module.build_report(rows, {"d1": 0, "d2": 2, "d3": 2})

    calls: list[str] = []

    async def fake_process(document_id: str):
        calls.append(document_id)
        return document_id != "d2"  # d2 模拟处理失败

    candidates = [state for state in report.states if state.needs_reindex]
    outcome = await module.reindex_documents(candidates, process=fake_process)

    assert calls == ["d1", "d2"], "只允许重索引 missing/partial，healthy 文档不得被重跑"
    assert outcome.succeeded == ["d1"]
    assert outcome.failed == ["d2"]


@pytest.mark.asyncio
async def test_fetch_indexed_chunk_counts_uses_document_id_aggregation():
    module = _load_script_module()

    class _FakeClient:
        def __init__(self):
            self.kwargs = None

        async def search(self, **kwargs):
            self.kwargs = kwargs
            return {
                "aggregations": {
                    "by_document": {
                        "buckets": [
                            {"key": "d1", "doc_count": 3},
                            {"key": "d2", "doc_count": 1},
                        ]
                    }
                }
            }

    client = _FakeClient()
    counts = await module.fetch_indexed_chunk_counts(client, "knowledge-index")

    assert counts == {"d1": 3, "d2": 1}
    agg = client.kwargs["aggs"]["by_document"]["terms"]
    assert agg["field"] == "document_id"
    assert client.kwargs["index"] == "knowledge-index"
    assert client.kwargs["size"] == 0


def test_script_reuses_unified_processor_and_has_no_direct_es_write_path():
    """修复动作必须复用 DocumentProcessor.process()，不得另造写入旁路。"""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "from app.worker.doc_processor import" in source
    assert "processor.process" in source or "process_call" in source
    # 脚本不得自己写 ES（bulk/index/delete_by_query 均为旁路信号）
    assert "bulk_index_documents" not in source
    assert ".delete_by_query(" not in source
