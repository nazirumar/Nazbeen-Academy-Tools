"""Market feature calculations — deterministic, documented, no look-ahead.

This module is the platform's single library of *market features*: pure
functions that derive measurable facts (candle anatomy, swing points,
structure sequences, structure breaks, zone interactions, data
completeness) from a series of OHLCV candles.

Design rules (Phase 4):

1. **Deterministic.** Every function is a pure function of the candles
   supplied. Same input -> byte-identical output.
2. **No look-ahead bias.** A feature computed from candles[0..t] never
   uses information from candles after ``t``. Where a definition requires
   subsequent candles for *confirmation* (e.g. a swing high needs
   ``strength`` later candles), the confirmation is an explicit, recorded
   property (``confirmed_at_time``) — the feature reports WHEN it became
   known, never earlier.
3. **Honest availability.** Functions return ``None``/empty results with
   reasons instead of approximating when inputs are insufficient. Nothing
   here fabricates data.
4. **Documented.** :data:`FEATURE_CATALOG` describes every feature:
   definition, required input history, parameters and limitations. The
   API exposes the catalog verbatim (``GET /api/v1/analysis/features``).

Candle input convention: a list of dicts with keys ``time`` (unix seconds),
``open``, ``high``, ``low``, ``close`` (floats), optional ``volume`` and
optional ``forming`` (bool). Ascending by ``time`` unless stated otherwise.
"""

from __future__ import annotations

from typing import Any

from app.domains.market_data.timeframes import timeframe_seconds

# --------------------------------------------------------------------------- #
# Feature catalog (published verbatim by GET /api/v1/analysis/features)
# --------------------------------------------------------------------------- #

FEATURE_CATALOG: list[dict[str, Any]] = [
    {
        "name": "candle_anatomy",
        "summary": "Direction, body, wicks and ratios of a single candle.",
        "definition": (
            "direction: bullish when close > open, bearish when close < open, "
            "doji otherwise. body = |close - open|; range = high - low; "
            "upper_wick = high - max(open, close); lower_wick = "
            "min(open, close) - low. Ratios divide each measure by range "
            "(None when range = 0)."
        ),
        "required_history": "1 candle.",
        "parameters": "None.",
        "limitations": (
            "Purely descriptive; zero-range candles report ratio None, not 0."
        ),
    },
    {
        "name": "moving_averages",
        "summary": "SMA and EMA of a price series (used by rules and regime).",
        "definition": (
            "SMA(p): arithmetic mean of the last p values. EMA(p): seeded "
            "with the SMA of the first p values, then smoothed with "
            "alpha = 2/(p+1). Values before the seed are None."
        ),
        "required_history": "p candles for the first value; more for stable EMA.",
        "parameters": "period (>= 2), source price field (open/high/low/close).",
        "limitations": (
            "EMA depends on the start of the supplied window: different "
            "window starts give slightly different values by design (only "
            "data available at evaluation time is used)."
        ),
    },
    {
        "name": "atr",
        "summary": "Average True Range (volatility measure).",
        "definition": (
            "True Range(i) = max(high-low, |high-prev_close|, "
            "|low-prev_close|); ATR(p) = arithmetic mean of the last p "
            "True Ranges. Needs p+1 candles (the first TR uses the "
            "previous close)."
        ),
        "required_history": "period + 1 candles.",
        "parameters": "period (default 14).",
        "limitations": (
            "Simple mean (not Wilder smoothing) — chosen for determinism "
            "and explainability, not for parity with any specific platform."
        ),
    },
    {
        "name": "swing_points",
        "summary": "Confirmed swing highs/lows with explicit confirmation time.",
        "definition": (
            "Candle i is a swing high when its high is STRICTLY greater "
            "than the highs of the ``strength`` candles on both sides; "
            "mirror for swing lows. Confirmed at the close of the "
            "``strength``-th candle after it (confirmed_at_time = that "
            "candle's open time). Candles near the right edge that still "
            "need future bars are NOT reported — no look-ahead."
        ),
        "required_history": "2*strength + 1 candles for any result.",
        "parameters": "kind (high|low), strength (1..10).",
        "limitations": (
            "Strict inequality: equal highs/lows are not swings. The "
            "choice of strength changes results; it is always user- "
            "or default-configured, never hard-coded."
        ),
    },
    {
        "name": "swing_structure",
        "summary": "HH / HL / LH / LL classification of consecutive swings.",
        "definition": (
            "Consecutive confirmed swing highs are labelled HH (higher "
            "high) or LH (lower high); consecutive swing lows HL/LL. "
            "Sequence trends: highs strictly rising and lows strictly "
            "rising => bullish structure, mirrors => bearish, otherwise "
            "mixed."
        ),
        "required_history": "Enough confirmed swings (>= 2 per kind for a trend hint).",
        "parameters": "kind, strength (via swing_points).",
        "limitations": (
            "Labels describe the PAST sequence; they are not a prediction."
        ),
    },
    {
        "name": "structure_breaks",
        "summary": "Historical closes beyond previously confirmed swings (BOS events).",
        "definition": (
            "Candle i is a bullish break when close[i] > price of the most "
            "recent swing high whose confirmation completed strictly "
            "before candle i (s + strength < i); bearish mirror. Each "
            "event records the breaking candle time and the reference "
            "level. This is OUR generic, precisely-defined break "
            "definition — not a claim of any universal ICT/SMC standard."
        ),
        "required_history": "2*strength + 2 candles for any event.",
        "parameters": "strength (1..10), kind filter optional.",
        "limitations": (
            "Snapshot semantics differ from the rule: the FEATURE lists "
            "historical events; the ``market_structure_break`` RULE tests "
            "only the latest candle."
        ),
    },
    {
        "name": "zone_interaction",
        "summary": "Price position, distance and touches for a user-defined zone.",
        "definition": (
            "Zone [low, high]. position: above/inside/below by latest "
            "close. distance: 0 inside, else distance to the nearest "
            "boundary. touched: any candle overlaps the zone (low <= high "
            "boundary and high >= low boundary)."
        ),
        "required_history": "1 candle for position; lookback candles for touches.",
        "parameters": "zone_low, zone_high (user-defined levels).",
        "limitations": (
            "Zones are static user input; the engine does not validate "
            "that they correspond to any market-generated level."
        ),
    },
    {
        "name": "data_completeness",
        "summary": "Counts, forming/closed split, gaps and coverage of a series.",
        "definition": (
            "total/closed/forming counts, first/last open times, number "
            "of gaps larger than 1.5x the timeframe interval, largest gap "
            "in intervals. Market closures (weekends) are expected; "
            "missing bars from the source are reported, never filled."
        ),
        "required_history": "0 candles (reports empty series honestly).",
        "parameters": "timeframe (for expected interval).",
        "limitations": (
            "Gap detection cannot distinguish a source gap from a market "
            "closure on intraday timeframes; both are reported as gaps."
        ),
    },
]


def feature_catalog() -> list[dict[str, Any]]:
    """Deep-ish copy of the catalog (callers must not mutate the source)."""

    return [dict(f) for f in FEATURE_CATALOG]


# --------------------------------------------------------------------------- #
# Candle anatomy
# --------------------------------------------------------------------------- #


def candle_direction(candle: dict[str, Any]) -> str:
    """bullish | bearish | doji (close == open)."""

    o, c = float(candle["open"]), float(candle["close"])
    if c > o:
        return "bullish"
    if c < o:
        return "bearish"
    return "doji"


def candle_anatomy(candle: dict[str, Any]) -> dict[str, Any]:
    """Full descriptive anatomy of ONE candle (see catalog)."""

    o = float(candle["open"])
    h = float(candle["high"])
    low = float(candle["low"])
    c = float(candle["close"])
    body = abs(c - o)
    rng = h - low
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - low

    def ratio(value: float) -> float | None:
        return value / rng if rng > 0 else None

    return {
        "direction": candle_direction(candle),
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "body": body,
        "range": rng,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "body_ratio": ratio(body),
        "upper_wick_ratio": ratio(upper_wick),
        "lower_wick_ratio": ratio(lower_wick),
    }


# --------------------------------------------------------------------------- #
# Series indicators
# --------------------------------------------------------------------------- #


def sma(series: list[float], period: int) -> list[float | None]:
    """Simple moving average; None before the first full window."""

    out: list[float | None] = [None] * len(series)
    if period <= 0 or len(series) < period:
        return out
    running = sum(series[:period])
    out[period - 1] = running / period
    for i in range(period, len(series)):
        running += series[i] - series[i - period]
        out[i] = running / period
    return out


def ema(series: list[float], period: int) -> list[float | None]:
    """EMA seeded with the SMA of the first ``period`` values."""

    out: list[float | None] = [None] * len(series)
    if period <= 1 or len(series) < period:
        return out
    alpha = 2.0 / (period + 1)
    prev = sum(series[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(series)):
        prev = alpha * series[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def true_ranges(candles: list[dict[str, Any]]) -> list[float]:
    """True Range per candle; the first candle uses high-low (no prev close)."""

    trs: list[float] = []
    prev_close: float | None = None
    for c in candles:
        h, low = float(c["high"]), float(c["low"])
        if prev_close is None:
            trs.append(h - low)
        else:
            trs.append(max(h - low, abs(h - prev_close), abs(low - prev_close)))
        prev_close = float(c["close"])
    return trs


def atr(candles: list[dict[str, Any]], period: int = 14) -> float | None:
    """Mean of the last ``period`` true ranges; None when unavailable."""

    if period <= 0 or len(candles) < period + 1:
        return None
    trs = true_ranges(candles)[-period:]
    return sum(trs) / period


# --------------------------------------------------------------------------- #
# Swing points (canonical implementation — the rule evaluators delegate here)
# --------------------------------------------------------------------------- #


def confirmed_swings(
    candles: list[dict[str, Any]], kind: str, strength: int
) -> list[dict[str, Any]]:
    """Confirmed swing highs/lows with explicit confirmation timing.

    A candle at index ``i`` is a swing high when its high is strictly
    greater than the highs of the ``strength`` candles before AND after it,
    and at least ``strength`` candles exist after it in the supplied list.
    The swing is *confirmed at* the ``strength``-th subsequent candle —
    ``confirmed_at_time`` records that candle's open time. Swings near the
    right edge that still need future candles are never returned.

    Returns entries ``{index, time, price, confirmed_at_index,
    confirmed_at_time}`` in chronological order.
    """

    if kind not in ("high", "low"):
        raise ValueError(f"kind must be 'high' or 'low', got {kind!r}")
    if strength < 1:
        raise ValueError("strength must be >= 1")

    swings: list[dict[str, Any]] = []
    n = len(candles)
    for i in range(strength, n - strength):
        if kind == "high":
            value = float(candles[i]["high"])
            is_swing = all(
                value > float(candles[j]["high"])
                for j in range(i - strength, i + strength + 1)
                if j != i
            )
        else:
            value = float(candles[i]["low"])
            is_swing = all(
                value < float(candles[j]["low"])
                for j in range(i - strength, i + strength + 1)
                if j != i
            )
        if is_swing:
            swings.append(
                {
                    "index": i,
                    "time": candles[i]["time"],
                    "price": value,
                    "confirmed_at_index": i + strength,
                    "confirmed_at_time": candles[i + strength]["time"],
                }
            )
    return swings


def swings_as_of(
    candles: list[dict[str, Any]], kind: str, strength: int, as_of_time: int
) -> list[dict[str, Any]]:
    """Swings KNOWN at ``as_of_time`` (no look-ahead helper).

    Equivalent to running :func:`confirmed_swings` on the prefix of candles
    with ``time <= as_of_time`` — provided as an explicit, testable
    guarantee that confirmation timing is respected.
    """

    prefix = [c for c in candles if c["time"] <= as_of_time]
    return confirmed_swings(prefix, kind, strength)


def swing_structure(
    swings: list[dict[str, Any]], kind: str
) -> dict[str, Any]:
    """HH/LH (kind=high) or HL/LL (kind=low) labels + structure trend hint."""

    labels: list[str] = []
    for i in range(1, len(swings)):
        rising = swings[i]["price"] > swings[i - 1]["price"]
        if kind == "high":
            labels.append("HH" if rising else "LH")
        else:
            labels.append("HL" if rising else "LL")
    trend = "mixed"
    if len(labels) >= 2 and all(label == labels[0] for label in labels):
        if labels[0] in ("HH", "HL"):
            trend = "bullish"
        else:
            trend = "bearish"
    return {
        "kind": kind,
        "labels": labels,
        "structure_trend": trend,
        "swing_count": len(swings),
    }


# --------------------------------------------------------------------------- #
# Structure breaks (feature-level historical events)
# --------------------------------------------------------------------------- #


def structure_breaks(
    candles: list[dict[str, Any]], strength: int = 2
) -> list[dict[str, Any]]:
    """Historical BOS-style break events (see catalog for the definition).

    Scans candles chronologically. A swing becomes the active reference
    only at the first candle AFTER its confirmation completed
    (``confirmed_at_index < i``); each candle is then tested against the
    ACTIVE reference. Each reference swing yields AT MOST ONE event — its
    first breaking close (``broken`` then suppresses re-firing until a
    newer swing confirms). Events are chronological, capped to the last 20:

    ``{time, side, level, reference_time, close}``
    """

    events: list[dict[str, Any]] = []
    n = len(candles)
    if n < 2 * strength + 2:
        return events
    swing_highs = confirmed_swings(candles, "high", strength)
    swing_lows = confirmed_swings(candles, "low", strength)

    hi = lo = 0
    current_high: dict[str, Any] | None = None
    current_low: dict[str, Any] | None = None
    broken_high = broken_low = False
    for i in range(strength + 1, n):
        # Adopt swings whose confirmation completed strictly before candle i.
        while hi < len(swing_highs) and swing_highs[hi]["confirmed_at_index"] < i:
            current_high = swing_highs[hi]
            broken_high = False
            hi += 1
        while lo < len(swing_lows) and swing_lows[lo]["confirmed_at_index"] < i:
            current_low = swing_lows[lo]
            broken_low = False
            lo += 1
        close = float(candles[i]["close"])
        if (
            current_high is not None
            and not broken_high
            and close > current_high["price"]
        ):
            events.append(
                {
                    "time": candles[i]["time"],
                    "side": "bullish",
                    "level": current_high["price"],
                    "reference_time": current_high["time"],
                    "close": close,
                }
            )
            broken_high = True
        if (
            current_low is not None
            and not broken_low
            and close < current_low["price"]
        ):
            events.append(
                {
                    "time": candles[i]["time"],
                    "side": "bearish",
                    "level": current_low["price"],
                    "reference_time": current_low["time"],
                    "close": close,
                }
            )
            broken_low = True
    return events[-20:]


# --------------------------------------------------------------------------- #
# Zone interaction
# --------------------------------------------------------------------------- #


def zone_interaction(
    candles: list[dict[str, Any]], zone_low: float, zone_high: float
) -> dict[str, Any]:
    """Position / distance / touches for a user-defined zone (see catalog)."""

    if zone_high < zone_low:
        raise ValueError("zone_high must be >= zone_low")
    touches: list[dict[str, Any]] = []
    position: str | None = None
    distance: float | None = None
    if candles:
        latest = candles[-1]
        close = float(latest["close"])
        if close > zone_high:
            position, distance = "above", close - zone_high
        elif close < zone_low:
            position, distance = "below", zone_low - close
        else:
            position, distance = "inside", 0.0
        for c in candles:
            if float(c["low"]) <= zone_high and float(c["high"]) >= zone_low:
                touches.append({"time": c["time"]})
    return {
        "zone_low": zone_low,
        "zone_high": zone_high,
        "position": position,
        "distance": distance,
        "touch_count": len(touches),
        "first_touch_time": touches[0]["time"] if touches else None,
        "last_touch_time": touches[-1]["time"] if touches else None,
    }


# --------------------------------------------------------------------------- #
# Data completeness
# --------------------------------------------------------------------------- #


def data_completeness(
    candles: list[dict[str, Any]], timeframe: str
) -> dict[str, Any]:
    """Honest series inventory (see catalog). Never fills gaps."""

    interval = timeframe_seconds(timeframe)
    total = len(candles)
    forming = sum(1 for c in candles if c.get("forming") is True)
    gaps = 0
    largest_gap = 0.0
    for i in range(1, total):
        delta = candles[i]["time"] - candles[i - 1]["time"]
        if delta > interval * 1.5:
            gaps += 1
            largest_gap = max(largest_gap, delta / interval)
    return {
        "timeframe": timeframe,
        "total": total,
        "closed": total - forming,
        "forming": forming,
        "first_time": candles[0]["time"] if candles else None,
        "last_time": candles[-1]["time"] if candles else None,
        "gap_count": gaps,
        "largest_gap_intervals": round(largest_gap, 2) if gaps else 0.0,
    }


# --------------------------------------------------------------------------- #
# Per-timeframe summary used by the analysis report
# --------------------------------------------------------------------------- #


def summarize_timeframe(
    timeframe: str,
    candles: list[dict[str, Any]],
    *,
    swing_strength: int = 2,
    zones: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Structure/zone observations for ONE timeframe (report building block).

    Purely descriptive; every datum comes from the supplied candles only.
    """

    anatomy_index = -1 if candles else None
    summary: dict[str, Any] = {
        "timeframe": timeframe,
        "completeness": data_completeness(candles, timeframe),
        "latest_candle": candle_anatomy(candles[anatomy_index]) if candles else None,
    }
    if candles:
        highs = confirmed_swings(candles, "high", swing_strength)
        lows = confirmed_swings(candles, "low", swing_strength)
        summary["swings"] = {
            "strength": swing_strength,
            "highs": [
                {"time": s["time"], "price": s["price"], "confirmed_at_time": s["confirmed_at_time"]}
                for s in highs[-5:]
            ],
            "lows": [
                {"time": s["time"], "price": s["price"], "confirmed_at_time": s["confirmed_at_time"]}
                for s in lows[-5:]
            ],
            "high_structure": swing_structure(highs, "high"),
            "low_structure": swing_structure(lows, "low"),
        }
        summary["structure_breaks"] = structure_breaks(candles, swing_strength)[-5:]
    else:
        summary["swings"] = None
        summary["structure_breaks"] = []
    zone_obs: list[dict[str, Any]] = []
    for zone in zones or []:
        try:
            zone_obs.append(
                zone_interaction(candles, float(zone["zone_low"]), float(zone["zone_high"]))
            )
        except (KeyError, TypeError, ValueError):
            continue
    if zone_obs:
        summary["zones"] = zone_obs
    return summary


__all__ = [
    "FEATURE_CATALOG",
    "atr",
    "candle_anatomy",
    "candle_direction",
    "confirmed_swings",
    "data_completeness",
    "ema",
    "feature_catalog",
    "structure_breaks",
    "summarize_timeframe",
    "sma",
    "swing_structure",
    "swings_as_of",
    "true_ranges",
    "zone_interaction",
]
