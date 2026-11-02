"""Unit tests: the low-level MT5 adapter, against a fake MetaTrader5 module.

Covers connection lifecycle, bounded reconnects, cooldown, timeouts, symbol
handling, data errors and credential hygiene — no live terminal, no DB.
"""

from __future__ import annotations

import asyncio

import pytest

from app.domains.market_data import mt5_adapter as adapter_module
from app.domains.market_data.mt5_adapter import (
    MT5Adapter,
    MT5ConnectionError,
    MT5DataError,
    MT5NotAvailableError,
    MT5SymbolError,
    MT5TimeoutError,
    get_mt5_adapter,
    reset_mt5_adapter,
)
from tests.fakes.fake_mt5 import FakeMT5, candles_until


pytestmark = pytest.mark.usefixtures("env_settings")


def make_adapter(fake: FakeMT5, **kwargs) -> MT5Adapter:
    from app.core.config import get_settings

    settings = get_settings()
    defaults = dict(mt5_module=fake, sleep=lambda _s: None)
    defaults.update(kwargs)
    return MT5Adapter(settings, **defaults)


class TestConnectionLifecycle:
    async def test_successful_check_connects_and_reports(self, fake_mt5) -> None:
        adapter = make_adapter(fake_mt5)
        info = await adapter.run(adapter.sync_check)
        assert fake_mt5.initialized is True
        assert fake_mt5.shutdown_calls == 0
        assert info["terminal"]["name"] == "MetaTrader 5 Fake Terminal"
        assert info["terminal"]["broker_connected"] is True
        assert info["account"]["accessible"] is True
        assert info["account"]["server"] == "TestServer-Demo"
        assert adapter._package_version == "5.0.45"

    async def test_initialize_failure_raises_after_bounded_retries(self) -> None:
        fake = FakeMT5()
        fake.initialize_result = False
        sleeps: list[float] = []
        from app.core.config import get_settings

        adapter = MT5Adapter(
            get_settings(), mt5_module=fake,
            sleep=sleeps.append, clock=lambda: 0.0,
        )
        adapter._connect_cooldown_seconds = 0.0
        with pytest.raises(MT5ConnectionError, match="3 attempt"):
            await adapter.run(adapter.sync_check)
        assert len(fake.initialize_calls) == 3  # bounded, not infinite
        assert len(sleeps) == 2  # backoff between attempts
        assert adapter._last_error is not None

    async def test_reconnect_cooldown_fails_fast(self) -> None:
        fake = FakeMT5()
        fake.initialize_result = False
        clock = {"t": 0.0}
        from app.core.config import get_settings

        adapter = MT5Adapter(
            get_settings(), mt5_module=fake, sleep=lambda _s: None, clock=lambda: clock["t"]
        )
        with pytest.raises(MT5ConnectionError):
            await adapter.run(adapter.sync_check)
        calls_after_first = len(fake.initialize_calls)
        clock["t"] = 1.0  # inside the 30s cooldown
        with pytest.raises(MT5ConnectionError, match="cooldown"):
            await adapter.run(adapter.sync_check)
        assert len(fake.initialize_calls) == calls_after_first  # no new attempts
        clock["t"] = 60.0  # cooldown elapsed -> retry allowed
        with pytest.raises(MT5ConnectionError):
            await adapter.run(adapter.sync_check)
        assert len(fake.initialize_calls) > calls_after_first

    async def test_terminal_info_none_after_init_is_connection_error(self) -> None:
        fake = FakeMT5()
        fake.terminal_info_result = None
        adapter = make_adapter(fake)
        adapter._connect_cooldown_seconds = 0.0
        with pytest.raises(MT5ConnectionError):
            await adapter.run(adapter.sync_check)
        assert fake.shutdown_calls >= 1  # half-open session released

    async def test_missing_package_raises_not_available(self, monkeypatch) -> None:
        monkeypatch.setattr(adapter_module, "load_mt5_module", lambda: None)
        from app.core.config import get_settings

        adapter = MT5Adapter(get_settings(), sleep=lambda _s: None)
        with pytest.raises(MT5NotAvailableError, match="not installed"):
            await adapter.run(adapter.sync_check)

    async def test_stale_session_reconnects(self, fake_mt5) -> None:
        adapter = make_adapter(fake_mt5)
        await adapter.run(adapter.sync_check)
        assert fake_mt5.initialized is True
        fake_mt5.initialized = False  # terminal died silently
        fake_mt5.shutdown_calls = 0
        await adapter.run(adapter.sync_symbols)
        assert fake_mt5.initialized is True  # re-initialized transparently

    async def test_shutdown_releases_resources(self, fake_mt5) -> None:
        adapter = make_adapter(fake_mt5)
        await adapter.run(adapter.sync_check)
        adapter.sync_shutdown()
        assert fake_mt5.shutdown_calls == 1
        assert adapter.snapshot().initialized is False
        adapter.sync_shutdown()  # idempotent
        assert fake_mt5.shutdown_calls == 1

    async def test_graceful_close_stops_executor(self, fake_mt5) -> None:
        adapter = make_adapter(fake_mt5)
        await adapter.run(adapter.sync_check)
        adapter.sync_close()
        assert adapter._executor._shutdown is True

    async def test_disabled_by_configuration(self, monkeypatch) -> None:
        fake = FakeMT5()
        from app.core.config import get_settings

        settings = get_settings().model_copy(update={"mt5_enabled": False})
        adapter = MT5Adapter(settings, mt5_module=fake, sleep=lambda _s: None)
        with pytest.raises(adapter_module.MT5DisabledError):
            await adapter.run(adapter.sync_check)
        assert fake.initialize_calls == []  # never touched the terminal


class TestDataOperations:
    async def test_candles_happy_path(self, fake_mt5) -> None:
        fake_mt5.rates[("EURUSD", fake_mt5.TIMEFRAME_M1)] = candles_until(1_700_000_000, 10)
        adapter = make_adapter(fake_mt5)
        rows = await adapter.run(adapter.sync_candles, "EURUSD", "M1", 10)
        assert len(rows) == 10
        assert rows[0]["time"] < rows[-1]["time"]
        assert isinstance(rows[0]["open"], float)

    async def test_unknown_symbol_raises_symbol_error(self, fake_mt5) -> None:
        adapter = make_adapter(fake_mt5)
        with pytest.raises(MT5SymbolError, match="AAPL"):
            await adapter.run(adapter.sync_candles, "AAPL", "M1", 5)

    async def test_symbol_selectable_after_select(self, fake_mt5) -> None:
        fake_mt5.selectable_after_select.add("GBPJPY")
        adapter = make_adapter(fake_mt5)
        info = await adapter.run(adapter.sync_symbol_info, "GBPJPY")
        assert info["name"] == "GBPJPY"

    async def test_rates_none_is_data_error(self, fake_mt5) -> None:
        fake_mt5.rates_none_symbols.add("EURUSD")
        adapter = make_adapter(fake_mt5)
        with pytest.raises(MT5DataError, match="no rate history"):
            await adapter.run(adapter.sync_candles, "EURUSD", "M1", 5)

    async def test_empty_rates_is_data_error(self, fake_mt5) -> None:
        fake_mt5.rates_empty_symbols.add("EURUSD")
        adapter = make_adapter(fake_mt5)
        with pytest.raises(MT5DataError, match="EMPTY"):
            await adapter.run(adapter.sync_candles, "EURUSD", "M1", 5)

    async def test_symbols_listing(self, fake_mt5) -> None:
        adapter = make_adapter(fake_mt5)
        symbols = await adapter.run(adapter.sync_symbols)
        names = {s["name"] for s in symbols}
        assert {"EURUSD", "XAUUSD"} <= names
        assert all("password" not in s for s in symbols)

    async def test_tick_payload(self, fake_mt5) -> None:
        adapter = make_adapter(fake_mt5)
        tick = await adapter.run(adapter.sync_tick, "EURUSD")
        assert tick["bid"] == 1.09123
        assert tick["ask"] == 1.09133
        assert tick["time_msc"] == 1700000060123

    async def test_tick_missing_is_data_error(self, fake_mt5) -> None:
        fake_mt5.tick_none_symbols.add("EURUSD")
        adapter = make_adapter(fake_mt5)
        with pytest.raises(MT5DataError, match="No tick"):
            await adapter.run(adapter.sync_tick, "EURUSD")


class TestTimeouts:
    async def test_blocking_call_times_out_and_marks_disconnected(self, fake_mt5) -> None:
        import time as _time

        original_terminal_info = fake_mt5.terminal_info

        def slow_terminal_info():
            _time.sleep(0.2)
            return original_terminal_info()

        fake_mt5.terminal_info = slow_terminal_info  # type: ignore[method-assign]
        from app.core.config import get_settings

        settings = get_settings().model_copy(update={"mt5_timeout_seconds": 0.05})
        adapter = MT5Adapter(settings, mt5_module=fake_mt5, sleep=lambda _s: None)
        with pytest.raises(MT5TimeoutError):
            await adapter.run(adapter.sync_check)
        snapshot = adapter.snapshot()
        # The wedged session is not trusted: next call re-initializes.
        assert snapshot.initialized is False
        assert snapshot.last_error is not None

    async def test_run_respects_custom_timeout(self, fake_mt5) -> None:
        import time as _time

        def sleepy_symbols():
            _time.sleep(0.15)
            return ()

        fake_mt5.symbols_get = sleepy_symbols  # type: ignore[method-assign]
        adapter = make_adapter(fake_mt5)
        with pytest.raises(MT5TimeoutError):
            await adapter.run(adapter.sync_symbols, timeout=0.05)


class TestCredentialHygiene:
    async def test_snapshots_never_contain_credentials(self, fake_mt5) -> None:
        from app.core.config import get_settings

        settings = get_settings().model_copy(
            update={
                "mt5_login": 123456,
                "mt5_password": "super-secret-password",
                "mt5_server": "SecretServer",
            }
        )
        adapter = MT5Adapter(settings, mt5_module=fake_mt5, sleep=lambda _s: None)
        await adapter.run(adapter.sync_check)
        for payload in (adapter.snapshot().to_dict(), await adapter.run(adapter.sync_check)):
            blob = repr(payload).lower()
            assert "super-secret-password" not in blob
            assert "password" not in blob
        # Credentials were passed to initialize (MT5 API contract) but the
        # SAFE snapshots and terminal/account payloads never contain them.
        assert fake_mt5.initialize_calls[-1]["password"] == "super-secret-password"
        safe_payloads = [
            adapter.snapshot().to_dict(),
            adapter.snapshot().terminal.to_dict(),
            adapter.snapshot().account.to_dict(),
        ]
        for payload in safe_payloads:
            blob = repr(payload).lower()
            assert "super-secret-password" not in blob
            assert "password" not in blob

    def test_settings_reject_partial_credentials(self) -> None:
        from app.core.config import ConfigError, Settings

        with pytest.raises(ConfigError, match="MT5_LOGIN"):
            Settings(
                database_url="postgresql+asyncpg://u:p@localhost:5432/db",
                mt5_login=123456,
            )
        with pytest.raises(ConfigError, match="without MT5_LOGIN"):
            Settings(
                database_url="postgresql+asyncpg://u:p@localhost:5432/db",
                mt5_server="SomeServer",
            )


class TestSingleton:
    def test_get_mt5_adapter_is_singleton(self, env_settings) -> None:
        reset_mt5_adapter()
        first = get_mt5_adapter(env_settings)
        second = get_mt5_adapter(env_settings)
        assert first is second
        reset_mt5_adapter()
        third = get_mt5_adapter(env_settings)
        assert third is not first
        reset_mt5_adapter()


class TestRatesConversion:
    def test_numpy_like_records_converted(self) -> None:
        fake = FakeMT5()
        fake.rates[("EURUSD", fake.TIMEFRAME_M1)] = candles_until(1_700_000_000, 3)
        raw = fake.copy_rates_from_pos("EURUSD", fake.TIMEFRAME_M1, 0, 3)
        rows = adapter_module.rates_to_dicts(raw)
        assert len(rows) == 3
        assert all(isinstance(r["time"], int) for r in rows)
        assert all(isinstance(r["close"], float) for r in rows)

    def test_malformed_record_fields_become_none(self) -> None:
        class JunkRow:
            def __getitem__(self, key: str):
                raise KeyError(key)

        rows = adapter_module.rates_to_dicts([JunkRow()])
        assert rows[0]["time"] is None
        assert rows[0]["open"] is None
