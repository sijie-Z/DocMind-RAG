"""上传校验失败必须诚实 4xx（round2 后端卫生③）—— 判别用例。

缺陷形态（K 支线评审 medium 实锤）：头像与文档两条上传入口，校验类失败
（类型/大小/MIME 不合规）在两处 except 链被逐层重包：
- service（upload_avatar / upload_file）：ValidationError(AppError, 400)
  被 `except Exception` 吞掉重包成 HTTPException(500)；
- 端点（files.py）：HTTPException(400) / AppError 又被
  `except Exception → AppError(str(e))` 重包成裸 AppError（基类 status=500）。
实测形态：合法扩展名 + 不符内容 → HTTP 500（_media_audit §3.4）。
仓内既有约定（issue #98 + tests/unit/test_control_flow_exception_passthrough.py）：
4xx 控制流异常原样放行，由全局 AppError handler（400 结构化 JSON）/
HTTPException handler 落码。本文件按同款做法直接调用服务/端点函数钉行为。

修复：service 四处（upload_file / upload_avatar / upload_chunk /
_process_merged_file 的合并校验）+ 端点三处（upload / upload/avatar /
upload-chunk）逐层放行 (HTTPException, AppError)；未识别的其余异常维持
原 500 语义（本文件同时钉住不放宽）。

变异点：
- 去掉 upload_avatar service 的 (HTTPException, AppError) 放行 →
  test_avatar_service_mime_mismatch_* 精确红；
- 去掉 files.py 头像端点的 (AppError, HTTPException) 放行 →
  test_avatar_endpoint_* 精确红。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.exceptions import AppError, ValidationError
from app.services.file_service import FileUploadService

# 真实 1x1 PNG（与 test_avatar_upload_image_mime.py 同款魔术字节）
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c626001000000ffff03000006000557bfabd400000000"
    "49454e44ae426082"
)


@pytest.fixture
def svc():
    return FileUploadService()


def _make_upload_file(
    filename: str, content: bytes, content_type: str = "application/octet-stream"
):
    f = MagicMock()
    f.filename = filename
    f.content_type = content_type
    f.read = AsyncMock(return_value=content)
    return f


def _fake_db():
    db = MagicMock()
    db.merge = AsyncMock(return_value=MagicMock(id=1))
    db.rollback = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    return db


class TestServiceLayer:
    """service 层：校验类失败不得被重包成 500。"""

    @pytest.mark.asyncio
    async def test_avatar_service_mime_mismatch_keeps_400(self, svc):
        upload = _make_upload_file("avatar.jpg", PNG_BYTES, "image/jpeg")
        with pytest.raises(ValidationError) as exc:
            await svc.upload_avatar(upload, user_id=1)
        assert exc.value.status_code == 400
        assert "doesn't match" in str(exc.value.message)

    @pytest.mark.asyncio
    async def test_avatar_service_bad_extension_keeps_400(self, svc):
        upload = _make_upload_file("avatar.pdf", b"%PDF-1.4", "application/pdf")
        with pytest.raises(HTTPException) as exc:
            await svc.upload_avatar(upload, user_id=1)
        assert exc.value.status_code == 400
        assert "不支持的文件格式" in exc.value.detail

    @pytest.mark.asyncio
    async def test_document_service_oversize_keeps_400(self, svc):
        svc.max_file_size = 10  # 68B PNG 超限 → 校验 400 应原样放行
        upload = _make_upload_file("scan.png", PNG_BYTES, "image/png")
        with pytest.raises(HTTPException) as exc:
            await svc.upload_file(upload, organization_id="1", user_id="u1")
        assert exc.value.status_code == 400
        assert "文件过大" in exc.value.detail

    @pytest.mark.asyncio
    async def test_document_service_mime_mismatch_keeps_400(self, svc):
        upload = _make_upload_file("report.jpg", PNG_BYTES, "image/jpeg")
        with pytest.raises(ValidationError) as exc:
            await svc.upload_file(upload, organization_id="1", user_id="u1")
        assert exc.value.status_code == 400
        assert "doesn't match" in str(exc.value.message)

    @pytest.mark.asyncio
    async def test_chunk_merged_validation_keeps_400(self, svc, monkeypatch, tmp_path):
        """分块合并后的扩展名校验失败必须原样 400（此前 _process_merged_file
        统一重包成裸 Exception → upload_chunk 的 except HTTPException 失配
        → 500）。"""
        from app.core.config import settings

        monkeypatch.setattr(settings, "UPLOAD_TEMP_DIR", str(tmp_path))
        file_hash = "c" * 64
        r0 = await svc.upload_chunk(b"xx", 0, 2, "scan.tiff", file_hash, "1", "u1")
        assert r0["status"] == "chunk_uploaded"
        with pytest.raises(HTTPException) as exc:
            await svc.upload_chunk(b"yy", 1, 2, "scan.tiff", file_hash, "1", "u1")
        assert exc.value.status_code == 400
        assert "不支持的文件格式" in exc.value.detail


class TestEndpointLayer:
    """端点层（files.py）：4xx 原样上抛，未识别异常维持 500 语义。"""

    @pytest.mark.asyncio
    async def test_avatar_endpoint_keeps_validation_error(self, monkeypatch):
        from app.api.v1.endpoints import files as files_ep

        fake_service = MagicMock()
        fake_service.upload_avatar = AsyncMock(
            side_effect=ValidationError("File content type 'image/png' is not allowed.")
        )
        monkeypatch.setattr(files_ep, "file_service", fake_service)
        db = _fake_db()

        with pytest.raises(ValidationError) as exc:
            await files_ep.upload_avatar(file=MagicMock(), current_user=MagicMock(), db=db)
        assert exc.value.status_code == 400
        db.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_avatar_endpoint_keeps_http_400(self, monkeypatch):
        from app.api.v1.endpoints import files as files_ep

        fake_service = MagicMock()
        fake_service.upload_avatar = AsyncMock(
            side_effect=HTTPException(status_code=400, detail="文件过大。最大支持 5MB")
        )
        monkeypatch.setattr(files_ep, "file_service", fake_service)
        db = _fake_db()

        with pytest.raises(HTTPException) as exc:
            await files_ep.upload_avatar(file=MagicMock(), current_user=MagicMock(), db=db)
        assert exc.value.status_code == 400
        db.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_avatar_endpoint_still_wraps_unexpected_as_500(self, monkeypatch):
        """不放宽的守线：非 4xx 的未知异常维持原有 500 包装。"""
        from app.api.v1.endpoints import files as files_ep

        fake_service = MagicMock()
        fake_service.upload_avatar = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(files_ep, "file_service", fake_service)
        db = _fake_db()

        with pytest.raises(AppError) as exc:
            await files_ep.upload_avatar(file=MagicMock(), current_user=MagicMock(), db=db)
        assert exc.value.status_code == 500
        assert "boom" in exc.value.message

    @pytest.mark.asyncio
    async def test_upload_endpoint_keeps_http_400(self, monkeypatch):
        from app.api.v1.endpoints import files as files_ep

        fake_service = MagicMock()
        fake_service.upload_file = AsyncMock(
            side_effect=HTTPException(status_code=400, detail="文件过大。最大支持 100MB")
        )
        monkeypatch.setattr(files_ep, "file_service", fake_service)
        monkeypatch.setattr(files_ep, "require_organization", AsyncMock(return_value=1))
        db = _fake_db()

        with pytest.raises(HTTPException) as exc:
            await files_ep.upload_file(
                background_tasks=MagicMock(),
                file=MagicMock(),
                description=None,
                tags=None,
                current_user=MagicMock(),
                db=db,
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_chunk_endpoint_keeps_http_400(self, monkeypatch):
        from app.api.v1.endpoints import files as files_ep

        fake_service = MagicMock()
        fake_service.upload_chunk = AsyncMock(
            side_effect=HTTPException(status_code=400, detail="分块数据不能为空")
        )
        monkeypatch.setattr(files_ep, "file_service", fake_service)
        monkeypatch.setattr(files_ep, "require_organization", AsyncMock(return_value=1))
        db = _fake_db()

        with pytest.raises(HTTPException) as exc:
            await files_ep.upload_file_chunk(
                chunk_data=b"",
                chunk_index=0,
                total_chunks=1,
                file_name="f.pdf",
                file_hash="a" * 64,
                current_user=MagicMock(),
                db=db,
            )
        assert exc.value.status_code == 400
