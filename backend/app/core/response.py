"""Consistent API response format helpers.

All API responses (success and error) follow the same schema:
    {
        "success": bool,
        "code": int,
        "message": str,
        "data": Any | None,
        "detail": Any | None,
        "request_id": str | None,     # only on errors
    }
"""

from typing import Any


def success_response(data: Any = None, message: str = "success", code: int = 200) -> dict:
    """Standard success response body."""
    return {
        "success": True,
        "code": code,
        "message": message,
        "data": data,
        "detail": None,
    }


def error_response(
    code: int = 400,
    message: str = "error",
    detail: Any = None,
    request_id: str | None = None,
    data: Any = None,
) -> dict:
    """Standard error response body."""
    return {
        "success": False,
        "code": code,
        "message": message,
        "detail": detail,
        "request_id": request_id,
        "data": data,
    }
