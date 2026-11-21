"""P0「文档解析档位可选」判别性测试。

覆盖：auto 旧行为、ocr 强制 OCR、table_accurate 精细表格参数、
非法档位拒绝、parse_profile_used 留痕，以及解析异常不影响上传主流程。
"""

from unittest.mock import MagicMock

import pytest

from app.services.document_parser import (
    _TABLE_ACCURATE_SETTINGS,
    DocumentParser,
    normalize_parse_profile,
)


@pytest.fixture
def parser():
    return DocumentParser()


def _fake_text_page(monkeypatch, *, text: str = "合同正文条款" * 20):
    """文本充足的 PDF 页（auto 不应 OCR），表格返回一行两列。"""
    import app.services.document_parser as mod

    page = MagicMock()
    page.extract_text.return_value = text
    page.extract_tables.return_value = [["A", "B"], ["1", "2"]]

    pdf = MagicMock()
    pdf.__enter__.return_value.pages = [page]
    pdf.__exit__.return_value = None
    monkeypatch.setattr(mod.pdfplumber, "open", lambda _path: pdf)
    return page


def _stub_chunks(monkeypatch, parser: DocumentParser):
    """绕开语义切分/LLM，直接产出可检查的 chunk 元数据。"""

    async def fake_chunks(_text, **_kwargs):
        return [
            {
                "chunk_index": 0,
                "chunk_text": "chunk",
                "original_text": "chunk",
                "text_provenance": "parser_original",
                "chunk_length": 5,
                "start_pos": 0,
                "end_pos": 5,
                "metadata": {"type": "contextual_semantic_split"},
            }
        ]

    monkeypatch.setattr(parser, "_create_contextual_chunks", fake_chunks)


# ── 档位归一化 ──────────────────────────────────────────────────


@pytest.mark.parametrize("value", [None, "", "   ", 123, MagicMock()])
def test_normalize_defaults_to_auto(value):
    assert normalize_parse_profile(value) == "auto"


@pytest.mark.parametrize("value,expected", [("auto", "auto"), (" OCR ", "ocr")])
def test_normalize_accepts_allowed_values(value, expected):
    assert normalize_parse_profile(value) == expected


def test_normalize_rejects_unknown_profile():
    with pytest.raises(ValueError, match="非法解析档位"):
        normalize_parse_profile("force_ocr")


# ── PDF 判别性行为 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_keeps_legacy_behavior(parser, monkeypatch, tmp_path):
    """文本充足页面：auto 不 OCR、extract_tables 不传参数。"""
    page = _fake_text_page(monkeypatch)
    _stub_chunks(monkeypatch, parser)
    ocr_calls = []

    async def spy_ocr(*_args):
        ocr_calls.append(1)
        return "OCR 不应被调用"

    monkeypatch.setattr(parser, "_ocr_pdf_page", spy_ocr)

    pdf = tmp_path / "auto.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")

    result = await parser.parse_document(str(pdf), "1")

    assert ocr_calls == []
    assert page.extract_tables.call_args.args == ()
    assert result["parse_profile_used"] == "auto"
    assert result["metadata"]["parse_profile"] == "auto"
    assert result["metadata"]["table_parse_mode"] == "default"
    assert result["chunks"][0]["metadata"]["parse_profile"] == "auto"


@pytest.mark.asyncio
async def test_ocr_profile_forces_ocr_on_text_rich_page(parser, monkeypatch, tmp_path):
    """ocr 档：即使文本充足也调用 OCR，且结果包含 OCR 文本。"""
    _fake_text_page(monkeypatch)
    _stub_chunks(monkeypatch, parser)
    ocr_calls = []

    async def spy_ocr(_path, page_num):
        ocr_calls.append(page_num)
        return "扫描件正文OCR"

    monkeypatch.setattr(parser, "_ocr_pdf_page", spy_ocr)

    pdf = tmp_path / "forced.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")

    result = await parser.parse_document(str(pdf), "1", parse_profile="ocr")

    assert ocr_calls == [1]
    assert "扫描件正文OCR" in result["content"]
    assert result["metadata"]["ocr_used"] is True
    assert result["parse_profile_used"] == "ocr"


@pytest.mark.asyncio
async def test_table_accurate_passes_explicit_table_settings(parser, monkeypatch, tmp_path):
    """table_accurate 档：向 pdfplumber 传入线框策略与容差（保留行列结构）。"""
    page = _fake_text_page(monkeypatch)
    _stub_chunks(monkeypatch, parser)

    async def no_ocr(*_args):
        return ""

    monkeypatch.setattr(parser, "_ocr_pdf_page", no_ocr)

    pdf = tmp_path / "table.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")

    result = await parser.parse_document(str(pdf), "1", parse_profile="table_accurate")

    assert page.extract_tables.call_args.kwargs["table_settings"] == _TABLE_ACCURATE_SETTINGS
    assert result["metadata"]["table_parse_mode"] == "accurate"
    assert result["parse_profile_used"] == "table_accurate"
    assert result["chunks"][0]["metadata"]["parse_profile"] == "table_accurate"


# ── 非法值与异常语义 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_profile_rejected_before_file_access(parser, tmp_path):
    """非法档位在任何文件/下载动作之前拒绝，且不污染上传主流程。"""
    with pytest.raises(ValueError, match="非法解析档位"):
        await parser.parse_document(str(tmp_path / "missing.pdf"), "1", parse_profile="aggressive")
    with pytest.raises(ValueError, match="非法解析档位"):
        await parser.parse_local_file(
            str(tmp_path / "missing.pdf"), "1", parse_profile="aggressive"
        )


@pytest.mark.asyncio
async def test_ocr_engine_failure_does_not_break_parse(parser, monkeypatch, tmp_path):
    """OCR 引擎失败由 _ocr_pdf_page 吞掉并回退文本层（异常不影响主流程）。"""
    import app.services.document_parser as parser_mod

    _fake_text_page(monkeypatch)
    _stub_chunks(monkeypatch, parser)

    def failing_engine():
        raise RuntimeError("ocr engine boom")

    monkeypatch.setattr(parser_mod, "_get_ocr_engine", failing_engine)

    pdf = tmp_path / "ocr-fail.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")

    result = await parser.parse_document(str(pdf), "1", parse_profile="ocr")

    assert "合同正文条款" in result["content"]
    assert result["metadata"]["ocr_used"] is False
    assert result["parse_profile_used"] == "ocr"
