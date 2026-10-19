"""Request ID middleware — binds a per-request id for logs, errors and handlers.

Implemented as pure ASGI middleware to avoid BaseHTTPMiddleware's
incompatibility with SQLAlchemy async sessions (greenlet_spawn error).

W0-9 contract:

- A well-formed inbound ``X-Request-ID`` (1–64 chars from ``[A-Za-z0-9._-]``)
  is propagated verbatim, so clients/proxies can correlate their logs with ours.
- Anything else (missing / too long / illegal characters) is replaced by a
  fresh uuid4 — the inbound value is client-controlled and must not be echoed
  or logged unchecked.
- The id is exposed three ways: ``scope["state"]["request_id"]`` (hence
  ``request.state.request_id`` for exception handlers), the module-level
  :data:`request_id_var` contextvar read by the structured logger, and
  :func:`get_request_id` for downstream code (error envelopes, tracing).
"""

import contextvars
import re
import uuid
from typing import Final

from starlette.types import ASGIApp, Receive, Scope, Send

# Client-controlled header value: bound length + charset before echoing it back
# into a response header / structured log (header/log injection hardening).
_VALID_REQUEST_ID: Final = re.compile(r"[A-Za-z0-9._-]{1,64}")

# Single source of truth: ``app.core.logging`` imports this same ContextVar so
# the JSON log formatter and handlers observe one value per request.
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


def get_request_id() -> str | None:
    """Return the request id bound to the current request context.

    Returns ``None`` when there is no HTTP request context to read from:

    - background jobs / startup code;
    - WebSocket scopes (this middleware only handles HTTP);
    - the catch-all ``Exception`` (500) handler — FastAPI/Starlette runs it in
      ``ServerErrorMiddleware``, which sits outside this middleware, so the
      ContextVar has already been reset. Use ``request.state.request_id`` there.

    Inside normal HTTP handlers and dependencies (sync or async) it returns the
    validated inbound id or the generated one for the current request.
    """
    return request_id_var.get()


def _inbound_request_id(scope: Scope, header_name: bytes) -> str | None:
    """Return a valid inbound request id, or ``None`` when absent/malformed.

    Duplicate occurrences: the first one decides; later duplicates are ignored
    (an invalid first value is not rescued by a later valid one).
    """
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name.lower() != header_name:
            continue
        try:
            candidate = raw_value.decode("ascii")
        except UnicodeDecodeError:
            return None
        return candidate if _VALID_REQUEST_ID.fullmatch(candidate) else None
    return None


class RequestIDMiddleware:
    """Assigns a request id to every HTTP request that reaches this middleware.

    - Valid inbound ``X-Request-ID`` is reused (distributed tracing).
    - Otherwise a uuid4 is generated.
    - Stores the id in scope state (``request.state.request_id``) and in
      :data:`request_id_var` for the current request context.
    - Sets the ``X-Request-ID`` response header (unless downstream already did).

    Requests short-circuited before this middleware (CORS preflight,
    rate-limit rejections) never get an id, and responses produced by the outer
    ``ServerErrorMiddleware`` (unhandled exception / 500) bypass ``send_wrapper``
    — its handler must use ``request.state.request_id`` instead.
    """

    def __init__(self, app: ASGIApp, header_name: str = "X-Request-ID"):
        self.app = app
        self.header_name = header_name
        self._header_name_bytes = header_name.lower().encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _inbound_request_id(scope, self._header_name_bytes) or str(uuid.uuid4())

        # Downstream handlers read request.state.request_id (exception handlers too).
        state = scope.get("state") or {}
        state["request_id"] = request_id
        scope["state"] = state

        token = request_id_var.set(request_id)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Only add if not already present
                has_header = any(
                    h[0].lower() == self._header_name_bytes for h in headers
                )
                if not has_header:
                    headers.append((self._header_name_bytes, request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # Never leak this request's id into whatever runs next on this context.
            request_id_var.reset(token)
