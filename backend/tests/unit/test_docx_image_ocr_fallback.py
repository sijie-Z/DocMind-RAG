"""扫描件 DOCX（只有图片、没有正文）应走内嵌图片 OCR，而不是直接失败。

真实语料复现（2026-11-14）：`面试的一些问题.docx` 是纯图片文档，
python-docx / docx2txt / zip-xml 三个文本解析器全部拿不到字，
旧实现直接抛「DOCX解析失败：未提取到有效文本内容」，用户无法知道该怎么办。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from docx import Document
from PIL import Image, ImageDraw, ImageFont

from app.services.document_parser import DocumentParser

TEXT = "PAYMENT TERMS 30 DAYS"


def _font(size: int = 44) -> ImageFont.FreeTypeFont:
    for candidate in ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf"):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    pytest.skip("没有可用的 TrueType 字体，跳过 OCR 断言")


def _make_scanned_docx(path: Path) -> Path:
    img = Image.new("RGB", (1100, 300), "white")
    ImageDraw.Draw(img).text((40, 110), TEXT, fill="black", font=_font())
    png = path.with_suffix(".png")
    img.save(png)

    doc = Document()
    doc.add_picture(str(png))
    doc.save(str(path))
    return path


def test_image_only_docx_falls_back_to_embedded_image_ocr(tmp_path: Path) -> None:
    target = _make_scanned_docx(tmp_path / "scanned.docx")

    result = asyncio.run(DocumentParser().parse_local_file(str(target), "1"))

    content = (result.get("content") or "").upper()
    assert "PAYMENT" in content, f"OCR 未取到文字，实际内容: {content[:120]!r}"
    assert result["metadata"].get("fallback_parser") == "ocr_docx_images"
