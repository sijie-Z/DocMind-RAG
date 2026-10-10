"""Unit tests for the unified document processor."""

from types import SimpleNamespace

import pytest

from app.worker.doc_processor import DocumentProcessor


@pytest.mark.asyncio
async def test_get_embeddings_preserves_length(monkeypatch):
    async def fake_get_embeddings(texts):
        return [[1.0]] * len(texts)

    monkeypatch.setattr(
        "app.worker.doc_processor.embedding_service.get_embeddings",
        fake_get_embeddings,
    )

    processor = DocumentProcessor()
    embeddings = await processor._get_embeddings(["hello", "", "world"])

    assert len(embeddings) == 3


@pytest.mark.parametrize("with_progress", [True, False])
@pytest.mark.asyncio
async def test_process_reports_progress_at_stage_boundaries(monkeypatch, with_progress):
    """心跳回调（issue #156）应在每个阶段边界各触发一次；缺省时为 no-op。"""
    processor = DocumentProcessor()
    events = []

    async def fake_lock(document_id):
        events.append("lock")
        return True

    async def fake_load(document_id, job_id):
        events.append("load")
        return SimpleNamespace(file_path="f.pdf", organization_id="org", id="doc-1")

    async def fake_parse(file_path, organization_id):
        events.append("parse")
        return {"chunks": [{"chunk_text": "hi", "chunk_index": 0, "chunk_length": 2}]}

    async def fake_embeddings(texts):
        events.append("embed")
        return [[1.0]]

    async def fake_replace(doc, chunks_data, embeddings):
        events.append("replace")

    async def fake_success(document_id, chunks_data, job_id):
        events.append("success")

    async def fake_release(document_id):
        events.append("release")

    async def fake_graph(doc, chunks_data):
        # 图谱抽取（a8a3d155）：INDEXED 之后的尽力而为步骤，不计入心跳阶段边界，
        # 由 tests/unit/test_doc_processor_graph_wiring.py 专门覆盖。此处只隔离 I/O。
        pass

    monkeypatch.setattr(processor, "_acquire_processing_lock", fake_lock)
    monkeypatch.setattr(processor, "_load_and_mark_parsing", fake_load)
    monkeypatch.setattr(processor.parser, "parse_document", fake_parse)
    monkeypatch.setattr(processor, "_get_embeddings", fake_embeddings)
    monkeypatch.setattr(processor, "_replace_document_state", fake_replace)
    monkeypatch.setattr(processor, "_mark_success", fake_success)
    monkeypatch.setattr(processor, "_release_processing_lock", fake_release)
    monkeypatch.setattr(processor, "_extract_graph_safely", fake_graph)

    on_progress = (lambda: events.append("beat")) if with_progress else None
    ok = await processor.process("doc-1", 7, on_progress=on_progress)

    assert ok is True
    expected = ["lock", "load", "parse", "embed", "replace", "success"]
    if with_progress:
        # 阶段边界：加锁后 / 解析后 / 向量化后 / 替换后 / 标记成功后
        expected = [
            "lock",
            "beat",
            "load",
            "parse",
            "beat",
            "embed",
            "beat",
            "replace",
            "beat",
            "success",
            "beat",
        ]
    expected.append("release")
    assert events == expected


@pytest.mark.asyncio
async def test_mark_failed_writes_document_and_job_failed(monkeypatch):
    """真实 _mark_failed 的落库写入（评审 R7：批内用例全部用替身，实现零覆盖）。

    #28 端到端「文档 FAILED」与 #30 终局日志「doc/job already marked FAILED」
    都依赖这两次真实状态写入；此前 S7 变异（删 `doc.status=FAILED` 一行）全绿。

    变异点：删掉 `doc.status = DocumentStatus.FAILED` 一行 → 本用例红。
    """
    import app.worker.doc_processor as dp_mod
    from app.models.document import Document, DocumentStatus
    from app.models.knowledge_job import KnowledgeJobStatus

    doc = SimpleNamespace(status=DocumentStatus.PARSING, parse_error=None)
    job = SimpleNamespace(
        status=KnowledgeJobStatus.PROCESSING,
        error_message=None,
        finished_at=None,
    )
    commits = []

    class _Session:
        async def get(self, model, pk):
            return doc if model is Document else job

        async def commit(self):
            commits.append(True)

    class _Ctx:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(dp_mod, "AsyncSessionLocal", lambda: _Ctx())

    processor = DocumentProcessor()
    await processor._mark_failed("doc-1", "boom", job_id=7)

    assert doc.status == DocumentStatus.FAILED
    assert doc.parse_error == "boom"
    assert job.status == KnowledgeJobStatus.FAILED
    assert job.error_message == "boom"
    assert job.finished_at is not None
    assert len(commits) == 2, "doc 与 job 各提交一次（两次独立 session）"
