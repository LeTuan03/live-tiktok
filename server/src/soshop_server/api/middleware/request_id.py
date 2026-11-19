"""Request-ID middleware + log filter.

Each inbound HTTP request is tagged with an ``X-Request-ID`` header. If the
caller supplies one we echo it back unchanged; otherwise a fresh
``uuid.uuid4().hex`` is generated. The value is stashed in a
:class:`contextvars.ContextVar` so background tasks (e.g. log records emitted
from deep inside a handler) see the same ID, and on ``request.state`` so
explicit consumers can pick it up without importing the context variable.

The companion :class:`RequestIDLogFilter` reads from the same context
variable and attaches ``record.request_id`` to every log record. Combined
with a log format that includes ``%(request_id)s``, every line for a given
request carries the ID — which is the whole point.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

# Default of empty string keeps log records readable when no request is in
# flight (the filter substitutes "-" so the format placeholder always has
# something to render).
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Bind ``X-Request-ID`` to the contextvar + response headers."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming if incoming else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class RequestIDLogFilter(logging.Filter):
    """Inject ``record.request_id`` from the contextvar.

    Falls back to ``"-"`` when no request is in flight so log records emitted
    from worker threads / lifespan hooks still format cleanly.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True
