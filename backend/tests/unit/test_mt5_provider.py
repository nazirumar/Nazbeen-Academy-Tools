"""Unit tests: MT5Provider composition (adapter + validation + errors).

Uses the fake MT5 module and NO database: the cache layer degrades to
warnings, proving live serving never depends on persistence being up.
"""

from __future__ import annotations

import time

import pytest

from app.core.errors import (
    MarketDataError,
    MarketDataUnavailableError,
    SymbolNotFoundError,
    ValidationAppError,
)
from app.domains.market_data.mt5_provider import MT5Provider
from tests.fakes.fake_mt5 import candles_until


pytestmark = pytest.mark.usefixtures("fake_mt5")

# M1-aligned "now" so forming-candle math is deterministic.
NOW_ALIGNED = int(time.time()) // 60 * 60


@pytest.fixture
def provider(env_settings) -> MT5Provider:
    """Hermetic provider: cache disabled so no database is ever touched."""

    return MT5Provider(
        env_settings.model_copy(update={"mt5_cache_enabled": False})
    )


def seed_history(fake, symbol: str = "EURUSD", count: int = 10,
                 end: int | None = None) -> None:
    # Align at CALL time, not import time: a long suite can roll the
    # minute between import and the provider call, which would flip the
    # last candle's forming flag and make the test wall-clock flaky.
    fake.rates[(symbol, fake.TIMEFRAME_M1)] = candles_until(
        end if end is not None else int(time.time()) // 60 * 60, count
    )


class TestInputValidation:
    async def test_empty_symbol_rejected(self, provider) -> None:
        with pytest.raises(ValidationAppError, match="empty"):
            await provider.get_candles("", "M1", 10)

    async def test_symbol_characters_rejected(self, provider) -> None:
        with pytest.raises(ValidationAppError, match="invalid characters"):
            await provider.get_candles("EUR$USD;rm -rf", "M1", 10)

    async def test_symbol_uppercased(self, provider, fake_mt5) -> None:
        seed_history(fake_mt5)
        series = await provider.get_candles("eurusd", "M1", 5)
        assert series.symbol == "EURUSD"

    async def test_unsupported_timeframe_rejected(self, provider) -> None:
        with pytest.raises(ValidationAppError, match="not supported"):
            await provider.get_candles("EURUSD", "H3", 10)

    async def test_lowercase_timeframe_accepted(self, provider, fake_mt5) -> None:
        seed_history(fake_mt5)
        series = await provider.get_candles("EURUSD", "m1", 5)
        assert series.timeframe == "M1"

    async def test_limit_zero_rejected(self, provider) -> None:
        with pytest.raises(ValidationAppError, match=">= 1"):
            await provider.get_candles("EURUSD", "M1", 0)

    async def test_limit_above_maximum_rejected(self, env_settings) -> None:
        provider = MT5Provider(
            env_settings.model_copy(update={"mt5_max_candles": 100})
        )
        with pytest.raises(ValidationAppError, match="maximum"):
            await provider.get_candles("EURUSD", "M1", 101)

    async def test_limit_at_maximum_accepted(self, env_settings, fake_mt5) -> None:
        provider = MT5Provider(
            env_settings.model_copy(
                update={"mt5_max_candles": 100, "mt5_cache_enabled": False}
            )
        )
        seed_history(fake_mt5, count=10)
        series = await provider.get_candles("EURUSD", "M1", 100)
        assert series.requested_count == 100
        assert series.data_status == "insufficient"  # honest about short history


class TestUnavailableModes:
    async def test_disabled_mode_raises_unavailable(self, env_settings) -> None:
        provider = MT5Provider(env_settings.model_copy(update={"mt5_enabled": False}))
        with pytest.raises(MarketDataUnavailableError, match="disabled"):
            await provider.get_candles("EURUSD", "M1", 10)

    async def test_missing_package_raises_unavailable(self, env_settings, monkeypatch) -> None:
        from app.domains.market_data import mt5_adapter as adapter_module

        monkeypatch.setattr(adapter_module, "load_mt5_module", lambda: None)
        # Install an adapter whose loader resolves to None (no package).
        adapter = adapter_module.MT5Adapter(env_settings, sleep=lambda _s: None)
        monkeypatch.setattr(adapter_module, "_adapter", adapter)
        provider = MT5Provider(env_settings)
        with pytest.raises(MarketDataUnavailableError, match="not installed"):
            await provider.get_candles("EURUSD", "M1", 10)
        adapter.sync_close()

    async def test_connection_failure_maps_to_503(self, env_settings) -> None:
        from app.domains.market_data import mt5_adapter as adapter_module

        fake_broken = object()  # behaves like a broken MetaTrader5 module
        adapter = adapter_module.MT5Adapter(
            env_settings, mt5_module=fake_broken, sleep=lambda _s: None
        )
        adapter._connect_cooldown_seconds = 0.0
        monkeypatch_singleton(adapter_module, adapter)
        provider = MT5Provider(env_settings)
        with pytest.raises(MarketDataUnavailableError):
            await provider.get_candles("EURUSD", "M1", 10)
        adapter.sync_close()


def monkeypatch_singleton(module, adapter) -> None:
    module._adapter = adapter


class TestCandleResponses:
    async def test_series_shape_and_provenance(self, provider, fake_mt5) -> None:
        seed_history(fake_mt5, count=10)
        series = await provider.get_candles("EURUSD", "M1", 10)
        assert series.data_provenance == "mt5_live"
        assert series.timezone == "MT5_SERVER"
        assert series.timezone_note
        assert series.count == 10
        assert series.candles[-1].forming is True  # last open == aligned grid
        assert series.first_time == series.candles[0].time
        assert series.last_time == series.candles[-1].time
        assert series.newest_closed_time == series.candles[-2].time
        assert series.warnings  # forming-candle info present

    async def test_unknown_symbol_maps_to_404(self, provider, fake_mt5) -> None:
        with pytest.raises(SymbolNotFoundError):
            await provider.get_candles("NOPEUSD", "M1", 5)

    async def test_none_rates_map_to_market_data_error(self, provider, fake_mt5) -> None:
        fake_mt5.rates_none_symbols.add("EURUSD")
        with pytest.raises(MarketDataError, match="no usable candles|rate history"):
            await provider.get_candles("EURUSD", "M1", 5)

    async def test_empty_rates_map_to_market_data_error(self, provider, fake_mt5) -> None:
        fake_mt5.rates_empty_symbols.add("EURUSD")
        with pytest.raises(MarketDataError):
            await provider.get_candles("EURUSD", "M1", 5)

    async def test_cache_failure_degrades_to_warning(
        self, env_settings, fake_mt5, monkeypatch
    ) -> None:
        """DB down must NOT block live data — a warning is surfaced instead."""

        # A guaranteed-dead URL (closed port) proves the cache layer degrades
        # independently of whatever else is running on the machine.
        dead_url_settings = env_settings.model_copy(
            update={
                "mt5_cache_enabled": True,
                "database_url": "postgresql+asyncpg://u:p@127.0.0.1:59999/none",
            }
        )
        provider = MT5Provider(dead_url_settings)
        # The store resolves its engine via app.db.session.get_settings; patch
        # it so the dead URL is the one actually used by the cache layer.
        import app.db.session as session_module
        from app.db.session import dispose_engine

        await dispose_engine()
        monkeypatch.setattr(session_module, "get_settings", lambda: dead_url_settings)
        seed_history(fake_mt5, count=8)
        try:
            series = await provider.get_candles("EURUSD", "M1", 8)
        finally:
            await dispose_engine()
        assert series.data_provenance == "mt5_live"
        codes = {w["code"] for w in series.warnings}
        assert "CACHE_WRITE_FAILED" in codes

    async def test_status_is_cheap_and_honest(self, provider, fake_mt5) -> None:
        status = provider.status()
        assert status.provider == "mt5"
        assert status.connected is False  # nothing fetched yet (lazy connect)
        seed_history(fake_mt5, count=5)
        await provider.get_candles("EURUSD", "M1", 5)
        assert provider.status().connected is True  # snapshot updated by use


class TestTickAndSymbols:
    async def test_tick_payload(self, provider, fake_mt5) -> None:
        tick = await provider.get_tick("EURUSD")
        assert tick["symbol"] == "EURUSD"
        assert tick["bid"] == 1.09123
        assert tick["ask"] == 1.09133
        assert tick["spread"]["points"] == 10
        assert tick["symbol_digits"] == 5
        assert tick["time_utc"].endswith("+00:00")

    async def test_tick_unknown_symbol_404(self, provider, fake_mt5) -> None:
        with pytest.raises(SymbolNotFoundError):
            await provider.get_tick("MISSING")

    async def test_symbols_list(self, provider, fake_mt5) -> None:
        payload = await provider.get_symbols(search=None, limit=10)
        assert payload["count"] >= 2
        assert payload["truncated"] is False
        names = {s["name"] for s in payload["symbols"]}
        assert "EURUSD" in names

    async def test_symbols_search_filter(self, provider, fake_mt5) -> None:
        payload = await provider.get_symbols(search="XAU*", limit=10)
        assert [s["name"] for s in payload["symbols"]] == ["XAUUSD"]


class TestDiagnostics:
    async def test_diagnostics_probe_runs_when_connected(self, provider, fake_mt5) -> None:
        seed_history(fake_mt5, count=10, end=1_700_000_000)
        diag = await provider.get_diagnostics(probe_symbol="EURUSD")
        assert diag["status"]["connected"] is True
        assert diag["probe"]["count"] == 10
        assert diag["probe"]["data_provenance"] == "mt5_live"
        assert diag["capabilities"]["order_execution"] is False
        assert "M1" in diag["capabilities"]["timeframes"]

    async def test_diagnostics_probe_skipped_when_disconnected(self, fake_mt5) -> None:
        fake_mt5.initialize_result = False
        from app.core.config import get_settings

        provider = MT5Provider(get_settings())
        diag = await provider.get_diagnostics()
        assert diag["probe"]["skipped"] is True
        assert diag["status"]["connected"] is False
