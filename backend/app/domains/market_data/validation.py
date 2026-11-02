"""MT5 candle validation and data-quality assessment (Phase 2).

The adapter is trusted for *transport*, never for *content*: every candle
payload is validated here before it reaches the API, the cache or any future
analysis. The validator NEVER fabricates, silently fills or interpolates
candles — it cleans, flags and reports.

Timezone convention (documented, stable):

- MetaTrader 5 reports candle open times as seconds since 1970-01-01 whose
  wall-clock value is the **broker server time** (MT5 convention).
- The platform preserves the original market timestamp unchanged (``time``)
  and additionally renders it as ISO-8601 UTC (``time_utc``) — i.e. the raw
  epoch is interpreted as UTC *notation* while remaining the untouched
  server-time instant. Responses carry ``timezone: "MT5_SERVER"`` plus a
  human-readable note so consumers can never be misled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.domains.market_data.timeframes import timeframe_seconds

TIMEZONE_CONVENTION = (
    "Candle timestamps are preserved exactly as reported by MetaTrader 5: "
    "epoch seconds whose wall-clock value is the broker SERVER time (the MT5 "
    "convention). 'time_utc' renders that same epoch as ISO-8601 UTC for a "
    "stable, comparable format. No re-basing or shifting is ever applied."
)

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"


@dataclass(frozen=True)
class QualityWarning:
    code: str
    severity: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity, "message": self.message}


@dataclass
class CandleValidation:
    """Result of validating one candle payload."""

    candles: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[QualityWarning] = field(default_factory=list)
    malformed_count: int = 0
    invalid_ohlc_count: int = 0
    duplicate_count: int = 0
    gap_count: int = 0
    forming_count: int = 0
    excluded_forming: int = 0
    is_stale: bool = False
    newest_closed_age_seconds: float | None = None
    server_time_skew_seconds: float | None = None
    data_status: str = "ok"  # ok | partial | stale | insufficient | empty

    @property
    def warning_dicts(self) -> list[dict[str, str]]:
        return [w.to_dict() for w in self.warnings]


def _finite_positive(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _ohlc_consistent(row: dict[str, Any]) -> bool:
    o, h, l, c = (float(row[k]) for k in ("open", "high", "low", "close"))
    return (
        h >= l
        and h >= o
        and h >= c
        and l <= o
        and l <= c
    )


def _preferred_volume(row: dict[str, Any]) -> float | None:
    """Real volume when positive, otherwise tick volume (MT5 convention)."""

    real, tick = row.get("real_volume"), row.get("tick_volume")

    def usable(value: Any, *, strictly_positive: bool) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and (float(value) > 0 if strictly_positive else float(value) >= 0)
        )

    if usable(real, strictly_positive=True):
        return float(real)
    if usable(tick, strictly_positive=False):
        return float(tick)
    return None


def _expected_open_for(now_epoch: float, interval: int) -> int:
    return int(now_epoch // interval * interval)


def _weekend_allowance_seconds(timeframe: str) -> int:
    """Extra staleness allowance for market closures (FX weekends/holidays)."""

    if timeframe == "D1":
        return 3 * timeframe_seconds(timeframe)  # Fri close -> Mon open
    if timeframe in ("H4", "H12", "W1"):
        return 2 * timeframe_seconds(timeframe)
    return 0


def validate_candles(
    records: list[dict[str, Any]] | None,
    timeframe: str,
    *,
    closed_only: bool = False,
    requested_count: int | None = None,
    now_epoch: float | None = None,
    stale_after_intervals: float = 3.0,
) -> CandleValidation:
    """Validate, clean and classify a candle payload from MT5.

    Guarantees:
    - output candles are sorted ascending by ``time`` and deduplicated
      (last occurrence of a timestamp wins — the freshest data);
    - malformed rows (missing/NaN/non-numeric prices, broken OHLC) are
      EXCLUDED and reported, never "fixed";
    - the forming candle is flagged; ``closed_only=True`` removes it;
    - nothing is interpolated: gaps are reported, never filled.
    """

    result = CandleValidation()
    now = float(now_epoch) if now_epoch is not None else datetime.now(tz=timezone.utc).timestamp()
    interval = timeframe_seconds(timeframe)

    if not records:
        result.data_status = "empty"
        result.warnings.append(
            QualityWarning(
                "EMPTY_RESPONSE",
                SEVERITY_WARNING,
                "The terminal returned no candles for this request. Nothing "
                "is fabricated — check symbol availability and history.",
            )
        )
        return result

    # ---- 1. structural sanity ------------------------------------------------
    clean: list[dict[str, Any]] = []
    malformed_times: list[int] = []
    invalid_ohlc_times: list[int] = []
    for row in records:
        if not isinstance(row, dict):
            malformed_times.append(-1)
            continue
        ts = row.get("time")
        prices = {k: row.get(k) for k in ("open", "high", "low", "close")}
        ts_ok = (
            isinstance(ts, int)
            and not isinstance(ts, bool)
            and ts > 0
        )
        prices_ok = all(_finite_positive(v) for v in prices.values())
        if not ts_ok or not prices_ok:
            # Rows without a usable timestamp are counted with a sentinel;
            # they can never be silently dropped from the report.
            malformed_times.append(int(ts) if ts_ok else -1)
            continue
        candidate = {
            "time": int(ts),
            "open": float(prices["open"]),
            "high": float(prices["high"]),
            "low": float(prices["low"]),
            "close": float(prices["close"]),
            "volume": _preferred_volume(row),
        }
        if not _ohlc_consistent(candidate):
            invalid_ohlc_times.append(int(ts))
            continue
        clean.append(candidate)
    result.malformed_count = len(malformed_times)
    result.invalid_ohlc_count = len(invalid_ohlc_times)
    if malformed_times:
        result.warnings.append(
            QualityWarning(
                "MALFORMED_CANDLES",
                SEVERITY_ERROR,
                f"{len(malformed_times)} candle(s) were excluded: missing or "
                f"non-numeric timestamps/prices (first around time "
                f"{malformed_times[0]}). Malformed data is never repaired.",
            )
        )
    if invalid_ohlc_times:
        result.warnings.append(
            QualityWarning(
                "INVALID_OHLC",
                SEVERITY_ERROR,
                f"{len(invalid_ohlc_times)} candle(s) violated OHLC "
                f"consistency (high must be >= open/close/low; low must be "
                f"<= open/close/high) — first at time {invalid_ohlc_times[0]}. "
                "They are excluded from the response.",
            )
        )

    if not clean:
        result.data_status = "empty"
        result.warnings.append(
            QualityWarning(
                "EMPTY_AFTER_VALIDATION",
                SEVERITY_ERROR,
                "Every candle in the response failed validation. Nothing is "
                "fabricated — the payload is rejected.",
            )
        )
        return result

    # ---- 2. ordering -----------------------------------------------------------
    times_before = [row["time"] for row in clean]
    clean.sort(key=lambda row: row["time"])
    if times_before != [row["time"] for row in clean]:
        result.warnings.append(
            QualityWarning(
                "TIMESTAMPS_UNSORTED",
                SEVERITY_WARNING,
                "The terminal returned candles out of order; they have been "
                "sorted ascending (the API contract).",
            )
        )

    # ---- 3. duplicates (keep the LAST occurrence = freshest) --------------------
    deduped: dict[int, dict[str, Any]] = {}
    for row in clean:
        deduped[row["time"]] = row
    result.duplicate_count = len(clean) - len(deduped)
    clean = sorted(deduped.values(), key=lambda row: row["time"])
    if result.duplicate_count:
        result.warnings.append(
            QualityWarning(
                "DUPLICATE_TIMESTAMPS",
                SEVERITY_WARNING,
                f"{result.duplicate_count} duplicate timestamp(s) received; "
                "the last occurrence (freshest data) was kept.",
            )
        )

    # ---- 4. forming vs closed ----------------------------------------------------
    for row in clean:
        row["forming"] = (row["time"] + interval) > now
    forming_rows = [row for row in clean if row["forming"]]
    result.forming_count = len(forming_rows)

    newest = clean[-1]
    expected_open = _expected_open_for(now, interval)
    if newest["time"] > expected_open:
        result.server_time_skew_seconds = newest["time"] - expected_open
        result.warnings.append(
            QualityWarning(
                "SERVER_TIME_AHEAD",
                SEVERITY_WARNING,
                "The newest candle opens in the future relative to this "
                f"server's clock by ~{int(result.server_time_skew_seconds)}s. "
                "This indicates broker-server/local-clock skew — the forming "
                "candle is flagged accordingly and staleness uses server "
                "timestamps only.",
            )
        )
    if forming_rows and not closed_only:
        result.warnings.append(
            QualityWarning(
                "FORMING_CANDLE_INCLUDED",
                SEVERITY_INFO,
                f"{len(forming_rows)} forming (incomplete) candle(s) included "
                "and flagged with \"forming\": true. Its values change until "
                "the candle closes — never treat it as final.",
            )
        )

    # ---- 5. closed-only filter -----------------------------------------------------
    if closed_only:
        before = len(clean)
        clean = [row for row in clean if not row["forming"]]
        result.excluded_forming = before - len(clean)
        if result.excluded_forming:
            result.warnings.append(
                QualityWarning(
                    "FORMING_CANDLE_EXCLUDED",
                    SEVERITY_INFO,
                    f"{result.excluded_forming} forming candle(s) excluded "
                    "(closed_only=true). Every returned candle is closed.",
                )
            )

    # ---- 6. gap detection (report only — never fill) -----------------------------
    tolerance = 4.5 if timeframe == "D1" else 1.5
    gaps: list[tuple[int, int]] = []
    for prev, cur in zip(clean, clean[1:]):
        delta = cur["time"] - prev["time"]
        if delta > interval * tolerance:
            gaps.append((prev["time"], cur["time"]))
    result.gap_count = len(gaps)
    if gaps:
        est_missing = sum(
            (end - start) // interval - 1 for start, end in gaps
        )
        result.warnings.append(
            QualityWarning(
                "GAPS_DETECTED",
                SEVERITY_WARNING,
                f"{len(gaps)} gap(s) exceed the {timeframe} interval "
                f"(~{est_missing} bar(s) missing, first between {gaps[0][0]} "
                f"and {gaps[0][1]}). Market closures are normal; missing "
                "source bars are NOT back-filled — history stays as delivered.",
            )
        )

    # ---- 7. staleness of the newest CLOSED candle -----------------------------------
    closed_rows = [row for row in clean if not row["forming"]]
    if closed_rows:
        newest_closed = closed_rows[-1]
        age = now - (newest_closed["time"] + interval)
        result.newest_closed_age_seconds = max(age, 0.0)
        allowed = (
            stale_after_intervals * interval
            + _weekend_allowance_seconds(timeframe)
        )
        if age > allowed:
            result.is_stale = True
            result.warnings.append(
                QualityWarning(
                    "STALE_DATA",
                    SEVERITY_WARNING,
                    "The newest closed candle is "
                    f"{int(result.newest_closed_age_seconds)}s old (allowed "
                    f"~{int(allowed)}s for {timeframe} including market "
                    "closures). Data may be outdated — a tick timestamp is "
                    "NOT proof that history is current.",
                )
            )

    # ---- 8. insufficient history ------------------------------------------------------
    if requested_count is not None and len(clean) < requested_count:
        result.warnings.append(
            QualityWarning(
                "INSUFFICIENT_HISTORY",
                SEVERITY_WARNING,
                f"Only {len(clean)} validated candle(s) available but "
                f"{requested_count} were requested. The terminal may not "
                "hold enough history; nothing is back-filled.",
            )
        )

    # ---- 9. overall status -------------------------------------------------------------
    if result.is_stale:
        result.data_status = "stale"
    elif result.malformed_count or result.invalid_ohlc_count:
        result.data_status = "partial"
    elif requested_count is not None and len(clean) < requested_count:
        result.data_status = "insufficient"
    else:
        result.data_status = "ok"

    # ---- 10. ISO rendering (stable, documented) -----------------------------------------
    for row in clean:
        row["time_utc"] = datetime.fromtimestamp(
            row["time"], tz=timezone.utc
        ).isoformat(timespec="seconds")

    result.candles = clean
    return result
