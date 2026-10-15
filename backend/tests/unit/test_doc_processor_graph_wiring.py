"""worker 文档处理 → 图谱抽取接线测试（issue：GraphRAG 写链缺一口气）。

钉住的两条判别契约：

1. **接上了**：索引成功路径会调用图谱摄入，且参数正确 —— document_id、
   organization_id（真实组织的 int）、chunk 全文拼成的 text、**真实的 llm_client**
   （`rag_service.openai_client`，此前缺口是根本不传 → 恒走规则回退）；
2. **非阻塞**：抽取抛错时文档仍走 `_mark_success`（INDEXED），绝不进 `_mark_failed`，
   只有一条 warning 日志 —— 变异点：去掉 `_extract_graph_safely` 的 try/except
   或把调用挪进 process() 的主 try → W2 红（文档会被标 FAILED）。

调用点选在 `_mark_success` 之后：抽取失败/崩溃都不影响 INDEXED 落库结果。
"""
import logging
from types import SimpleNamespace

import pytest

import app.services.graph_rag_service as graph_mod
from app.worker.doc_processor import DocumentProcessor

SENTINEL_CLIENT = object()

CHUNK_TEXT = "DocMind 架构文档：FastAPI 网关 + Redis 缓存 + Elasticsearch 检索组件的关键设计说明。"


def _harness(monkeypatch):
    """processor 内部替身：锁/加载/解析/向量/替换/标记全替身，只留图谱一步真实。"""
    processor = DocumentProcessor()
    events = []

    async def fake_lock(document_id):
        return True

    async def fake_load(document_id, job_id):
        return SimpleNamespace(file_path="f.pdf", organization_id=3, id=document_id)

    async def fake_parse(file_path, organization_id):
        events.append("parse")
        return {"chunks": [
            {"chunk_text": CHUNK_TEXT, "chunk_index": 0, "chunk_length": len(CHUNK_TEXT)}
        ]}

    async def fake_embeddings(texts):
        events.append("embed")
        return [[1.0] for _ in texts]

    async def fake_replace(doc, chunks_data, embeddings):
        events.append("replace")

    async def fake_success(document_id, chunks_data, job_id):
        events.append("success")

    async def fake_failed(document_id, error, job_id):
        events.append(("failed", error))

    async def fake_release(document_id):
        pass

    processor._acquire_processing_lock = fake_lock
    processor._load_and_mark_parsing = fake_load
    monkeypatch.setattr(processor.parser, "parse_document", fake_parse)
    processor._get_embeddings = fake_embeddings
    processor._replace_document_state = fake_replace
    processor._mark_success = fake_success
    processor._mark_failed = fake_failed
    processor._release_processing_lock = fake_release
    return processor, events


def _patch_graph(monkeypatch, recorder, *, result=None, error=None):
    """把图谱摄入替身挂到单例上；rag_service 也替换为携带哨兵 client 的假对象。"""
    monkeypatch.setattr(
        "app.services.rag_service.rag_service",
        SimpleNamespace(openai_client=SENTINEL_CLIENT),
    )

    async def fake_ingest(**kwargs):
        recorder.append(kwargs)
        if error is not None:
            raise error
        return result if result is not None else {"status": "extracted", "entities": 2}

    monkeypatch.setattr(
        graph_mod.graph_rag_service, "ingest_document", fake_ingest, raising=False
    )


@pytest.mark.asyncio
async def test_index_success_triggers_graph_ingest_with_real_params(monkeypatch):
    processor, events = _harness(monkeypatch)
    calls: list[dict] = []
    _patch_graph(monkeypatch, calls)

    ok = await processor.process("doc-1", 7)

    assert ok is True
    assert "success" in events, "文档照常标记 INDEXED"
    assert len(calls) == 1, "索引成功后必须恰好触发一次图谱摄入"
    kw = calls[0]
    assert kw["document_id"] == "doc-1"
    assert kw["organization_id"] == 3, "必须用真实组织 id（非硬编码默认 1）"
    assert "DocMind 架构文档" in kw["text"], "text 必须来自 chunk 全文"
    assert kw["llm_client"] is SENTINEL_CLIENT, "必须传真实 llm_client（此前缺口）"


@pytest.mark.asyncio
async def test_graph_failure_is_non_blocking_document_still_indexed(monkeypatch, caplog):
    processor, events = _harness(monkeypatch)
    calls: list[dict] = []
    _patch_graph(monkeypatch, calls, error=RuntimeError("LLM 不可用"))

    with caplog.at_level(logging.WARNING, logger="app.worker.doc_processor"):
        ok = await processor.process("doc-1", 7)

    assert ok is True, "图谱失败不得使文档处理失败"
    assert "success" in events and not any(
        isinstance(e, tuple) and e[0] == "failed" for e in events
    ), "文档必须仍走 INDEXED，绝不进 _mark_failed"
    assert any(
        "graph extraction failed" in r.message.lower() and r.levelno >= logging.WARNING
        for r in caplog.records
    ), f"必须有 warning 留痕: {[r.message for r in caplog.records]}"


@pytest.mark.asyncio
async def test_graph_skip_status_does_not_affect_processing(monkeypatch):
    processor, events = _harness(monkeypatch)
    calls: list[dict] = []
    _patch_graph(monkeypatch, calls, result={"status": "skipped", "entities": 0})

    ok = await processor.process("doc-1", 7)

    assert ok is True
    assert "success" in events
    assert len(calls) == 1
