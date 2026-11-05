"""Market replay persistence model (Phase 11).

A replay session is a *saved, deterministic cursor* over the platform's
stored closed-candle cache. It is deliberately small:

* the immutable window (symbol/timeframe/start/end) and the resolved data
  quality are stored at creation time;
* ``cursor_time`` is the open time of the newest visible candle;
* ``visible_candles`` / ``total_candles`` drive progress without loading the
  full series on every request.

No future candle is ever returned by the API: chart reads are bounded by
``cursor_time``. Playback speed is presentation-only and never changes the
market chronology or (in a later slice) trade outcomes.

Ownership is ``owner_id`` scoping, exactly like every other domain. Foreign
owners receive 404s and never learn whether a session exists.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Double,
    Index,
    Integer,
    String,
    UUID,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

REPLAY_SCHEMA_VERSION = "1.0"
REPLAY_STATUSES = ("active", "paused", "completed")


class ReplaySession(Base, TimestampMixin):
    """One owner-scoped historical replay session (saved cursor + quality)."""

    __tablename__ = "replay_sessions"
    __table_args__ = (
        CheckConstraint("mode IN ('forex', 'binary_options')", name="valid_mode"),
        CheckConstraint(
            "status IN ('active', 'paused', 'completed')", name="valid_status"
        ),
        CheckConstraint("visible_candles >= 1", name="valid_visible_candles"),
        CheckConstraint("total_candles >= 1", name="valid_total_candles"),
        CheckConstraint(
            "visible_candles <= total_candles", name="visible_lte_total"
        ),
        CheckConstraint(
            "playback_speed > 0", name="valid_playback_speed"
        ),
        Index(
            "ix_replay_sessions_owner_created_at", "owner_id", "created_at"
        ),
        Index("ix_replay_sessions_owner_status", "owner_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    mode: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)

    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="paused", index=True
    )

    # Requested window (UTC). ``end_time`` is inclusive at creation; all
    # subsequent chart reads are additionally bounded by ``cursor_time``.
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Resolved candle window (closed, validated, deduplicated).
    first_candle_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_candle_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    cursor_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    total_candles: Mapped[int] = mapped_column(Integer, nullable=False)
    visible_candles: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Presentation-only; never used to alter chronology or fills.
    playback_speed: Mapped[float] = mapped_column(
        Double, nullable=False, default=1.0
    )

    # Audit-complete session configuration + honest data-quality summary.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    data_quality: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    session_schema_version: Mapped[str] = mapped_column(
        String(8), nullable=False, default=REPLAY_SCHEMA_VERSION
    )
    last_advanced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["REPLAY_SCHEMA_VERSION", "REPLAY_STATUSES", "ReplaySession"]
