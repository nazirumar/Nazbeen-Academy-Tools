"""Integration tests: Phase 8 research API — optimization, walk-forward,
robustness, leakage prevention, ownership and persistence.

Runs against real PostgreSQL (pgserver) with the FakeMT5 module; research
runs consume the SAME stored candle cache and the SAME Phase 6 engine as
production. External AI/MT5 dependencies are faked; no live terminal is
required.

Leakage tests intentionally attempt to expose future-data usage: the
fixture data makes the out-of-sample-best parameter DIFFERENT from the
in-sample-best parameter, so any implementation that peeked into the
test window during selection would fail these assertions.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.integration

RESEARCH = "/api/v1/research/optimization"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def flat_candles(
    count: int,
    base_epoch: int,
    step: int = 60,
    price: float = 1.10,
) -> list[dict]:
    """Deterministic flat candle series (valid OHLC, distinct times)."""

    out = []
    for i in range(count):
        t = base_epoch - (count - 1 - i) * step
        out.append(
            {
                "time": t,
                "open": round(price, 5),
                "high": round(price + 0.0001, 5),
                "low": round(price - 0.0001, 5),
                "close": round(price, 5),
            }
        )
    return out


def rising_candles(
    count: int,
    base_epoch: int,
    step: int = 60,
    start: float = 1.10,
    drift: float = 0.0002,
) -> list[dict]:
    out = []
    price = start
    for i in range(count):
        t = base_epoch - (count - 1 - i) * step
        o = round(price, 5)
        c = round(price + drift, 5)
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


def price_level_rule_set(
    mode: str = "forex",
    timeframe: str = "M1",
    operator: str = "above",
    value: float = 1.10,
    lookback: int = 30,
    symbols: list[str] | None = None,
) -> dict:
    rule_set = {
        "schema_version": "2.0",
        "mode": mode,
        "timeframes": [timeframe],
        "direction": "bullish",
        "conditions": {
            "logic": "AND",
            "conditions": [
                {
                    "id": "px",
                    "type": "price_level",
                    "timeframe": timeframe,
                    "lookback": lookback,
                    "params": {"field": "close", "operator": operator, "value": value},
                }
            ],
            "groups": [],
        },
    }
    if symbols:
        rule_set["symbols"] = symbols
    return rule_set


async def create_strategy(
    client, *, mode: str = "forex", name: str = "Research", rule_set: dict | None = None,
    publish: bool = True, headers: dict | None = None,
) -> dict:
    hdrs = headers or {}
    cat = (
        await client.post(
            "/api/v1/strategy-categories", json={"name": f"cat-{name}", "mode": mode},
            headers=hdrs,
        )
    ).json()["data"]
    strat = (
        await client.post(
            "/api/v1/strategies",
            json={"name": name, "mode": mode, "direction": "bullish", "category_id": cat["id"]},
            headers=hdrs,
        )
    ).json()["data"]
    saved = (
        await client.post(
            f"/api/v1/strategies/{strat['id']}/versions",
            json={"rule_set": rule_set or price_level_rule_set(mode=mode), "changelog": "initial"},
            headers=hdrs,
        )
    ).json()["data"]
    if publish:
        await client.post(
            f"/api/v1/strategies/{strat['id']}/versions/{saved['version']}/publish",
            headers=hdrs,
        )
    return (
        await client.get(f"/api/v1/strategies/{strat['id']}", headers=hdrs)
    ).json()["data"]


async def seed_flat_candles(client, fake_mt5, *, count: int = 400, price=1.10, symbol: str = "EURUSD") -> int:
    base = int(time.time()) // 60 * 60
    fake_mt5.rates[(symbol, fake_mt5.TIMEFRAME_M1)] = flat_candles(count, base, price=price)
    fake_mt5.base_epoch = base
    resp = await client.get(
        "/api/v1/market/candles",
        params={"symbol": symbol, "timeframe": "M1", "limit": count},
    )
    assert resp.status_code == 200, resp.text
    return base


async def seed_rising_candles(client, fake_mt5, *, count: int = 600, symbol: str = "EURUSD") -> int:
    base = int(time.time()) // 60 * 60
    fake_mt5.rates[(symbol, fake_mt5.TIMEFRAME_M1)] = rising_candles(count, base)
    fake_mt5.base_epoch = base
    resp = await client.get(
        "/api/v1/market/candles",
        params={"symbol": symbol, "timeframe": "M1", "limit": count},
    )
    assert resp.status_code == 200, resp.text
    return base


def window_for(count: int, step: int = 60) -> dict[str, str]:
    base = int(time.time()) // 60 * 60
    start = datetime.fromtimestamp(base - (count - 1) * step, tz=timezone.utc)
    end = datetime.fromtimestamp(base, tz=timezone.utc)
    return {"start": start.isoformat(), "end": end.isoformat()}


def holdout_payload(detail: dict, *, parameters: list[dict], objective="conditions_met_count", **kw) -> dict:
    payload = {
        "mode": detail["mode"],
        "strategy_id": detail["id"],
        "strategy_version": detail["published_version_number"],
        "symbol": "EURUSD",
        **window_for(400),
        "parameters": parameters,
        "objective": objective if isinstance(objective, dict) else {"metric": objective},
        "validation_mode": "holdout",
    }
    payload.update(kw)
    return payload


async def wait_for_status(
    client, run_id: str, statuses: set[str], timeout: float = 30.0, headers: dict | None = None,
) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        resp = await client.get(f"{RESEARCH}/{run_id}", headers=headers or {})
        assert resp.status_code == 200, resp.text
        last = resp.json()["data"]
        if last["status"] in statuses:
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"Run did not reach {statuses}; last status: {last and last['status']}, "
        f"error: {last and last.get('error')}"
    )


def forex_execution(**kw) -> dict:
    base = {
        "enabled": True,
        "spread_pips": 1.0,
        "slippage_pips": 0.5,
        "stop_loss_pips": 10.0,
        "take_profit_pips": 20.0,
        "risk_mode": "fixed_fractional",
        "risk_pct_per_trade": 1.0,
        "pip_value_per_lot": 10.0,
        "pip_size": 0.0001,
        "account_start_balance": 10000.0,
    }
    base.update(kw)
    return base


def candidate_by_index(run_detail: dict, items: list[dict], index: int) -> dict:
    for item in items:
        if item["candidate_index"] == index:
            return item
    raise AssertionError(f"candidate {index} not in page")


# --------------------------------------------------------------------------- #
# Validation cascade
# --------------------------------------------------------------------------- #


class TestValidationCascade:
    async def test_draft_version_rejected(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-draft", publish=False)
        await seed_flat_candles(app_client, fake_mt5)
        payload = holdout_payload(
            detail, parameters=[{"condition_id": "px", "field": "lookback", "values": [10, 20]}]
        )
        payload["strategy_version"] = detail["current_version"]
        resp = await app_client.post(RESEARCH, json=payload)
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "DRAFT_VERSION_NOT_RESEARCHABLE"

    async def test_mode_mismatch_rejected(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, mode="forex", name="rs-mode")
        await seed_flat_candles(app_client, fake_mt5)
        payload = holdout_payload(
            detail, parameters=[{"condition_id": "px", "field": "lookback", "values": [10, 20]}]
        )
        payload["mode"] = "binary_options"
        resp = await app_client.post(RESEARCH, json=payload)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "MODE_MISMATCH"

    async def test_symbol_out_of_scope_rejected(self, app_client, fake_mt5):
        detail = await create_strategy(
            app_client,
            name="rs-scope",
            rule_set=price_level_rule_set(symbols=["EURUSD"]),
        )
        await seed_flat_candles(app_client, fake_mt5)
        payload = holdout_payload(
            detail, parameters=[{"condition_id": "px", "field": "lookback", "values": [10, 20]}]
        )
        payload["symbol"] = "GBPUSD"
        resp = await app_client.post(RESEARCH, json=payload)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "SYMBOL_NOT_IN_SCOPE"

    async def test_unknown_condition_rejected(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-unknown-cond")
        await seed_flat_candles(app_client, fake_mt5)
        resp = await app_client.post(
            RESEARCH,
            json=holdout_payload(
                detail,
                parameters=[{"condition_id": "ghost", "field": "lookback", "values": [10, 20]}],
            ),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "PARAM_CONDITION_NOT_FOUND"

    async def test_new_param_key_rejected(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-new-key")
        await seed_flat_candles(app_client, fake_mt5)
        resp = await app_client.post(
            RESEARCH,
            json=holdout_payload(
                detail,
                parameters=[
                    {"condition_id": "px", "field": "params.brand_new", "values": [1, 2]}
                ],
            ),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "PARAM_KEY_NOT_FOUND"

    async def test_too_many_combinations_rejected(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-combos")
        await seed_flat_candles(app_client, fake_mt5)
        resp = await app_client.post(
            RESEARCH,
            json=holdout_payload(
                detail,
                parameters=[
                    {"condition_id": "px", "field": "lookback", "values": list(range(1, 13))},
                    {"condition_id": "px", "field": "params.value", "values": [1.0 + i * 0.01 for i in range(12)]},
                    {"condition_id": "px", "field": "params.field", "values": ["close", "open"]},
                ],
            ),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "TOO_MANY_COMBINATIONS"

    async def test_objective_requires_trade_simulation(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-obj")
        await seed_flat_candles(app_client, fake_mt5)
        resp = await app_client.post(
            RESEARCH,
            json=holdout_payload(
                detail,
                parameters=[{"condition_id": "px", "field": "lookback", "values": [10, 20]}],
                objective="net_pnl",
            ),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "OBJECTIVE_REQUIRES_TRADE_SIMULATION"

    async def test_cost_variants_require_execution_config(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-costreq")
        await seed_flat_candles(app_client, fake_mt5)
        resp = await app_client.post(
            RESEARCH,
            json=holdout_payload(
                detail,
                parameters=[{"condition_id": "px", "field": "lookback", "values": [10, 20]}],
                forex_cost_variants=[{"spread_pips": 2.0}],
            ),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "COST_VARIANTS_REQUIRE_EXECUTION_CONFIG"

    async def test_forex_mode_rejects_binary_variants(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-crossmode")
        await seed_flat_candles(app_client, fake_mt5)
        resp = await app_client.post(
            RESEARCH,
            json=holdout_payload(
                detail,
                parameters=[{"condition_id": "px", "field": "lookback", "values": [10, 20]}],
                binary_cost_variants=[{"payout_pct": 0.7}],
            ),
        )
        assert resp.status_code == 422
        details = resp.json()["error"]["details"]
        assert any(
            "binary_cost_variants cannot be combined" in (d.get("message") or "")
            for d in details
        )

    async def test_insufficient_walk_forward_history_rejected(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-wfhist")
        await seed_flat_candles(app_client, fake_mt5, count=400)
        resp = await app_client.post(
            RESEARCH,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(400),
                "parameters": [{"condition_id": "px", "field": "lookback", "values": [10, 20]}],
                "objective": {"metric": "conditions_met_count"},
                "validation_mode": "walk_forward",
                "walk_forward": {
                    "train_candles": 2000,
                    "test_candles": 100,
                    "step_candles": 100,
                },
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "INSUFFICIENT_HISTORY_FOR_WINDOWS"

    async def test_single_window_rejected(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-wf1")
        await seed_flat_candles(app_client, fake_mt5, count=300)
        resp = await app_client.post(
            RESEARCH,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(300),
                "parameters": [{"condition_id": "px", "field": "lookback", "values": [10, 20]}],
                "objective": {"metric": "conditions_met_count"},
                "validation_mode": "walk_forward",
                "walk_forward": {"train_candles": 200, "test_candles": 100, "step_candles": 100},
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "INSUFFICIENT_HISTORY_FOR_WINDOWS"

    async def test_walk_budget_exceeded_rejected(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-budget")
        await seed_flat_candles(app_client, fake_mt5, count=400)
        resp = await app_client.post(
            RESEARCH,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(400),
                "parameters": [
                    {"condition_id": "px", "field": "lookback", "values": list(range(1, 11))},
                    {"condition_id": "px", "field": "params.value", "values": [1.0 + i * 0.01 for i in range(10)]},
                ],
                "objective": {"metric": "conditions_met_count"},
                "validation_mode": "walk_forward",
                "walk_forward": {"train_candles": 20, "test_candles": 10, "step_candles": 10},
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "RESEARCH_WALK_BUDGET_EXCEEDED"

    async def test_overlapping_test_windows_rejected(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-overlap")
        await seed_flat_candles(app_client, fake_mt5)
        resp = await app_client.post(
            RESEARCH,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(400),
                "parameters": [{"condition_id": "px", "field": "lookback", "values": [10, 20]}],
                "objective": {"metric": "conditions_met_count"},
                "validation_mode": "walk_forward",
                "walk_forward": {"train_candles": 200, "test_candles": 100, "step_candles": 50},
            },
        )
        assert resp.status_code == 422
        # Overlap is caught by the schema validator (step < test).
        details = resp.json()["error"]["details"]
        assert any(
            "step_candles must be >= test_candles" in (d.get("message") or "")
            for d in details
        )


# --------------------------------------------------------------------------- #
# Holdout optimization + LEAKAGE PREVENTION
# --------------------------------------------------------------------------- #


class TestHoldoutOptimization:
    async def test_holdout_run_completes_with_ranking(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-holdout")
        await seed_flat_candles(app_client, fake_mt5)
        resp = await app_client.post(
            RESEARCH,
            json=holdout_payload(
                detail,
                parameters=[{"condition_id": "px", "field": "lookback", "values": [10, 20]}],
            ),
        )
        assert resp.status_code == 201, resp.text
        run = resp.json()["data"]
        run_id = run["id"]
        assert run["status"] == "queued"
        assert run["validation_mode"] == "holdout"

        final = await wait_for_status(app_client, run_id, {"completed", "failed"})
        assert final["status"] == "completed", final.get("error")

        # Candidate 0 = original parameterization is always present.
        cand = await app_client.get(f"{RESEARCH}/{run_id}/candidates?sort=index&page_size=100")
        items = cand.json()["data"]["items"]
        assert [c["candidate_index"] for c in items] == [0, 1, 2]
        assert items[0]["params"]["patch"] == []
        assert items[0]["params"]["label"] == "original parameters"

        # Ranked candidates: with flat 1.10 prices the 'above' condition is
        # never met but the COUNT is a defined zero — all three candidates
        # are rankable (ties resolve to the lower candidate index).
        ranks = sorted(c["rank"] for c in items if c["rank"] is not None)
        assert ranks == [1, 2, 3]

        # Config is reproducible: seed, limits, objective all recorded.
        config = final["config"]
        assert config["seed"] == 7
        assert config["validation_mode"] == "holdout"
        assert config["in_sample_end"]
        assert config["software"]["engine_version"]
        assert config["combination_count"] == 2

    async def test_selection_never_peeks_into_out_of_sample(self, app_client, fake_mt5):
        """THE leakage test.

        Data design: in-sample closes = 1.09 (below 1.10), out-of-sample
        closes = 1.12 (above 1.10). Candidate 'below' is the IN-SAMPLE
        best (met on every IS candle, never OOS); candidate 'above' is
        the OUT-OF-SAMPLE best (met on every OOS candle, never IS).

        An implementation that peeked into the OOS window during
        selection would pick 'above'; the honest run must select
        'below' and evaluate ONLY 'below' out of sample.
        """

        detail = await create_strategy(
            app_client,
            name="rs-leak",
            rule_set=price_level_rule_set(operator="above", value=1.10),
        )
        # IS: 280 candles at 1.09 (below 1.10 -> 'below' met, 'above' not).
        # OOS: 120 candles at 1.12 (above 1.10 -> 'above' met, 'below' not).
        base = int(time.time()) // 60 * 60
        fake_mt5.rates[("EURUSD", fake_mt5.TIMEFRAME_M1)] = (
            flat_candles(280, base - 120 * 60, price=1.09)
            + flat_candles(120, base, price=1.12)
        )
        fake_mt5.base_epoch = base
        resp = await app_client.get(
            "/api/v1/market/candles",
            params={"symbol": "EURUSD", "timeframe": "M1", "limit": 400},
        )
        assert resp.status_code == 200

        payload = holdout_payload(
            detail,
            parameters=[
                {
                    "condition_id": "px",
                    "field": "params.operator",
                    "values": ["below", "above"],
                }
            ],
        )
        resp = await app_client.post(RESEARCH, json=payload)
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["data"]["id"]
        final = await wait_for_status(app_client, run_id, {"completed", "failed"})
        assert final["status"] == "completed", final.get("error")

        cand = await app_client.get(f"{RESEARCH}/{run_id}/candidates?sort=index&page_size=100")
        items = cand.json()["data"]["items"]
        by_index = {c["candidate_index"]: c for c in items}

        # Candidate 1 = operator 'below' (in-sample best: met 280x).
        # Candidates 0 and 2 (both 'above') tie at a defined zero in-sample;
        # the tie resolves to the lower candidate index.
        assert by_index[1]["params"]["patch"][0]["value"] == "below"
        assert by_index[2]["params"]["patch"][0]["value"] == "above"
        assert by_index[1]["rank"] == 1, by_index
        assert by_index[0]["rank"] == 2
        assert by_index[2]["rank"] == 3

        # ONLY the in-sample winner was evaluated out of sample.
        detail_resp = await app_client.get(f"{RESEARCH}/{run_id}/candidates/1")
        cand1 = detail_resp.json()["data"]
        detail_resp = await app_client.get(f"{RESEARCH}/{run_id}/candidates/2")
        cand2 = detail_resp.json()["data"]
        assert cand1["out_of_sample"] is not None
        assert cand2["out_of_sample"] is None

        # The selected candidate's honest OOS result: 'below' NEVER meets
        # in the OOS period.
        oos_status = cand1["out_of_sample"]["setups"]["by_status"]["conditions_met"]
        assert oos_status == 0

        # Boundary evidence: in-sample evaluations never touch OOS time
        # (metric digests carry epoch-second evaluation instants).
        is_end = datetime.fromisoformat(final["config"]["in_sample_end"])
        last_is = datetime.fromtimestamp(
            cand1["in_sample"]["setups"]["last_evaluation_time"], tz=timezone.utc
        )
        assert last_is <= is_end
        first_oos = datetime.fromtimestamp(
            cand1["out_of_sample"]["setups"]["first_evaluation_time"], tz=timezone.utc
        )
        assert first_oos > is_end

        # Aggregate names the best candidate and carries disclaimers.
        aggregate = final["aggregate"]
        assert aggregate["best_candidate"]["candidate_index"] == 1
        assert any("not predictions" in d for d in aggregate["disclaimers"])

    async def test_reproducibility_identical_runs(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-repro")
        await seed_flat_candles(app_client, fake_mt5)
        payload = holdout_payload(
            detail,
            parameters=[{"condition_id": "px", "field": "lookback", "values": [10, 20]}],
        )
        r1 = await app_client.post(RESEARCH, json=payload)
        r2 = await app_client.post(RESEARCH, json=payload)
        assert r1.status_code == 201 and r2.status_code == 201
        id1, id2 = r1.json()["data"]["id"], r2.json()["data"]["id"]
        f1 = await wait_for_status(app_client, id1, {"completed", "failed"})
        f2 = await wait_for_status(app_client, id2, {"completed", "failed"})
        assert f1["status"] == f2["status"] == "completed"

        c1 = (await app_client.get(f"{RESEARCH}/{id1}/candidates?page_size=100")).json()["data"]["items"]
        c2 = (await app_client.get(f"{RESEARCH}/{id2}/candidates?page_size=100")).json()["data"]["items"]
        assert [(c["candidate_index"], c["score"], c["rank"]) for c in c1] == [
            (c["candidate_index"], c["score"], c["rank"]) for c in c2
        ]
        d1 = (await app_client.get(f"{RESEARCH}/{id1}/candidates/1")).json()["data"]
        d2 = (await app_client.get(f"{RESEARCH}/{id2}/candidates/1")).json()["data"]
        assert d1["in_sample"] == d2["in_sample"]

    async def test_undefined_metrics_never_ranked_as_zero(self, app_client, fake_mt5):
        """A trade-level objective with a strategy that never triggers ->
        no simulated trades -> every candidate is unrankable with an
        explicit reason (never 0)."""

        detail = await create_strategy(
            app_client,
            name="rs-undef",
            rule_set=price_level_rule_set(value=1.50),  # never met at 1.10
        )
        await seed_flat_candles(app_client, fake_mt5)
        payload = holdout_payload(
            detail,
            parameters=[{"condition_id": "px", "field": "lookback", "values": [10, 20]}],
            objective="net_pnl",
            forex_execution=forex_execution(),
        )
        resp = await app_client.post(RESEARCH, json=payload)
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["data"]["id"]
        final = await wait_for_status(app_client, run_id, {"completed", "failed"})
        assert final["status"] == "completed"
        cand = (await app_client.get(f"{RESEARCH}/{run_id}/candidates?page_size=100")).json()["data"]["items"]
        assert all(c["score"] is None for c in cand)
        assert all(c["rank"] is None for c in cand)
        assert all("undefined" in (c["rank_note"] or "").lower() for c in cand)

    async def test_constraints_block_selection(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-constraints")
        await seed_flat_candles(app_client, fake_mt5)
        payload = holdout_payload(
            detail,
            parameters=[{"condition_id": "px", "field": "lookback", "values": [10, 20]}],
            objective={"metric": "conditions_met_count", "constraints": {"min_decided_trades": 5}},
        )
        resp = await app_client.post(RESEARCH, json=payload)
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["data"]["id"]
        final = await wait_for_status(app_client, run_id, {"completed", "failed"})
        assert final["status"] == "completed"
        cand = (await app_client.get(f"{RESEARCH}/{run_id}/candidates?page_size=100")).json()["data"]["items"]
        # Setup-only run: no decided trades anywhere -> nothing selectable.
        assert all(c["selectable"] is False for c in cand)
        assert any(
            v["code"] == "MIN_DECIDED_TRADES"
            for c in cand
            for v in c["constraint_violations"]
        )
        # No OOS walk happened (nothing selectable).
        assert final["aggregate"]["out_of_sample"] is None
        assert any(
            w["code"] == "NO_SELECTABLE_CANDIDATE" for w in final["aggregate"]["warnings"]
        )


# --------------------------------------------------------------------------- #
# Walk-forward validation
# --------------------------------------------------------------------------- #


class TestWalkForward:
    async def test_walk_forward_chronological_windows(self, app_client, fake_mt5):
        detail = await create_strategy(
            app_client,
            mode="binary_options",
            name="rs-wf",
            rule_set=price_level_rule_set(mode="binary_options"),
        )
        await seed_rising_candles(app_client, fake_mt5, count=601)
        resp = await app_client.post(
            RESEARCH,
            json={
                "mode": "binary_options",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(601),
                "parameters": [{"condition_id": "px", "field": "lookback", "values": [10, 20]}],
                "objective": {"metric": "net_pnl"},
                "validation_mode": "walk_forward",
                "walk_forward": {"train_candles": 200, "test_candles": 100, "step_candles": 100},
            },
        )
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["data"]["id"]
        final = await wait_for_status(app_client, run_id, {"completed", "failed"}, timeout=60)
        assert final["status"] == "completed", final.get("error")

        wf = (await app_client.get(f"{RESEARCH}/{run_id}/walk-forward")).json()["data"]
        windows = wf["items"]
        assert len(windows) == 4

        from datetime import datetime as dt

        parsed = [
            {
                "i": w["window_index"],
                "ts": dt.fromisoformat(w["train_start"]),
                "te": dt.fromisoformat(w["train_end"]),
                "ss": dt.fromisoformat(w["test_start"]),
                "se": dt.fromisoformat(w["test_end"]),
            }
            for w in windows
        ]
        for i, w in enumerate(parsed):
            # Contiguous train -> test (no gap, no overlap).
            assert w["te"] == w["ss"]
            assert w["ts"] < w["te"] < w["se"]
            if i:
                # Non-overlapping chronological test windows (step == test).
                assert w["ss"] >= parsed[i - 1]["se"]
                assert w["ts"] > parsed[i - 1]["ts"]

        # Every window selected a candidate on TRAIN data only and tested it.
        for w in windows:
            assert w["status"] == "completed"
            assert w["selected_candidate_index"] is not None
            assert w["selected_params"] is not None
            assert w["test_trades"]["decided_trades"] > 0
            train_scores = [e["score"] for e in w["train_evaluations"]]
            assert len(train_scores) == 3  # original + 2 candidates
            selected_entry = next(
                e
                for e in w["train_evaluations"]
                if e["candidate_index"] == w["selected_candidate_index"]
            )
            assert selected_entry["score"] == w["selection_score"]

        # Selection never used test data: training scores exist for all
        # candidates; the frozen winner is what produced the test digest.
        aggregate = final["aggregate"]
        oos = aggregate["out_of_sample"]
        assert oos["windows_aggregated"] == 4
        per_window_decided = sum(p["decided_trades"] or 0 for p in oos["per_window"])
        assert oos["trades"]["decided_trades"] == per_window_decided
        assert oos["trades"]["decided_trades"] > 0
        # Rising series with bullish calls -> every decided trade wins.
        assert oos["trades"]["losses"] == 0
        assert "disclaimers" in aggregate

        # Candidate ranking is intentionally NULL in walk-forward mode.
        cand = (await app_client.get(f"{RESEARCH}/{run_id}/candidates?page_size=100")).json()["data"]["items"]
        assert all(c["rank"] is None for c in cand)
        assert all("walk-forward" in (c["rank_note"] or "").lower() for c in cand)
        assert all(c["times_selected"] >= 0 for c in cand)
        assert sum(c["times_selected"] for c in cand) == 4

    async def test_walk_forward_window_skip_on_gaps(self, app_client, fake_mt5):
        """A training window with fewer stored candles than the configured
        minimum is SKIPPED with an explicit reason — never faked."""

        detail = await create_strategy(
            app_client,
            mode="binary_options",
            name="rs-wfskip",
            rule_set=price_level_rule_set(mode="binary_options"),
        )
        base = int(time.time()) // 60 * 60
        full = rising_candles(601, base)
        # Delete candles 200..459 so later training windows hold far less
        # stored data than the configured minimum (seeded in ONE pass —
        # the candle cache would mask post-hoc edits).
        gapped = full[:200] + full[460:]
        fake_mt5.rates[("EURUSD", fake_mt5.TIMEFRAME_M1)] = gapped
        fake_mt5.base_epoch = base
        resp = await app_client.get(
            "/api/v1/market/candles",
            params={"symbol": "EURUSD", "timeframe": "M1", "limit": 601},
        )
        assert resp.status_code == 200
        resp = await app_client.post(
            RESEARCH,
            json={
                "mode": "binary_options",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(601),
                "parameters": [{"condition_id": "px", "field": "lookback", "values": [10]}],
                "objective": {"metric": "net_pnl"},
                "validation_mode": "walk_forward",
                "walk_forward": {"train_candles": 200, "test_candles": 100, "step_candles": 100},
            },
        )
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["data"]["id"]
        final = await wait_for_status(app_client, run_id, {"completed", "failed"}, timeout=60)
        assert final["status"] == "completed", final.get("error")
        wf = (await app_client.get(f"{RESEARCH}/{run_id}/walk-forward")).json()["data"]
        skipped = [w for w in wf["items"] if w["status"] == "skipped"]
        assert skipped, "expected at least one skipped window"
        for w in skipped:
            assert w["error"]["code"] == "INSUFFICIENT_WINDOW_DATA"
        agg = final["aggregate"]["out_of_sample"]
        assert agg.get("skipped_windows")


# --------------------------------------------------------------------------- #
# Robustness scenarios
# --------------------------------------------------------------------------- #


class TestRobustness:
    async def test_forex_cost_sensitivity(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-fxcost")
        await seed_rising_candles(app_client, fake_mt5, count=800)
        resp = await app_client.post(
            RESEARCH,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(800),
                "parameters": [{"condition_id": "px", "field": "lookback", "values": [10, 20]}],
                "objective": {"metric": "net_pnl"},
                "validation_mode": "holdout",
                "forex_execution": forex_execution(),
                "forex_cost_variants": [
                    {"commission_per_lot_per_side": 5.0},
                    {"commission_per_lot_per_side": 2.0},
                ],
            },
        )
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["data"]["id"]
        final = await wait_for_status(app_client, run_id, {"completed", "failed"}, timeout=60)
        assert final["status"] == "completed", final.get("error")

        cand_debug = (await app_client.get(f"{RESEARCH}/{run_id}/candidates?page_size=100")).json()["data"]["items"]
        print("DEBUG candidates:", [(c["candidate_index"], c["status"], c["score"], c["rank"], c.get("rank_note")) for c in cand_debug])
        rob = (await app_client.get(f"{RESEARCH}/{run_id}/robustness")).json()["data"]
        scenarios = rob["items"]
        cost = [s for s in scenarios if s["category"] == "cost_variant"]
        assert len(cost) == 2
        for s in cost:
            assert s["status"] == "completed", s.get("error")
            assert s["assumptions"]["kind"] == "execution_cost"
            assert s["assumptions"]["market"] == "forex"
            assert s["note"]  # documents what the scenario cannot establish
            # Higher costs -> strictly lower OOS net P&L than baseline.
            delta = s["delta"]
            assert delta is not None
            assert delta["net_pnl"]["baseline"] is not None
            assert delta["net_pnl"]["scenario"] is not None
            assert delta["net_pnl"]["change"] < 0, delta

        # Param-neighbour scenario for the single-dimension grid.
        neighbours = [s for s in scenarios if s["category"] == "param_neighbor"]
        assert neighbours, "expected parameter-neighbour scenarios"
        assert all(s["assumptions"]["selection_window_only"] is True for s in neighbours)

    async def test_binary_payout_sensitivity(self, app_client, fake_mt5):
        detail = await create_strategy(
            app_client,
            mode="binary_options",
            name="rs-bopayout",
            rule_set=price_level_rule_set(mode="binary_options"),
        )
        await seed_rising_candles(app_client, fake_mt5, count=601)
        resp = await app_client.post(
            RESEARCH,
            json={
                "mode": "binary_options",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(601),
                "parameters": [{"condition_id": "px", "field": "lookback", "values": [10]}],
                "objective": {"metric": "net_pnl"},
                "validation_mode": "holdout",
                "binary_cost_variants": [
                    {"payout_pct": 0.9},
                    {"payout_pct": 0.5},
                ],
            },
        )
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["data"]["id"]
        final = await wait_for_status(app_client, run_id, {"completed", "failed"}, timeout=60)
        assert final["status"] == "completed", final.get("error")

        rob = (await app_client.get(f"{RESEARCH}/{run_id}/robustness")).json()["data"]
        cost = {s["label"]: s for s in rob["items"] if s["category"] == "cost_variant"}
        assert len(cost) == 2
        payouts = {
            s["assumptions"]["overrides"]["payout_pct"]: s["delta"]["net_pnl"]
            for s in cost.values()
        }
        # Every trade wins (rising series, bullish calls): higher payout
        # strictly increases net P&L.
        assert payouts[0.9]["change"] > 0
        assert payouts[0.5]["change"] < 0
        # Binary disclaimers are present in the aggregate.
        assert any(
            "historical simulations" in d for d in final["aggregate"]["disclaimers"]
        )

    async def test_bootstrap_scenario_seeded(self, app_client, fake_mt5):
        detail = await create_strategy(
            app_client,
            mode="binary_options",
            name="rs-bootstrap",
            rule_set=price_level_rule_set(mode="binary_options"),
        )
        await seed_rising_candles(app_client, fake_mt5, count=600)
        payload = {
            "mode": "binary_options",
            "strategy_id": detail["id"],
            "strategy_version": detail["published_version_number"],
            "symbol": "EURUSD",
            **window_for(600),
            "parameters": [{"condition_id": "px", "field": "lookback", "values": [10]}],
            "objective": {"metric": "net_pnl"},
            "validation_mode": "holdout",
            "seed": 123,
        }
        runs = []
        for _ in range(2):
            resp = await app_client.post(RESEARCH, json=payload)
            assert resp.status_code == 201, resp.text
            runs.append(resp.json()["data"]["id"])
        finals = [await wait_for_status(app_client, rid, {"completed", "failed"}, timeout=60) for rid in runs]
        assert all(f["status"] == "completed" for f in finals)
        boots = []
        for rid in runs:
            rob = (await app_client.get(f"{RESEARCH}/{rid}/robustness")).json()["data"]
            b = [s for s in rob["items"] if s["category"] == "bootstrap"]
            assert len(b) == 1
            assert b[0]["status"] == "completed"
            assert b[0]["metrics"]["out_of_sample"]["seed"] == 123
            assert "NOT" in b[0]["metrics"]["out_of_sample"]["method_note"]
            boots.append(b[0]["metrics"])
        # Same seed -> identical bootstrap percentiles (reproducible).
        assert boots[0] == boots[1]

    async def test_stability_views_derived(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-stability")
        await seed_flat_candles(app_client, fake_mt5)
        payload = holdout_payload(
            detail,
            parameters=[{"condition_id": "px", "field": "lookback", "values": [10, 20]}],
        )
        resp = await app_client.post(RESEARCH, json=payload)
        run_id = resp.json()["data"]["id"]
        final = await wait_for_status(app_client, run_id, {"completed", "failed"})
        assert final["status"] == "completed"
        views = final["aggregate"]["stability_views"]
        assert "time_periods" in views
        assert "market_regimes" in views
        assert "data_quality" in views
        assert views["data_quality"]["overall_status"] in (None, "ok", "partial", "insufficient")


# --------------------------------------------------------------------------- #
# AI research layer integration (Phase 7 layer over Phase 8 evidence)
# --------------------------------------------------------------------------- #


class TestAIIntegration:
    async def test_ai_chat_explains_a_completed_research_run(
        self, app_client, fake_mt5, monkeypatch
    ):
        from tests.fakes.fake_ai import ScriptedProvider
        from app.domains.ai import service as ai_service_mod

        detail = await create_strategy(app_client, name="rs-ai")
        await seed_flat_candles(app_client, fake_mt5)
        resp = await app_client.post(
            RESEARCH,
            json=holdout_payload(
                detail,
                parameters=[{"condition_id": "px", "field": "lookback", "values": [10, 20]}],
            ),
        )
        run_id = resp.json()["data"]["id"]
        run = await wait_for_status(app_client, run_id, {"completed", "failed"})
        assert run["status"] == "completed"

        run_ref = f"run:{run_id}"
        provider = ScriptedProvider(
            content={
                "answer": "The run selected the candidate with rank 1 on the in-sample window.",
                "evidence_cited": [
                    {"text": "Stored objective.", "refs": ["research:objective"]},
                    {"text": "Run identity.", "refs": [run_ref]},
                ],
                "uncertainty": ["Historical simulation only."],
                "unavailable_information": ["No live data was fetched."],
                "suggested_follow_ups": ["Inspect the walk-forward windows."],
            }
        )
        monkeypatch.setattr(
            ai_service_mod,
            "_resolve_provider",
            lambda _settings, _provider=None: provider,
        )

        chat = await app_client.post(
            "/api/v1/ai/research/chat",
            json={"message": "What did the optimization find?", "research_id": run_id},
        )
        assert chat.status_code == 201, chat.text
        data = chat.json()["data"]
        assert data["conversation"]["research_id"] == run_id
        assert data["messages"][1]["validation"]["evidence_refs_ok"] is True
        kinds = {r["kind"] for r in data["refs"]}
        assert kinds == {"research"}

        # A foreign owner's run cannot be attached (404, no existence leak).
        foreign = await app_client.post(
            "/api/v1/ai/research/chat",
            json={"message": "explain", "research_id": run_id},
            headers={"X-User-Id": "mallory"},
        )
        assert foreign.status_code == 404

    async def test_ai_chat_rejects_unknown_research_run(
        self, app_client, fake_mt5, monkeypatch
    ):
        from tests.fakes.fake_ai import ScriptedProvider
        from app.domains.ai import service as ai_service_mod

        monkeypatch.setattr(
            ai_service_mod,
            "_resolve_provider",
            lambda _settings, _provider=None: ScriptedProvider(content={}),
        )
        resp = await app_client.post(
            "/api/v1/ai/research/chat",
            json={"message": "explain", "research_id": "00000000-0000-0000-0000-000000000000"},
        )
        assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Persistence, ownership, pagination, cancellation, failure handling
# --------------------------------------------------------------------------- #


class TestPersistenceAndOwnership:
    async def test_strategy_version_immutability_across_run(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-immutable")
        await seed_flat_candles(app_client, fake_mt5)
        payload = holdout_payload(
            detail,
            parameters=[{"condition_id": "px", "field": "lookback", "values": [10]}],
        )
        resp = await app_client.post(RESEARCH, json=payload)
        run_id = resp.json()["data"]["id"]
        # "Edit" the strategy: a new draft version (the old one becomes
        # superseded only on publish) — the queued run must be unaffected.
        await app_client.post(
            f"/api/v1/strategies/{detail['id']}/versions",
            json={"rule_set": price_level_rule_set(value=1.50), "changelog": "edit"},
        )
        final = await wait_for_status(app_client, run_id, {"completed", "failed"})
        assert final["status"] == "completed"
        assert final["config"]["strategy_version"] == 1
        assert final["config"]["rule_set"]["conditions"]["conditions"][0]["params"]["value"] == 1.10
        assert final["strategy_version"] == 1

    async def test_cross_user_isolation(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-own", headers={"X-User-Id": "alice"})
        await seed_flat_candles(app_client, fake_mt5)
        resp = await app_client.post(
            RESEARCH,
            json=holdout_payload(
                detail,
                parameters=[{"condition_id": "px", "field": "lookback", "values": [10]}],
            ),
            headers={"X-User-Id": "alice"},
        )
        assert resp.status_code == 201
        run_id = resp.json()["data"]["id"]
        await wait_for_status(
            app_client, run_id, {"completed", "failed"}, headers={"X-User-Id": "alice"}
        )

        # Another owner sees NOTHING (404, no existence leak).
        for path in (
            f"{RESEARCH}/{run_id}",
            f"{RESEARCH}/{run_id}/candidates",
            f"{RESEARCH}/{run_id}/walk-forward",
            f"{RESEARCH}/{run_id}/robustness",
        ):
            resp = await app_client.get(path, headers={"X-User-Id": "bob"})
            assert resp.status_code == 404, path
        resp = await app_client.get(RESEARCH, headers={"X-User-Id": "bob"})
        assert resp.json()["data"]["page"]["total"] == 0
        # Cancelling someone else's run is also a 404.
        resp = await app_client.post(f"{RESEARCH}/{run_id}/cancel", headers={"X-User-Id": "bob"})
        assert resp.status_code == 404

    async def test_candidate_pagination(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-page")
        await seed_flat_candles(app_client, fake_mt5)
        payload = holdout_payload(
            detail,
            parameters=[
                {"condition_id": "px", "field": "lookback", "values": [10, 20, 30]},
                {"condition_id": "px", "field": "params.value", "values": [1.0, 1.5, 2.0]},
            ],
            validation_mode="in_sample_only",
        )
        resp = await app_client.post(RESEARCH, json=payload)
        run_id = resp.json()["data"]["id"]
        final = await wait_for_status(app_client, run_id, {"completed", "failed"})
        assert final["status"] == "completed"
        page1 = (await app_client.get(f"{RESEARCH}/{run_id}/candidates?page_size=4")).json()["data"]
        assert page1["page"]["total"] == 10
        assert page1["page"]["pages"] == 3
        assert len(page1["items"]) == 4
        page3 = (
            await app_client.get(f"{RESEARCH}/{run_id}/candidates?page=3&page_size=4")
        ).json()["data"]
        assert len(page3["items"]) == 2
        # in_sample_only run carries the explicit warning.
        assert any(
            w["code"] == "IN_SAMPLE_ONLY" for w in final["aggregate"]["warnings"]
        )

    async def test_cancel_finished_run_conflict(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-cancel1")
        await seed_flat_candles(app_client, fake_mt5)
        resp = await app_client.post(
            RESEARCH,
            json=holdout_payload(
                detail,
                parameters=[{"condition_id": "px", "field": "lookback", "values": [10]}],
            ),
        )
        run_id = resp.json()["data"]["id"]
        await wait_for_status(app_client, run_id, {"completed", "failed"})
        resp = await app_client.post(f"{RESEARCH}/{run_id}/cancel")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "RUN_ALREADY_FINISHED"

    async def test_cancel_untracked_run_honest_answer(self, app_client, fake_mt5):
        """A queued run not tracked by THIS process (e.g. after a restart)
        answers honestly instead of pretending the cancel worked."""

        from app.core.config import get_settings
        from app.db.models import ResearchRun
        from app.db.session import get_session_factory

        detail = await create_strategy(app_client, name="rs-cancel2")
        await seed_flat_candles(app_client, fake_mt5)
        payload = holdout_payload(
            detail,
            parameters=[{"condition_id": "px", "field": "lookback", "values": [10]}],
        )
        from app.domains.research.schemas import ResearchCreateRequest
        from app.domains.research import service as research_service

        factory = get_session_factory()
        async with factory() as session:
            req = ResearchCreateRequest(**payload)
            run = await research_service.validate_and_create(
                session, "local", get_settings(), req
            )
            await session.commit()
            run_id = run.id
        resp = await app_client.post(f"{RESEARCH}/{run_id}/cancel")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["cancel_requested"] is False
        assert "not executing" in data["note"]

    async def test_cooperative_cancellation_deterministic(self, app_client, fake_mt5):
        """Submit a run to a FRESH runner and cancel it before the task's
        first engine step — the run must end 'cancelled' with partial
        (empty) results persisted honestly."""

        from app.core.config import get_settings
        from app.db.models import ResearchRun
        from app.db.session import get_session_factory
        from app.domains.research import service as research_service
        from app.domains.research.runner import ResearchRunner
        from app.domains.research.schemas import ResearchCreateRequest

        detail = await create_strategy(app_client, name="rs-cancel3")
        await seed_flat_candles(app_client, fake_mt5, count=800)
        payload = holdout_payload(
            detail,
            parameters=[
                {"condition_id": "px", "field": "lookback", "values": [10, 20, 30]},
                {"condition_id": "px", "field": "params.value", "values": [1.0, 1.5, 2.0]},
            ],
        )
        factory = get_session_factory()
        async with factory() as session:
            req = ResearchCreateRequest(**payload)
            run = await research_service.validate_and_create(
                session, "local", get_settings(), req
            )
            await session.commit()
            run_id = run.id

        runner = ResearchRunner(max_concurrent=1)
        runner.submit(run_id)
        assert runner.cancel(run_id) is True

        deadline = time.monotonic() + 20
        status = None
        while time.monotonic() < deadline:
            async with factory() as session:
                row = await session.get(ResearchRun, run_id)
                status = row.status
            if status == "cancelled":
                break
            await asyncio.sleep(0.05)
        assert status == "cancelled", status
        # The cancel endpoint on the now-cancelled run answers 409.
        resp = await app_client.post(f"{RESEARCH}/{run_id}/cancel")
        assert resp.status_code == 409

    async def test_corrupted_config_fails_honestly(self, app_client, fake_mt5):
        """A run whose persisted config cannot be parsed is marked failed
        with an explicit error (never silently 'completed')."""

        from sqlalchemy import text

        from app.db.models import ResearchRun
        from app.db.session import get_session_factory, get_engine
        from app.domains.research.runner import ResearchRunner

        run_id = uuid.uuid4()
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO research_runs (
                        id, owner_id, mode, symbol, timeframe, validation_mode,
                        strategy_version, status, start_time, end_time, config,
                        research_schema_version, research_version, engine_version,
                        walks_executed, created_at, updated_at
                    ) VALUES (
                        :id, 'local', 'forex', 'EURUSD', 'M1', 'holdout',
                        1, 'queued', now(), now(), :config,
                        '1.0', '8.0.0', '6.0.0', 0, now(), now()
                    )
                    """
                ),
                {"id": run_id, "config": '{"mode": "forex"}'},
            )
        runner = ResearchRunner(max_concurrent=1)
        runner.submit(run_id)
        factory = get_session_factory()
        deadline = time.monotonic() + 20
        status, error = None, None
        while time.monotonic() < deadline:
            async with factory() as session:
                row = await session.get(ResearchRun, run_id)
                status = row.status
                error = row.error
            if status in ("failed", "completed"):
                break
            await asyncio.sleep(0.05)
        assert status == "failed", (status, error)
        assert error["code"] == "RESEARCH_FAILED"

    async def test_run_listing_filters_and_fields(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-list")
        await seed_flat_candles(app_client, fake_mt5)
        resp = await app_client.post(
            RESEARCH,
            json=holdout_payload(
                detail,
                parameters=[{"condition_id": "px", "field": "lookback", "values": [10]}],
            ),
        )
        run_id = resp.json()["data"]["id"]
        await wait_for_status(app_client, run_id, {"completed", "failed"})
        listing = (await app_client.get(RESEARCH)).json()["data"]
        assert listing["page"]["total"] >= 1
        item = next(i for i in listing["items"] if i["id"] == run_id)
        assert item["validation_mode"] == "holdout"
        assert item["objective_metric"] == "conditions_met_count"
        assert item["best_candidate"] is not None

        filtered = (
            await app_client.get(RESEARCH, params={"validation_mode": "walk_forward"})
        ).json()["data"]
        assert filtered["page"]["total"] == 0

    async def test_parameter_schema_endpoint(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="rs-schema")
        resp = await app_client.get(
            f"{RESEARCH}/parameter-schema",
            params={
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["strategy_version"] == detail["published_version_number"]
        px = next(c for c in data["parameters"] if c["condition_id"] == "px")
        fields = {f["field"]: f for f in px["fields"]}
        assert "lookback" in fields
        assert fields["lookback"]["current"] == 30
        assert "params.value" in fields
        assert fields["params.value"]["current"] == 1.10

    async def test_startup_recovery_marks_stale_runs_failed(self, app_client, fake_mt5):
        from sqlalchemy import text

        from app.db.session import get_engine
        from app.domains.research import service as research_service

        run_id = uuid.uuid4()
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO research_runs (
                        id, owner_id, mode, symbol, timeframe, validation_mode,
                        strategy_version, status, start_time, end_time, config,
                        research_schema_version, research_version, engine_version,
                        walks_executed, created_at, updated_at
                    ) VALUES (
                        :id, 'local', 'forex', 'EURUSD', 'M1', 'holdout',
                        1, 'running', now(), now(), '{}', '1.0', '8.0.0',
                        '6.0.0', 0, now(), now()
                    )
                    """
                ),
                {"id": run_id},
            )
        from app.db.session import get_session_factory
        from app.db.models import ResearchRun

        async with get_session_factory()() as session:
            recovered = await research_service.recover_interrupted(session)
            await session.commit()
        assert recovered >= 1
        async with get_session_factory()() as session:
            row = await session.get(ResearchRun, run_id)
            assert row.status == "failed"
            assert row.error["code"] == "RUNNER_RESTARTED"
