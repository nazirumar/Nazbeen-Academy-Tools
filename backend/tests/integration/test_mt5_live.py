"""OPTIONAL live-terminal test — NEVER runs in CI or normal suites.

Run this ON a Windows machine (or Wine) where:
  1. the MetaTrader 5 terminal is installed, running and logged in;
  2. the MetaTrader5 Python package is installed:
        pip install -r requirements-mt5.txt
  3. the backend environment is configured (DATABASE_URL is required by
     Settings; the DB itself is not needed for these checks).

Procedure:
    set RUN_MT5_LIVE=1            (Windows cmd)
    set MT5_ENABLED=true
    python -m pytest tests/integration/test_mt5_live.py -v -m live

Every test below is skipped unless RUN_MT5_LIVE=1, so a normal
`pytest tests/` run NEVER claims a real MT5 connection was tested.
"""

from __future__ import annotations

import os

import pytest

live = pytest.mark.skipif(
    os.getenv("RUN_MT5_LIVE") != "1",
    reason="Live MetaTrader 5 terminal test — set RUN_MT5_LIVE=1 on a Windows "
    "host with a running, logged-in MT5 terminal (see module docstring).",
)

pytestmark = [pytest.mark.live]


@live
class TestLiveTerminal:
    async def test_connects_to_real_terminal(self) -> None:
        from app.core.config import get_settings
        from app.domains.market_data.mt5_adapter import get_mt5_adapter, reset_mt5_adapter

        reset_mt5_adapter()
        adapter = get_mt5_adapter(get_settings())
        try:
            info = await adapter.run(
                adapter.sync_check, timeout=adapter.connect_budget_seconds()
            )
            assert info["terminal"]["name"], "terminal_info() returned no name"
        finally:
            reset_mt5_adapter()

    async def test_fetches_real_candles(self) -> None:
        from app.core.config import get_settings
        from app.domains.market_data.mt5_provider import MT5Provider

        provider = MT5Provider(get_settings())
        series = await provider.get_candles("EURUSD", "M1", 30)
        assert series.count > 0, "terminal returned no EURUSD M1 candles"
        assert series.data_provenance == "mt5_live"
        # Data-quality gate ran: every candle is internally consistent.
        for candle in series.candles:
            assert candle.high >= max(candle.open, candle.close, candle.low)
            assert candle.low <= min(candle.open, candle.close, candle.high)

    async def test_real_symbol_list(self) -> None:
        from app.core.config import get_settings
        from app.domains.market_data.mt5_provider import MT5Provider

        provider = MT5Provider(get_settings())
        payload = await provider.get_symbols(search="EURUSD*", limit=10)
        assert payload["count"] >= 1
