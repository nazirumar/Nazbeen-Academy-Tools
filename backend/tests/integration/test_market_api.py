"""Integration tests: market-data & MT5 API endpoints (real PostgreSQL).

The MetaTrader5 module is faked (FakeMT5) so no live terminal is needed;
the PostgreSQL side (cache persistence, uniqueness) is REAL.
"""

from __future__ import annotations

import json
import time

import pytest

from tests.fakes.fake_mt5 import candles_until

pytestmark = pytest.mark.integration

# M1-aligned "now" so forming/staleness math matches real time. Aligned at
# CALL time (not import): a long suite can roll the minute between import
# and the request, flipping the last candle's forming flag and making the
# tests wall-clock flaky.
@pytest.fixture
def seeded_history(fake_mt5):
    base = int(time.time()) // 60 * 60
    fake_mt5.rates[("EURUSD", fake_mt5.TIMEFRAME_M1)] = candles_until(
        base, 50
    )
    fake_mt5.rates[("EURUSD", fake_mt5.TIMEFRAME_H1)] = candles_until(
        base - 3 * 3600, 50, step=3600
    )
    return fake_mt5


def candles_payload(res) -> dict:
    body = res.json()
    assert body["success"] is True, body
    return body["data"]


class TestMT5StatusEndpoint:
    async def test_connected_terminal_reported(self, app_client, fake_mt5) -> None:
        res = await app_client.get("/api/v1/mt5/status")
        assert res.status_code == 200
        data = candles_payload(res)
        assert data["provider"] == "mt5"
        assert data["connected"] is True
        assert data["package_available"] is True
        assert data["terminal"]["name"] == "MetaTrader 5 Fake Terminal"
        assert data["terminal"]["build"] == 4400
        assert data["account"]["accessible"] is True
        assert data["account"]["server"] == "TestServer-Demo"
        assert data["configuration"]["max_candles"] >= 1
        assert data["last_success_at"] is not None

    async def test_no_credentials_anywhere_in_payload(self, app_client, fake_mt5) -> None:
        res = await app_client.get("/api/v1/mt5/status")
        blob = json.dumps(res.json()).lower()
        assert "password" not in blob
        assert "secret" not in blob

    async def test_disconnected_terminal_reported_honestly(
        self, app_client, fake_mt5
    ) -> None:
        fake_mt5.initialize_result = False
        res = await app_client.get("/api/v1/mt5/status")
        assert res.status_code == 200
        data = candles_payload(res)
        assert data["connected"] is False
        assert data["last_error"]
        assert data["terminal"] is None or data["terminal"].get("accessible") in (
            None,
            False,
        )

    async def test_package_missing_reported(self, app_client, monkeypatch) -> None:
        """No fake installed AND loader patched to None → honest 200 report."""

        from app.domains.market_data import mt5_adapter as adapter_module
        from app.core.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setattr(adapter_module, "load_mt5_module", lambda: None)
        monkeypatch.setattr(adapter_module, "_adapter", None)
        # The real package is installed on some Windows dev hosts; simulate
        # its absence explicitly so this test asserts the package-missing path.
        monkeypatch.setattr(
            adapter_module.MT5Adapter, "_probe_package", lambda self: False
        )
        res = await app_client.get("/api/v1/mt5/status")
        assert res.status_code == 200
        data = candles_payload(res)
        assert data["package_available"] is False
        assert data["connected"] is False
        assert "not installed" in data["detail"].lower()
        get_settings.cache_clear()


class TestMarketCandles:
    async def test_candles_happy_path(self, app_client, seeded_history) -> None:
        res = await app_client.get(
            "/api/v1/market/candles",
            params={"symbol": "EURUSD", "timeframe": "M1", "limit": 20},
        )
        assert res.status_code == 200
        data = candles_payload(res)
        assert data["symbol"] == "EURUSD"
        assert data["timeframe"] == "M1"
        assert data["count"] == 20
        assert data["requested_count"] == 20
        assert data["data_status"] == "ok"
        assert data["data_provenance"] == "mt5_live"
        assert data["timezone"] == "MT5_SERVER"
        assert data["timezone_note"]
        first, last = data["candles"][0], data["candles"][-1]
        assert first["time"] < last["time"]  # ascending
        assert last["forming"] is True  # newest M1 candle is forming
        assert first["forming"] is False
        assert last["time_utc"].endswith("+00:00")
        for candle in data["candles"]:
            assert candle["high"] >= max(candle["open"], candle["close"], candle["low"])
            assert candle["low"] <= min(candle["open"], candle["close"], candle["high"])

    async def test_second_request_served_from_cache(
        self, app_client, seeded_history
    ) -> None:
        params = {"symbol": "EURUSD", "timeframe": "M1", "limit": 20}
        first = candles_payload(
            await app_client.get("/api/v1/market/candles", params=params)
        )
        assert first["data_provenance"] == "mt5_live"
        second = candles_payload(
            await app_client.get("/api/v1/market/candles", params=params)
        )
        assert second["data_provenance"] == "postgres_cache"
        assert second["count"] == 20
        assert [c["time"] for c in second["candles"]] == [
            c["time"] for c in first["candles"]
        ]

    async def test_closed_only_excludes_forming(self, app_client, seeded_history) -> None:
        res = await app_client.get(
            "/api/v1/market/candles",
            params={
                "symbol": "EURUSD", "timeframe": "M1", "limit": 20, "closed_only": "true"
            },
        )
        data = candles_payload(res)
        assert data["closed_only"] is True
        assert data["includes_forming"] is False
        assert all(c["forming"] is False for c in data["candles"])
        assert any(
            w["code"] == "FORMING_CANDLE_EXCLUDED" for w in data["warnings"]
        )

    async def test_invalid_timeframe_structured_422(self, app_client, seeded_history) -> None:
        res = await app_client.get(
            "/api/v1/market/candles",
            params={"symbol": "EURUSD", "timeframe": "H2", "limit": 10},
        )
        assert res.status_code == 422
        body = res.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert body["error"]["details"][0]["path"] == "timeframe"

    async def test_limit_zero_structured_422(self, app_client, seeded_history) -> None:
        res = await app_client.get(
            "/api/v1/market/candles",
            params={"symbol": "EURUSD", "timeframe": "M1", "limit": 0},
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_limit_above_maximum_structured_422(
        self, app_client, seeded_history, monkeypatch
    ) -> None:
        from app.core.config import get_settings

        monkeypatch.setenv("MT5_MAX_CANDLES", "50")
        get_settings.cache_clear()
        res = await app_client.get(
            "/api/v1/market/candles",
            params={"symbol": "EURUSD", "timeframe": "M1", "limit": 51},
        )
        get_settings.cache_clear()
        assert res.status_code == 422
        body = res.json()
        assert body["error"]["details"][0]["code"] == "maximum"

    async def test_bad_symbol_pattern_422(self, app_client, seeded_history) -> None:
        res = await app_client.get(
            "/api/v1/market/candles",
            params={"symbol": "DROP TABLE users;--", "timeframe": "M1", "limit": 5},
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_unknown_symbol_404(self, app_client, seeded_history) -> None:
        res = await app_client.get(
            "/api/v1/market/candles",
            params={"symbol": "NOPEUSD", "timeframe": "M1", "limit": 5},
        )
        assert res.status_code == 404
        body = res.json()
        assert body["error"]["code"] == "SYMBOL_NOT_FOUND"
        assert "NOPEUSD" in body["error"]["message"]

    async def test_no_history_maps_to_502(self, app_client, seeded_history) -> None:
        seeded_history.rates_none_symbols.add("EURUSD")
        res = await app_client.get(
            "/api/v1/market/candles",
            params={"symbol": "EURUSD", "timeframe": "H1", "limit": 5},
        )
        assert res.status_code == 502
        assert res.json()["error"]["code"] == "MARKET_DATA_ERROR"

    async def test_empty_history_maps_to_502(self, app_client, seeded_history) -> None:
        seeded_history.rates_empty_symbols.add("XAUUSD")
        seeded_history.rates[("XAUUSD", seeded_history.TIMEFRAME_M1)] = []
        res = await app_client.get(
            "/api/v1/market/candles",
            params={"symbol": "XAUUSD", "timeframe": "M1", "limit": 5},
        )
        assert res.status_code == 502
        body = res.json()
        assert body["error"]["code"] == "MARKET_DATA_ERROR"

    async def test_data_quality_warnings_surfaced(
        self, app_client, seeded_history
    ) -> None:
        # Break OHLC on one candle INSIDE the newest-20 window the request
        # returns, and delete another candle somewhere in history (a gap the
        # response will never paper over).
        rates = seeded_history.rates[("EURUSD", seeded_history.TIMEFRAME_M1)]
        rates[35] = {**rates[35], "high": rates[35]["low"] - 0.01}
        invalid_time = rates[35]["time"]
        del rates[10]
        res = await app_client.get(
            "/api/v1/market/candles",
            params={"symbol": "EURUSD", "timeframe": "M1", "limit": 20},
        )
        data = candles_payload(res)
        codes = {w["code"] for w in data["warnings"]}
        assert "INVALID_OHLC" in codes
        assert data["data_status"] == "partial"
        # 20 newest requested, 1 invalid excluded — nothing repaired.
        assert len(data["candles"]) == 19
        returned_times = {c["time"] for c in data["candles"]}
        assert invalid_time not in returned_times  # invalid candle gone


class TestMarketTick:
    async def test_tick_payload(self, app_client, seeded_history) -> None:
        res = await app_client.get("/api/v1/market/tick", params={"symbol": "EURUSD"})
        assert res.status_code == 200
        data = candles_payload(res)
        assert data["bid"] == 1.09123
        assert data["ask"] == 1.09133
        assert data["spread"]["points"] == 10
        assert data["time_utc"].endswith("+00:00")

    async def test_tick_unknown_symbol_404(self, app_client, seeded_history) -> None:
        res = await app_client.get("/api/v1/market/tick", params={"symbol": "ZZZZZZ"})
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "SYMBOL_NOT_FOUND"


class TestMarketSymbols:
    async def test_symbols_listing(self, app_client, seeded_history) -> None:
        res = await app_client.get("/api/v1/market/symbols")
        assert res.status_code == 200
        data = candles_payload(res)
        names = {s["name"] for s in data["symbols"]}
        assert {"EURUSD", "XAUUSD"} <= names
        assert data["truncated"] is False

    async def test_symbols_search(self, app_client, seeded_history) -> None:
        res = await app_client.get(
            "/api/v1/market/symbols", params={"search": "EUR*"}
        )
        data = candles_payload(res)
        assert [s["name"] for s in data["symbols"]] == ["EURUSD"]


class TestMarketDiagnostics:
    async def test_diagnostics_with_connection(self, app_client, seeded_history) -> None:
        res = await app_client.get("/api/v1/market/diagnostics")
        assert res.status_code == 200
        data = candles_payload(res)
        assert data["status"]["connected"] is True
        assert data["probe"]["count"] == 10
        assert data["capabilities"]["order_execution"] is False
        assert set(data["capabilities"]["binary_options_timeframes"]) == {"M1"}

    async def test_diagnostics_without_connection(self, app_client, fake_mt5) -> None:
        fake_mt5.initialize_result = False
        res = await app_client.get("/api/v1/market/diagnostics")
        assert res.status_code == 200
        data = candles_payload(res)
        assert data["status"]["connected"] is False


class TestDisabledMode:
    @pytest.fixture
    def mt5_disabled(self, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.setenv("MT5_ENABLED", "false")
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    async def test_candles_503_when_disabled(
        self, app_client, seeded_history, mt5_disabled
    ) -> None:
        res = await app_client.get(
            "/api/v1/market/candles",
            params={"symbol": "EURUSD", "timeframe": "M1", "limit": 5},
        )
        assert res.status_code == 503
        body = res.json()
        assert body["error"]["code"] == "MARKET_DATA_UNAVAILABLE"
        assert "disabled" in body["error"]["message"].lower()

    async def test_status_reports_disabled(
        self, app_client, seeded_history, mt5_disabled
    ) -> None:
        res = await app_client.get("/api/v1/mt5/status")
        assert res.status_code == 200
        data = candles_payload(res)
        assert data["enabled"] is False
        assert data["connected"] is False
        assert "disabled" in data["detail"].lower()


class TestLegacyStatusEndpoint:
    async def test_market_data_status_live_check(self, app_client, seeded_history) -> None:
        res = await app_client.get("/api/v1/market-data/status")
        assert res.status_code == 200
        data = candles_payload(res)
        assert data["provider"] == "mt5"
        assert data["connected"] is True
        assert data["live_data_enabled"] is True
