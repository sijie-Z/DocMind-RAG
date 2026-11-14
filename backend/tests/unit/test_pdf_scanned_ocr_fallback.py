"""扫描件 PDF（无文本层）应走页面渲染 + OCR，而不是解析失败。

真实语料复现（2026-11-14）：`_parse_pdf` 的 OCR 分支依赖 pytesseract + pdf2image，
两者在本环境都没安装 → 扫描件 PDF 永远拿不到文字。而 `pypdfium2` 与 RapidOCR 都在，
可以纯 Python 完成渲染 + 识别，不需要 poppler / tesseract 这类系统二进制。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.services.document_parser import DocumentParser

TEXT = "PAYMENT TERMS 30 DAYS"


def _font(size: int = 44) -> ImageFont.FreeTypeFont:
    for candidate in ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf"):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    pytest.skip("没有可用的 TrueType 字体，跳过 OCR 断言")


def _make_scanned_pdf(path: Path) -> Path:
    """只有图像、没有文本层的 PDF（模拟扫描件）。"""
    img = Image.new("RGB", (1240, 1754), "white")  # A4 @150dpi
    ImageDraw.Draw(img).text((80, 400), TEXT, fill="black", font=_font(56))
    img.save(str(path), "PDF", resolution=150.0)
    return path


def test_scanned_pdf_falls_back_to_page_ocr(tmp_path: Path) -> None:
    target = _make_scanned_pdf(tmp_path / "scanned.pdf")

    result = asyncio.run(DocumentParser().parse_local_file(str(target), "1"))

    content = (result.get("content") or "").upper()
    assert "PAYMENT" in content, f"OCR 未取到文字，实际内容: {content[:160]!r}"
    assert result["metadata"].get("ocr_used") is True
