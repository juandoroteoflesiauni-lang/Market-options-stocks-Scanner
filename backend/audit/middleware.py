from __future__ import annotations
"""Correlation ID Middleware — propagates a unique request ID through all logs.

Every incoming HTTP request receives a UUID correlation ID that is:
* Stored in ``contextvars`` so all loggers can read it.
* Attached to the response header ``X-Correlation-ID``.
* Available to all downstream services via ``get_correlation_id()``.
"""


import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from backend.audit.structured_logger import set_correlation_id


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Injects a correlation ID into every request/response cycle."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Use client-provided header or generate a new one
        corr_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        set_correlation_id(corr_id)

        response = await call_next(request)
        response.headers["X-Correlation-ID"] = corr_id
        return response
