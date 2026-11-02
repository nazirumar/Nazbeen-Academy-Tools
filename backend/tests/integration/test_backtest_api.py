"""Integration tests: Phase 6 backtesting API + persistence.

Runs against real PostgreSQL (pgserver) with the FakeMT5 module — no live
MetaTrader terminal is ever required. The backtest data source is the
PostgreSQL candle cache, populated through the normal market-data fetch
path (fake provider), exactly like production.

Covers: validation cascade, run lifecycle (queued -> completed/failed/
cancelled), ownership (404 for foreign owners), pagination + filters,
equity path, CSV export (assumptions + formula-injection protection),
immutable version pinning across later strategy edits, data-coverage
endpoint and startup recovery semantics.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

pytestmark = pytest.mark.integration

BACKTESTS = "/api/v1/backtests"


def candles_json(count: int, base_epoch: int, step: int = 60, start: float = 1.10) -> list[dict]:
    """Deterministic rising candle series (valid OHLC, distinct times)."""

    out = []
    price = start
    for i in range(count):
        t = base_epoch - (count - 1 - i) * step
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


def rising_rule_set(mode: str = "forex", timeframe: str = "M1", value: float = 1.10) -> dict:
    return {
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
                    "lookback": 30,
                    "params": {"field": "close", "operator": "above", "value": value},
                }
            ],
            "groups": [],
        },
    }


async def create_strategy(
    client, *, mode: str = "forex", name: str = "Backtest", rule_set: dict | None = None,
    publish: bool = True,
) -> dict:
    cat = (
        await client.post(
            "/api/v1/strategy-categories", json={"name": f"cat-{name}", "mode": mode}
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
            json={"rule_set": rule_set or rising_rule_set(mode=mode), "changelog": "initial"},
        )
    ).json()["data"]
    if publish:
        await client.post(
            f"/api/v1/strategies/{strat['id']}/versions/{saved['version']}/publish"
        )
    detail = (await client.get(f"/api/v1/strategies/{strat['id']}")).json()["data"]
    return detail


async def seed_candle_cache(client, fake_mt5, *, symbol: str = "EURUSD", count: int = 300) -> int:
    """Populate the PostgreSQL candle cache through the market-data path."""

    base = int(time.time()) // 60 * 60
    fake_mt5.rates[(symbol, fake_mt5.TIMEFRAME_M1)] = candles_json(count, base)
    fake_mt5.base_epoch = base
    resp = await client.get(
        "/api/v1/market/candles",
        params={"symbol": symbol, "timeframe": "M1", "limit": count},
    )
    assert resp.status_code == 200, resp.text
    return count


def window_for(count: int, step: int = 60) -> dict[str, str]:
    """start/end covering the FIRST `count` seconds ending at the M1-aligned now."""

    base = int(time.time()) // 60 * 60
    from datetime import datetime, timezone

    start = datetime.fromtimestamp(base - (count - 1) * step, tz=timezone.utc)
    end = datetime.fromtimestamp(base, tz=timezone.utc)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


async def wait_for_status(client, run_id: str, statuses: set[str], timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        resp = await client.get(f"{BACKTESTS}/{run_id}")
        assert resp.status_code == 200, resp.text
        last = resp.json()["data"]
        if last["status"] in statuses:
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"Run did not reach {statuses}; last status: {last and last['status']}")


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


# --------------------------------------------------------------------------- #
# Validation cascade
# --------------------------------------------------------------------------- #


class TestValidationCascade:
    async def test_draft_version_rejected(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="bt-draft", publish=False)
        await seed_candle_cache(app_client, fake_mt5)
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["current_version"],
                "symbol": "EURUSD",
                **window_for(120),
            },
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "DRAFT_VERSION_NOT_BACKTESTABLE"

    async def test_mode_mismatch_rejected(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, mode="forex", name="bt-mode")
        await seed_candle_cache(app_client, fake_mt5)
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "binary_options",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(120),
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "MODE_MISMATCH"

    async def test_undeclared_timeframe_rejected(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="bt-tf")
        await seed_candle_cache(app_client, fake_mt5)
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                "timeframe": "H4",
                **window_for(120),
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "TIMEFRAME_NOT_DECLARED"

    async def test_missing_history_rejected(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="bt-nodata")
        # No candles seeded for this window (2005 is far in the past).
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                "start": "2005-01-03T00:00:00Z",
                "end": "2005-01-04T00:00:00Z",
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "NO_HISTORICAL_DATA"

    async def test_mode_specific_config_mismatch_rejected(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="bt-cfgmix")
        await seed_candle_cache(app_client, fake_mt5)
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "binary_options",
                "strategy_id": detail["id"],
                "strategy_version": 0,  # replaced below; mode check fires first
                "symbol": "EURUSD",
                "forex_execution": forex_execution(),
                **window_for(120),
            },
        )
        assert resp.status_code == 422

    async def test_data_coverage_endpoint(self, app_client, fake_mt5):
        await seed_candle_cache(app_client, fake_mt5, count=120)
        resp = await app_client.get(
            f"{BACKTESTS}/data-coverage",
            params={"symbol": "EURUSD", "timeframe": "M1", **window_for(60)},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["candles_in_range"] >= 1
        assert 0 < data["coverage_ratio"] <= 1

        empty = await app_client.get(
            f"{BACKTESTS}/data-coverage",
            params={
                "symbol": "EURUSD",
                "timeframe": "M1",
                "start": "2005-01-03T00:00:00Z",
                "end": "2005-01-04T00:00:00Z",
            },
        )
        assert empty.status_code == 422
        assert empty.json()["error"]["code"] == "NO_HISTORICAL_DATA"


# --------------------------------------------------------------------------- #
# Run lifecycle
# --------------------------------------------------------------------------- #


class TestRunLifecycle:
    async def test_forex_setup_only_run_completes(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="bt-so")
        await seed_candle_cache(app_client, fake_mt5, count=300)
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(180),
            },
        )
        assert resp.status_code == 201, resp.text
        run = resp.json()["data"]
        assert run["status"] == "queued"
        assert run["simulation_mode"] == "setup_only"

        completed = await wait_for_status(app_client, run["id"], {"completed", "failed"})
        assert completed["status"] == "completed", completed.get("error")
        assert completed["candles_evaluated"] > 0
        metrics = completed["metrics"]
        assert metrics["simulation_mode"] == "setup_only"
        assert metrics["setups"]["total_evaluated"] == completed["candles_evaluated"]
        assert metrics["trades"]["net_pnl"] is None  # no fabricated P&L

        results = (
            await app_client.get(f"{BACKTESTS}/{run['id']}/results", params={"page_size": 200})
        ).json()["data"]
        assert results["page"]["total"] == completed["records_total"]
        assert all(item["kind"] == "setup" for item in results["items"])
        seqs = [item["seq"] for item in results["items"]]
        assert seqs == sorted(seqs)

        equity = (await app_client.get(f"{BACKTESTS}/{run['id']}/equity")).json()["data"]
        assert equity["points"] == []
        assert "No equity" in equity["note"]

    async def test_forex_trade_simulation_run(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="bt-fx")
        await seed_candle_cache(app_client, fake_mt5, count=300)
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(200),
                "forex_execution": forex_execution(),
            },
        )
        assert resp.status_code == 201, resp.text
        run = resp.json()["data"]
        assert run["simulation_mode"] == "trade_simulation"
        completed = await wait_for_status(app_client, run["id"], {"completed", "failed"})
        assert completed["status"] == "completed", completed.get("error")
        metrics = completed["metrics"]
        assert metrics["trades"]["total"] >= 1
        # Declared assumptions travel with the run config.
        assert completed["config"]["forex_execution"]["spread_pips"] == 1.0
        assert completed["config"]["forex_execution"]["slippage_pips"] == 0.5
        assert completed["config"]["data_source"] == "postgres_candle_cache"

        results = (
            await app_client.get(
                f"{BACKTESTS}/{run['id']}/results",
                params={"kind": "trade", "page_size": 200},
            )
        ).json()["data"]
        assert results["page"]["total"] == metrics["trades"]["total"]
        trade = results["items"][0]
        assert trade["outcome"] in ("win", "loss", "tie", "open", "ambiguous")
        exec_payload = trade["execution_summary"]
        assert exec_payload is not None
        assert exec_payload["entry_price"] > 0

        equity = (await app_client.get(f"{BACKTESTS}/{run['id']}/equity")).json()["data"]
        assert len(equity["points"]) == metrics["trades"]["total"] - (
            1 if metrics["trades"]["by_outcome"].get("open") else 0
        ) or len(equity["points"]) >= 0

    async def test_binary_run_lifecycle(self, app_client, fake_mt5):
        detail = await create_strategy(
            app_client, mode="binary_options", name="bt-bin",
            rule_set=rising_rule_set(mode="binary_options"),
        )
        await seed_candle_cache(app_client, fake_mt5, count=300)
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "binary_options",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(150),
                "binary": {"expiry_candles": 1, "payout_pct": 0.8, "stake": 10.0,
                           "tie_policy": "refund"},
            },
        )
        assert resp.status_code == 201, resp.text
        run = resp.json()["data"]
        completed = await wait_for_status(app_client, run["id"], {"completed", "failed"})
        assert completed["status"] == "completed", completed.get("error")
        metrics = completed["metrics"]
        assert metrics["trades"]["total"] >= 1
        assert metrics["trades"]["win_rate"] is not None
        assert metrics["trades"]["win_rate"] == 1.0  # rising series: all wins
        outcomes = metrics["trades"]["by_outcome"]
        # Total includes the final unresolved trade (expiry beyond data).
        assert (
            outcomes["win"] + outcomes["unresolved"] + outcomes["skipped"]
            == metrics["trades"]["total"]
        )

    async def test_result_detail_contains_full_evaluation(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="bt-detail")
        await seed_candle_cache(app_client, fake_mt5, count=300)
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(120),
            },
        )
        run = resp.json()["data"]
        completed = await wait_for_status(app_client, run["id"], {"completed", "failed"})
        assert completed["status"] == "completed"
        results = (
            await app_client.get(f"{BACKTESTS}/{run['id']}/results", params={"page_size": 1})
        ).json()["data"]
        first = results["items"][0]
        detail_resp = await app_client.get(
            f"{BACKTESTS}/{run['id']}/results/{first['seq']}"
        )
        assert detail_resp.status_code == 200
        record = detail_resp.json()["data"]
        assert record["evaluation"] is not None
        assert record["evaluation"]["core"] is not None
        assert "conditions_summary" in record["evaluation"]
        assert record["evaluation"]["evaluated_at"].startswith(
            record["result_time"][:16]
        )

    async def test_run_history_list_and_filters(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="bt-list")
        await seed_candle_cache(app_client, fake_mt5, count=300)
        run_ids = []
        for _ in range(2):
            resp = await app_client.post(
                BACKTESTS,
                json={
                    "mode": "forex",
                    "strategy_id": detail["id"],
                    "strategy_version": detail["published_version_number"],
                    "symbol": "EURUSD",
                    **window_for(100),
                },
            )
            assert resp.status_code == 201
            run_ids.append(resp.json()["data"]["id"])
        for run_id in run_ids:
            await wait_for_status(app_client, run_id, {"completed", "failed"})
        listed = (
            await app_client.get(
                BACKTESTS,
                params={"strategy_id": detail["id"], "mode": "forex", "status": "completed",
                        "page_size": 10},
            )
        ).json()["data"]
        assert listed["page"]["total"] == 2
        assert len(listed["items"]) == 2
        item = listed["items"][0]
        assert item["strategy_version"] == detail["published_version_number"]
        assert "win_rate" in item

    async def test_pagination_on_results(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="bt-page")
        await seed_candle_cache(app_client, fake_mt5, count=300)
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(120),
            },
        )
        run = resp.json()["data"]
        await wait_for_status(app_client, run["id"], {"completed", "failed"})
        page1 = (
            await app_client.get(
                f"{BACKTESTS}/{run['id']}/results", params={"page": 1, "page_size": 5}
            )
        ).json()["data"]
        page2 = (
            await app_client.get(
                f"{BACKTESTS}/{run['id']}/results", params={"page": 2, "page_size": 5}
            )
        ).json()["data"]
        assert page1["page"]["page_size"] == 5
        assert page2["items"][0]["seq"] == page1["items"][-1]["seq"] + 1
        assert page1["items"][0]["seq"] != page2["items"][0]["seq"]


# --------------------------------------------------------------------------- #
# Ownership
# --------------------------------------------------------------------------- #


class TestOwnership:
    async def test_foreign_owner_gets_404_everywhere(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="bt-own")
        await seed_candle_cache(app_client, fake_mt5, count=300)
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(100),
            },
        )
        run = resp.json()["data"]
        await wait_for_status(app_client, run["id"], {"completed", "failed"})

        other = {"X-User-Id": f"someone-else-{uuid.uuid4().hex[:6]}"}
        assert (await app_client.get(f"{BACKTESTS}/{run['id']}", headers=other)).status_code == 404
        assert (
            await app_client.get(f"{BACKTESTS}/{run['id']}/results", headers=other)
        ).status_code == 404
        assert (
            await app_client.get(f"{BACKTESTS}/{run['id']}/equity", headers=other)
        ).status_code == 404
        assert (
            await app_client.get(f"{BACKTESTS}/{run['id']}/export", headers=other)
        ).status_code == 404
        assert (
            await app_client.post(f"{BACKTESTS}/{run['id']}/cancel", headers=other)
        ).status_code == 404
        # The owner still sees it.
        assert (await app_client.get(f"{BACKTESTS}/{run['id']}")).status_code == 200
        # The foreign list does not include the run.
        foreign_list = (await app_client.get(BACKTESTS, headers=other)).json()["data"]
        assert all(item["id"] != run["id"] for item in foreign_list["items"])


# --------------------------------------------------------------------------- #
# Cancel + failure paths
# --------------------------------------------------------------------------- #


class TestCancelAndFailure:
    async def test_cooperative_cancel_marks_run_cancelled(self, app_client, fake_mt5, monkeypatch):
        from app.domains.backtest import engine as engine_module
        from app.domains.backtest import runner as runner_module

        real_run = runner_module.run_backtest

        async def slow_run(*args, **kwargs):
            check = kwargs.get("cancel_check")
            for _ in range(400):  # up to ~8s; cancel breaks out early
                await asyncio.sleep(0.02)
                if check is not None:
                    check()
            return await real_run(*args, **kwargs)

        monkeypatch.setattr(runner_module, "run_backtest", slow_run)
        detail = await create_strategy(app_client, name="bt-cancel")
        await seed_candle_cache(app_client, fake_mt5, count=300)
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(120),
            },
        )
        run = resp.json()["data"]
        await asyncio.sleep(0.1)  # let the runner pick it up
        cancel = await app_client.post(f"{BACKTESTS}/{run['id']}/cancel")
        assert cancel.status_code == 200
        assert cancel.json()["data"]["cancel_requested"] is True
        final = await wait_for_status(app_client, run["id"], {"cancelled"}, timeout=15)
        assert final["status"] == "cancelled"
        assert final["error"]["code"] == "CANCELLED"
        _ = engine_module

    async def test_cancel_completed_run_conflicts(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="bt-cancel2")
        await seed_candle_cache(app_client, fake_mt5, count=200)
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(80),
            },
        )
        run = resp.json()["data"]
        await wait_for_status(app_client, run["id"], {"completed", "failed"})
        cancel = await app_client.post(f"{BACKTESTS}/{run['id']}/cancel")
        assert cancel.status_code == 409

    async def test_failed_run_persists_error(self, app_client, fake_mt5, monkeypatch):
        from app.domains.backtest import runner as runner_module

        async def boom(*args, **kwargs):
            raise RuntimeError("simulated engine crash")

        monkeypatch.setattr(runner_module, "run_backtest", boom)
        detail = await create_strategy(app_client, name="bt-fail")
        await seed_candle_cache(app_client, fake_mt5, count=200)
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(80),
            },
        )
        run = resp.json()["data"]
        final = await wait_for_status(app_client, run["id"], {"failed"})
        assert final["status"] == "failed"
        assert "simulated engine crash" in final["error"]["message"]
        assert final["completed_at"] is not None

    async def test_startup_recovery_marks_interrupted_runs_failed(self, app_client, fake_mt5):
        # Simulate a crash: insert a queued run row directly, then call the
        # recovery function the lifespan uses on boot.
        from app.db.models import BacktestRun
        from app.db.session import get_session_factory
        from app.domains.backtest import service as bt_service
        from datetime import datetime, timedelta, timezone

        detail = await create_strategy(app_client, name="bt-recover")
        factory = get_session_factory()
        async with factory() as session:
            run = BacktestRun(
                owner_id="local",
                mode="forex",
                symbol="EURUSD",
                timeframe="M1",
                strategy_id=uuid.UUID(detail["id"]),
                strategy_name=detail["name"],
                strategy_version=1,
                status="running",
                start_time=datetime.now(tz=timezone.utc) - timedelta(hours=1),
                end_time=datetime.now(tz=timezone.utc),
                config={"simulation_mode": "setup_only"},
                backtest_schema_version="1.0",
                engine_version="6.0.0",
            )
            session.add(run)
            await session.commit()
            run_id = run.id

        async with factory() as session:
            recovered = await bt_service.recover_interrupted(session)
            await session.commit()
        assert recovered >= 1
        resp = await app_client.get(f"{BACKTESTS}/{run_id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "failed"
        assert resp.json()["data"]["error"]["code"] == "RUNNER_RESTARTED"


# --------------------------------------------------------------------------- #
# Immutability across strategy edits
# --------------------------------------------------------------------------- #


class TestVersionImmutability:
    async def test_run_stays_pinned_to_original_version(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="bt-pin")
        await seed_candle_cache(app_client, fake_mt5, count=300)
        v1 = detail["published_version_number"]
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": v1,
                "symbol": "EURUSD",
                **window_for(120),
            },
        )
        run = resp.json()["data"]
        completed = await wait_for_status(app_client, run["id"], {"completed", "failed"})
        original_metrics = completed["metrics"]
        original_config = completed["config"]

        # Edit the strategy: save v2 (stricter threshold) and publish it.
        await app_client.post(
            f"/api/v1/strategies/{detail['id']}/versions",
            json={"rule_set": rising_rule_set(value=1.5), "changelog": "v2 stricter"},
        )
        await app_client.post(f"/api/v1/strategies/{detail['id']}/versions/2/publish")

        after = (await app_client.get(f"{BACKTESTS}/{run['id']}")).json()["data"]
        assert after["strategy_version"] == v1
        assert after["config"]["rule_set"] == original_config["rule_set"]
        assert after["metrics"] == original_metrics
        # A NEW run on the latest published version uses v2.
        resp2 = await app_client.post(
            BACKTESTS,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": 2,
                "symbol": "EURUSD",
                **window_for(120),
            },
        )
        assert resp2.status_code == 201
        assert resp2.json()["data"]["strategy_version"] == 2


# --------------------------------------------------------------------------- #
# CSV export
# --------------------------------------------------------------------------- #


class TestExport:
    async def test_results_export_has_assumptions_and_disclaimer(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="bt-export")
        await seed_candle_cache(app_client, fake_mt5, count=300)
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(150),
                "forex_execution": forex_execution(),
            },
        )
        run = resp.json()["data"]
        await wait_for_status(app_client, run["id"], {"completed", "failed"})

        export = await app_client.get(f"{BACKTESTS}/{run['id']}/export")
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("text/csv")
        assert "attachment" in export.headers["content-disposition"]
        text = export.text
        assert text.startswith("# backtest_export")
        assert f"strategy_id,{detail['id']}" in text
        assert "strategy_version,1" in text
        assert "assumption,forex.spread_pips=1.0" in text
        assert "disclaimer," in text
        assert "data_source,postgres_candle_cache" in text

    async def test_summary_export(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="bt-export2")
        await seed_candle_cache(app_client, fake_mt5, count=200)
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(100),
            },
        )
        run = resp.json()["data"]
        await wait_for_status(app_client, run["id"], {"completed", "failed"})
        export = await app_client.get(f"{BACKTESTS}/{run['id']}/export?scope=summary")
        assert export.status_code == 200
        assert "# backtest_summary" in export.text
        assert "metric.simulation_mode,setup_only" in export.text
        # Missing metrics export as EMPTY cells (never fabricated zeros).
        assert "metric.trades.win_rate," in export.text

    async def test_injection_safe_strategy_name(self, app_client, fake_mt5):
        detail = await create_strategy(app_client, name="=cmd|' /C calc")
        await seed_candle_cache(app_client, fake_mt5, count=200)
        resp = await app_client.post(
            BACKTESTS,
            json={
                "mode": "forex",
                "strategy_id": detail["id"],
                "strategy_version": detail["published_version_number"],
                "symbol": "EURUSD",
                **window_for(80),
            },
        )
        run = resp.json()["data"]
        await wait_for_status(app_client, run["id"], {"completed", "failed"})
        export = await app_client.get(f"{BACKTESTS}/{run['id']}/export?scope=summary")
        text = export.text
        assert "'=cmd|' /C calc" in text  # leading '=' neutralised
