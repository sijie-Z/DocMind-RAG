# -*- coding: utf-8 -*-
"""Middleware that wraps 200-series JSON responses into the standard API format.

Skips endpoints that already return the standard format (dict with "success" key),
streaming responses, and excluded paths like /health, /metrics, /docs.
"""

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.background import BackgroundTask
from typing import Set


class ResponseFormatMiddleware(BaseHTTPMiddleware):
    """Wrap 200-series JSON responses into the standard API envelope.

    The standard format is:
        {"success": True, "code": 200, "message": "success", "data": <body>, "detail": None}
    """

    def __init__(self, app, exclude_paths: Set[str] | None = None):
        super().__init__(app)
        self.exclude_paths = exclude_paths or {
            "/health", "/metrics", "/info", "/openapi.json",
            "/docs", "/redoc", "/favicon.ico",
        }

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip excluded paths
        for prefix in self.exclude_paths:
            if path.startswith(prefix):
                return await call_next(request)

        # Skip static files
        if path.startswith("/static/") or path.startswith("/files/"):
            return await call_next(request)

        response = await call_next(request)

        # Only wrap 2xx JSON responses
        if not (200 <= response.status_code < 300):
            return response

        # Don't wrap streaming or non-JSON responses
        if isinstance(response, StreamingResponse):
            return response

        content_type = response.headers.get("content-type", "")
        if "json" not in content_type and "application/json" not in content_type:
            return response

        # Try to read the body
        try:
            body = response.body
            if not body:
                return response

            import json as json_lib
            data = json_lib.loads(body)

            # If already in standard format (has "success" key), pass through
            if isinstance(data, dict) and "success" in data:
                return response

            # Wrap in standard success format
            wrapped = {
                "success": True,
                "code": response.status_code,
                "message": "success",
                "data": data,
                "detail": None,
            }

            new_body = json_lib.dumps(wrapped, ensure_ascii=False).encode("utf-8")

            return JSONResponse(
                status_code=response.status_code,
                content=wrapped,
                headers={k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "content-encoding")},
                background=response.background,
            )
        except (ValueError, TypeError, AttributeError):
            # Not valid JSON or can't read body — pass through
            return response
