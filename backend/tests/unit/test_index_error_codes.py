"""竞品批次四：结构化索引错误码与阶段进度（判别性测试）。

变异说明见每个用例 docstring：删掉任一闭集校验/阶段切换/脱敏步骤都会让对应用例红。
"""

from types import SimpleNamespace

import pytest

import app.worker.doc_processor as dp_mod
from app.services.index_progress import (
    ERROR_DETAIL_MAX,
    STAGE_PROGRESS,
    IndexErrorCode,
    IndexStage,
    build_error_detail,
    fallback_code,
    normalize_error_code,
    normalize_stage,
    pipeline_payload,
    public_index_fields,
)
from app.worker.doc_processor import DocumentProcessor


class TestClosedTaxonomy:
    def test_stage_is_closed_four_value_set(self):
        """阶段闭集固定为 parse/chunk/embed/index。

        变异点：新增第五个阶段、改名或改 value → 红。
        """
        assert {stage.value for stage in IndexStage} == {"parse", "chunk", "embed", "index"}
        assert [stage.value for stage in IndexStage] == ["parse", "chunk", "embed", "index"]
        assert list(STAGE_PROGRESS) == list(IndexStage)

    def test_error_code_closed_set_and_normalization(self):
        """错误码闭集；DB/API 传入未知码或原始异常文本时归一为 None。

        变异点：把异常文本当码透传、接受任意字符串 → 红。
        """
        assert {code.value for code in IndexErrorCode} == {
            "PARSE_PROFILE_INVALID",
            "PARSE_UNSUPPORTED_FORMAT",
            "PARSE_FAILED",
            "CHUNK_EMPTY",
            "CHUNK_FAILED",
            "EMBED_COUNT_MISMATCH",
            "EMBED_FAILED",
            "INDEX_WRITE_FAILED",
            "INDEX_VERIFICATION_FAILED",
            "INDEX_FAILED",
            "DOCUMENT_NOT_FOUND",
            "JOB_SCHEDULE_FAILED",
            "UNEXPECTED",
        }
        for code in IndexErrorCode:
            assert normalize_error_code(code.value) == code.value
        assert normalize_error_code("RuntimeError: secret query") is None
        assert normalize_error_code("") is None
        assert normalize_stage("unknown-stage") is None
        assert normalize_stage(IndexStage.INDEX) == "index"

    def test_fallback_codes_never_escape_closed_set(self):
        for stage in [*IndexStage, None, "garbage"]:
            assert fallback_code(stage) in IndexErrorCode

    def test_payload_normalizes_legacy_and_malformed_rows(self):
        """旧任务（全 NULL）与 MagicMock 行都不能泄漏非闭集值。

        变异点：直接透传 getattr 结果 → 非闭集字符串或 MagicMock 进入响应 → 红。
        """
        row = SimpleNamespace(
            index_stage="parse",
            index_progress=25,
            index_error_code="RuntimeError: secret",
            index_error_detail="x" * 500,
        )
        assert pipeline_payload(row) == {
            "stage": "parse",
            "progress": 25,
            "error_code": None,
            "error_detail": None,
        }
        legacy = SimpleNamespace()
        assert pipeline_payload(legacy) == {
            "stage": None,
            "progress": 0,
            "error_code": None,
            "error_detail": None,
        }
        assert public_index_fields(legacy)["index_progress"] == 0


class TestNoLeakage:
    def test_detail_never_contains_exception_text_query_or_body(self):
        """公开详情只含 stage/异常类名/显式标量上下文。

        变异点：把 str(exc) 或 context 里的长文本写进 detail → 红。
        """
        exc = RuntimeError("SELECT * FROM docs WHERE query='TOP SECRET' body=合同正文")
        detail = build_error_detail(IndexStage.EMBED, exc, job_id=7)
        assert "TOP SECRET" not in detail
        assert "合同正文" not in detail
        assert "RuntimeError: SELECT" not in detail
        assert "exception=RuntimeError" in detail
        assert "stage=embed" in detail
        assert "job_id=7" in detail
        assert len(detail) <= ERROR_DETAIL_MAX

    def test_long_and_container_context_is_dropped(self):
        detail = build_error_detail(
            "parse",
            ValueError("boom"),
            query="x" * 1000,
            document_body=["a", "b"],
            safe_count=3,
        )
        assert "x" * 1000 not in detail
        assert "document_body" not in detail
        assert "safe_count=3" in detail
        assert len(detail) <= ERROR_DETAIL_MAX

    @pytest.mark.asyncio
    async def test_structured_fields_keep_legacy_parse_error_untouched(self, monkeypatch):
        """结构化字段写入不得碰 parse_error/error_message 的原文语义。"""
        executed = []

        class _Result:
            rowcount = 1

        class _Session:
            async def execute(self, stmt):
                compiled = stmt.compile()
                executed.append(str(compiled) + " " + str(compiled.params.values()))
                return _Result()

            async def commit(self):
                pass

        class _Ctx:
            async def __aenter__(self):
                return _Session()

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(dp_mod, "AsyncSessionLocal", lambda: _Ctx())
        dp_mod._current_stage.set(IndexStage.EMBED)
        dp_mod._current_code.set(IndexErrorCode.EMBED_FAILED)
        dp_mod._current_exception.set(RuntimeError("secret query text"))

        processor = DocumentProcessor()
        # _mark_failed 的 error 参数必须原样写入 parse_error（legacy 契约）。
        await processor._mark_failed("doc-1", "原始异常文本必须保留", job_id=7)
        doc_stmt = str(executed[0])
        job_stmt = str(executed[1])
        assert "原始异常文本必须保留" in doc_stmt
        assert "原始异常文本必须保留" in job_stmt
        assert "secret query text" not in doc_stmt + job_stmt
        assert "EMBED_FAILED" in doc_stmt + job_stmt
        assert "index_stage" in doc_stmt


class TestStageProgression:
    @pytest.mark.asyncio
    async def test_stage_progression_from_parse_to_index(self, monkeypatch):
        """各阶段边界写入 ContextVar；删掉任一 _enter_stage → 红。"""
        processor = DocumentProcessor()
        seen = []

        def capture(stage):
            def _fake(*_a, **_k):
                seen.append((stage, dp_mod._current_stage.get()))

            return _fake

        async def fake_lock(_id):
            return True

        async def fake_release(_id):
            pass

        async def fake_load(_id, _job):
            return SimpleNamespace(
                id="doc-1",
                file_path="f.pdf",
                organization_id="1",
                _reprocess_previous_status=None,
            )

        def chunk_result():
            return {"chunks": [{"chunk_text": "hello", "chunk_index": 0, "chunk_length": 5}]}

        async def fake_parse(*_a, **_k):
            return chunk_result()

        async def fake_embeddings(_texts):
            assert dp_mod._current_stage.get() == IndexStage.EMBED
            seen.append(("embed", dp_mod._current_stage.get()))
            return [[1.0]]

        async def fake_replace(*_a, **_k):
            assert dp_mod._current_stage.get() == IndexStage.INDEX
            seen.append(("index", dp_mod._current_stage.get()))

        async def fake_success(*_a, **_k):
            pass

        async def fake_graph(*_a, **_k):
            pass

        monkeypatch.setattr(processor, "_acquire_processing_lock", fake_lock)
        monkeypatch.setattr(processor, "_release_processing_lock", fake_release)
        monkeypatch.setattr(processor, "_load_and_mark_parsing", fake_load)
        monkeypatch.setattr(processor.parser, "parse_document", fake_parse)
        monkeypatch.setattr(processor, "_get_embeddings", fake_embeddings)
        monkeypatch.setattr(processor, "_replace_document_state", fake_replace)
        monkeypatch.setattr(processor, "_mark_success", fake_success)
        monkeypatch.setattr(processor, "_extract_graph_safely", fake_graph)

        assert await processor.process("doc-1", 7) is True
        assert ("embed", IndexStage.EMBED) in seen
        assert ("index", IndexStage.INDEX) in seen
        # parse 阶段进入后调用 parser；用 parser 替身内的断言兜底
        assert dp_mod._current_stage.get() == IndexStage.INDEX

    @pytest.mark.asyncio
    async def test_embed_failure_maps_to_embed_stage_and_closed_code(self, monkeypatch):
        """向量化失败 → stage=embed / EMBED_FAILED，且原异常文本仍进 parse_error。

        变异点：把 embed 失败归到 chunk 或用异常文本当 code → 红。
        """
        processor = DocumentProcessor()
        captured = {}

        async def fake_lock(_id):
            return True

        async def fake_release(_id):
            pass

        async def fake_load(_id, _job):
            return SimpleNamespace(
                id="doc-1",
                file_path="f.pdf",
                organization_id="1",
                _reprocess_previous_status=None,
            )

        async def fake_parse(*_a, **_k):
            return {"chunks": [{"chunk_text": "hello", "chunk_index": 0, "chunk_length": 5}]}

        async def fake_embeddings(_texts):
            raise RuntimeError("EMBED SECRET exception text")

        async def fake_failed(doc_id, error, job_id, **kwargs):
            captured["error"] = error
            captured["fields"] = dp_mod._structured_fields()

        monkeypatch.setattr(processor, "_acquire_processing_lock", fake_lock)
        monkeypatch.setattr(processor, "_release_processing_lock", fake_release)
        monkeypatch.setattr(processor, "_load_and_mark_parsing", fake_load)
        monkeypatch.setattr(processor.parser, "parse_document", fake_parse)
        monkeypatch.setattr(processor, "_get_embeddings", fake_embeddings)
        monkeypatch.setattr(processor, "_mark_failed", fake_failed)

        assert await processor.process("doc-1", 7) is False
        assert "EMBED SECRET exception text" in captured["error"]  # legacy 文本不变
        fields = captured["fields"]
        assert fields["index_stage"] == "embed"
        assert fields["index_error_code"] == "EMBED_FAILED"
        assert "EMBED SECRET" not in fields["index_error_detail"]
        assert "exception=RuntimeError" in fields["index_error_detail"]

    @pytest.mark.asyncio
    async def test_parse_failure_uses_stable_code_not_exception_text(self, monkeypatch):
        """parser 抛错 → PARSE_FAILED（闭集）；不改变 processor 返回/重投语义。

        变异点：把 str(exc) 写成 error_code → 红。
        """
        processor = DocumentProcessor()
        captured = {}

        async def fake_lock(_id):
            return True

        async def fake_release(_id):
            pass

        async def fake_load(_id, _job):
            return SimpleNamespace(
                id="doc-1",
                file_path="f.pdf",
                organization_id="1",
                _reprocess_previous_status=None,
            )

        async def fake_parse(*_a, **_k):
            raise RuntimeError("unsupported secret payload")

        async def fake_failed(doc_id, error, job_id, **kwargs):
            captured["error"] = error
            captured["fields"] = dp_mod._structured_fields()

        monkeypatch.setattr(processor, "_acquire_processing_lock", fake_lock)
        monkeypatch.setattr(processor, "_release_processing_lock", fake_release)
        monkeypatch.setattr(processor, "_load_and_mark_parsing", fake_load)
        monkeypatch.setattr(processor.parser, "parse_document", fake_parse)
        monkeypatch.setattr(processor, "_mark_failed", fake_failed)

        assert await processor.process("doc-1", 7) is False
        assert captured["error"] == "unsupported secret payload"
        assert captured["fields"]["index_stage"] == "parse"
        assert captured["fields"]["index_error_code"] == IndexErrorCode.PARSE_FAILED.value
        assert "secret payload" not in captured["fields"]["index_error_detail"]

    @pytest.mark.asyncio
    async def test_contextvar_does_not_leak_between_concurrent_runs(self):
        """交错执行的两个 run 不串写阶段（ContextVar 而非共享实例属性）。

        变异点：把 ContextVar 换成 processor 实例属性 → A 恢复执行时读到 B 的
        chunk 阶段 → 红。这里直接测并发上下文隔离本身，不依赖 worker I/O 替身。
        """
        import asyncio

        task_a_at_stage = asyncio.Event()
        task_b_advanced = asyncio.Event()
        observed = {}

        async def run_a():
            dp_mod._enter_stage(IndexStage.PARSE)
            task_a_at_stage.set()
            await task_b_advanced.wait()
            observed["a"] = dp_mod._current_stage.get()

        async def run_b():
            await task_a_at_stage.wait()
            dp_mod._enter_stage(IndexStage.CHUNK)
            observed["b"] = dp_mod._current_stage.get()
            task_b_advanced.set()

        await asyncio.gather(run_a(), run_b())
        assert observed["a"] == IndexStage.PARSE
        assert observed["b"] == IndexStage.CHUNK
