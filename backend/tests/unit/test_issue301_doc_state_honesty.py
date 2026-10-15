"""issue #301 批 D 单元回归：文档状态机诚实化（#28 / #29 / #229）。

复现原文归档：`_orchestrate/round2/_docs301/repro_28_baseline.txt` /
`repro_29_baseline.txt` / `repro_229_baseline.txt`（修前实测输出）。

本文件钉住的语义：

- **#28**：扫描版 PDF（OCR 依赖缺失）与空内容 —— 解析层显式失败（对齐 _parse_docx
  先例）、分块层空文本返回零 chunk、处理层剔除全空白 chunk 后判空并走 FAILED，
  **绝不 INDEXED**。修前：空 chunk 数=1 绕过 `if not chunks_data`，空内容向量化
  后照写 INDEXED（列表"已完成"、内容为空、永远搜不到）。
- **#29**：file_service 异步壳异常必显形（ERROR + exc_info）并尽力把文档推进为
  FAILED；兜底标记自身失败时响亮记录（critical），绝不静默（修前：仅一条 error
  日志、文档永久卡 UPLOADED，无 FAILED/无 job 行/无重试入口）。
- **#229**：demo 端点 embedding 失败 → 文档如实 FAILED（带原因），不写 INDEXED；
  ES 索引全失败 → FAILED；响应如实（success=False + 失败计数）。修前：创建即
  INDEXED、embedding 失败仅 warning 跳过 ES、响应仍「加载成功」且 documents_created
  悄然少 1。

变异验证（tools/mutate.py，行内注明对应点）：
- 去掉 _parse_pdf 的空内容检查 → #28 解析层用例红；
- 分块层空文本恢复 `[{"text": text}]` → #28 分块层用例红；
- 处理层去掉空白 chunk 过滤 → #28 处理层用例红；
- 去掉 #29 的 `_mark_document_failed_best_effort` 调用 → #29 用例红；
- demo 创建时恢复 INDEXED / 去掉 embedding 失败置 FAILED → #229 用例红。
"""
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.document_parser import document_service
from app.services.semantic_chunker import semantic_chunker
from app.worker.doc_processor import DocumentProcessor

# ══════════════════════════════════════════════════════════════════════════
# #28 — 扫描件 / 空内容绝不 INDEXED
# ══════════════════════════════════════════════════════════════════════════

def make_blank_pdf() -> bytes:
    """纯 stdlib 构造一页无任何文字的合法 PDF（扫描件的文本形态）。"""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode())
        out.write(body)
        out.write(b"\nendobj\n")
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return out.getvalue()


class TestParsePdfBlankContent:
    @pytest.mark.asyncio
    async def test_blank_pdf_raises_instead_of_returning_empty_content(self, tmp_path):
        """修前（复现 B 段）：空白 PDF 返回 content="" + 1 个空 chunk、不抛错。

        变异点：删掉 _parse_pdf 里 `if not text_content.strip(): raise` → 本用例红。
        """
        path = tmp_path / "blank.pdf"
        path.write_bytes(make_blank_pdf())

        with pytest.raises(Exception, match="未提取到有效文本内容"):
            await document_service._parse_pdf(str(path))


class TestChunkerEmptyText:
    @pytest.mark.asyncio
    async def test_empty_text_returns_no_chunks(self):
        """修前（复现 A 段）：split_text("") → [{"text": ""}]，空 chunk 绕过下游护栏。

        变异点：分块层恢复 `if not text or len(text) < 500: return [{"text": text, ...}]`
        → 本用例红（同时钉住全空白文本）。
        """
        assert await semantic_chunker.split_text("") == []
        assert await semantic_chunker.split_text("   \n\n  ") == []

    @pytest.mark.asyncio
    async def test_short_text_still_single_chunk(self):
        """回归对照：非空短文本的行为不变。"""
        chunks = await semantic_chunker.split_text("这是一段短文本。")
        assert len(chunks) == 1
        assert chunks[0]["text"] == "这是一段短文本。"


def _processor_with_fakes(monkeypatch, parse_result=None, parse_error=None):
    """processor 内部替身工厂：返回 (processor, events)。

    注意 parser 是共享单例（document_service），必须用 monkeypatch 挂替身
    （自动还原），不得直接赋值造成跨用例污染。
    """
    processor = DocumentProcessor()
    events = []

    async def fake_lock(document_id):
        return True

    async def fake_load(document_id, job_id):
        return SimpleNamespace(file_path="f.pdf", organization_id="1", id=document_id)

    async def fake_parse(file_path, organization_id):
        events.append("parse")
        if parse_error is not None:
            raise parse_error
        return parse_result

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

    async def fake_graph(doc, chunks_data):
        # 图谱抽取（a8a3d155）：成功路径的尽力而为步骤，非本文件主题；
        # 隔离真实 Redis/LLM I/O，接线契约见 test_doc_processor_graph_wiring.py。
        pass

    processor._acquire_processing_lock = fake_lock
    processor._load_and_mark_parsing = fake_load
    monkeypatch.setattr(processor.parser, "parse_document", fake_parse)
    processor._get_embeddings = fake_embeddings
    processor._replace_document_state = fake_replace
    processor._mark_success = fake_success
    processor._mark_failed = fake_failed
    processor._release_processing_lock = fake_release
    processor._extract_graph_safely = fake_graph
    return processor, events


class TestProcessorBlankContentFails:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("blank", ["", "   ", "\n\n"])
    async def test_all_blank_chunks_fail_not_indexed(self, blank, monkeypatch):
        """修前（复现 C 段）：chunks_data=[空 chunk] 不触发护栏，走到 _mark_success。

        变异点：删掉 process() 里的 `non_empty = [...]` 过滤 → 本用例红
        （events 里会出现 "success"）。
        """
        processor, events = _processor_with_fakes(
            monkeypatch,
            parse_result={"chunks": [{"chunk_text": blank, "chunk_index": 0, "chunk_length": 0}]},
        )
        ok = await processor.process("doc-blank", 7)

        assert ok is False
        assert "replace" not in events and "success" not in events
        failed = [e for e in events if isinstance(e, tuple) and e[0] == "failed"]
        assert failed and "无有效文本" in failed[0][1]

    @pytest.mark.asyncio
    async def test_mixed_blank_chunks_filtered_and_processed(self, monkeypatch):
        """部分空白：空白块被剔除后照常处理（留痕，不误杀有效内容）。"""
        processor, events = _processor_with_fakes(
            monkeypatch,
            parse_result={
                "chunks": [
                    {"chunk_text": "有效内容", "chunk_index": 0, "chunk_length": 4},
                    {"chunk_text": "   ", "chunk_index": 1, "chunk_length": 3},
                ]
            }
        )
        ok = await processor.process("doc-mixed", 7)
        assert ok is True
        assert "success" in events

    @pytest.mark.asyncio
    async def test_parser_failure_marks_failed(self, monkeypatch):
        """解析层抛错（如空白 PDF）→ 文档 FAILED、绝不 INDEXED。"""
        processor, events = _processor_with_fakes(
            monkeypatch,
            parse_error=Exception("PDF 解析失败: 未提取到有效文本内容（可能是扫描件...）")
        )
        ok = await processor.process("doc-scan", 7)
        assert ok is False
        assert "success" not in events and "replace" not in events
        failed = [e for e in events if isinstance(e, tuple) and e[0] == "failed"]
        assert failed and "未提取到有效文本内容" in failed[0][1]


# ══════════════════════════════════════════════════════════════════════════
# #29 — file_service 异步壳：异常必显形 + 状态推进（FAILED）
# ══════════════════════════════════════════════════════════════════════════

class _Ctx:
    def __init__(self, obj):
        self._obj = obj

    async def __aenter__(self):
        return self._obj

    async def __aexit__(self, *a):
        return False


class _BoomCtx:
    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *a):
        return False


class TestFileServiceAsyncShell:
    @pytest.mark.asyncio
    async def test_shell_marks_document_failed_on_exception(self, monkeypatch):
        """修前（复现 29）：异常被吞、文档停在 UPLOADED；现在必须推进为 FAILED。

        变异点：删掉 except 里的 `await self._mark_document_failed_best_effort(...)`
        调用 → 本用例红（doc.status 停在 uploaded）。
        """
        import app.services.file_service as fs_mod
        from app.models.document import DocumentStatus

        doc = SimpleNamespace(
            id="doc-1", status=DocumentStatus.UPLOADED, parse_error=None,
            organization_id=1,
        )
        mark_session = MagicMock()
        mark_session.get = AsyncMock(return_value=doc)
        mark_session.commit = AsyncMock()

        calls = {"n": 0}

        def session_factory():
            calls["n"] += 1
            if calls["n"] == 1:
                return _BoomCtx(RuntimeError("simulated DB outage at job-creation window"))
            return _Ctx(mark_session)

        monkeypatch.setattr(fs_mod, "AsyncSessionLocal", session_factory)

        service = fs_mod.FileUploadService()
        await service._process_document_async("doc-1")

        assert doc.status == DocumentStatus.FAILED
        assert "异步处理失败" in (doc.parse_error or "")
        mark_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shell_records_critical_when_marking_also_fails(self, monkeypatch, caplog):
        """兜底标记自身失败 → 响亮记录（critical），绝不再次静默。"""
        import logging

        import app.services.file_service as fs_mod

        monkeypatch.setattr(
            fs_mod, "AsyncSessionLocal",
            lambda: _BoomCtx(RuntimeError("db totally down")),
        )

        service = fs_mod.FileUploadService()
        with caplog.at_level(logging.CRITICAL, logger="app.services.file_service"):
            await service._process_document_async("doc-1")  # 不抛异常

        assert any(
            "FAILED 状态写入也失败" in r.message and r.levelno == logging.CRITICAL
            for r in caplog.records
        ), f"应有 critical 记录: {[r.message for r in caplog.records]}"

    @pytest.mark.asyncio
    async def test_indexed_document_not_overwritten_by_late_marking(self, monkeypatch):
        """已 INDEXED 的文档不因晚到的兜底异常被改写为 FAILED。"""
        import app.services.file_service as fs_mod
        from app.models.document import DocumentStatus

        doc = SimpleNamespace(
            id="doc-1", status=DocumentStatus.INDEXED, parse_error=None,
            organization_id=1,
        )
        mark_session = MagicMock()
        mark_session.get = AsyncMock(return_value=doc)
        mark_session.commit = AsyncMock()

        calls = {"n": 0}

        def session_factory():
            calls["n"] += 1
            if calls["n"] == 1:
                return _BoomCtx(RuntimeError("late failure"))
            return _Ctx(mark_session)

        monkeypatch.setattr(fs_mod, "AsyncSessionLocal", session_factory)

        service = fs_mod.FileUploadService()
        await service._process_document_async("doc-1")

        assert doc.status == DocumentStatus.INDEXED
        mark_session.commit.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════
# #229 — demo 端点：embedding 失败 → 绝不 INDEXED + 响应如实
# ══════════════════════════════════════════════════════════════════════════

def _demo_user():
    return SimpleNamespace(
        id=1, organization_id=1, is_superuser=False, role="user", is_active=True
    )


def _demo_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    res = MagicMock()
    res.scalar = MagicMock(return_value=0)  # 无既有示例数据
    db.execute = AsyncMock(return_value=res)
    return db


def _capture(db):
    added = []
    db.add.side_effect = added.append
    return added


class TestDemoSeedHonesty:
    @pytest.mark.asyncio
    async def test_embedding_failure_marks_docs_failed_not_indexed(self):
        """修前（复现 229）：3 篇文档全部 INDEXED、ES 零条、响应「加载成功」。

        变异点：demo 创建文档时恢复 `status=DocumentStatus.INDEXED`（或去掉
        embedding 失败分支里的 FAILED 赋值）→ 本用例红。
        """
        import app.api.v1.endpoints.demo as demo_mod
        from app.models.document import Document, DocumentStatus

        db = _demo_db()
        added = _capture(db)
        es_calls = []

        async def _embedding_down(texts):
            raise RuntimeError("simulated embedding endpoint outage")

        async def _fake_index(doc_id, document):
            es_calls.append(doc_id)

        with patch.object(demo_mod.embedding_service, "get_embeddings", _embedding_down), \
             patch.object(demo_mod.ElasticsearchTools, "index_document", _fake_index):
            body = await demo_mod.seed_demo_data(current_user=_demo_user(), db=db)

        docs = [o for o in added if isinstance(o, Document)]
        assert len(docs) == len(demo_mod.DEMO_DOCUMENTS)
        for d in docs:
            assert d.status == DocumentStatus.FAILED, f"{d.filename} 不得写 INDEXED"
            assert d.parse_error and "向量化失败" in d.parse_error
        assert es_calls == [], "embedding 失败时不得索引 ES"
        assert body["success"] is False
        assert body["data"]["documents_created"] == 0
        assert body["data"]["documents_failed"] == len(demo_mod.DEMO_DOCUMENTS)
        assert "失败" in body["message"]

    @pytest.mark.asyncio
    async def test_all_es_failures_mark_docs_failed(self):
        """embedding 成功但 ES 索引全失败 → 同样不得写 INDEXED（#181/#229 不变式）。"""
        import app.api.v1.endpoints.demo as demo_mod
        from app.models.document import Document, DocumentStatus

        db = _demo_db()
        added = _capture(db)

        async def _embeddings_ok(texts):
            return [[0.1, 0.2] for _ in texts]

        async def _es_down(doc_id, document):
            raise RuntimeError("ES unreachable")

        with patch.object(demo_mod.embedding_service, "get_embeddings", _embeddings_ok), \
             patch.object(demo_mod.ElasticsearchTools, "index_document", _es_down):
            body = await demo_mod.seed_demo_data(current_user=_demo_user(), db=db)

        docs = [o for o in added if isinstance(o, Document)]
        assert docs and all(d.status == DocumentStatus.FAILED for d in docs)
        assert all("ES 索引全部失败" in (d.parse_error or "") for d in docs)
        assert body["success"] is False
        assert body["data"]["documents_failed"] == len(demo_mod.DEMO_DOCUMENTS)

    @pytest.mark.asyncio
    async def test_embedding_length_mismatch_marks_failed(self):
        """向量数量与文本块数量不匹配 → 如实失败（不得 zip 截断后照写 INDEXED）。"""
        import app.api.v1.endpoints.demo as demo_mod
        from app.models.document import Document, DocumentStatus

        db = _demo_db()
        added = _capture(db)

        async def _embeddings_short(texts):
            return [[0.1, 0.2]]  # 恒 1 条，必然少于 chunks 数

        async def _fake_index(doc_id, document):  # pragma: no cover - 不应被调用
            raise AssertionError("mismatch 时不得索引 ES")

        with patch.object(demo_mod.embedding_service, "get_embeddings", _embeddings_short), \
             patch.object(demo_mod.ElasticsearchTools, "index_document", _fake_index):
            body = await demo_mod.seed_demo_data(current_user=_demo_user(), db=db)

        docs = [o for o in added if isinstance(o, Document)]
        assert docs and all(d.status == DocumentStatus.FAILED for d in docs)
        assert body["success"] is False

    @pytest.mark.asyncio
    async def test_healthy_seed_still_reports_success(self):
        """回归对照：一切正常时行为不变（INDEXED + success=True + created=3）。"""
        import app.api.v1.endpoints.demo as demo_mod
        from app.models.document import Document, DocumentStatus

        db = _demo_db()
        added = _capture(db)
        es_calls = []

        async def _embeddings_ok(texts):
            return [[0.1, 0.2] for _ in texts]

        async def _fake_index(doc_id, document):
            es_calls.append(doc_id)

        with patch.object(demo_mod.embedding_service, "get_embeddings", _embeddings_ok), \
             patch.object(demo_mod.ElasticsearchTools, "index_document", _fake_index):
            body = await demo_mod.seed_demo_data(current_user=_demo_user(), db=db)

        docs = [o for o in added if isinstance(o, Document)]
        assert len(docs) == len(demo_mod.DEMO_DOCUMENTS)
        assert all(d.status == DocumentStatus.INDEXED for d in docs)
        assert all(d.indexed_at is not None for d in docs)
        assert len(es_calls) == sum(len(d["chunks"]) for d in demo_mod.DEMO_DOCUMENTS)
        assert body["success"] is True
        assert body["data"]["documents_created"] == len(demo_mod.DEMO_DOCUMENTS)
        assert body["data"]["documents_failed"] == 0
