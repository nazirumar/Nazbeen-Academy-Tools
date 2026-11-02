"""Bearer-token authentication for the v1 API (Phase 9).

Threat model & design
---------------------
Phase 3 introduced *ownership scoping* (``X-User-Id``), which segregates data
but authenticates nothing — any caller could claim any owner id. Phase 9 adds
a real, deliberately small authentication layer **in front of** that scoping:

* Tokens live ONLY in server-side configuration (environment / ``backend/.env``).
  No endpoint accepts or returns them; they are never logged.
* ``API_AUTH_TOKEN``        — one deployment-wide access token (no owner binding:
  ``X-User-Id`` scoping keeps working for multi-user single-operator setups).
* ``API_AUTH_TOKENS``       — comma-separated list of ``token`` or ``token:owner``
  entries. A token WITH an owner binding locks the request to that owner: the
  ``X-User-Id`` header is then ignored, so a holder cannot impersonate anyone.
* When APP_ENV=production at least one token is REQUIRED — the app refuses to
  start otherwise (fail safe, see ``Settings._environ_consistency``).
* When no token is configured (local/test single-user mode) the dependency is a
  no-op and behaviour is byte-for-byte identical to previous phases.

Comparison is constant-time (``secrets.compare_digest`` per candidate token).
The authenticated owner (when bound) is published through a context var that
``app.core.ownership.current_owner`` consults first.

Documented limitation (stated honestly): this is bearer-token access control
for a self-hosted analysis tool — it is not a user-account system with
passwords, sessions or OAuth. Token rotation = change the env value and
restart; see docs/operations.md.
"""

from __future__ import annotations

import contextvars
import secrets
from collections.abc import Iterator

from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Owner id forced by a token binding (None when auth is disabled/unbound).
principal_owner_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "principal_owner", default=None
)


class AuthenticationAppError(AppError):
    """Missing/invalid bearer credentials (HTTP 401)."""

    status_code = 401
    code = "UNAUTHORIZED"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        # Standard challenge; tells clients *how* to authenticate, never *what*.
        self.headers = {"WWW-Authenticate": "Bearer"}


def _unauthorized() -> AuthenticationAppError:
    return AuthenticationAppError(
        "Missing or invalid API credentials. Send 'Authorization: Bearer <token>' "
        "with a configured API token."
    )


def _matches(credential: str, token: str) -> bool:
    """Constant-time comparison, safe for different lengths."""
    return secrets.compare_digest(credential.encode("utf-8"), token.encode("utf-8"))


async def require_api_auth(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Iterator[None]:
    """Router-level dependency guarding every ``/api/v1`` endpoint.

    * No tokens configured  → no-op (local single-user mode).
    * Tokens configured     → require a valid bearer token; on a token with an
      owner binding, pin the request to that owner for the whole request
      lifecycle (reset afterwards).
    """

    tokens = settings.resolved_api_tokens
    if not tokens:
        yield
        return

    header = request.headers.get("authorization", "")
    scheme, _, credential = header.partition(" ")
    credential = credential.strip()
    if scheme.lower() != "bearer" or not credential:
        raise _unauthorized()

    matched_owner: str | None = None
    matched = False
    for token, bound_owner in tokens:
        if _matches(credential, token):
            matched = True
            matched_owner = bound_owner
            break
    if not matched:
        logger.warning(
            "api_auth_rejected",
            extra={"path": request.url.path, "method": request.method},
        )
        raise _unauthorized()

    token_handle = principal_owner_var.set(matched_owner)
    try:
        yield
    finally:
        principal_owner_var.reset(token_handle)


__all__ = [
    "AuthenticationAppError",
    "principal_owner_var",
    "require_api_auth",
]
