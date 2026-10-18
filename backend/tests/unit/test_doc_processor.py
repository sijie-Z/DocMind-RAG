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

    A3-P0 起文档写入改为条件 UPDATE（``WHERE status != 'INDEXED'``）：
    这里验证语句内容（SET failed + 原因、条件排除 INDEXED）与 job 终态写入。

    变异点：去掉 UPDATE 的 ``status != INDEXED`` 条件 / 删掉 job.status 赋值 →
    对应用例红（条件语句断言 / job 断言）。
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
    executed = []

    class _Result:
        rowcount = 1

    class _Session:
        async def execute(self, stmt):
            executed.append(stmt)
            return _Result()

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

    assert executed, "文档 FAILED 必须经条件 UPDATE 写入"
    compiled = executed[0].compile()
    rendered = str(compiled)
    assert "documents.status !=" in rendered, "FAILED 写入必须带 status != INDEXED 条件"
    params = compiled.params.values()
    assert DocumentStatus.FAILED in params
    assert DocumentStatus.INDEXED in params
    assert "boom" in params
    assert job.status == KnowledgeJobStatus.FAILED
    assert job.error_message == "boom"
    assert job.finished_at is not None
    assert len(commits) == 2, "doc 与 job 各提交一次（两次独立 session）"


# ── 图片 OCR 链路（worker 级；仅引擎与边界服务用替身）────────────────

def _make_image_doc(tmp_path, monkeypatch, engine_result):
    """搭好图片链路：本地 PNG + 引擎替身 + MinIO 下载替身。

    路径含 '/'，两个平台都会走 parse_document 的 MinIO 分支 —— fget_object
    被替身覆盖为"从本地拷贝到解析层临时文件"，从而在不联网的情况下把
    「下载 → 格式分发 → _parse_image」整条真链路跑通。
    """
    import shutil

    from PIL import Image

    import app.services.document_parser as parser_mod

    png = tmp_path / "photo.png"
    Image.linear_gradient("L").convert("RGB").resize((320, 200)).save(str(png))

    class _FakeEngine:
        def __call__(self, img, **kwargs):
            return engine_result

    monkeypatch.setattr(parser_mod, "_get_ocr_engine", lambda: _FakeEngine())
    monkeypatch.setattr(
        "app.core.minio_client.minio_client.fget_object",
        lambda bucket, name, dest: shutil.copyfile(str(png), dest),
    )

    return SimpleNamespace(
        id="doc-img",
        file_path=tmp_path.as_posix() + "/photo.png",
        organization_id="org-1",
    )


@pytest.mark.asyncio
async def test_process_image_document_zero_text_marks_failed(monkeypatch, tmp_path):
    """引擎检测不到文字 → worker 必须判 False 且 FAILED 原因含「未识别到文字」。

    这是 #301 诚实语义在图片链路上的落点：照片类图片不许被伪装成有内容的
    INDEXED（既不是 INDEXED，也不是含糊的通用解析错误）。
    """
    doc = _make_image_doc(tmp_path, monkeypatch, engine_result=(None, None))

    processor = DocumentProcessor()
    failed = {}

    async def fake_lock(_document_id):
        return True

    async def fake_load(_document_id, _job_id):
        return doc

    async def fake_failed(_document_id, error, _job_id):
        failed["error"] = error

    async def fake_release(_document_id):
        pass

    monkeypatch.setattr(processor, "_acquire_processing_lock", fake_lock)
    monkeypatch.setattr(processor, "_load_and_mark_parsing", fake_load)
    monkeypatch.setattr(processor, "_mark_failed", fake_failed)
    monkeypatch.setattr(processor, "_release_processing_lock", fake_release)

    ok = await processor.process("doc-img")

    assert ok is False
    assert "未识别到文字" in failed["error"]


@pytest.mark.asyncio
async def test_process_image_document_success_path_feeds_chunks(monkeypatch, tmp_path):
    """引擎识别出文字 → OCR 文本进入 chunks/嵌入/索引替换链路，process 判 True。"""
    engine_result = (
        [[[[10, 10], [300, 10], [300, 50], [10, 50]], "文档图像识别文本", 0.93]],
        [0.05],
    )
    doc = _make_image_doc(tmp_path, monkeypatch, engine_result=engine_result)

    processor = DocumentProcessor()
    captured = {}

    async def fake_lock(_document_id):
        return True

    async def fake_load(_document_id, _job_id):
        return doc

    async def fake_embeddings(texts):
        captured["texts"] = texts
        return [[1.0]] * len(texts)

    async def fake_replace(_doc, chunks_data, _embeddings):
        captured["chunks"] = chunks_data

    async def fake_success(_document_id, _chunks_data, _job_id):
        pass

    async def fake_release(_document_id):
        pass

    monkeypatch.setattr(processor, "_acquire_processing_lock", fake_lock)
    monkeypatch.setattr(processor, "_load_and_mark_parsing", fake_load)
    monkeypatch.setattr(processor, "_get_embeddings", fake_embeddings)
    monkeypatch.setattr(processor, "_replace_document_state", fake_replace)
    monkeypatch.setattr(processor, "_mark_success", fake_success)
    monkeypatch.setattr(processor, "_release_processing_lock", fake_release)

    ok = await processor.process("doc-img")

    assert ok is True
    assert any("文档图像识别文本" in c["chunk_text"] for c in captured["chunks"])
    assert any("文档图像识别文本" in t for t in captured["texts"])
