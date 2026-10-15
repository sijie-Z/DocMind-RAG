"""File service unit tests — validation, hashing, MIME checks."""
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.exceptions import ValidationError
from app.services.file_service import _ALLOWED_MIME_TYPES, FileUploadService

# ── Helpers ──────────────────────────────────────────────────────────

def _make_upload_file(filename: str = "test.pdf", content: bytes = b"dummy", content_type: str = "application/pdf"):
    """Create a mock UploadFile."""
    f = MagicMock()
    f.filename = filename
    f.content_type = content_type
    f.read = AsyncMock(return_value=content)
    return f


# ── Service instance ────────────────────────────────────────────────

@pytest.fixture
def svc():
    return FileUploadService()


# ── _validate_file ──────────────────────────────────────────────────

class TestValidateFile:
    def test_valid_pdf(self, svc):
        svc._validate_file("report.pdf", 1024)

    def test_valid_docx(self, svc):
        svc._validate_file("report.docx", 1024)

    def test_valid_txt(self, svc):
        svc._validate_file("notes.txt", 1024)

    def test_invalid_extension_raises(self, svc):
        with pytest.raises(HTTPException) as exc_info:
            svc._validate_file("virus.exe", 1024)
        assert exc_info.value.status_code == 400

    def test_file_too_large_raises(self, svc):
        with pytest.raises(HTTPException) as exc_info:
            svc._validate_file("big.pdf", 200 * 1024 * 1024)
        assert exc_info.value.status_code == 400

    def test_avatar_valid(self, svc):
        svc._validate_file("avatar.jpg", 1024, is_avatar=True)

    def test_avatar_invalid_extension(self, svc):
        with pytest.raises(HTTPException):
            svc._validate_file("avatar.pdf", 1024, is_avatar=True)

    def test_avatar_too_large(self, svc):
        with pytest.raises(HTTPException):
            svc._validate_file("avatar.jpg", 10 * 1024 * 1024, is_avatar=True)


# ── _generate_file_hash ─────────────────────────────────────────────

class TestGenerateFileHash:
    def test_returns_sha256(self, svc):
        content = b"hello world"
        expected = hashlib.sha256(content).hexdigest()
        assert svc._generate_file_hash(content) == expected

    def test_empty_content(self, svc):
        assert svc._generate_file_hash(b"") == hashlib.sha256(b"").hexdigest()

    def test_different_content_different_hash(self, svc):
        h1 = svc._generate_file_hash(b"abc")
        h2 = svc._generate_file_hash(b"def")
        assert h1 != h2


# ── _validate_mime_type ─────────────────────────────────────────────

class TestValidateMimeType:
    def test_matching_mime_and_extension(self, svc):
        mock_ft = MagicMock()
        kind = MagicMock()
        kind.mime = "application/pdf"
        mock_ft.guess.return_value = kind
        with patch.dict("sys.modules", {"filetype": mock_ft}):
            svc._validate_mime_type(b"%PDF-1.4 fake", "report.pdf")

    def test_mismatched_extension_raises(self, svc):
        mock_ft = MagicMock()
        kind = MagicMock()
        kind.mime = "application/pdf"
        mock_ft.guess.return_value = kind
        with patch.dict("sys.modules", {"filetype": mock_ft}):
            with pytest.raises(ValidationError, match="doesn't match"):
                svc._validate_mime_type(b"%PDF-1.4 fake", "report.docx")

    def test_unknown_mime_raises(self, svc):
        mock_ft = MagicMock()
        kind = MagicMock()
        kind.mime = "application/x-unknown-type"
        mock_ft.guess.return_value = kind
        with patch.dict("sys.modules", {"filetype": mock_ft}):
            with pytest.raises(ValidationError, match="not allowed"):
                svc._validate_mime_type(b"unknown", "file.xyz")

    def test_none_detection_allows_text_extensions(self, svc):
        mock_ft = MagicMock()
        mock_ft.guess.return_value = None
        with patch.dict("sys.modules", {"filetype": mock_ft}):
            svc._validate_mime_type(b"plain text", "notes.txt")

    def test_none_detection_rejects_non_text(self, svc):
        mock_ft = MagicMock()
        mock_ft.guess.return_value = None
        with patch.dict("sys.modules", {"filetype": mock_ft}):
            with pytest.raises(ValidationError, match="Unable to verify"):
                svc._validate_mime_type(b"data", "file.xyz")


# ── _ALLOWED_MIME_TYPES ─────────────────────────────────────────────

class TestAllowedMimeTypes:
    def test_pdf_mime(self):
        assert ".pdf" in _ALLOWED_MIME_TYPES["application/pdf"]

    def test_docx_mime(self):
        assert ".docx" in _ALLOWED_MIME_TYPES["application/vnd.openxmlformats-officedocument.wordprocessingml.document"]

    def test_text_plain_allows_multiple(self):
        exts = _ALLOWED_MIME_TYPES["text/plain"]
        assert ".txt" in exts
        assert ".md" in exts
        assert ".csv" in exts

    def test_unsupported_image_types_removed(self):
        # Keep upload acceptance aligned with the document parser's capability set.
        assert "image/jpeg" not in _ALLOWED_MIME_TYPES
        assert "image/png" not in _ALLOWED_MIME_TYPES
        assert "application/msword" not in _ALLOWED_MIME_TYPES


# ── upload identity sanitizer ────────────────────────────────────

class TestUploadIdentitySanitizer:
    def test_rejects_path_traversal_filename(self, svc):
        with pytest.raises(HTTPException) as exc_info:
            svc._sanitize_upload_identity("../evil.pdf", "a" * 64)
        assert exc_info.value.status_code == 400

    def test_rejects_windows_separator_filename(self, svc):
        with pytest.raises(HTTPException):
            svc._sanitize_upload_identity("..\\evil.pdf", "a" * 64)

    def test_rejects_invalid_hash(self, svc):
        with pytest.raises(HTTPException):
            svc._sanitize_upload_identity("report.pdf", "not-a-hash")

    def test_accepts_valid_identity(self, svc):
        name, digest = svc._sanitize_upload_identity("report.pdf", "A" * 64)
        assert name == "report.pdf"
        assert digest == "a" * 64


# ── delete_file ─────────────────────────────────────────────────────

class TestDeleteFile:
    @pytest.mark.asyncio
    @patch("app.services.file_service.minio_client")
    @patch("app.services.file_service.Path")
    async def test_deletes_from_minio_and_local(self, mock_path_cls, mock_minio, svc):
        mock_path_instance = MagicMock()
        mock_path_instance.is_absolute.return_value = False
        mock_path_instance.exists.return_value = True
        mock_path_instance.is_file.return_value = True
        mock_path_cls.return_value = mock_path_instance

        with patch("app.services.file_service.os"):
            result = await svc.delete_file("documents/test.pdf")
            assert result is True
            mock_minio.remove_object.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.file_service.minio_client")
    @patch("app.services.file_service.Path")
    async def test_handles_minio_failure_gracefully(self, mock_path_cls, mock_minio, svc):
        mock_minio.remove_object.side_effect = Exception("connection refused")
        mock_path_instance = MagicMock()
        mock_path_instance.is_absolute.return_value = True
        mock_path_instance.exists.return_value = False
        mock_path_cls.return_value = mock_path_instance

        result = await svc.delete_file("some/path.pdf")
        # Should still return True even if MinIO fails (file might be local only)
        assert result is True

    @pytest.mark.asyncio
    @patch("app.services.file_service.minio_client")
    @patch("app.services.file_service.Path")
    async def test_extracts_object_name_from_url(self, mock_path_cls, mock_minio, svc):
        mock_path_instance = MagicMock()
        mock_path_instance.is_absolute.return_value = True
        mock_path_instance.exists.return_value = False
        mock_path_cls.return_value = mock_path_instance

        await svc.delete_file("http://minio:9000/documents/org1/file.pdf")
        mock_minio.remove_object.assert_called_once_with("org1/file.pdf")


# ── chunk upload validation ─────────────────────────────────────────

class TestChunkValidation:
    @pytest.mark.asyncio
    async def test_empty_chunk_raises(self, svc):
        with pytest.raises(HTTPException) as exc_info:
            await svc.upload_chunk(b"", 0, 3, "test.pdf", "hash", "org1", "user1")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_chunk_index_raises(self, svc):
        with pytest.raises(HTTPException) as exc_info:
            await svc.upload_chunk(b"data", -1, 3, "test.pdf", "hash", "org1", "user1")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_chunk_index_out_of_range_raises(self, svc):
        with pytest.raises(HTTPException) as exc_info:
            await svc.upload_chunk(b"data", 5, 3, "test.pdf", "hash", "org1", "user1")
        assert exc_info.value.status_code == 400


# ── _check_all_chunks_uploaded ──────────────────────────────────────

class TestCheckAllChunks:
    @pytest.mark.asyncio
    async def test_all_present(self, svc):
        mock_dir = MagicMock()
        chunk_files = {i: MagicMock() for i in range(3)}
        mock_dir.__truediv__ = lambda self, name: chunk_files.get(int(name.split("_")[1]), MagicMock(exists=lambda: False))
        # Simplified: use real Path with tmp
        import os
        import tempfile
        tmpdir = tempfile.mkdtemp()
        for i in range(3):
            open(os.path.join(tmpdir, f"chunk_{i}"), "w").close()
        result = await svc._check_all_chunks_uploaded(Path(tmpdir), 3)
        assert result is True

    @pytest.mark.asyncio
    async def test_missing_chunk(self, svc):
        import os
        import tempfile
        tmpdir = tempfile.mkdtemp()
        # Only create 2 of 3 chunks
        for i in range(2):
            open(os.path.join(tmpdir, f"chunk_{i}"), "w").close()
        result = await svc._check_all_chunks_uploaded(Path(tmpdir), 3)
        assert result is False
