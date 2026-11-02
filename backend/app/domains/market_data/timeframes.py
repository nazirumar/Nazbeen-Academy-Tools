"""Timeframe and trading-mode definitions (single source of truth).

The frontend fetches these whitelists from ``GET /api/v1/meta`` rather than
hard-coding them, so the UI can never drift from the engine.
"""

from __future__ import annotations

from enum import Enum

SUPPORTED_MODES = ("forex", "binary_options")


class Timeframe(str, Enum):
    """Supported chart timeframes (extensible whitelist)."""

    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"


TIMEFRAME_MINUTES: dict[str, int] = {
    Timeframe.M1.value: 1,
    Timeframe.M5.value: 5,
    Timeframe.M15.value: 15,
    Timeframe.H1.value: 60,
    Timeframe.H4.value: 240,
    Timeframe.D1.value: 1440,
}

SUPPORTED_TIMEFRAMES: tuple[str, ...] = tuple(TIMEFRAME_MINUTES.keys())

# Default timeframe profiles per analysis mode. Binary Options analysis is
# M1-only by product decision; Forex uses the full default ladder.
MODE_TIMEFRAME_PROFILES: dict[str, list[str]] = {
    "forex": ["D1", "H4", "H1", "M15", "M5", "M1"],
    "binary_options": ["M1"],
}


class UnsupportedTimeframeError(ValueError):
    pass


def timeframes_for_mode(mode: str) -> list[str]:
    """Default timeframes for a mode (used by /meta and API validation)."""

    try:
        return list(MODE_TIMEFRAME_PROFILES[mode])
    except KeyError as exc:
        raise UnsupportedTimeframeError(
            f"Unknown mode '{mode}'. Supported: {', '.join(SUPPORTED_MODES)}"
        ) from exc


def is_timeframe_supported(timeframe: str) -> bool:
    return timeframe in TIMEFRAME_MINUTES


def timeframe_seconds(timeframe: str) -> int:
    try:
        return TIMEFRAME_MINUTES[timeframe] * 60
    except KeyError as exc:
        raise UnsupportedTimeframeError(
            f"Unknown timeframe '{timeframe}'. Supported: "
            f"{', '.join(SUPPORTED_TIMEFRAMES)}"
        ) from exc


def validate_mode_timeframes(mode: str, timeframes: list[str]) -> list[str]:
    """Validate a timeframe list against a mode; returns error messages.

    Used by the market-data API where the Phase 2 product rule applies
    (Binary Options candle fetching is M1-only by default).
    """

    errors: list[str] = []
    for tf in timeframes:
        if not is_timeframe_supported(tf):
            errors.append(
                f"Timeframe '{tf}' is not supported. Supported: "
                f"{', '.join(SUPPORTED_TIMEFRAMES)}"
            )
    if timeframes and len(set(timeframes)) != len(timeframes):
        errors.append("Timeframes must not contain duplicates.")
    if mode == "binary_options" and timeframes and set(timeframes) != {"M1"}:
        errors.append(
            "Binary Options analysis supports the M1 timeframe only "
            "(product rule; Forex mode allows D1, H4, H1, M15, M5, M1)."
        )
    return errors


def validate_strategy_timeframes(mode: str, timeframes: list[str]) -> list[str]:
    """Validate the timeframe list of a STRATEGY rule set (Phase 3 rules).

    Difference to :func:`validate_mode_timeframes` (market-data API):

    * Binary Options strategies DEFAULT to M1 only, but a user may
      explicitly configure higher timeframes (e.g. an M5 context filter).
      The platform never forces higher timeframes into Binary Options
      strategies — it only permits them when the user declares them.
    * Forex accepts the full supported ladder.

    Unsupported timeframes and duplicates are always hard errors.
    """

    errors: list[str] = []
    for tf in timeframes:
        if not is_timeframe_supported(tf):
            errors.append(
                f"Timeframe '{tf}' is not supported. Supported: "
                f"{', '.join(SUPPORTED_TIMEFRAMES)}"
            )
    if timeframes and len(set(timeframes)) != len(timeframes):
        errors.append("Timeframes must not contain duplicates.")
    return errors
