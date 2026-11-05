"""Integration tests: health & readiness endpoints (real PostgreSQL)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class TestLiveness:
    async def test_live_ok(self, app_client) -> None:
        res = await app_client.get("/health/live")
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert body["data"]["status"] == "live"
        assert body["meta"]["request_id"]
        assert body["meta"]["api_version"] == "v1"


class TestReadiness:
    @pytest.mark.parametrize(
        "app_client", [{"MT5_ENABLED": "false"}], indirect=True
    )
    async def test_ready_with_migrated_db(self, app_client) -> None:
        res = await app_client.get("/health/ready")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["status"] == "ready"
        assert data["checks"]["database"]["status"] == "up"
        # MT5 is not connected in tests — reported honestly, not fatal.
        md = data["checks"]["market_data"]
        assert md["connected"] is False
        assert md["blocks_readiness"] is False
        assert (
            "Phase 2" in md["detail"]
            or "MetaTrader" in md["detail"]
            or "disabled" in md["detail"]
        )

    async def test_db_probe_reports_migration_state(self, app_client) -> None:
        res = await app_client.get("/health/db")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["status"] == "up"
        assert data["migrated"] is True


class TestEnvelopeConvention:
    async def test_unknown_route_returns_envelope_404(self, app_client) -> None:
        res = await app_client.get("/api/v1/definitely-not-here")
        assert res.status_code == 404
        body = res.json()
        assert body["success"] is False
        assert body["data"] is None
        assert body["error"]["code"] == "NOT_FOUND"
        assert body["meta"]["request_id"]

    async def test_validation_error_returns_details(self, app_client) -> None:
        res = await app_client.post("/api/v1/strategies", json={"name": ""})
        assert res.status_code == 422
        body = res.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert body["error"]["details"]

    async def test_request_id_header_echoed(self, app_client) -> None:
        res = await app_client.get(
            "/health/live", headers={"X-Request-ID": "test-correlation-id"}
        )
        assert res.headers["X-Request-ID"] == "test-correlation-id"

    async def test_root_service_info(self, app_client) -> None:
        res = await app_client.get("/")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["api_prefix"] == "/api/v1"


class TestMeta:
    async def test_meta_whitelists(self, app_client) -> None:
        res = await app_client.get("/api/v1/meta")
        assert res.status_code == 200
        data = res.json()["data"]
        modes = {m["name"] for m in data["modes"]}
        assert modes == {"forex", "binary_options"}
        forex = next(m for m in data["modes"] if m["name"] == "forex")
        assert forex["default_timeframes"] == ["D1", "H4", "H1", "M15", "M5", "M1"]
        bo = next(m for m in data["modes"] if m["name"] == "binary_options")
        assert bo["default_timeframes"] == ["M1"]
        rf = data["rule_format"]
        # Phase 3: the registry exposes the full supported rule-type set.
        # Phase 4 promoted mss_choch/bos/liquidity_sweep/fvg to operational.
        assert "price_level" in rf["condition_types"]
        assert "moving_average" in rf["condition_types"]
        assert "mss_choch" in rf["condition_types"]
        assert "order_block" not in rf["condition_types"]  # still planned
        # Phase 4: fine-grained regime names joined the applicability whitelist.
        for regime in ("any", "trending", "ranging", "uptrend", "downtrend", "range", "breakout", "transition"):
            assert regime in rf["market_regimes"]
        assert rf["schema_version"] == "2.0"
        assert "1.0" in rf["schema_versions"]
        assert "both" in rf["directions"]
        assert set(rf["operators"]) == {"above", "below", "crosses_above", "crosses_below"}
        assert rf["code_execution"] is False
        # Planned rule types are listed but clearly separated.
        assert "order_block" in data["rule_types"]["planned"]
        assert "price_level" in data["rule_types"]["available"]
        assert "mss_choch" in data["rule_types"]["available"]
