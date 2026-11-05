"""Integration tests: Phase 4 analysis engine API.

Runs against real PostgreSQL (pgserver) with the FakeMT5 module — no live
MetaTrader terminal is ever required. Covers: engine run + persistence,
retrieval + history, ownership, honest refusal without a provider, regime
gating, forming-candle policy, staleness, binary-mode rules and engine
readiness/features endpoints.
"""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.integration

BASE_EPOCH = None  # set in fixtures (M1-aligned now)


def candles_json(count: int, base_epoch: int, step: int = 60, start: float = 1.10) -> list[dict]:
    """Rising candle series ending at base_epoch (for inline checks)."""

    out = []
    price = start
    first = base_epoch - (count - 1) * step
    for i in range(count):
        t = first + i * step
        o = round(price, 5)
        c = round(price + 0.0002, 5)
        out.append(
            {
                "time": t,
                "open": o,
                "high": round(c + 0.0001, 5),
                "low": round(o - 0.0001, 5),
                "close": c,
            }
        )
        price = c
    return out


def forex_rule_set(timeframes: list[str] | None = None, **kw) -> dict:
    payload = {
        "schema_version": "2.0",
        "mode": "forex",
        "timeframes": timeframes or ["M1"],
        "direction": "bullish",
        "conditions": {
            "logic": "AND",
            "conditions": [
                {
                    "id": "px",
                    "type": "price_level",
                    "timeframe": (timeframes or ["M1"])[0],
                    "lookback": 30,
                    "params": {"field": "close", "operator": "above", "value": 1.10},
                }
            ],
            "groups": [],
        },
    }
    payload.update(kw)
    return payload


async def create_strategy(
    client, *, mode: str = "forex", name: str = "Engine test", rule_set: dict | None = None, publish: bool = True
) -> dict:
    """Create + save + (optionally) publish a strategy; returns detail dict."""

    cat = (
        await client.post(
            "/api/v1/strategy-categories",
            json={"name": f"cat-{name}", "mode": mode},
        )
    ).json()["data"]
    strat = (
        await client.post(
            "/api/v1/strategies",
            json={"name": name, "mode": mode, "direction": "bullish", "category_id": cat["id"]},
        )
    ).json()["data"]
    saved = (
        await client.post(
            f"/api/v1/strategies/{strat['id']}/versions",
            json={"rule_set": rule_set or forex_rule_set(), "changelog": "initial"},
        )
    ).json()["data"]
    if publish:
        await client.post(f"/api/v1/strategies/{strat['id']}/versions/{saved['version']}/publish")
    detail = (await client.get(f"/api/v1/strategies/{strat['id']}")).json()["data"]
    return detail


@pytest.fixture
def seeded_mt5(fake_mt5):
    """FakeMT5 with M1 + H1 rising histories ending at the M1-aligned now."""

    base = int(time.time()) // 60 * 60
    fake_mt5.rates[("EURUSD", fake_mt5.TIMEFRAME_M1)] = candles_json(
        150, base
    )
    fake_mt5.rates[("EURUSD", fake_mt5.TIMEFRAME_H1)] = candles_json(
        120, base // 3600 * 3600, step=3600
    )
    fake_mt5.base_epoch = base
    return fake_mt5


class TestRunAnalysis:
    async def test_run_persists_and_returns_versioned_report(self, app_client, seeded_mt5):
        detail = await create_strategy(app_client, name="run-basic")
        resp = await app_client.post(
            "/api/v1/analysis/run",
            json={"strategy_id": detail["id"], "symbol": "EURUSD"},
        )
        assert resp.status_code == 201, resp.text
        report = resp.json()["data"]

        assert report["report_schema_version"] == "1.0"
        assert report["engine_version"]
        assert report["symbol"] == "EURUSD"
        assert report["mode"] == "forex"
        assert report["strategy"]["id"] == detail["id"]
        assert report["strategy"]["version"] == 1
        assert report["strategy"]["version_status"] == "published"
        assert report["strategy"]["immutable_definition"] is True
        assert report["strategy"]["rule_set"]["schema_version"] == "2.0"

        # Data: only the required timeframe, provenance + forming policy.
        assert report["required_timeframes"] == ["M1"]
        tf_data = report["data"]["timeframes"]
        assert len(tf_data) == 1
        assert tf_data[0]["timeframe"] == "M1"
        assert tf_data[0]["provenance"] in ("mt5_live", "postgres_cache")
        assert tf_data[0]["used_candle_count"] > 0
        assert report["data"]["closed_candles_only"] is True

        # Regime + agreement + applicability.
        assert report["regime"]["primary_timeframe"] == "M1"
        assert report["regime"]["primary"]["regime"] in (
            "uptrend", "downtrend", "range", "breakout", "transition", "uncertain",
        )
        assert report["regime"]["agreement"]["status"] in (
            "agreement", "conflict", "partial", "insufficient",
        )
        assert report["regime"]["applicability"]["applicable"] is True

        # Evaluation tree + honest limitations (no profit claims).
        assert report["evaluation"]["setup_status"] in (
            "conditions_met", "conditions_missing", "invalidated",
            "insufficient_data", "inconclusive", "regime_mismatch",
        )
        assert report["evaluation"]["conditions_summary"]["met"] >= 0
        blob = " ".join(report["limitations"]).lower()
        assert "not a prediction" in blob
        assert "probability of profit" in blob

        # Persistence: history + retrieval return the same payload.
        history = (
            await app_client.get("/api/v1/analysis/reports")
        ).json()["data"]
        assert history["page"]["total"] == 1
        item = history["items"][0]
        assert item["symbol"] == "EURUSD"
        assert item["setup_status"] == report["evaluation"]["setup_status"]
        fetched = (
            await app_client.get(f"/api/v1/analysis/reports/{item['id']}")
        ).json()["data"]
        assert fetched == report

    async def test_no_candle_data_duplicated_in_report(self, app_client, seeded_mt5):
        detail = await create_strategy(app_client, name="run-nodup")
        report = (
            await app_client.post(
                "/api/v1/analysis/run",
                json={"strategy_id": detail["id"], "symbol": "EURUSD"},
            )
        ).json()["data"]
        blob = str(report["data"])
        assert '"open":' not in blob and "'open':" not in blob
        assert report["data"]["timeframes"][0]["first_time"] is not None
        assert report["data"]["timeframes"][0]["last_time"] is not None

    async def test_multi_timeframe_forex_reports_agreement(self, app_client, seeded_mt5):
        detail = await create_strategy(
            app_client,
            name="run-mtf",
            rule_set=forex_rule_set(timeframes=["H1", "M1"]),
        )
        report = (
            await app_client.post(
                "/api/v1/analysis/run",
                json={"strategy_id": detail["id"], "symbol": "EURUSD"},
            )
        ).json()["data"]
        assert report["required_timeframes"] == ["H1", "M1"]
        assert {t["timeframe"] for t in report["data"]["timeframes"]} == {"H1", "M1"}
        assert len(report["regime"]["by_timeframe"]) == 2
        assert report["regime"]["primary_timeframe"] == "H1"
        assert report["regime"]["agreement"]["biases"]  # both biases reported

    async def test_regime_requirement_mismatch_blocks_satisfaction(self, app_client, seeded_mt5):
        detail = await create_strategy(
            app_client,
            name="run-regime",
            rule_set=forex_rule_set(market_regime="ranging"),
        )
        report = (
            await app_client.post(
                "/api/v1/analysis/run",
                json={"strategy_id": detail["id"], "symbol": "EURUSD"},
            )
        ).json()["data"]
        # Rising fake data -> not a range -> strategy must NOT be satisfied.
        assert report["regime"]["applicability"]["applicable"] is False
        assert report["evaluation"]["setup_status"] == "regime_mismatch"

    async def test_symbol_outside_scope_rejected(self, app_client, seeded_mt5):
        detail = await create_strategy(
            app_client,
            name="run-scope",
            rule_set=forex_rule_set(symbols=["XAUUSD"]),
        )
        resp = await app_client.post(
            "/api/v1/analysis/run",
            json={"strategy_id": detail["id"], "symbol": "EURUSD"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "SYMBOL_NOT_IN_SCOPE"

    async def test_explicit_version_is_respected(self, app_client, seeded_mt5):
        detail = await create_strategy(app_client, name="run-versions")
        strategy_id = detail["id"]
        # Save a second (draft) version with a different rule.
        v2 = forex_rule_set()
        v2["conditions"]["conditions"][0]["params"]["value"] = 99.0
        await app_client.post(
            f"/api/v1/strategies/{strategy_id}/versions",
            json={"rule_set": v2, "changelog": "draft v2"},
        )
        report = (
            await app_client.post(
                "/api/v1/analysis/run",
                json={"strategy_id": strategy_id, "symbol": "EURUSD", "version": 1},
            )
        ).json()["data"]
        assert report["strategy"]["version"] == 1
        assert report["strategy"]["version_status"] == "published"
        assert report["strategy"]["rule_set"]["conditions"]["conditions"][0]["params"]["value"] == 1.10

    async def test_include_forming_is_provisional(self, app_client, seeded_mt5):
        detail = await create_strategy(app_client, name="run-forming")
        report = (
            await app_client.post(
                "/api/v1/analysis/run",
                json={
                    "strategy_id": detail["id"],
                    "symbol": "EURUSD",
                    "include_forming": True,
                },
            )
        ).json()["data"]
        assert report["data"]["forming_candle_used"] is True
        assert report["data"]["closed_candles_only"] is False
        codes = {w["code"] for w in report["data"]["warnings"]}
        assert "PROVISIONAL_FORMING_CANDLE" in codes
        assert any("PROVISIONAL" in line for line in report["evaluation"]["summary"])

    async def test_stale_data_is_reported(self, app_client, fake_mt5):
        # Candles ending an hour ago -> stale on M1 (tolerance 3 intervals).
        base = int(time.time()) // 60 * 60 - 3600
        fake_mt5.rates[("EURUSD", fake_mt5.TIMEFRAME_M1)] = candles_json(150, base)
        detail = await create_strategy(app_client, name="run-stale")
        report = (
            await app_client.post(
                "/api/v1/analysis/run",
                json={"strategy_id": detail["id"], "symbol": "EURUSD"},
            )
        ).json()["data"]
        assert report["data"]["timeframes"][0]["stale"] is True
        codes = {w["code"] for w in report["data"]["warnings"]}
        assert "STALE_DATA" in codes
        assert report["data"]["quality_status"] == "stale"

    async def test_binary_mode_disclaimer_and_m1_default(self, app_client, seeded_mt5):
        binary_rule = {
            "schema_version": "2.0",
            "mode": "binary_options",
            "timeframes": ["M1"],
            "direction": "bullish",
            "conditions": {
                "logic": "AND",
                "conditions": [
                    {
                        "id": "px",
                        "type": "price_level",
                        "timeframe": "M1",
                        "lookback": 30,
                        "params": {"field": "close", "operator": "above", "value": 1.10},
                    }
                ],
                "groups": [],
            },
        }
        detail = await create_strategy(
            app_client, mode="binary_options", name="run-binary", rule_set=binary_rule
        )
        report = (
            await app_client.post(
                "/api/v1/analysis/run",
                json={"strategy_id": detail["id"], "symbol": "EURUSD"},
            )
        ).json()["data"]
        assert report["mode"] == "binary_options"
        assert report["required_timeframes"] == ["M1"]
        blob = " ".join(report["limitations"]).lower()
        assert "not your binary options broker" in blob
        assert "otc" in blob and "never simulated" in blob

    async def test_ownership_scoped(self, app_client, seeded_mt5):
        detail = await create_strategy(app_client, name="run-owner")
        headers = {"X-User-Id": "someone-else"}
        report = (
            await app_client.post(
                "/api/v1/analysis/run",
                json={"strategy_id": detail["id"], "symbol": "EURUSD"},
            )
        ).json()["data"]
        report_id = report["analysis_id"]
        # Foreign owner: retrieval 404 + invisible in listing.
        assert (
            await app_client.get(f"/api/v1/analysis/reports/{report_id}", headers=headers)
        ).status_code == 404
        foreign_list = (
            await app_client.get("/api/v1/analysis/reports", headers=headers)
        ).json()["data"]
        assert foreign_list["page"]["total"] == 0

    async def test_list_filters(self, app_client, seeded_mt5):
        detail = await create_strategy(app_client, name="run-filter")
        await app_client.post(
            "/api/v1/analysis/run",
            json={"strategy_id": detail["id"], "symbol": "EURUSD"},
        )
        ok_list = (
            await app_client.get("/api/v1/analysis/reports", params={"symbol": "EURUSD"})
        ).json()["data"]
        assert ok_list["page"]["total"] == 1
        empty = (
            await app_client.get("/api/v1/analysis/reports", params={"symbol": "XAUUSD"})
        ).json()["data"]
        assert empty["page"]["total"] == 0


class TestEngineRefusal:
    @pytest.mark.parametrize(
        "app_client", [{"MT5_ENABLED": "false"}], indirect=True
    )
    async def test_disconnected_provider_refuses_with_503(self, app_client):
        """No fake MT5 installed: provider honestly reports disconnection.

        The engine must NOT fabricate candles or serve a cached fake
        analysis — it refuses with MARKET_DATA_UNAVAILABLE and persists
        nothing.
        """

        detail = await create_strategy(app_client, name="run-refuse")
        resp = await app_client.post(
            "/api/v1/analysis/run",
            json={"strategy_id": detail["id"], "symbol": "EURUSD"},
        )
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "MARKET_DATA_UNAVAILABLE"
        history = (await app_client.get("/api/v1/analysis/reports")).json()["data"]
        assert history["page"]["total"] == 0

    async def test_unknown_symbol_from_provider_is_404(self, app_client, seeded_mt5):
        detail = await create_strategy(app_client, name="run-sym404")
        resp = await app_client.post(
            "/api/v1/analysis/run",
            json={"strategy_id": detail["id"], "symbol": "GBPUSD"},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "SYMBOL_NOT_FOUND"

    async def test_strategy_without_rule_set_rejected(self, app_client, seeded_mt5):
        cat = (
            await app_client.post(
                "/api/v1/strategy-categories", json={"name": "empty", "mode": "forex"}
            )
        ).json()["data"]
        strat = (
            await app_client.post(
                "/api/v1/strategies",
                json={"name": "no-rules", "mode": "forex", "direction": "bullish", "category_id": cat["id"]},
            )
        ).json()["data"]
        resp = await app_client.post(
            "/api/v1/analysis/run", json={"strategy_id": strat["id"], "symbol": "EURUSD"}
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "NO_RULE_SET_SAVED"

    async def test_invalid_symbol_pattern_rejected(self, app_client, seeded_mt5):
        detail = await create_strategy(app_client, name="run-badsym")
        resp = await app_client.post(
            "/api/v1/analysis/run",
            json={"strategy_id": detail["id"], "symbol": "EU R$USD!"},
        )
        assert resp.status_code == 422


class TestEngineMeta:
    async def test_features_endpoint_documents_everything(self, app_client):
        data = (await app_client.get("/api/v1/analysis/features")).json()["data"]
        names = {f["name"] for f in data["features"]}
        assert {"candle_anatomy", "swing_points", "structure_breaks", "data_completeness"} <= names
        assert {r["name"] for r in data["regimes"]} == {
            "uptrend", "downtrend", "range", "breakout", "transition", "uncertain",
        }
        for feature in data["features"]:
            assert feature["definition"] and feature["required_history"]

    async def test_readiness_reports_honestly(self, app_client, seeded_mt5):
        data = (await app_client.get("/api/v1/analysis/readiness")).json()["data"]
        assert data["ready"] is True
        assert data["live_analysis_ready"] is True
        assert data["checks"]["rule_registry"]["coherent"] is True
        assert data["checks"]["rule_registry"]["available"] >= 12
        assert data["checks"]["database"]["status"] == "up"

    @pytest.mark.parametrize(
        "app_client", [{"MT5_ENABLED": "false"}], indirect=True
    )
    async def test_readiness_without_provider(self, app_client):
        data = (await app_client.get("/api/v1/analysis/readiness")).json()["data"]
        assert data["ready"] is True  # engine usable (DB + registry)
        assert data["live_analysis_ready"] is False
        assert "503" in data["note"] or "disconnected" in data["note"].lower()

    async def test_evaluate_endpoint_still_works(self, app_client):
        """Phase 1 inline evaluation must remain functional (regression)."""

        base = int(time.time()) // 60 * 60
        payload = {
            "rule_set": forex_rule_set(),
            "candles": {"M1": candles_json(30, base)},
        }
        resp = await app_client.post("/api/v1/analysis/evaluate", json=payload)
        assert resp.status_code == 200
        assert resp.json()["data"]["evaluation"]["setup_status"] in (
            "conditions_met", "conditions_missing",
        )
