"""Consistent API response envelope.

Success responses:
```json
{"success": true, "data": <payload>, "error": null, "meta": {...}}
```
Error responses are produced by ``app.core.errors`` with the same shape.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi.responses import JSONResponse

API_VERSION = "v1"


def build_meta(request_id: str | None = None) -> dict[str, Any]:
    from app.core.logging import request_id_var

    return {
        "request_id": request_id or request_id_var.get(),
        "timestamp": datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds"),
        "api_version": API_VERSION,
    }


def envelope(data: Any, request_id: str | None = None) -> dict[str, Any]:
    return {
        "success": True,
        "data": data,
        "error": None,
        "meta": build_meta(request_id),
    }


def ok(data: Any, status_code: int = 200, request_id: str | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=envelope(data, request_id))


def created(data: Any, request_id: str | None = None) -> JSONResponse:
    return ok(data, status_code=201, request_id=request_id)
