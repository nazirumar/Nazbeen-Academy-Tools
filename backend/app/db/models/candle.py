"""Candle persistence model (Phase 2 market-data cache).

- Natural composite primary key ``(symbol, timeframe, open_time)`` makes
  duplicate candle inserts impossible at the database level; the store layer
  uses batched ``INSERT ... ON CONFLICT DO UPDATE`` so a forming candle can be
  refreshed as it evolves and eventually closes.
- ``open_time`` is stored as ``TIMESTAMPTZ`` and is ALWAYS the UTC candle-open
  instant (MT5 server time is converted on ingest — see
  ``app.domains.market_data.validation``).
- ``source`` documents provenance ("mt5" today); cached rows are never
  presented as live data — responses carry ``data_provenance`` explicitly.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    Index,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

CANDLE_SOURCES = ("mt5",)


class CandleRow(Base):
    """One OHLCV candle for one symbol/timeframe/open_time (UTC)."""

    __tablename__ = "candles"
    __table_args__ = (
        CheckConstraint("open > 0 AND high > 0 AND low > 0 AND close > 0", name="positive_prices"),
        CheckConstraint("high >= low", name="high_gte_low"),
        # DESC ordering matches the hot read path (latest N candles per series).
        Index(
            "ix_candles_series_open_time_desc",
            "symbol",
            "timeframe",
            text("open_time DESC"),
        ),
        Index("ix_candles_open_time", "open_time"),
    )

    symbol: Mapped[str] = mapped_column(String(40), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    open_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    open: Mapped[float] = mapped_column(Double, nullable=False)
    high: Mapped[float] = mapped_column(Double, nullable=False)
    low: Mapped[float] = mapped_column(Double, nullable=False)
    close: Mapped[float] = mapped_column(Double, nullable=False)
    volume: Mapped[float | None] = mapped_column(Double, nullable=True)
    # False while the candle is still forming (kept fresh by re-upserts).
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="mt5")
    # Wall-clock moment the row was last written by the ingest pipeline.
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    # Candle index within the day (MT5 semantics; informational).
    day_of_week: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "open_time": self.open_time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "is_closed": self.is_closed,
            "source": self.source,
            "fetched_at": self.fetched_at,
        }
