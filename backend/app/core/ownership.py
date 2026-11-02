"""Request ownership resolution (Phase 3 scoping + Phase 9 authentication).

Ownership is resolved in two layers:

1. **Phase 9 authentication (when configured):** ``app.core.auth`` validates
   the bearer token. A token WITH an owner binding pins the request to that
   owner — the ``X-User-Id`` header is then IGNORED, so a bound token holder
   cannot impersonate another owner. Unbound tokens leave scoping to (2).
2. **Phase 3 scoping (fallback / unbound tokens):** the optional
   ``X-User-Id`` header:

   * Absent/empty header → the default ``local`` owner (single-user desktop
     deployments behave exactly like Phase 1/2).
   * A header value identifies the owner; every category/strategy operation
     is scoped to it, and foreign-owned records are indistinguishable from
     non-existent ones (404 — no existence leak).

The header value is validated (length + charset) and NEVER interpreted as a
credential. When APP_ENV=production no tokens are configured the app refuses
to start (see ``app.core.config``), so production requests are always
authenticated before ownership is resolved.
"""

from __future__ import annotations

import re

from fastapi import Header

from app.core.auth import principal_owner_var
from app.core.errors import ValidationAppError
from app.db.models.strategy import DEFAULT_OWNER_ID, OWNER_ID_MAX_LEN

_OWNER_PATTERN = re.compile(r"^[A-Za-z0-9._@-]+$")


def current_owner(
    x_user_id: str | None = Header(
        default=None,
        description="Owner scope for strategies/categories (optional).",
    ),
) -> str:
    """FastAPI dependency resolving the owner scope of the request."""

    # Phase 9: an authenticated token with an owner binding wins outright.
    principal = principal_owner_var.get()
    if principal is not None:
        return principal

    if x_user_id is None or not x_user_id.strip():
        return DEFAULT_OWNER_ID
    owner = x_user_id.strip()
    if len(owner) > OWNER_ID_MAX_LEN or not _OWNER_PATTERN.match(owner):
        raise ValidationAppError(
            "X-User-Id must be 1-64 characters from [A-Za-z0-9._@-].",
            code="INVALID_OWNER_ID",
        )
    return owner


__all__ = ["current_owner", "DEFAULT_OWNER_ID"]
