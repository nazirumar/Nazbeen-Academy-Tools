"""Integration tests: analysis endpoint (user-supplied candles)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

BASE = 1_700_000_000


def candles(*closes: float, step: int = 60) -> list[dict]:
    return [
        {
            "time": BASE + i * step,
            "open": c - 0.5,
            "high": c + 0.5,
            "low": c - 1.0,
            "close": c,
            "volume": None,
        }
        for i, c in enumerate(closes)
    ]


def bo_rule_set() -> dict:
    return {
        "schema_version": "1.0",
        "mode": "binary_options",
        "timeframes": ["M1"],
        "direction": "bullish",
        "conditions": {
            "logic": "AND",
            "conditions": [
                {
                    "id": "above",
                    "type": "price_level",
                    "field": "close",
                    "operator": "above",
                    "value": 100.0,
                    "timeframe": "M1",
                    "lookback": 5,
                }
            ],
            "groups": [],
        },
    }


class TestEvaluateInline:
    async def test_conditions_met(self, app_client) -> None:
        res = await app_client.post(
            "/api/v1/analysis/evaluate",
            json={
                "rule_set": bo_rule_set(),
                "candles": {"M1": candles(99, 100, 101)},
            },
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["data_provenance"] == "user_supplied_inline"
        assert data["evaluation"]["setup_status"] == "conditions_met"
        leaf = data["evaluation"]["core"]["children"][0]
        assert leaf["satisfied"] is True
        assert leaf["evidence"]["candles_evaluated"] == 3

    async def test_conditions_missing(self, app_client) -> None:
        res = await app_client.post(
            "/api/v1/analysis/evaluate",
            json={
                "rule_set": bo_rule_set(),
                "candles": {"M1": candles(101, 100, 99)},
            },
        )
        assert res.json()["data"]["evaluation"]["setup_status"] == "conditions_missing"

    async def test_empty_candles_rejected(self, app_client) -> None:
        res = await app_client.post(
            "/api/v1/analysis/evaluate",
            json={"rule_set": bo_rule_set(), "candles": {}},
        )
        assert res.status_code == 422
        body = res.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"

    async def test_missing_referenced_timeframe_reports_insufficient(
        self, app_client
    ) -> None:
        rule = bo_rule_set()
        # Multi-timeframe rules require forex mode (binary is M1-only).
        rule["mode"] = "forex"
        rule["timeframes"] = ["M1", "M5"]
        rule["conditions"]["groups"] = [
            {
                "logic": "AND",
                "conditions": [
                    {
                        "id": "m5c",
                        "type": "price_level",
                        "field": "close",
                        "operator": "above",
                        "value": 1.0,
                        "timeframe": "M5",
                    }
                ],
                "groups": [],
            }
        ]
        res = await app_client.post(
            "/api/v1/analysis/evaluate",
            json={"rule_set": rule, "candles": {"M1": candles(101)}},
        )
        assert res.status_code == 200
        evaluation = res.json()["data"]["evaluation"]
        assert evaluation["setup_status"] == "insufficient_data"
        assert "M5" in evaluation["core"]["evidence"]["missing_timeframes"]

    async def test_invalid_rule_set_via_evaluate(self, app_client) -> None:
        rule = bo_rule_set()
        rule["conditions"]["conditions"][0]["operator"] = "teleports"
        res = await app_client.post(
            "/api/v1/analysis/evaluate",
            json={"rule_set": rule, "candles": {"M1": candles(101)}},
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "RULE_VALIDATION_FAILED"

    async def test_data_quality_warnings_surface(self, app_client) -> None:
        data = candles(99, 100, 101)
        data[2]["time"] += 3600  # 1h gap in M1 series
        res = await app_client.post(
            "/api/v1/analysis/evaluate",
            json={"rule_set": bo_rule_set(), "candles": {"M1": data}},
        )
        codes = {w["code"] for w in res.json()["data"]["evaluation"]["warnings"]}
        assert "TIMESTAMP_GAPS" in codes

    async def test_market_data_status_present_in_response(self, app_client) -> None:
        res = await app_client.post(
            "/api/v1/analysis/evaluate",
            json={"rule_set": bo_rule_set(), "candles": {"M1": candles(101)}},
        )
        md = res.json()["data"]["market_data_provider"]
        assert md["provider"] == "mt5"
        assert md["connected"] is False


class TestEvaluateStoredStrategy:
    async def test_evaluate_saved_version(self, app_client) -> None:
        strat = (
            await app_client.post(
                "/api/v1/strategies",
                json={
                    "name": "Stored Eval",
                    "mode": "binary_options",
                    "direction": "bullish",
                },
            )
        ).json()["data"]
        res = await app_client.post(
            f"/api/v1/strategies/{strat['id']}/versions",
            json={"rule_set": bo_rule_set(), "changelog": "init"},
        )
        assert res.status_code == 201

        res = await app_client.post(
            "/api/v1/analysis/evaluate",
            json={
                "strategy_id": strat["id"],
                "candles": {"M1": candles(100.5)},
            },
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["source"]["version"] == 1
        assert data["evaluation"]["setup_status"] == "conditions_met"

    async def test_evaluate_specific_version(self, app_client) -> None:
        strat = (
            await app_client.post(
                "/api/v1/strategies",
                json={
                    "name": "Versioned Eval",
                    "mode": "binary_options",
                    "direction": "bullish",
                },
            )
        ).json()["data"]
        for value in (100.0, 300.0):
            rule = bo_rule_set()
            rule["conditions"]["conditions"][0]["value"] = value
            await app_client.post(
                f"/api/v1/strategies/{strat['id']}/versions", json={"rule_set": rule}
            )
        res = await app_client.post(
            "/api/v1/analysis/evaluate",
            json={
                "strategy_id": strat["id"],
                "version": 1,
                "candles": {"M1": candles(100.5)},
            },
        )
        assert res.json()["data"]["source"]["version"] == 1

    async def test_strategy_without_rule_set_clear_error(self, app_client) -> None:
        strat = (
            await app_client.post(
                "/api/v1/strategies",
                json={
                    "name": "No Rules Yet",
                    "mode": "binary_options",
                    "direction": "bullish",
                },
            )
        ).json()["data"]
        res = await app_client.post(
            "/api/v1/analysis/evaluate",
            json={"strategy_id": strat["id"], "candles": {"M1": candles(100.5)}},
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "NO_RULE_SET_SAVED"


class TestMarketDataUnavailable:
    @pytest.mark.parametrize(
        "app_client", [{"MT5_ENABLED": "false"}], indirect=True
    )
    async def test_candles_request_refused_honestly(self, app_client) -> None:
        """The provider refuses data requests instead of fabricating candles."""
        from app.core.errors import MarketDataUnavailableError
        from app.domains.market_data.base import get_provider

        provider = get_provider()
        with pytest.raises(MarketDataUnavailableError) as exc_info:
            await provider.get_candles("EURUSD", "M1", 100)
        message = str(exc_info.value).lower()
        # Honest refusal: explains WHY it cannot serve (package/terminal).
        assert (
            "not installed" in message
            or "unavailable" in message
            or "disabled" in message
        )

    @pytest.mark.parametrize(
        "app_client", [{"MT5_ENABLED": "false"}], indirect=True
    )
    async def test_status_endpoint(self, app_client) -> None:
        res = await app_client.get("/api/v1/market-data/status")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["provider"] == "mt5"
        assert data["connected"] is False
        assert data["live_data_enabled"] is False
        assert "Phase 2" in data["detail"] or "not installed" in data["detail"] or "disabled" in data["detail"]
