"""DocumentParser 纯函数测试（不依赖外部服务）。"""
import os
import tempfile

import pytest

from app.services.document_parser import DocumentParser


@pytest.fixture
def parser():
    return DocumentParser()


class TestParseTxt:
    @pytest.mark.asyncio
    async def test_parse_plain_text(self, parser: DocumentParser):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("这是一段测试文本。\n第二行内容。")
            f.flush()
            path = f.name
        try:
            result = await parser._parse_txt(path)
            assert "测试文本" in result["content"]
            assert "encoding" in result["metadata"]
            assert result["metadata"]["encoding"] == "utf-8"
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_parse_markdown(self, parser: DocumentParser):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# 标题\n\n正文内容\n\n## 子标题\n\n更多内容。")
            f.flush()
            path = f.name
        try:
            result = await parser._parse_txt(path)
            assert "标题" in result["content"]
            assert result["metadata"]["line_count"] >= 4
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_parse_empty_txt(self, parser: DocumentParser):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("")
            f.flush()
            path = f.name
        try:
            result = await parser._parse_txt(path)
            assert result["content"] == ""
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_parse_txt_metadata(self, parser: DocumentParser):
        content = "第一行\n第二行\n第三行"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(content)
            f.flush()
            path = f.name
        try:
            result = await parser._parse_txt(path)
            assert result["metadata"]["char_count"] == len(content)
            assert result["metadata"]["line_count"] == 3
        finally:
            os.unlink(path)


class TestParseDocx:
    @pytest.mark.asyncio
    async def test_parse_docx(self, parser: DocumentParser):
        """Create a minimal .docx and parse it."""
        try:
            from docx import Document as DocxDocument
        except ImportError:
            pytest.skip("python-docx not installed")

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            path = f.name

        doc = DocxDocument()
        doc.add_paragraph("第一段内容。")
        doc.add_paragraph("第二段内容。")
        doc.save(path)

        try:
            result = await parser._parse_docx(path)
            assert "第一段内容" in result["content"]
            assert "第二段内容" in result["content"]
            assert "paragraphs" in result["metadata"]
        finally:
            os.unlink(path)


class TestSupportedContent:
    def test_supported_extensions_include_common_types(self, parser: DocumentParser):
        assert ".pdf" in parser.supported_extensions
        assert ".docx" in parser.supported_extensions
        assert ".txt" in parser.supported_extensions
        assert ".md" in parser.supported_extensions
        assert ".xlsx" in parser.supported_extensions


# ── 图片 OCR（位图 → 文本 → 现有 chunk 管道）─────────────────────────

def _make_text_png(path: str, lines=("DOCMIND OCR TEST 12345", "Hello RapidOCR 2026"), *, size=(900, 300), font_size=48) -> None:
    """用 PIL 在测试时生成 ASCII 文本 PNG。

    确定性：Pillow ≥10.1 的 ``ImageFont.load_default(size=...)`` 使用随包
    分发的位图字体，不依赖系统字体（CI/本地渲染一致）。
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=font_size)
    y = 30
    for line in lines:
        draw.text((40, y), line, fill="black", font=font)
        y += font_size + 50
    img.save(path)


class TestParseImage:
    @pytest.mark.asyncio
    async def test_parse_image_real_engine_reads_ascii_text(self, parser: DocumentParser, tmp_path):
        """真实 RapidOCR 引擎（不 mock）：ASCII 文本 PNG → 文本命中 + 引擎元数据。"""
        png = tmp_path / "ascii.png"
        _make_text_png(str(png))

        result = await parser._parse_image(str(png))

        normalized = result["content"].replace(" ", "").upper()
        assert "DOCMIND" in normalized
        assert "12345" in normalized
        assert result["metadata"]["ocr_engine"] == "rapidocr-onnxruntime"
        score = result["metadata"]["ocr_avg_score"]
        assert 0.0 < score <= 1.0
        assert len(result["chunks"]) >= 1
        assert result["chunks"][0]["chunk_text"]

    @pytest.mark.asyncio
    async def test_parse_image_no_text_fails_honestly(self, parser: DocumentParser, tmp_path):
        """无文字图片（近似照片）→ 显式失败（#301 诚实原则）。

        不许产出空内容 chunk 让文档被标成"有内容"的 INDEXED；具体失败文案
        必须点明"未识别到文字"（区别于其它解析错误）。
        """
        from PIL import Image

        png = tmp_path / "photo_like.png"
        # 平滑灰度渐变：无文本、无高频边缘，真实引擎检测不到任何文本行
        Image.linear_gradient("L").convert("RGB").resize((600, 400)).save(str(png))

        with pytest.raises(Exception, match="未识别到文字"):
            await parser._parse_image(str(png))

    @pytest.mark.asyncio
    async def test_parse_image_mocked_engine_orders_lines_and_records_meta(
        self, parser: DocumentParser, tmp_path, monkeypatch
    ):
        """引擎替身（乱序返回）→ 行序按 y 稳定重排 + 置信度均值/行数入元数据。"""
        import app.services.document_parser as parser_mod

        png = tmp_path / "x.png"
        _make_text_png(str(png))
        canned = [
            [[[30, 220], [500, 220], [500, 260], [30, 260]], "第二行", 0.9],
            [[[30, 40], [500, 40], [500, 80], [30, 80]], "第一行", 0.8],
            [[[520, 40], [700, 40], [700, 80], [520, 80]], "收尾", 0.7],
        ]

        class _FakeEngine:
            def __call__(self, img, **kwargs):
                return canned, [0.1]

        monkeypatch.setattr(parser_mod, "_get_ocr_engine", lambda: _FakeEngine())

        result = await parser._parse_image(str(png))

        # 同一行两段中文直接拼接（无空格）；不同行以换行分隔
        assert result["content"].splitlines() == ["第一行收尾", "第二行"]
        assert result["metadata"]["ocr_lines"] == 2
        assert abs(result["metadata"]["ocr_avg_score"] - 0.8) < 1e-6

    @pytest.mark.asyncio
    async def test_parse_image_mocked_engine_none_raises(self, parser: DocumentParser, tmp_path, monkeypatch):
        """引擎替身返回 (None, None)（检测不到）→ 与真实引擎空结果同一条诚实失败路径。"""
        import app.services.document_parser as parser_mod

        png = tmp_path / "x.png"
        _make_text_png(str(png))

        class _FakeEngine:
            def __call__(self, img, **kwargs):
                return None, None

        monkeypatch.setattr(parser_mod, "_get_ocr_engine", lambda: _FakeEngine())

        with pytest.raises(Exception, match="未识别到文字"):
            await parser._parse_image(str(png))

    def test_image_extensions_dispatched(self, parser: DocumentParser):
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"):
            assert ext in parser.supported_extensions, ext
            assert parser.supported_extensions[ext] == parser._parse_image

    def test_prepare_image_caps_long_side(self, parser: DocumentParser, tmp_path):
        """大图降采样：最长边压到上限（防超大图拖死 worker）。"""
        from PIL import Image

        from app.services.document_parser import _OCR_MAX_IMAGE_SIDE

        png = tmp_path / "big.png"
        Image.new("RGB", (4000, 1200), "white").save(str(png))

        with Image.open(str(png)) as im:
            prepared = parser._prepare_image(im)

        assert max(prepared.size) == _OCR_MAX_IMAGE_SIDE == 2000
        assert prepared.size == (2000, 600)

    def test_prepare_image_keeps_small_size(self, parser: DocumentParser, tmp_path):
        from PIL import Image

        png = tmp_path / "small.png"
        Image.new("RGB", (800, 600), "white").save(str(png))

        with Image.open(str(png)) as im:
            prepared = parser._prepare_image(im)

        assert prepared.size == (800, 600)

    def test_prepare_image_applies_exif_orientation(self, parser: DocumentParser, tmp_path):
        """手机竖拍照片（EXIF Orientation=6）：不矫正会把横向内容送 OCR。"""
        from PIL import Image

        jpg = tmp_path / "rot.jpg"
        img = Image.new("RGB", (300, 100), "white")
        exif = Image.Exif()
        exif[274] = 6  # Orientation: Rotate 90 CW
        img.save(str(jpg), "JPEG", exif=exif)

        with Image.open(str(jpg)) as im:
            prepared = parser._prepare_image(im)

        assert prepared.size == (100, 300)

    def test_ocr_engine_lazy_singleton(self, monkeypatch):
        """引擎惰性单例：多次获取只构造一次（模型不因每篇文档重复加载）。"""
        import app.services.document_parser as parser_mod

        created = []

        class _FakeRapidOCR:
            def __init__(self):
                created.append(1)

        monkeypatch.setattr(parser_mod, "_ocr_engine", None)
        monkeypatch.setattr("rapidocr_onnxruntime.RapidOCR", _FakeRapidOCR)

        first = parser_mod._get_ocr_engine()
        second = parser_mod._get_ocr_engine()

        assert first is second
        assert created == [1]

    def test_prepare_image_composites_transparent_on_white(self, parser: DocumentParser, tmp_path):
        """透明底 PNG → 合成白底（评审 M5 缺口：删掉合成分支此前全绿）。

        构造用的是"承载色为黑"的透明像素：不合成、直接 convert("RGB") 会得到
        黑底，深色文字等于消失；合成后透明区必须是白底、不透明内容原样保留。
        """
        from PIL import Image

        png = tmp_path / "transparent.png"
        img = Image.new("RGBA", (200, 80), (0, 0, 0, 0))  # 全透明（承载色为黑）
        img.paste(Image.new("RGBA", (60, 30), (0, 0, 0, 255)), (20, 20))  # 不透明黑块
        img.save(str(png))

        with Image.open(str(png)) as im:
            prepared = parser._prepare_image(im)

        assert prepared.mode == "RGB"
        assert prepared.getpixel((5, 5)) == (255, 255, 255)  # 透明区 → 白底
        assert prepared.getpixel((40, 35)) == (0, 0, 0)  # 不透明内容保留

    @pytest.mark.asyncio
    async def test_parse_image_real_engine_transparent_png(self, parser: DocumentParser, tmp_path):
        """透明底文本 PNG（真实引擎）：合白底后文字可被识别（端到端钉住 M5 分支）。"""
        from PIL import Image, ImageDraw, ImageFont

        png = tmp_path / "transparent_text.png"
        img = Image.new("RGBA", (900, 200), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.text(
            (40, 60),
            "TRANSPARENT OCR 777",
            fill=(0, 0, 0, 255),
            font=ImageFont.load_default(size=48),
        )
        img.save(str(png))

        result = await parser._parse_image(str(png))

        normalized = result["content"].replace(" ", "").upper()
        assert "TRANSPARENT" in normalized
        assert "777" in normalized

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fmt", ["WEBP", "BMP"])
    async def test_parse_image_real_engine_webp_bmp(
        self, parser: DocumentParser, tmp_path, fmt
    ):
        """WEBP / BMP 各一个真实解析用例（评审缺口：此前只登记了扩展名）。"""
        from PIL import Image

        src = tmp_path / "ascii.png"
        _make_text_png(str(src))
        target = tmp_path / f"ascii.{fmt.lower()}"
        with Image.open(str(src)) as im:
            im.save(str(target), fmt)

        result = await parser._parse_image(str(target))

        normalized = result["content"].replace(" ", "").upper()
        assert "DOCMIND" in normalized
        assert "12345" in normalized
        assert result["metadata"]["ocr_engine"] == "rapidocr-onnxruntime"
        assert 0.0 < result["metadata"]["ocr_avg_score"] <= 1.0
