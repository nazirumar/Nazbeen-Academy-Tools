"""Backtest persistence models (Phase 6).

* ``BacktestRun`` — one validated backtest configuration + its lifecycle
  (queued -> running -> completed | failed | cancelled), the resolved
  immutable strategy-version snapshot, data-quality summary and aggregate
  metrics. The configuration payload is stored as validated at creation
  time, so a later strategy edit can never silently alter old results.
* ``BacktestResult`` — one chronological evaluation record (a setup
  evaluation and/or a simulated trade). ``seq`` is the strict
  chronological order within the run; ``result_time`` is the simulated
  evaluation instant (the close time of the evaluation candle). The full
  explainable evaluation tree and any execution details live in JSONB —
  candle data is referenced (times/prices), never duplicated as rows.

Ownership: ``owner_id`` scopes every query; foreign owners get 404s (no
existence leak). ``strategy_id`` / ``strategy_version_id`` are
``ON DELETE SET NULL`` so run history survives strategy deletion; the
denormalised name + version number keep listings meaningful.

Consistency: every completed result is associated with the exact
immutable strategy version selected for the run — the rule-set snapshot
inside ``BacktestRun.config`` proves what was evaluated even if the
strategy or its versions are later deleted.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UUID,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

BACKTEST_SCHEMA_VERSION = "1.0"
RUN_STATUSES = ("queued", "running", "completed", "failed", "cancelled")
RESULT_KINDS = ("setup", "trade")
TRADE_OUTCOMES = (
    "win",
    "loss",
    "tie",
    "open",          # still open when the data ended (forex)
    "unresolved",    # expiry beyond the available data (binary)
    "ambiguous",     # OHLC cannot resolve SL/TP ordering (forex, per policy)
    "skipped",       # direction 'both' or no executable side defined
)


class BacktestRun(Base, TimestampMixin):
    """One backtest run: configuration, lifecycle, quality, metrics."""

    __tablename__ = "backtest_runs"
    __table_args__ = (
        CheckConstraint("mode IN ('forex', 'binary_options')", name="valid_mode"),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="valid_status",
        ),
        Index("ix_backtest_runs_owner_created_at", "owner_id", "created_at"),
        Index("ix_backtest_runs_owner_status", "owner_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    mode: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)

    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    strategy_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    strategy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategy_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Full validated configuration incl. the immutable rule-set snapshot and
    # the mode-specific execution assumptions (see docs/backtest-schema.md).
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Data source + quality summary (coverage, gaps, duplicates, staleness).
    data_quality: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Aggregate performance metrics (None until completed; None values inside
    # mean "not available", never fabricated zeros).
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Failure details {code, message, details} for failed runs.
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    backtest_schema_version: Mapped[str] = mapped_column(String(8), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)

    candles_evaluated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BacktestResult(Base, TimestampMixin):
    """One chronological evaluation/setup/trade record of a run."""

    __tablename__ = "backtest_results"
    __table_args__ = (
        CheckConstraint("kind IN ('setup', 'trade')", name="valid_kind"),
        CheckConstraint(
            "outcome IN ('win', 'loss', 'tie', 'open', 'unresolved', "
            "'ambiguous', 'skipped') OR outcome IS NULL",
            name="valid_outcome",
        ),
        Index("ix_backtest_results_run_seq", "run_id", "seq", unique=True),
        Index("ix_backtest_results_run_time", "run_id", "result_time"),
        Index("ix_backtest_results_owner_id", "owner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("backtest_runs.id", ondelete="CASCADE"),
        nullable=False,
        # ``ix_backtest_results_run_seq`` already leads with run_id; a separate
        # single-column index would be redundant.
    )
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)

    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    result_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    setup_status: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    regime: Mapped[str | None] = mapped_column(String(16), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)

    # Realized P&L in account currency (None when not applicable).
    pnl: Mapped[float | None] = mapped_column(Double, nullable=True)
    return_pct: Mapped[float | None] = mapped_column(Double, nullable=True)
    equity_after: Mapped[float | None] = mapped_column(Double, nullable=True)

    # Full explainable evaluation (four-state tree + evidence + warnings) or
    # execution details, depending on ``kind``. Setup rows may also carry a
    # ``trade`` sub-payload when a trade was taken from that setup.
    evaluation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    execution: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    warnings: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = [
    "BACKTEST_SCHEMA_VERSION",
    "BacktestResult",
    "BacktestRun",
    "RESULT_KINDS",
    "RUN_STATUSES",
    "TRADE_OUTCOMES",
]
