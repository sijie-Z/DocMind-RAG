# -*- coding: utf-8 -*-
"""Request ID middleware — assigns a unique request_id to every request.

This middleware MUST be registered first (outermost) so that all downstream
middleware and handlers can access ``request.state.request_id``.
"""

import uuid
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assigns a unique UUID to every request.

    - Reads existing X-Request-ID header if present (for distributed tracing).
    - Falls back to uuid4.
    - Stores on ``request.state.request_id``.
    - Sets ``X-Request-ID`` response header.
    """

    def __init__(self, app, header_name: str = "X-Request-ID"):
        super().__init__(app)
        self.header_name = header_name

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(self.header_name) or str(uuid.uuid4())
        request.state.request_id = request_id

        response = await call_next(request)

        response.headers[self.header_name] = request_id
        return response
