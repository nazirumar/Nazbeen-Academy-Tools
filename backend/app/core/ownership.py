"""Request ownership resolution (Phase 3 lightweight ownership scoping).

The platform is a local, user-defined analysis tool; full account machinery
is out of scope for this phase. Ownership is resolved from the optional
``X-User-Id`` header:

* Absent/empty header → the default ``local`` owner (single-user desktop
  deployments behave exactly like Phase 1/2).
* A header value identifies the owner; every category/strategy operation is
  scoped to it, and foreign-owned records are indistinguishable from
  non-existent ones (404 — no existence leak).

The header value is validated (length + charset) and NEVER interpreted as a
credential: it scopes data, it does not authenticate. Real authentication is
documented as future work in the README.
"""

from __future__ import annotations

import re

from fastapi import Header

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
