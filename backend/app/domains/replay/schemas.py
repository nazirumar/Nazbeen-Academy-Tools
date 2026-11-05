"""Validated request models for the market replay API (Phase 11).

Only request bodies live here. Responses are built by the service as plain
JSON payloads (the same convention as the backtest/research routers) so the
persisted JSONB content and API contract cannot drift.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SYMBOL_PATTERN = r"^[A-Za-z0-9._#&+-]{1,40}$"


class ReplayCreateRequest(BaseModel):
    """Create a saved replay session over a stored candle window."""

    model_config = {"extra": "forbid"}

    mode: Literal["forex", "binary_options"]
    symbol: str = Field(pattern=SYMBOL_PATTERN, min_length=1, max_length=40)
    timeframe: str = Field(min_length=2, max_length=8)
    start: datetime
    end: datetime
    playback_speed: float = Field(default=1.0, gt=0.0, le=60.0)

    @field_validator("start", "end")
    @classmethod
    def _timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("start/end must be timezone-aware ISO-8601 timestamps.")
        return value

    def validate_window(self) -> None:
        if self.start >= self.end:
            raise ValueError("start must be earlier than end.")
        if (self.end - self.start).total_seconds() > 366 * 24 * 3600:
            raise ValueError("the replay window cannot exceed 366 days.")


class ReplayStepRequest(BaseModel):
    """Advance the deterministic cursor by one or more closed candles."""

    model_config = {"extra": "forbid"}

    count: int = Field(default=1, ge=1, le=100)


class ReplaySeekRequest(BaseModel):
    """Jump to a permitted historical timestamp (never beyond the window)."""

    model_config = {"extra": "forbid"}

    target_time: datetime

    @field_validator("target_time")
    @classmethod
    def _timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("target_time must be timezone-aware ISO-8601.")
        return value


class ReplayUpdateRequest(BaseModel):
    """Presentation-only replay settings."""

    model_config = {"extra": "forbid"}

    playback_speed: float = Field(gt=0.0, le=60.0)


__all__ = [
    "ReplayCreateRequest",
    "ReplaySeekRequest",
    "ReplayStepRequest",
    "ReplayUpdateRequest",
]
