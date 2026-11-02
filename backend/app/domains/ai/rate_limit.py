"""Per-owner sliding-window rate limiter (Phase 7).

A deliberately small in-process limiter: the platform's documented,
minimal architecture (no external task/limiter infrastructure). Enough
to stop runaway chat loops or accidental floods on a single deployment.

Documented limitation: this is per-process state. Multi-process
deployments would need a shared store — intentionally out of scope, and
stated honestly rather than pretended otherwise.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from app.domains.ai.errors import RateLimitExceededError

_WINDOWS: dict[str, deque[float]] = defaultdict(deque)


def check_rate_limit(key: str, limit_per_minute: int, *, now: float | None = None) -> int:
    """Raise :class:`RateLimitExceededError` when over the limit.

    Returns the number of requests still allowed in the current window
    (useful for honest response metadata).
    """

    moment = time.monotonic() if now is None else now
    window = _WINDOWS[key]
    horizon = moment - 60.0
    while window and window[0] <= horizon:
        window.popleft()
    if len(window) >= limit_per_minute:
        raise RateLimitExceededError(
            f"AI request rate limit exceeded ({limit_per_minute}/minute). "
            "Wait a moment and try again."
        )
    window.append(moment)
    return limit_per_minute - len(window)


def reset_rate_limits() -> None:
    """Test helper: clear all windows."""
    _WINDOWS.clear()


__all__ = ["check_rate_limit", "reset_rate_limits"]
