"""Application error hierarchy and global exception handlers.

Every error returned by the API uses the same envelope convention:

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "NOT_FOUND",
    "message": "Strategy '…' was not found.",
    "details": []
  },
  "meta": {"request_id": "…", "timestamp": "…", "api_version": "v1"}
}
```

Internal errors never leak stack traces to clients; the ``request_id`` in the
envelope matches the server log entry containing the full traceback.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, request_id_var

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for all expected application errors."""

    status_code: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details: list[dict[str, Any]] = details or []


class ValidationAppError(AppError):
    """Client input failed semantic validation (HTTP 422)."""

    status_code = 422
    code = "VALIDATION_ERROR"


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class RuleValidationFailedError(AppError):
    """A strategy rule set failed whitelist validation (HTTP 422)."""

    status_code = 422
    code = "RULE_VALIDATION_FAILED"


class MarketDataUnavailableError(AppError):
    """The configured market-data provider is not connected (HTTP 503)."""

    status_code = 503
    code = "MARKET_DATA_UNAVAILABLE"


class MarketDataError(AppError):
    """The upstream market-data source returned an unusable response (HTTP 502)."""

    status_code = 502
    code = "MARKET_DATA_ERROR"


class SymbolNotFoundError(AppError):
    """The requested instrument is unknown to the terminal (HTTP 404)."""

    status_code = 404
    code = "SYMBOL_NOT_FOUND"


class DependencyUnavailableError(AppError):
    """A backing dependency (e.g. PostgreSQL) is unavailable (HTTP 503)."""

    status_code = 503
    code = "DEPENDENCY_UNAVAILABLE"


def _meta() -> dict[str, Any]:
    from app.core.responses import build_meta

    return build_meta()


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the consistent error envelope to every failure path."""

    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        body: dict[str, Any] = {
            "success": False,
            "data": None,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
            "meta": _meta(),
        }
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(RequestValidationError)
    async def _request_validation_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details: list[dict[str, Any]] = []
        for err in exc.errors():
            ctx = err.get("ctx")
            details.append(
                {
                    "path": ".".join(str(p) for p in err.get("loc", [])),
                    "code": err.get("type", "validation_error"),
                    "message": err.get("msg", "Invalid input."),
                    "input": _safe(err.get("input")),
                    "constraint": _safe(ctx) if ctx is not None else None,
                }
            )
        body = {
            "success": False,
            "data": None,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request payload failed validation.",
                "details": details,
            },
            "meta": _meta(),
        }
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code_map = {
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            413: "PAYLOAD_TOO_LARGE",
        }
        body = {
            "success": False,
            "data": None,
            "error": {
                "code": code_map.get(exc.status_code, f"HTTP_{exc.status_code}"),
                "message": str(exc.detail),
                "details": [],
            },
            "meta": _meta(),
        }
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = request_id_var.get()
        logger.exception(
            "Unhandled exception on %s %s",
            request.method,
            request.url.path,
            extra={"path": request.url.path, "http_method": request.method},
        )
        body = {
            "success": False,
            "data": None,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected server error occurred. "
                "Quote the request_id when reporting this issue.",
                "details": [],
            },
            "meta": _meta(),
        }
        return JSONResponse(status_code=500, content=body)


def _safe(value: Any) -> Any:
    """Make arbitrary exception payload data JSON-safe."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in list(value.items())[:10]}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in list(value)[:10]]
    return str(value)
