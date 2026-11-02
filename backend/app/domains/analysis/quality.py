"""Candle data-quality assessment.

Produces honest, human-readable warnings so users know exactly how much to
trust an evaluation result (missing candles, weekend gaps, inverted OHLC,
insufficient lookback, ...). Warnings never silently alter the evaluation.
"""

from __future__ import annotations

import math
from typing import Any

from app.domains.market_data.timeframes import timeframe_seconds


def assess_candle_series(
    candles: list[dict[str, Any]], timeframe: str, required_count: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate one candle series.

    Returns ``(warnings, sorted_candles)``. The returned candles are sorted by
    time ascending (a copy — input is never mutated).
    """

    warnings: list[dict[str, Any]] = []
    if not candles:
        return warnings, []

    rows = [dict(c) for c in candles]

    # --- timestamp sanity -------------------------------------------------------
    unsorted = any(rows[i]["time"] > rows[i + 1]["time"] for i in range(len(rows) - 1))
    if unsorted:
        warnings.append(
            {
                "code": "TIMESTAMPS_UNSORTED",
                "timeframe": timeframe,
                "message": "Candles were not sorted by time; they have been "
                "sorted ascending before evaluation.",
            }
        )
    rows.sort(key=lambda c: c["time"])
    duplicates = sum(
        1 for i in range(1, len(rows)) if rows[i]["time"] == rows[i - 1]["time"]
    )
    if duplicates:
        warnings.append(
            {
                "code": "DUPLICATE_TIMESTAMPS",
                "timeframe": timeframe,
                "message": f"{duplicates} duplicate timestamp(s) detected; "
                "duplicates can distort cross detection.",
            }
        )

    # --- gap detection -------------------------------------------------------------
    # D1 gets a weekend-tolerant threshold (Fri -> Mon gap is normal).
    interval = timeframe_seconds(timeframe)
    tolerance = 4.5 if timeframe == "D1" else 1.5
    large_gaps = 0
    first_gap: int | None = None
    for i in range(1, len(rows)):
        delta = rows[i]["time"] - rows[i - 1]["time"]
        if delta > interval * tolerance:
            large_gaps += 1
            if first_gap is None:
                first_gap = rows[i - 1]["time"]
    if large_gaps:
        warnings.append(
            {
                "code": "TIMESTAMP_GAPS",
                "timeframe": timeframe,
                "message": f"{large_gaps} gap(s) larger than the expected "
                f"{timeframe} interval were found"
                + (f", first before {first_gap}" if first_gap else "")
                + ". Market closures are normal; missing bars from the "
                "source are not.",
            }
        )

    # --- OHLC sanity ------------------------------------------------------------------
    invalid = 0
    first_invalid_time: int | None = None
    for row in rows:
        o, h, l, c = (row.get(k) for k in ("open", "high", "low", "close"))
        prices = (o, h, l, c)
        if any(
            p is None
            or isinstance(p, bool)
            or not isinstance(p, (int, float))
            or not math.isfinite(float(p))
            or float(p) <= 0
            for p in prices
        ):
            invalid += 1
            if first_invalid_time is None:
                first_invalid_time = row["time"]
            continue
        if not (float(h) >= float(l) and float(h) >= float(o) and float(h) >= float(c)
                and float(l) <= float(o) and float(l) <= float(c)):
            invalid += 1
            if first_invalid_time is None:
                first_invalid_time = row["time"]
    if invalid:
        warnings.append(
            {
                "code": "INVALID_OHLC",
                "timeframe": timeframe,
                "message": f"{invalid} candle(s) have invalid OHLC values "
                "(non-positive, non-numeric, NaN or high/low inconsistent "
                "with open/close). Affected candles are excluded from "
                "condition evaluation."
                + (f" First at time={first_invalid_time}." if first_invalid_time else ""),
            }
        )

    # --- volume (optional field) -----------------------------------------------------
    bad_volume = sum(
        1
        for row in rows
        if row.get("volume") is not None
        and (isinstance(row["volume"], bool) or not isinstance(row["volume"], (int, float))
             or not math.isfinite(float(row["volume"]))
             or float(row["volume"]) < 0)
    )
    if bad_volume:
        warnings.append(
            {
                "code": "INVALID_VOLUME",
                "timeframe": timeframe,
                "message": f"{bad_volume} candle(s) have an invalid volume; "
                "volume is ignored by Phase 1 condition types.",
            }
        )

    # --- sufficiency ---------------------------------------------------------------------
    if len(rows) < required_count:
        warnings.append(
            {
                "code": "INSUFFICIENT_CANDLES",
                "timeframe": timeframe,
                "message": f"{len(rows)} candle(s) provided but the strictest "
                f"condition needs {required_count}. Results may be "
                "incomplete or unverifiable.",
            }
        )

    return warnings, rows
