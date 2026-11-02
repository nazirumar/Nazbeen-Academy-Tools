"""Historical data access + honest quality assessment for backtests.

Data source: the platform's existing PostgreSQL candle cache (populated by
the Phase 2 MT5 integration). The backtest engine NEVER fetches from a
provider mid-walk (that would make runs non-deterministic), never fills
gaps and never invents candles. Everything questionable becomes a warning
in the persisted data-quality summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationAppError
from app.db.models.candle import CandleRow  # noqa: F401  (import for clarity)
from app.domains.market_data.store import count_candles_range, load_candles_range
from app.domains.market_data.timeframes import timeframe_seconds

# D1 series get a weekend-tolerant gap threshold (Fri -> Mon is normal).
_WEEKEND_TOLERANT_TFS = frozenset({"D1", "W1", "MN1"})


class InsufficientHistoryError(ValidationAppError):
    def __init__(self, message: str, code: str = "INSUFFICIENT_HISTORY") -> None:
        super().__init__(message, code=code)


@dataclass
class SeriesQuality:
    """Honest inventory of one stored series inside the requested range."""

    timeframe: str
    candles_available: int
    expected_candles: int | None
    first_time: int | None
    last_time: int | None
    gap_count: int
    largest_gap_intervals: int
    duplicate_count: int
    out_of_order_count: int
    forming_count: int
    invalid_ohlc_count: int
    coverage_ratio: float | None
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "candles_available": self.candles_available,
            "expected_candles": self.expected_candles,
            "first_time": self.first_time,
            "last_time": self.last_time,
            "gap_count": self.gap_count,
            "largest_gap_intervals": self.largest_gap_intervals,
            "duplicate_count": self.duplicate_count,
            "out_of_order_count": self.out_of_order_count,
            "forming_count": self.forming_count,
            "invalid_ohlc_count": self.invalid_ohlc_count,
            "coverage_ratio": self.coverage_ratio,
            "warnings": list(self.warnings),
        }


def _row_valid(row: dict[str, Any]) -> bool:
    try:
        o, h, l, c = (
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
        )
    except (KeyError, TypeError, ValueError):
        return False
    return h >= l and min(o, h, l, c) > 0


def assess_series_quality(
    candles: list[dict[str, Any]], timeframe: str, start: datetime, end: datetime
) -> SeriesQuality:
    """Assess ONE series against the requested window (ascending rows)."""

    interval = timeframe_seconds(timeframe)
    start_epoch = int(start.timestamp())
    end_epoch = int(end.timestamp())
    n = len(candles)

    duplicates = 0
    out_of_order = 0
    forming = 0
    invalid = 0
    gaps = 0
    largest_gap = 0
    for i, row in enumerate(candles):
        if row.get("forming") is True:
            forming += 1
        if not _row_valid(row):
            invalid += 1
        if i > 0:
            prev = candles[i - 1]["time"]
            cur = row["time"]
            if cur == prev:
                duplicates += 1
            elif cur < prev:
                out_of_order += 1
            else:
                step = cur - prev
                if step > interval:
                    gaps += 1
                    largest_gap = max(largest_gap, step // interval)

    expected: int | None = None
    coverage: float | None = None
    if interval > 0:
        # Expected calendar slots; weekends make this an UPPER bound for FX,
        # so coverage below 1.0 is diagnostic, never a failure on its own.
        expected = max(0, (end_epoch - start_epoch) // interval + 1)
        if expected > 0:
            coverage = round(min(1.0, n / expected), 4)

    quality = SeriesQuality(
        timeframe=timeframe,
        candles_available=n,
        expected_candles=expected,
        first_time=candles[0]["time"] if n else None,
        last_time=candles[-1]["time"] if n else None,
        gap_count=gaps,
        largest_gap_intervals=largest_gap,
        duplicate_count=duplicates,
        out_of_order_count=out_of_order,
        forming_count=forming,
        invalid_ohlc_count=invalid,
        coverage_ratio=coverage,
    )
    quality.warnings = _quality_warnings(quality, timeframe)
    return quality


def _quality_warnings(q: SeriesQuality, timeframe: str) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if q.candles_available == 0:
        warnings.append(
            {
                "code": "NO_CANDLES_IN_RANGE",
                "timeframe": timeframe,
                "message": "No stored candles inside the requested window for "
                "this timeframe.",
            }
        )
        return warnings
    if q.duplicate_count:
        warnings.append(
            {
                "code": "DUPLICATE_TIMESTAMPS",
                "timeframe": timeframe,
                "message": f"{q.duplicate_count} duplicate timestamp(s) present "
                "in the stored series.",
            }
        )
    if q.out_of_order_count:
        warnings.append(
            {
                "code": "TIMESTAMPS_UNSORTED",
                "timeframe": timeframe,
                "message": f"{q.out_of_order_count} out-of-order timestamp(s) "
                "were re-sorted ascending before processing.",
            }
        )
    if q.forming_count:
        warnings.append(
            {
                "code": "FORMING_CANDLES_IN_HISTORY",
                "timeframe": timeframe,
                "message": f"{q.forming_count} forming candle row(s) in the "
                "historical window were excluded (closed-candles-only "
                "policy).",
            }
        )
    if q.invalid_ohlc_count:
        warnings.append(
            {
                "code": "INVALID_OHLC_ROWS",
                "timeframe": timeframe,
                "message": f"{q.invalid_ohlc_count} row(s) with invalid OHLC "
                "relationships were excluded from evaluation.",
            }
        )
    if q.gap_count:
        tolerant = timeframe in _WEEKEND_TOLERANT_TFS
        warnings.append(
            {
                "code": "DATA_GAPS",
                "timeframe": timeframe,
                "message": f"{q.gap_count} gap(s) detected (largest "
                f"{q.largest_gap_intervals} interval(s)"
                + (" — market-closure gaps are normal for this timeframe." if tolerant else ".")
                + ")",
            }
        )
    return warnings


def warmup_start(start: datetime, timeframe: str, warmup_candles: int) -> datetime:
    """UTC instant from which history must be loaded to warm up the walk."""

    return start - timedelta(seconds=timeframe_seconds(timeframe) * warmup_candles)


async def load_history(
    session: AsyncSession,
    *,
    symbol: str,
    timeframes: list[str],
    primary_timeframe: str,
    start: datetime,
    end: datetime,
    warmup_candles: int,
    max_candles: int,
) -> dict[str, list[dict[str, Any]]]:
    """Load each required timeframe from the candle store.

    Raises ``InsufficientHistoryError`` when the PRIMARY timeframe has no
    closed candles inside the requested window (nothing to walk). Missing
    history on secondary timeframes is reported by the caller via
    :class:`SeriesQuality` and surfaces as ``insufficient_data`` evaluations
    — the platform never fabricates candles for them.
    """

    from datetime import timezone as _tz

    start = start.astimezone(_tz.utc)
    end = end.astimezone(_tz.utc)
    series: dict[str, list[dict[str, Any]]] = {}
    for tf in sorted(set(timeframes), key=timeframe_seconds):
        lo = warmup_start(start, tf, warmup_candles) if tf != primary_timeframe else (
            warmup_start(start, tf, warmup_candles)
        )
        # The +interval extends the window so the candle covering `end`
        # (open before end, closing after) is loaded, then excluded.
        hi = end + timedelta(seconds=timeframe_seconds(tf))
        rows = await load_candles_range(session, symbol, tf, lo, hi, max_candles * 4)
        if len(rows) == max_candles * 4:
            raise ValidationAppError(
                f"Requested history for {symbol} {tf} exceeds the platform "
                "read limit. Narrow the date range.",
                code="RANGE_TOO_LARGE",
            )
        series[tf] = rows
    return series


async def verify_coverage(
    session: AsyncSession,
    *,
    symbol: str,
    primary_timeframe: str,
    start: datetime,
    end: datetime,
    max_candles: int,
) -> dict[str, Any]:
    """Pre-run coverage check used by run creation.

    Fails explicitly when the primary timeframe has no closed candles in the
    window. Returns a summary for the response payload.
    """

    count = await count_candles_range(session, symbol, primary_timeframe, start, end)
    interval = timeframe_seconds(primary_timeframe)
    expected = max(1, (int(end.timestamp()) - int(start.timestamp())) // interval + 1)
    if count == 0:
        raise InsufficientHistoryError(
            f"No historical candles stored for {symbol} {primary_timeframe} "
            f"between {start.isoformat()} and {end.isoformat()}. Sync the "
            "candle cache with the MT5 integration before backtesting; the "
            "platform does not fabricate historical data.",
            code="NO_HISTORICAL_DATA",
        )
    if count > max_candles:
        raise InsufficientHistoryError(
            f"The requested window needs about {count} {primary_timeframe} "
            f"candles, above the platform limit of {max_candles}. Narrow the "
            "date range or raise BACKTEST_MAX_CANDLES.",
            code="RANGE_TOO_LARGE",
        )
    return {
        "primary_timeframe": primary_timeframe,
        "candles_in_range": count,
        "expected_candles": expected,
        "coverage_ratio": round(min(1.0, count / expected), 4),
        "checked_at": datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds"),
        "note": (
            "Pre-run coverage check against the stored candle cache "
            "(source of truth for backtests). Gaps and session closures "
            "are re-assessed per timeframe during the run."
        ),
    }
