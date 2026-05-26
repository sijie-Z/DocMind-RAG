"""Tests for the unified exception hierarchy."""
from app.exceptions import (
    AccountLockedError,
    AppError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    PipelineError,
    RateLimitError,
    StorageError,
    TokenBlacklistedError,
    TokenExpiredError,
    ValidationError,
)


class TestExceptionHierarchy:
    def test_app_error_is_base(self):
        assert issubclass(ValidationError, AppError)
        assert issubclass(AuthenticationError, AppError)
        assert issubclass(NotFoundError, AppError)

    def test_token_errors_inherit_authentication(self):
        assert issubclass(TokenExpiredError, AuthenticationError)
        assert issubclass(TokenBlacklistedError, AuthenticationError)
        assert issubclass(AccountLockedError, AuthenticationError)

    def test_status_codes(self):
        assert ValidationError.status_code == 400
        assert AuthenticationError.status_code == 401
        assert TokenExpiredError.status_code == 401
        assert AuthorizationError.status_code == 403
        assert NotFoundError.status_code == 404
        assert ConflictError.status_code == 409
        assert RateLimitError.status_code == 429
        assert ExternalServiceError.status_code == 502
        assert PipelineError.status_code == 500
        assert StorageError.status_code == 500

    def test_error_codes(self):
        assert NotFoundError.error_code == "NOT_FOUND"
        assert AuthenticationError.error_code == "AUTHENTICATION_ERROR"
        assert TokenExpiredError.error_code == "TOKEN_EXPIRED"
        assert AccountLockedError.error_code == "ACCOUNT_LOCKED"

    def test_default_messages(self):
        e = NotFoundError()
        assert e.message == "资源不存在"
        assert e.status_code == 404

    def test_custom_message(self):
        e = NotFoundError("文档不存在")
        assert e.message == "文档不存在"
        assert e.status_code == 404

    def test_detail_and_extra(self):
        e = ValidationError("字段错误", field="email", max_length=255)
        assert e.message == "字段错误"
        assert e.detail is None  # detail not set via positional
        assert e.extra == {"field": "email", "max_length": 255}

    def test_detail_kwarg(self):
        e = AppError("失败", detail="stack trace here")
        assert e.detail == "stack trace here"

    def test_is_exception(self):
        e = NotFoundError()
        assert isinstance(e, Exception)
        assert isinstance(e, AppError)
