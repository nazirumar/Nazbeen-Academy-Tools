"""MetaTrader 5 timeframe mapping (extensible, MT5-isolated).

The canonical timeframe whitelist lives in ``app.domains.market_data.timeframes``
(platform-neutral). This module maps those codes onto the ``TIMEFRAME_*``
constants exposed by the MetaTrader5 package. The mapping is DATA, resolved
lazily against whatever module object is passed in — production code passes
the real ``MetaTrader5`` module, tests pass a fake. Adding a timeframe means
adding an entry here plus the whitelist entry upstream; nothing else changes.
"""

from __future__ import annotations

from typing import Any

from app.domains.market_data.timeframes import (
    MODE_TIMEFRAME_PROFILES,
    SUPPORTED_TIMEFRAMES,
    UnsupportedTimeframeError,
)

# Canonical code -> MetaTrader5 constant NAME (resolved lazily via getattr so
# no MT5 import ever happens at module load and tests can fake the module).
MT5_TIMEFRAME_CONSTANTS: dict[str, str] = {
    "M1": "TIMEFRAME_M1",
    "M2": "TIMEFRAME_M2",
    "M3": "TIMEFRAME_M3",
    "M4": "TIMEFRAME_M4",
    "M5": "TIMEFRAME_M5",
    "M6": "TIMEFRAME_M6",
    "M10": "TIMEFRAME_M10",
    "M12": "TIMEFRAME_M12",
    "M15": "TIMEFRAME_M15",
    "M20": "TIMEFRAME_M20",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H2": "TIMEFRAME_H2",
    "H3": "TIMEFRAME_H3",
    "H4": "TIMEFRAME_H4",
    "H6": "TIMEFRAME_H6",
    "H8": "TIMEFRAME_H8",
    "H12": "TIMEFRAME_H12",
    "D1": "TIMEFRAME_D1",
    "W1": "TIMEFRAME_W1",
    "MN1": "TIMEFRAME_MN1",
}

# Timeframes the platform currently serves over the API. Extensible: extend
# the upstream whitelist (timeframes.py) + this mapping; defaults per mode
# stay configuration data (MODE_TIMEFRAME_PROFILES), never hard-coded calls.
PLATFORM_MT5_TIMEFRAMES: tuple[str, ...] = tuple(SUPPORTED_TIMEFRAMES)


def resolve_mt5_timeframe(mt5_module: Any, timeframe: str) -> int:
    """Resolve a canonical timeframe code to the MT5 constant value.

    Raises ``UnsupportedTimeframeError`` for codes outside the platform
    whitelist or when the installed MetaTrader5 package does not define the
    constant (version drift).
    """

    if timeframe not in PLATFORM_MT5_TIMEFRAMES:
        raise UnsupportedTimeframeError(
            f"Timeframe '{timeframe}' is not enabled for MT5 retrieval. "
            f"Enabled: {', '.join(PLATFORM_MT5_TIMEFRAMES)}"
        )
    constant_name = MT5_TIMEFRAME_CONSTANTS.get(timeframe)
    if constant_name is None:
        raise UnsupportedTimeframeError(
            f"Timeframe '{timeframe}' has no MT5 mapping configured."
        )
    value = getattr(mt5_module, constant_name, None)
    if value is None:
        raise UnsupportedTimeframeError(
            f"The installed MetaTrader5 package does not define {constant_name}."
        )
    return int(value)


def default_timeframes_for_mode(mode: str) -> list[str]:
    """Mode defaults (Forex ladder / Binary Options M1) — configuration data."""

    try:
        return list(MODE_TIMEFRAME_PROFILES[mode])
    except KeyError as exc:
        raise UnsupportedTimeframeError(
            f"Unknown mode '{mode}'."
        ) from exc
