"""文档路径图片上传放行（round2 后端卫生②）—— 判别用例。

缺陷形态（OCR 支线遗留，见 _orchestrate/round2/_ocr_decisions.txt 二）：
主端点 /documents/upload 已全链支持图片（OCR 已接线），但 /files/upload
与 /files/upload-chunk 落到 services/file_service.py 的白名单——
allowed_extensions（7 种文档格式）与 _ALLOWED_MIME_TYPES（无 image/*）——
仍把图片拒绝，从这两个入口传图必失败。

修复：两表白名单同步加入 .png/.jpg/.jpeg/.webp/.bmp/.gif（mime 键为
filetype 实测的 image/* 串），_get_content_type 补图片项（分块合并路径的
content_type 不再退化为 octet-stream）；主端点与解析器行为不动。

变异点：从 _ALLOWED_MIME_TYPES 去掉 "image/png" → 本文件真 PNG 用例精确红
（ValidationError: File content type 'image/png' is not allowed）。
"""
import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.exceptions import ValidationError
from app.services.file_service import _ALLOWED_MIME_TYPES, FileUploadService

# 真实 1x1 PNG（与 test_avatar_upload_image_mime.py 同款魔术字节）
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c626001000000ffff03000006000557bfabd400000000"
    "49454e44ae426082"
)


@pytest.fixture
def svc():
    return FileUploadService()


class _FakeSession:
    """最小 async session：upload_file 的 DB 触点是 execute/add/commit/refresh。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *a, **k):
        return MagicMock()

    def add(self, obj):
        pass

    async def commit(self):
        pass

    async def refresh(self, obj):
        # Document.created_at 是 server_default，fake 层替它补上
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(UTC)


def _make_upload_file(filename, content, content_type):
    f = MagicMock()
    f.filename = filename
    f.content_type = content_type
    f.read = AsyncMock(return_value=content)
    return f


def _wire_fakes(monkeypatch):
    """upload_file 的 DB 与后台任务脱实：fake session + 后台解析占位。"""
    monkeypatch.setattr(
        "app.services.file_service.AsyncSessionLocal", lambda: _FakeSession()
    )
    monkeypatch.setattr(FileUploadService, "_process_document_async", AsyncMock())


class TestWhitelistCoverage:
    def test_extension_whitelist_covers_images(self, svc):
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            svc._validate_file(f"photo{ext}", 1024)
        with pytest.raises(HTTPException):
            svc._validate_file("photo.tiff", 1024)  # 未接入解析链的格式仍拒

    def test_mime_table_covers_images(self):
        assert ".png" in _ALLOWED_MIME_TYPES["image/png"]
        assert {".jpg", ".jpeg"} <= _ALLOWED_MIME_TYPES["image/jpeg"]
        assert ".gif" in _ALLOWED_MIME_TYPES["image/gif"]
        assert ".webp" in _ALLOWED_MIME_TYPES["image/webp"]
        assert ".bmp" in _ALLOWED_MIME_TYPES["image/bmp"]

    def test_real_png_passes_document_mime_check(self, svc):
        svc._validate_mime_type(PNG_BYTES, "scan.png")

    def test_png_content_with_wrong_extension_still_rejected(self, svc):
        with pytest.raises(ValidationError, match="doesn't match"):
            svc._validate_mime_type(PNG_BYTES, "scan.jpg")


class TestUploadFileWithImage:
    """端到端：/files/upload（upload_file）真 PNG 全链通过。"""

    @pytest.mark.asyncio
    @patch("app.services.file_service.minio_client")
    async def test_real_png_upload_full_path(self, mock_minio, svc, monkeypatch):
        _wire_fakes(monkeypatch)
        upload = _make_upload_file("scan.png", PNG_BYTES, "image/png")

        result = await svc.upload_file(upload, organization_id="1", user_id="u1")
        await asyncio.sleep(0)  # 让后台任务跑完，避免遗留 pending task

        assert result["filename"] == "scan.png"
        assert result["file_size"] == len(PNG_BYTES)
        mock_minio.put_object.assert_called_once()
        kwargs = mock_minio.put_object.call_args.kwargs
        assert kwargs["object_name"].endswith(".png")
        assert kwargs["content_type"] == "image/png"


class TestChunkedUploadWithImage:
    """端到端：/files/upload-chunk（upload_chunk）真 PNG 分两块合并通过。"""

    @pytest.mark.asyncio
    @patch("app.services.file_service.minio_client")
    async def test_chunked_png_upload_completes(self, mock_minio, svc, monkeypatch, tmp_path):
        from app.core.config import settings

        _wire_fakes(monkeypatch)
        monkeypatch.setattr(settings, "UPLOAD_TEMP_DIR", str(tmp_path))

        half = len(PNG_BYTES) // 2
        file_hash = "b" * 64
        r1 = await svc.upload_chunk(
            PNG_BYTES[:half], 0, 2, "scan.png", file_hash, "1", "u1"
        )
        r2 = await svc.upload_chunk(
            PNG_BYTES[half:], 1, 2, "scan.png", file_hash, "1", "u1"
        )
        await asyncio.sleep(0)

        assert r1["status"] == "chunk_uploaded"
        assert r2["status"] == "completed"
        assert r2["document_id"]
        kwargs = mock_minio.put_object.call_args.kwargs
        assert kwargs["content_type"] == "image/png"  # _get_content_type 补的图片项
