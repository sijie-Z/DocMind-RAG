"""Unified exception hierarchy — maps domain errors to HTTP status codes.

Usage:
    raise NotFoundError("文档不存在", resource="document", resource_id="abc123")
    raise AuthenticationError("令牌已过期")
    raise ValidationError("文件大小超出限制", field="file", max_mb=50)
"""
from typing import Any


class AppError(Exception):
    """Base application error. All domain exceptions inherit from this."""

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"
    message: str = "服务器内部错误"

    def __init__(
        self,
        message: str | None = None,
        *,
        error_code: str | None = None,
        detail: Any = None,
        **kwargs: Any,
    ):
        self.message = message or self.__class__.message
        self.error_code = error_code or self.__class__.error_code
        self.detail = detail
        self.extra = kwargs
        super().__init__(self.message)


# ---- 4xx Client Errors ----


class ValidationError(AppError):
    """400 — request payload or parameter validation failed."""
    status_code = 400
    error_code = "VALIDATION_ERROR"
    message = "请求参数校验失败"


class AuthenticationError(AppError):
    """401 — missing or invalid credentials."""
    status_code = 401
    error_code = "AUTHENTICATION_ERROR"
    message = "认证失败"


class TokenExpiredError(AuthenticationError):
    """401 — token has expired."""
    error_code = "TOKEN_EXPIRED"
    message = "令牌已过期，请重新登录"


class TokenBlacklistedError(AuthenticationError):
    """401 — token has been revoked."""
    error_code = "TOKEN_REVOKED"
    message = "令牌已失效，请重新登录"


class AccountLockedError(AuthenticationError):
    """401 — too many failed login attempts."""
    error_code = "ACCOUNT_LOCKED"
    message = "登录尝试次数过多，请稍后再试"


class AuthorizationError(AppError):
    """403 — authenticated but insufficient permissions."""
    status_code = 403
    error_code = "AUTHORIZATION_ERROR"
    message = "权限不足"


class NotFoundError(AppError):
    """404 — requested resource does not exist."""
    status_code = 404
    error_code = "NOT_FOUND"
    message = "资源不存在"


class ConflictError(AppError):
    """409 — resource state conflict (duplicate, concurrent edit, etc.)."""
    status_code = 409
    error_code = "CONFLICT"
    message = "资源冲突"


class RateLimitError(AppError):
    """429 — too many requests."""
    status_code = 429
    error_code = "RATE_LIMIT_EXCEEDED"
    message = "请求过于频繁，请稍后再试"


# ---- 5xx Server Errors ----


class ExternalServiceError(AppError):
    """502 — upstream service (LLM, ES, Redis) returned an error."""
    status_code = 502
    error_code = "EXTERNAL_SERVICE_ERROR"
    message = "外部服务调用失败"


class PipelineError(AppError):
    """500 — RAG pipeline execution failed."""
    status_code = 500
    error_code = "PIPELINE_ERROR"
    message = "检索管道执行失败"


class StorageError(AppError):
    """500 — file storage (MinIO) operation failed."""
    status_code = 500
    error_code = "STORAGE_ERROR"
    message = "文件存储操作失败"
