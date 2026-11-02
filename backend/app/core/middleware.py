"""HTTP middleware: request correlation id + structured access logging."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger, request_id_var

logger = get_logger("app.http")

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign a correlation id to every request (inbound header wins)."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            request_id_var.reset(token)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log method, path, status and duration for every request."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "request_failed",
                extra={
                    "http_method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            raise
        duration_ms = (time.perf_counter() - started) * 1000
        level = logging.DEBUG if request.url.path.startswith("/health") else logging.INFO
        logger.log(
            level,
            "request_completed",
            extra={
                "http_method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )
        return response


# --------------------------------------------------------------------------- #
# Phase 9: security headers on every API response
# --------------------------------------------------------------------------- #

#: Applied to API + health responses. The API serves JSON only, so a
#: restrictive CSP is safe here; the (local-only) /docs pages are excluded.
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defence-in-depth headers on API and health responses.

    ``X-Content-Type-Options`` stops MIME sniffing, ``X-Frame-Options`` /
    ``frame-ancestors`` mitigate clickjacking, ``Referrer-Policy`` avoids
    leaking URLs (which can contain query parameters) to other origins, and
    ``Cache-Control: no-store`` keeps API payloads (which include user data)
    out of shared caches. Swagger/OpenAPI routes are excluded: their UI loads
    remote assets and would break under the strict CSP (and is disabled in
    production anyway).
    """

    EXEMPT_PREFIXES = ("/docs", "/redoc", "/openapi.json")

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        path = request.url.path
        if any(path.startswith(p) for p in self.EXEMPT_PREFIXES):
            return response
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Content-Security-Policy", API_CSP)
        if path.startswith(("/api/", "/health")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response
