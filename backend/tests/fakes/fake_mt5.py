"""A faithful in-memory fake of the ``MetaTrader5`` package for tests.

Mirrors only the surface the adapter uses: initialize/shutdown/last_error/
version/terminal_info/account_info/symbols_get/symbol_info/symbol_select/
copy_rates_from_pos/symbol_info_tick plus the TIMEFRAME_* constants.
Every behaviour that matters in production (init failures, missing symbols,
empty/None rates, disconnected terminal) is toggleable per test.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

# Real MT5 constant values (kept in one place; the FakeMT5 class mirrors them).
_TIMEFRAMES = {
    "TIMEFRAME_M1": 1, "TIMEFRAME_M2": 2, "TIMEFRAME_M3": 3, "TIMEFRAME_M4": 4,
    "TIMEFRAME_M5": 5, "TIMEFRAME_M6": 6, "TIMEFRAME_M10": 10, "TIMEFRAME_M12": 12,
    "TIMEFRAME_M15": 15, "TIMEFRAME_M20": 20, "TIMEFRAME_M30": 30,
    "TIMEFRAME_H1": 16385, "TIMEFRAME_H2": 16386, "TIMEFRAME_H3": 16387,
    "TIMEFRAME_H4": 16388, "TIMEFRAME_H6": 16390, "TIMEFRAME_H8": 16392,
    "TIMEFRAME_H12": 16396, "TIMEFRAME_D1": 16408, "TIMEFRAME_W1": 32769,
    "TIMEFRAME_MN1": 49153,
}


def make_candle(time: int, open_: float, close: float, high: float | None = None,
                low: float | None = None, volume: int = 100) -> dict[str, Any]:
    return {
        "time": time,
        "open": open_,
        "high": high if high is not None else max(open_, close),
        "low": low if low is not None else min(open_, close),
        "close": close,
        "tick_volume": volume,
        "spread": 10,
        "real_volume": 0,
    }


def candles_until(base_epoch: int, count: int, step: int = 60,
                  start: float = 1.10) -> list[dict[str, Any]]:
    """`count` candles whose LAST open time is `base_epoch` (M1 grid)."""

    out = []
    price = start
    first = base_epoch - (count - 1) * step
    for i in range(count):
        t = first + i * step
        open_ = round(price, 5)
        close = round(price + 0.0002, 5)
        out.append(make_candle(t, open_, close, high=round(close + 0.0001, 5),
                               low=round(open_ - 0.0001, 5)))
        price = close
    return out


class FakeMT5:
    """Configurable fake of the MetaTrader5 module (constants included)."""

    # TIMEFRAME_* constants exposed like the real package (getattr-compatible).
    TIMEFRAME_M1: int = 1
    TIMEFRAME_M2: int = 2
    TIMEFRAME_M3: int = 3
    TIMEFRAME_M4: int = 4
    TIMEFRAME_M5: int = 5
    TIMEFRAME_M6: int = 6
    TIMEFRAME_M10: int = 10
    TIMEFRAME_M12: int = 12
    TIMEFRAME_M15: int = 15
    TIMEFRAME_M20: int = 20
    TIMEFRAME_M30: int = 30
    TIMEFRAME_H1: int = 16385
    TIMEFRAME_H2: int = 16386
    TIMEFRAME_H3: int = 16387
    TIMEFRAME_H4: int = 16388
    TIMEFRAME_H6: int = 16390
    TIMEFRAME_H8: int = 16392
    TIMEFRAME_H12: int = 16396
    TIMEFRAME_D1: int = 16408
    TIMEFRAME_W1: int = 32769
    TIMEFRAME_MN1: int = 49153

    def __init__(self) -> None:
        self.initialized = False
        self.initialize_calls: list[dict[str, Any]] = []
        self.shutdown_calls = 0
        self.initialize_result = True
        self.terminal_info_result: Any = SimpleNamespace(
            name="MetaTrader 5 Fake Terminal",
            build=4400,
            connected=True,
            trade_allowed=True,
        )
        self.terminal_info_raises: Exception | None = None
        self.account_info_result: Any = SimpleNamespace(
            login=12345678,
            server="TestServer-Demo",
            company="Fake Broker Ltd",
            currency="USD",
            leverage=100,
            trade_allowed=False,
        )
        self.package_version: Any = (5, 0, 45)
        # symbol -> SimpleNamespace(symbol metadata)
        self.symbols: dict[str, Any] = {
            "EURUSD": SimpleNamespace(
                name="EURUSD", description="Euro vs US Dollar", path="Forex\\EURUSD",
                visible=True, selectable=True, trade_mode=4, digits=5, point=0.00001,
                spread=10, tick_value=1.0, tick_size=0.00001, contract_size=100000,
                volume_min=0.01, volume_max=500.0, volume_step=0.01,
                currency_profit="USD", currency_base="EUR", spread_float=True,
                stops_level=0, freeze_level=0, trade_calc_mode=0, filling_mode=1,
                trade_exemode=1, session_deals=0, chart_mode=0, currency_margin="EUR",
            ),
            "XAUUSD": SimpleNamespace(
                name="XAUUSD", description="Gold", path="Metals\\XAUUSD",
                visible=True, selectable=True, trade_mode=4, digits=2, point=0.01,
                spread=25, tick_value=1.0, tick_size=0.01, contract_size=100,
                volume_min=0.01, volume_max=50.0, volume_step=0.01,
                currency_profit="USD", currency_base="XAU", spread_float=False,
                stops_level=0, freeze_level=0, trade_calc_mode=0, filling_mode=1,
                trade_exemode=1, session_deals=0, chart_mode=0, currency_margin="XAU",
            ),
        }
        # (symbol, timeframe) -> list of candle dicts (the "terminal history")
        self.rates: dict[tuple[str, int], list[dict[str, Any]]] = {}
        self.rates_none_symbols: set[str] = set()
        self.rates_empty_symbols: set[str] = set()
        # symbol -> SimpleNamespace tick
        self.ticks: dict[str, Any] = {
            "EURUSD": SimpleNamespace(time=1700000060, time_msc=1700000060123,
                                      bid=1.09123, ask=1.09133, last=0.0, volume=0,
                                      flags=6),
        }
        self.tick_none_symbols: set[str] = set()
        self.selectable_after_select: set[str] = set()
        self.symbols_get_raises: Exception | None = None

    # -- lifecycle ------------------------------------------------------------
    def initialize(self, **kwargs: Any) -> bool:
        self.initialize_calls.append(kwargs)
        self.initialized = bool(self.initialize_result)
        return self.initialized

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.initialized = False

    def last_error(self) -> tuple[int, str]:
        if self.initialized:
            return (0, "no error")
        return (4001, "terminal not initialized")

    def version(self) -> tuple[Any, ...]:
        return self.package_version

    def terminal_info(self) -> Any:
        if self.terminal_info_raises:
            raise self.terminal_info_raises
        return self.terminal_info_result if self.initialized else None

    def account_info(self) -> Any:
        return self.account_info_result if self.initialized else None

    # -- symbols ---------------------------------------------------------------
    def symbols_get(self, group: str | None = None) -> tuple[Any, ...] | None:
        if self.symbols_get_raises:
            raise self.symbols_get_raises
        items = list(self.symbols.values())
        if group:
            needle = group.strip("*").lower()
            items = [s for s in items if needle in s.name.lower()]
        return tuple(items)

    def symbol_info(self, symbol: str) -> Any:
        return self.symbols.get(symbol)

    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        if symbol in self.symbols:
            return True
        if symbol in self.selectable_after_select:
            self.symbols[symbol] = SimpleNamespace(
                name=symbol, description=f"late-selected {symbol}", path="",
                visible=True, selectable=True, trade_mode=4, digits=5,
                point=0.00001, spread=15, tick_value=1.0, tick_size=0.00001,
                contract_size=100000, volume_min=0.01, volume_max=100.0,
                volume_step=0.01, currency_profit="USD", currency_base=symbol[:3],
                spread_float=True, stops_level=0, freeze_level=0,
                trade_calc_mode=0, filling_mode=1, trade_exemode=1,
                session_deals=0, chart_mode=0, currency_margin=symbol[:3],
            )
            return True
        return False

    # -- data -------------------------------------------------------------------
    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int):
        """MT5 semantics: pos 0 = NEWEST candle, counting BACK in history."""
        if symbol in self.rates_none_symbols:
            return None
        series = self.rates.get((symbol, timeframe), [])
        if symbol in self.rates_empty_symbols or not series:
            # Real MT5 returns an empty result (not an error) when the symbol
            # exists but has no data at this depth.
            return []
        end = len(series) - start  # exclusive
        begin = max(end - count, 0)
        if end <= 0:
            return []
        return series[begin:end]

    def symbol_info_tick(self, symbol: str) -> Any:
        if symbol in self.tick_none_symbols:
            return None
        return self.ticks.get(symbol)
