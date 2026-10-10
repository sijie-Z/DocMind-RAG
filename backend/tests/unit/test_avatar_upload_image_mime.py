"""头像上传真图片判别用例（媒体解析审计 §3.4 实锤，任务 ad488b01 追加）。

真栈实测：POST /api/v1/files/upload/avatar + 合法 tiny.png →
500 "File content type 'image/png' is not allowed."

根因（backend/app/services/file_service.py）：
- upload_avatar 走 _validate_file(is_avatar=True)：扩展名白名单已放行
  {.jpg,.jpeg,.png,.gif,.webp}；
- 但 _validate_mime_type 共用**文档** MIME 表 _ALLOWED_MIME_TYPES（无任何
  image/*）→ 魔术字节识别出 image/png 后直接拒绝 → 端点在赋值前炸成 500。
  （K 支线当时文档路径保持无 image/*；round2 后端卫生修复②后文档表也放行
  图片——解析链已接 OCR——本文件的对应断言已随修复②更新。）

修复：_validate_mime_type 增 is_avatar 分支用 _ALLOWED_AVATAR_MIME_TYPES，
upload_avatar 传 is_avatar=True；文档白名单原样不动。

变异点：upload_avatar 的校验回退共用文档表（去掉 is_avatar=True）
→ 本文件「真 PNG / 真 JPEG」用例精确红（ValidationError: image/png is not allowed）。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.exceptions import ValidationError
from app.services.file_service import _ALLOWED_MIME_TYPES, FileUploadService

# ── 真实图片字节（魔术字节与真实文件一致，filetype 可识别）──────────────
# 1x1 PNG
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c626001000000ffff03000006000557bfabd400000000"
    "49454e44ae426082"
)
# 最小 JPEG 头（FF D8 FF E0 + JFIF）
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + b"0" * 20


def _make_upload_file(
    filename: str = "avatar.png",
    content: bytes = PNG_BYTES,
    content_type: str = "image/png",
):
    """Create a mock UploadFile (与 test_file_service.py 同款样板)."""
    f = MagicMock()
    f.filename = filename
    f.content_type = content_type
    f.read = AsyncMock(return_value=content)
    return f


@pytest.fixture
def svc():
    return FileUploadService()


class TestAvatarUploadImageMime:
    """头像上传：真实图片字节必须通过（媒体审计实锤的 500 修复）。"""

    @pytest.mark.asyncio
    @patch("app.services.file_service.minio_client")
    async def test_real_png_avatar_accepted(self, mock_minio, svc):
        upload = _make_upload_file("avatar.png", PNG_BYTES, "image/png")
        result = await svc.upload_avatar(upload, user_id=1)

        # 真实返回值 = MinIO 对象名（file_path 统一存对象名）
        assert result.endswith(".png")
        assert result.startswith("avatars/1/")
        mock_minio.put_object.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.file_service.minio_client")
    async def test_real_jpeg_avatar_accepted(self, mock_minio, svc):
        upload = _make_upload_file("avatar.jpg", JPEG_BYTES, "image/jpeg")
        result = await svc.upload_avatar(upload, user_id=7)
        assert result.endswith(".jpg")
        mock_minio.put_object.assert_called_once()

    def test_png_content_with_jpg_extension_still_rejected(self, svc):
        """内容与扩展名不符仍须拒绝（头像分支不放宽一致性校验）。"""
        with pytest.raises(ValidationError, match="doesn't match"):
            svc._validate_mime_type(PNG_BYTES, "avatar.jpg", is_avatar=True)

    @pytest.mark.asyncio
    async def test_mismatch_avatar_end_to_end_rejected(self, svc):
        """端到端：不符内容仍拒绝（状态码为既有 500 包裹行为，与文档路径
        upload_file 同款 except 链；本次只修「真图片被判非法」的主 bug，不动
        该状态码映射，留作已知残余）。"""
        upload = _make_upload_file("avatar.jpg", PNG_BYTES, "image/jpeg")
        with pytest.raises(HTTPException) as exc_info:
            await svc.upload_avatar(upload, user_id=1)
        assert exc_info.value.status_code == 500
        assert "doesn't match" in str(exc_info.value.detail)

    def test_document_path_accepts_images_after_unification(self, svc):
        """round2 后端卫生修复②：文档路径同步放行图片（解析链已接 OCR，
        /files/upload 与主端点口径对齐）——真 PNG 内容通过 MIME 校验；
        内容/扩展名一致性校验不放宽。"""
        svc._validate_mime_type(PNG_BYTES, "report.png")
        with pytest.raises(ValidationError, match="doesn't match"):
            svc._validate_mime_type(PNG_BYTES, "report.jpg")
        assert ".png" in _ALLOWED_MIME_TYPES["image/png"]

    def test_avatar_whitelist_covers_frontend_accept(self, svc):
        """头像专用 MIME 表覆盖前端 accept=image/* 的主要格式（png/jpeg/gif/webp）。"""
        from app.services.file_service import _ALLOWED_AVATAR_MIME_TYPES

        for mime, exts in {
            "image/png": {".png"},
            "image/jpeg": {".jpg", ".jpeg"},
            "image/gif": {".gif"},
            "image/webp": {".webp"},
        }.items():
            assert mime in _ALLOWED_AVATAR_MIME_TYPES
            assert exts <= _ALLOWED_AVATAR_MIME_TYPES[mime]
