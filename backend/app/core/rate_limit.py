"""Per-owner sliding-window rate limiter (shared core, Phase 8).

A deliberately small in-process limiter, mirroring the Phase 7 AI
layer's approach: the platform's documented minimal architecture (no
external limiter infrastructure). Used by the research/optimization
creation endpoint so one client cannot flood the bounded in-process
runner with new runs.

Documented limitation: this is per-process state. Multi-process
deployments would need a shared store — intentionally out of scope and
stated honestly rather than pretended otherwise.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from app.core.errors import AppError

__all__ = ["RateLimitAppError", "check_rate_limit", "reset_rate_limits"]


class RateLimitAppError(AppError):
    """The per-owner request rate limit was exceeded (HTTP 429)."""

    status_code = 429
    code = "RATE_LIMITED"


_WINDOWS: dict[str, deque[float]] = defaultdict(deque)


def check_rate_limit(
    key: str, limit_per_minute: int, *, now: float | None = None
) -> int:
    """Raise :class:`RateLimitAppError` when ``key`` is over the limit.

    Returns the number of requests still allowed in the current window
    (for honest response metadata). ``now`` is injectable for
    deterministic tests.
    """

    moment = time.monotonic() if now is None else now
    window = _WINDOWS[key]
    horizon = moment - 60.0
    while window and window[0] <= horizon:
        window.popleft()
    if len(window) >= max(1, limit_per_minute):
        raise RateLimitAppError(
            f"Request rate limit exceeded ({limit_per_minute}/minute). "
            "Wait a moment and try again."
        )
    window.append(moment)
    return max(1, limit_per_minute) - len(window)


def reset_rate_limits() -> None:
    """Test helper: clear all windows."""

    _WINDOWS.clear()
