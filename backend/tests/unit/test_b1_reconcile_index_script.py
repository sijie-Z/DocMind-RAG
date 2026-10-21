"""B1 · issue #180 收尾：批量重索引/一致性核对运维脚本。

背景（分诊结论）：主链路已由 #214/#230/#316/#421 修复（INDEXED 回读校验、
/health 能发现「声称已索引但 ES 全空」）。遗留的是**部署数据态**：缺少一个
把 ES 索引计数与 documents 标记逐文档对账、并能触发重索引的最小运维脚本。

脚本契约（钉住）：
- **对账**：以 documents 表 INDEXED 记录为基准，逐文档比较 ES 实际 chunk 数
  与 `chunk_count`；额外报告 ES 里存在但 DB 未标记的孤儿文档。
- **重索引只走统一处理链**：先落 QUEUED/reprocess job，再复用
  `DocumentProcessor.process(document_id, job_id)`（自带文档锁、回读校验、
  缓存失效），**不得**直接扫 MySQL 重写 ES——那是另一条会再造假索引的旁路。
- **必须传 job_id**：`process(document_id, job_id=None)` 对已 INDEXED 文档会
  命中 STALE_RUN 判定并返回 True（空转被记为成功）——这是本脚本最容易踩的
  回归点，单独用「真实 process 契约」的 fake 钉住。
- **判定**：missing（ES 0 条）/ partial（少于 chunk_count）/ extra（多于
  chunk_count，含 chunk_count=0 却有 chunk 的异常形态）/ ok；只有
  missing/partial 触发重索引。
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "reconcile_knowledge_index.py"


def _load_script_module():
    assert SCRIPT_PATH.exists(), f"缺少运维脚本：{SCRIPT_PATH.name}（#180 收尾）"
    spec = importlib.util.spec_from_file_location("reconcile_knowledge_index", SCRIPT_PATH)
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


def test_reconcile_flags_chunks_without_expected_count_as_extra():
    """chunk_count=0 却有 chunk：陈旧/串写形态，必须报告为 extra（不重索引）。"""
    module = _load_script_module()
    report = module.build_report([("d0", "z.txt", 1, 0)], {"d0": 4})
    state = report.states[0]
    assert state.status == "extra"
    assert state.needs_reindex is False


@pytest.mark.asyncio
async def test_reindex_only_touches_broken_documents_via_processor_pipeline():
    module = _load_script_module()
    rows = [("d1", "a.txt", 1, 5), ("d2", "b.txt", 1, 3), ("d3", "c.txt", 1, 2)]
    report = module.build_report(rows, {"d1": 0, "d2": 2, "d3": 2})

    calls: list[tuple[str, int]] = []

    async def fake_process(document_id: str, job_id: int):
        # 复刻真实契约：job_id=None 对 INDEXED 文档会走 STALE_RUN 并返回 True
        # （什么都没做却“成功”）。脚本必须传 job_id，否则下面的断言即红。
        calls.append((document_id, job_id))
        assert job_id is not None, "process 必须收到 reprocess job id（否则空转）"
        return document_id != "d2"  # d2 模拟处理失败

    async def fake_job_creator(state):
        return 100 + len(calls)

    candidates = [state for state in report.states if state.needs_reindex]
    outcome = await module.reindex_documents(
        candidates, process=fake_process, job_creator=fake_job_creator
    )

    assert [doc_id for doc_id, _ in calls] == [
        "d1",
        "d2",
    ], "只允许重索引 missing/partial，healthy 文档不得被重跑"
    assert outcome.succeeded == ["d1"]
    assert outcome.failed == ["d2"]


@pytest.mark.asyncio
async def test_reindex_creates_queued_reprocess_job_and_passes_its_id(monkeypatch):
    """修复动作必须先落 QUEUED/reprocess job（与 rebuild 端点同形态）。"""
    module = _load_script_module()
    report = module.build_report([("d1", "a.txt", 7, 2)], {"d1": 0})
    state = report.states[0]

    class _FakeSession:
        def __init__(self):
            self.added = []

        def add(self, obj):
            self.added.append(obj)

        async def commit(self):
            self.added[0].id = 42

    session = _FakeSession()

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    import app.core.database as database_mod

    monkeypatch.setattr(database_mod, "AsyncSessionLocal", _Factory())

    job_id = await module._enqueue_reprocess_job(state)

    assert job_id == 42
    job = session.added[0]
    assert job.document_id == "d1"
    assert job.organization_id == 7
    assert job.trigger_type == "reprocess"
    assert job.status.value == "queued"
    assert job.local_fallback is True


@pytest.mark.asyncio
async def test_reindex_records_exception_and_deferred_without_aborting():
    """单文档异常/锁竞争（DEFERRED）不得中断其余修复，且必须如实分类。"""
    module = _load_script_module()
    report = module.build_report(
        [("d1", "a.txt", 1, 2), ("d2", "b.txt", 1, 2), ("d3", "c.txt", 1, 2)],
        {"d1": 0, "d2": 0, "d3": 0},
    )

    async def fake_process(document_id: str, job_id: int):
        if document_id == "d1":
            raise RuntimeError("parse boom")
        if document_id == "d2":
            from app.worker.doc_processor import DEFERRED

            return DEFERRED
        return True

    async def fake_job_creator(state):
        return 1

    outcome = await module.reindex_documents(
        [state for state in report.states if state.needs_reindex],
        process=fake_process,
        job_creator=fake_job_creator,
    )

    assert outcome.attempted == 3
    assert outcome.failed == ["d1"]
    assert outcome.deferred == ["d2"]
    assert outcome.succeeded == ["d3"]


@pytest.mark.asyncio
async def test_fetch_indexed_chunk_counts_uses_composite_pagination():
    """composite 分页聚合：不得因 terms 10k 截断把未入桶文档误判为 missing。"""

    module = _load_script_module()

    class _FakeClient:
        def __init__(self):
            self.calls = []

        async def search(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {
                    "aggregations": {
                        "by_document": {
                            "buckets": [
                                {"key": {"document_id": "d1"}, "doc_count": 3},
                                {"key": {"document_id": "d2"}, "doc_count": 1},
                            ],
                            "after_key": {"document_id": "d2"},
                        }
                    }
                }
            return {"aggregations": {"by_document": {"buckets": []}}}

    client = _FakeClient()
    counts = await module.fetch_indexed_chunk_counts(client, "knowledge-index")

    assert counts == {"d1": 3, "d2": 1}
    assert len(client.calls) == 2, "有 after_key 时必须继续翻页"
    first = client.calls[0]
    assert first["index"] == "knowledge-index"
    assert first["size"] == 0
    composite = first["aggs"]["by_document"]["composite"]
    assert composite["sources"] == [{"document_id": {"terms": {"field": "document_id"}}}]
    assert "after" not in composite
    assert client.calls[1]["aggs"]["by_document"]["composite"]["after"] == {"document_id": "d2"}


def test_org_filter_is_applied_to_es_query():
    module = _load_script_module()
    import asyncio

    class _FakeClient:
        def __init__(self):
            self.calls = []

        async def search(self, **kwargs):
            self.calls.append(kwargs)
            return {"aggregations": {"by_document": {"buckets": []}}}

    client = _FakeClient()
    asyncio.run(module.fetch_indexed_chunk_counts(client, "idx", organization_id=3))
    assert client.calls[0]["query"] == {"term": {"organization_id": "3"}}


def test_limit_must_be_positive():
    module = _load_script_module()
    parser = module.build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--limit", "0"])
    assert parser.parse_args(["--limit", "2"]).limit == 2


def test_find_unresolved_includes_docs_that_left_indexed_state():
    """复核时已不在 INDEXED 集合（PARSING/FAILED）的文档不能算恢复成功。"""
    module = _load_script_module()
    report_after = module.build_report(
        [("d-ok", "a.txt", 1, 2), ("d-extra", "b.txt", 1, 0)],
        {"d-ok": 2, "d-extra": 1},
    )
    unresolved = module.find_unresolved(["d-ok", "d-extra", "d-departed"], report_after)
    assert unresolved == ["d-departed"]


def test_find_unresolved_flags_still_broken_docs():
    module = _load_script_module()
    report_after = module.build_report([("d1", "a.txt", 1, 5)], {"d1": 0})
    assert module.find_unresolved(["d1"], report_after) == ["d1"]
    assert module.find_unresolved([], report_after) == []


def test_script_reuses_unified_processor_and_has_no_direct_es_write_path():
    """修复动作必须复用 DocumentProcessor.process()，不得另造写入旁路。"""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "from app.worker.doc_processor import" in source
    assert "processor.process" in source or "process_call" in source
    # 必须先建 reprocess job 并传 job_id（否则 process 对 INDEXED 文档走 STALE_RUN）
    assert "KnowledgeProcessingJob(" in source
    assert 'trigger_type="reprocess"' in source
    assert "process_call(state.document_id, job_id)" in source
    # 脚本不得自己写 ES（bulk/index/delete_by_query 均为旁路信号）
    assert "bulk_index_documents" not in source
    assert ".delete_by_query(" not in source
