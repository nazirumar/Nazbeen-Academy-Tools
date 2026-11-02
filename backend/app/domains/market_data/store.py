"""Candle persistence in PostgreSQL (Phase 2 cache layer).

Scope & policies
----------------
- Persistence is used ONLY for historical candle caching (dedup + reload
  without re-fetching the terminal). The primary source of truth is always
  the MT5 terminal; rows carry ``source`` and responses carry
  ``data_provenance`` so raw MT5 data, cached data and validated data are
  never conflated.
- The composite PK ``(symbol, timeframe, open_time)`` makes duplicate candle
  inserts impossible; upserts are batched and transactional, and a forming
  candle can be refreshed in place as it evolves and closes.
- Reads always limit + order (latest N candles) — the full historical
  dataset is never loaded per request.
- No Redis: PostgreSQL covers the current access pattern (single-user,
  read-mostly cache with timeframe-aware freshness).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.candle import CandleRow
from app.domains.market_data.timeframes import timeframe_seconds

logger = get_logger(__name__)

UPSERT_BATCH_SIZE = 500


def _epoch_to_utc(epoch: int) -> datetime:
    """MT5 epoch seconds -> timezone-aware UTC datetime (MT5 convention)."""

    return datetime.fromtimestamp(int(epoch), tz=timezone.utc)


def _rows_to_upsert(
    symbol: str, timeframe: str, candles: list[dict[str, Any]], source: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candle in candles:
        try:
            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "open_time": _epoch_to_utc(candle["time"]),
                    "open": float(candle["open"]),
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                    "close": float(candle["close"]),
                    "volume": (
                        float(candle["volume"])
                        if candle.get("volume") is not None
                        else None
                    ),
                    "is_closed": not bool(candle.get("forming", False)),
                    "source": source,
                    "fetched_at": datetime.now(tz=timezone.utc),
                }
            )
        except (KeyError, TypeError, ValueError):
            # Validation should have filtered these; never persist junk.
            continue
    return rows


async def upsert_candles(
    session: AsyncSession,
    symbol: str,
    timeframe: str,
    candles: list[dict[str, Any]],
    *,
    source: str = "mt5",
) -> int:
    """Batched transactional upsert. Returns the number of rows written.

    Repeated inserts of the same candle update the existing row (a forming
    candle converges to its final values); duplicates can never violate the
    primary key.
    """

    rows = _rows_to_upsert(symbol, timeframe, candles, source)
    written = 0
    for start in range(0, len(rows), UPSERT_BATCH_SIZE):
        batch = rows[start : start + UPSERT_BATCH_SIZE]
        stmt = pg_insert(CandleRow).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "timeframe", "open_time"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "is_closed": stmt.excluded.is_closed,
                "source": stmt.excluded.source,
                "fetched_at": func.now(),
            },
        )
        await session.execute(stmt)
        written += len(batch)
    return written


def _row_to_candle(row: CandleRow) -> dict[str, Any]:
    open_epoch = int(row.open_time.timestamp())
    return {
        "time": open_epoch,
        "time_utc": row.open_time.isoformat(timespec="seconds"),
        "open": row.open,
        "high": row.high,
        "low": row.low,
        "close": row.close,
        "volume": row.volume,
        # The cached closed-state is authoritative for stored rows: a forming
        # candle is stored with is_closed=false and refreshed by later upserts
        # until it closes. (Historical candles are always is_closed=true.)
        "forming": not row.is_closed,
    }


async def load_candles(
    session: AsyncSession,
    symbol: str,
    timeframe: str,
    limit: int,
    *,
    closed_only: bool = False,
) -> list[dict[str, Any]]:
    """Latest ``limit`` candles, ascending. Never loads the whole table."""

    stmt = (
        select(CandleRow)
        .where(CandleRow.symbol == symbol, CandleRow.timeframe == timeframe)
        .order_by(CandleRow.open_time.desc())
        .limit(limit * 2 if closed_only else limit)
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    candles = [_row_to_candle(row) for row in rows]
    if closed_only:
        candles = [c for c in candles if not c["forming"]]
    candles = candles[:limit]
    candles.reverse()  # ascending for the API contract
    return candles


async def load_candles_range(
    session: AsyncSession,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    """Candles whose OPEN time lies in ``[start, end]``, ascending.

    Historical range read used by the backtest engine (Phase 6). Rows are
    returned exactly as stored — never re-ordered into a different series,
    never filled; the caller assesses quality (gaps, duplicates, forming
    candles). ``limit`` caps the read (hard upper bound, engine fails
    explicitly when the range exceeds it instead of truncating silently).
    """

    stmt = (
        select(CandleRow)
        .where(
            CandleRow.symbol == symbol,
            CandleRow.timeframe == timeframe,
            CandleRow.open_time >= start,
            CandleRow.open_time <= end,
        )
        .order_by(CandleRow.open_time.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    return [_row_to_candle(row) for row in rows]


async def count_candles_range(
    session: AsyncSession,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> int:
    """Number of stored candles with open time in ``[start, end]``."""

    stmt = (
        select(func.count())
        .select_from(CandleRow)
        .where(
            CandleRow.symbol == symbol,
            CandleRow.timeframe == timeframe,
            CandleRow.open_time >= start,
            CandleRow.open_time <= end,
        )
    )
    return int((await session.execute(stmt)).scalar_one())


async def series_state(
    session: AsyncSession,
    symbol: str,
    timeframe: str,
) -> dict[str, Any] | None:
    """Freshness facts about a stored series (newest row + coverage)."""

    stmt = (
        select(CandleRow)
        .where(CandleRow.symbol == symbol, CandleRow.timeframe == timeframe)
        .order_by(CandleRow.open_time.desc())
        .limit(1)
    )
    newest = (await session.execute(stmt)).scalar_one_or_none()
    if newest is None:
        return None
    count_stmt = (
        select(func.count())
        .select_from(CandleRow)
        .where(CandleRow.symbol == symbol, CandleRow.timeframe == timeframe)
    )
    total = (await session.execute(count_stmt)).scalar_one()
    return {
        "newest_open_time": int(newest.open_time.timestamp()),
        "newest_is_closed": newest.is_closed,
        "cached_rows": int(total),
        "source": newest.source,
        "fetched_at": newest.fetched_at.isoformat(timespec="seconds"),
    }


def is_series_fresh(
    state: dict[str, Any] | None,
    timeframe: str,
    *,
    now_epoch: float | None = None,
    cache_max_age_seconds: int = 90,
) -> bool:
    """Cache-serve policy for one series.

    A cached series may satisfy a request when its newest candle opened
    within ``max(cache_max_age_seconds, interval + grace)`` seconds. A series
    with too few rows is never considered "fresh enough" on its own — the
    caller decides coverage (the provider fetches when the cache cannot
    satisfy the requested count).
    """

    if state is None:
        return False
    now = now_epoch if now_epoch is not None else datetime.now(tz=timezone.utc).timestamp()
    interval = timeframe_seconds(timeframe)
    grace = max(cache_max_age_seconds, interval + 15)
    newest_open = state["newest_open_time"]
    newest_close = newest_open + (interval if state["newest_is_closed"] else 0)
    return (now - newest_open) <= grace and (now - newest_close) <= grace
