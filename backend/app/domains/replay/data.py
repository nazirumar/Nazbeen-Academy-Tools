"""Read-only historical data access for market replay.

The replay session never fetches from MetaTrader during playback. It walks
the PostgreSQL candle cache created by the Phase 2 MT5 integration. Every
read is explicit about the direction of time:

* creation loads the requested window and assesses its quality;
* chart reads return only candles whose OPEN time is <= the session cursor;
* stepping loads only candles AFTER the cursor.

No row is interpolated, filled, re-ordered into a different series or
synthesised. Missing/invalid rows are reported as warnings and excluded from
the replay series according to the same closed-candle rules as backtests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationAppError
from app.db.models.candle import CandleRow
from app.domains.backtest.data import (
    InsufficientHistoryError,
    SeriesQuality,
    assess_series_quality,
)
from app.domains.market_data.store import load_candles_range


def _row_to_candle(row: CandleRow) -> dict[str, Any]:
    """API-shaped candle from a stored row (timestamps unchanged)."""

    return {
        "time": int(row.open_time.timestamp()),
        "time_utc": row.open_time.isoformat(timespec="seconds"),
        "open": row.open,
        "high": row.high,
        "low": row.low,
        "close": row.close,
        "volume": row.volume,
        "forming": False,
    }


def _valid(row: dict[str, Any]) -> bool:
    try:
        o, h, low, c = (
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
        )
    except (KeyError, TypeError, ValueError):
        return False
    return h >= low and min(o, h, low, c) > 0


@dataclass
class ReplayWindow:
    """Validated, closed, deduplicated candles for a replay window."""

    candles: list[dict[str, Any]]
    quality: dict[str, Any]
    raw_row_count: int

    @property
    def first_time(self) -> datetime:
        return datetime.fromtimestamp(self.candles[0]["time"], tz=timezone.utc)

    @property
    def last_time(self) -> datetime:
        return datetime.fromtimestamp(self.candles[-1]["time"], tz=timezone.utc)


def _quality_payload(
    quality: SeriesQuality,
    *,
    raw_rows: int,
    closed_rows: int,
    timeframe: str,
) -> dict[str, Any]:
    payload = quality.to_dict()
    payload["candles_available_raw"] = raw_rows
    payload["candles_available"] = closed_rows
    payload["closed_candles_only"] = True
    payload["forming_candles_used"] = False
    payload["source"] = "postgres_candle_cache"
    payload["source_note"] = (
        "Replay walks the platform's stored historical candle cache synced "
        "from MetaTrader 5. Missing candles are never filled or fabricated."
    )

    codes = {warning["code"] for warning in payload.get("warnings", [])}
    if codes & {
        "DUPLICATE_TIMESTAMPS",
        "TIMESTAMPS_UNSORTED",
        "FORMING_CANDLES_IN_HISTORY",
        "INVALID_OHLC_ROWS",
    }:
        payload["overall_status"] = "partial"
    else:
        # Calendar/session gaps are normal for FX and remain a warning, not a
        # reason to call otherwise valid data partial.
        payload["overall_status"] = "ok"
    payload["timeframe"] = timeframe
    payload["note"] = (
        "Only closed candles are counted in the replay. Forming rows and "
        "invalid OHLC rows are excluded and reported; they are never used "
        "to advance the replay clock. Data gaps are surfaced, not filled."
    )
    return payload


async def load_window(
    session: AsyncSession,
    *,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    max_candles: int,
) -> ReplayWindow:
    """Load and validate a replay window.

    Raises ``InsufficientHistoryError`` when there are no usable closed
    candles and ``ValidationAppError`` when the window exceeds the configured
    read cap. The cap is checked before truncating silently.
    """

    raw = await load_candles_range(
        session, symbol, timeframe, start, end, max_candles + 1
    )
    if len(raw) > max_candles:
        raise ValidationAppError(
            f"The replay window contains more than {max_candles} stored rows. "
            "Narrow the date range or raise REPLAY_MAX_CANDLES.",
            code="REPLAY_RANGE_TOO_LARGE",
        )

    quality = assess_series_quality(raw, timeframe, start, end)
    usable: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in raw:
        if row.get("forming") is True or not _valid(row):
            continue
        stamp = int(row["time"])
        if stamp in seen:
            continue
        seen.add(stamp)
        usable.append(row)
    usable.sort(key=lambda row: int(row["time"]))

    if not usable:
        raise InsufficientHistoryError(
            f"No closed historical candles are stored for {symbol} "
            f"{timeframe} between {start.isoformat()} and {end.isoformat()}. "
            "Sync the MT5 candle cache first; replay never fabricates data.",
            code="NO_HISTORICAL_DATA",
        )
    if len(usable) > max_candles:
        raise ValidationAppError(
            f"The replay window contains {len(usable)} usable candles, above "
            f"the limit of {max_candles}.",
            code="REPLAY_RANGE_TOO_LARGE",
        )

    quality_payload = _quality_payload(
        quality,
        raw_rows=len(raw),
        closed_rows=len(usable),
        timeframe=timeframe,
    )
    # The replay series is the usable closed series, so its first/last times
    # must not point at a forming or invalid row that was excluded.
    quality_payload["first_time"] = int(usable[0]["time"])
    quality_payload["last_time"] = int(usable[-1]["time"])
    return ReplayWindow(
        candles=usable,
        quality=quality_payload,
        raw_row_count=len(raw),
    )


async def count_closed(
    session: AsyncSession,
    *,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> int:
    stmt = (
        select(func.count())
        .select_from(CandleRow)
        .where(
            CandleRow.symbol == symbol,
            CandleRow.timeframe == timeframe,
            CandleRow.open_time >= start,
            CandleRow.open_time <= end,
            CandleRow.is_closed.is_(True),
        )
    )
    return int((await session.execute(stmt)).scalar_one())


async def load_next_closed(
    session: AsyncSession,
    *,
    symbol: str,
    timeframe: str,
    after: datetime,
    end: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    """The next ``limit`` closed candles strictly AFTER ``after``."""

    stmt = (
        select(CandleRow)
        .where(
            CandleRow.symbol == symbol,
            CandleRow.timeframe == timeframe,
            CandleRow.open_time > after,
            CandleRow.open_time <= end,
            CandleRow.is_closed.is_(True),
        )
        .order_by(CandleRow.open_time.asc())
        .limit(limit)
    )
    rows = list((await session.scalars(stmt)).all())
    return [_row_to_candle(row) for row in rows]


async def load_visible(
    session: AsyncSession,
    *,
    symbol: str,
    timeframe: str,
    first: datetime,
    cursor: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    """Newest ``limit`` visible closed candles (ascending in the response)."""

    stmt = (
        select(CandleRow)
        .where(
            CandleRow.symbol == symbol,
            CandleRow.timeframe == timeframe,
            CandleRow.open_time >= first,
            CandleRow.open_time <= cursor,
            CandleRow.is_closed.is_(True),
        )
        .order_by(CandleRow.open_time.desc())
        .limit(limit)
    )
    rows = list((await session.scalars(stmt)).all())
    candles = [_row_to_candle(row) for row in rows]
    candles.reverse()
    return candles


async def latest_at_or_before(
    session: AsyncSession,
    *,
    symbol: str,
    timeframe: str,
    first: datetime,
    target: datetime,
) -> dict[str, Any] | None:
    stmt = (
        select(CandleRow)
        .where(
            CandleRow.symbol == symbol,
            CandleRow.timeframe == timeframe,
            CandleRow.open_time >= first,
            CandleRow.open_time <= target,
            CandleRow.is_closed.is_(True),
        )
        .order_by(CandleRow.open_time.desc())
        .limit(1)
    )
    row = (await session.scalars(stmt)).first()
    return _row_to_candle(row) if row is not None else None


def coverage_payload(
    window: ReplayWindow, *, symbol: str, timeframe: str, start: datetime, end: datetime
) -> dict[str, Any]:
    quality = window.quality
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "start": start.isoformat(timespec="milliseconds"),
        "end": end.isoformat(timespec="milliseconds"),
        "candles_in_range": len(window.candles),
        "expected_candles": quality.get("expected_candles"),
        "coverage_ratio": quality.get("coverage_ratio"),
        "first_time": quality.get("first_time"),
        "last_time": quality.get("last_time"),
        "data_quality": quality,
        "checked_at": datetime.now(tz=timezone.utc).isoformat(
            timespec="milliseconds"
        ),
        "note": (
            "Read-only coverage check against the stored candle cache. It "
            "does not create a session, fetch new data or fill gaps."
        ),
    }


__all__ = [
    "ReplayWindow",
    "count_closed",
    "coverage_payload",
    "latest_at_or_before",
    "load_next_closed",
    "load_visible",
    "load_window",
]
