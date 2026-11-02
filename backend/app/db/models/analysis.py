"""Analysis report persistence model (Phase 4).

``AnalysisReport`` stores one immutable, explainable analysis run:

* The FULL report payload lives in ``report`` (JSONB, versioned by
  ``report_schema_version``) — the strategy rule-set snapshot, per-
  condition evaluation tree, regime evidence and data-quality warnings
  are all inside, so every historical report stays auditable and
  reproducible even if the strategy or its versions are later deleted.
* Candle data is NEVER duplicated: reports reference the candles used
  (counts, first/last times, provenance, staleness) inside the payload.
* ``strategy_id`` is ``ON DELETE SET NULL`` (the analysis history
  survives strategy deletion); ``strategy_name`` is denormalised so the
  history list stays meaningful.
* Ownership: ``owner_id`` scopes every query; foreign owners get 404s.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

REPORT_SCHEMA_VERSION = "1.0"
SETUP_STATUSES = (
    "conditions_met",
    "conditions_missing",
    "invalidated",
    "insufficient_data",
    "inconclusive",
    "regime_mismatch",
)


class AnalysisReport(Base, TimestampMixin):
    """One persisted analysis-engine run (immutable after creation)."""

    __tablename__ = "analysis_reports"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('forex', 'binary_options')", name="valid_mode"
        ),
        CheckConstraint(
            "setup_status IN ('conditions_met', 'conditions_missing', "
            "'invalidated', 'insufficient_data', 'inconclusive', "
            "'regime_mismatch')",
            name="valid_setup_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    strategy_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    strategy_version: Mapped[int] = mapped_column(Integer, nullable=False)

    setup_status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    regime: Mapped[str | None] = mapped_column(String(16), nullable=True)

    report_schema_version: Mapped[str] = mapped_column(String(8), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # The complete, versioned report payload (no candle rows inside —
    # references only). See docs/analysis-report-schema.md.
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
