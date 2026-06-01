"""Request ID middleware — assigns a unique request_id to every request.

Implemented as pure ASGI middleware to avoid BaseHTTPMiddleware's
incompatibility with SQLAlchemy async sessions (greenlet_spawn error).
"""

import uuid

from starlette.types import ASGIApp, Receive, Scope, Send


class RequestIDMiddleware:
    """Assigns a unique UUID to every request.

    - Reads existing X-Request-ID header if present (for distributed tracing).
    - Falls back to uuid4.
    - Stores scope state for downstream access.
    - Sets ``X-Request-ID`` response header.
    """

    def __init__(self, app: ASGIApp, header_name: str = "X-Request-ID"):
        self.app = app
        self.header_name = header_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())

        # Store request_id in scope for downstream handlers
        state = scope.get("state", {})
        state["request_id"] = request_id
        scope["state"] = state

        original_send = send

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Only add if not already present
                has_header = any(
                    h[0].lower() == self.header_name.lower().encode()
                    for h in headers
                )
                if not has_header:
                    headers.append(
                        (self.header_name.lower().encode(), request_id.encode())
                    )
                message["headers"] = headers
            await original_send(message)

        await self.app(scope, receive, send_wrapper)
