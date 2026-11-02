"""Unit tests: shared per-owner sliding-window rate limiter (core)."""

from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.core.rate_limit import (
    RateLimitAppError,
    check_rate_limit,
    reset_rate_limits,
)


@pytest.fixture(autouse=True)
def _clean_windows():
    reset_rate_limits()
    yield
    reset_rate_limits()


class TestSlidingWindow:
    def test_allows_up_to_limit_then_raises(self) -> None:
        now = 1_000.0
        # The return value is the REMAINING budget after the request.
        assert check_rate_limit("k", 3, now=now) == 2
        assert check_rate_limit("k", 3, now=now + 0.1) == 1
        assert check_rate_limit("k", 3, now=now + 0.2) == 0
        with pytest.raises(RateLimitAppError) as exc:
            check_rate_limit("k", 3, now=now + 0.3)
        assert exc.value.status_code == 429
        assert exc.value.code == "RATE_LIMITED"
        assert isinstance(exc.value, AppError)

    def test_keys_are_independent(self) -> None:
        now = 1_000.0
        assert check_rate_limit("a", 1, now=now) == 0  # a's budget now spent
        with pytest.raises(RateLimitAppError):
            check_rate_limit("a", 1, now=now + 0.1)
        # A different key (another owner) still has its own budget.
        assert check_rate_limit("b", 1, now=now) == 0

    def test_old_requests_age_out(self) -> None:
        now = 2_000.0
        check_rate_limit("k", 2, now=now)
        check_rate_limit("k", 2, now=now + 1)
        with pytest.raises(RateLimitAppError):
            check_rate_limit("k", 2, now=now + 2)
        # 61s later the first two hits left the 60s window.
        assert check_rate_limit("k", 2, now=now + 61.5) == 1

    def test_limit_is_floored_at_one(self) -> None:
        now = 3_000.0
        check_rate_limit("k", 0, now=now)  # limit 0 behaves like 1
        with pytest.raises(RateLimitAppError):
            check_rate_limit("k", 0, now=now + 0.1)
