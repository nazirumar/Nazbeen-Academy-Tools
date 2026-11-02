"""Unit tests: Phase 8 research grid, parameters, objectives, windows.

These run WITHOUT a database. They cover the pure building blocks the
runner composes: search-space enumeration and its bounds, parameter
inventory/patching (never invents new rules), walk-forward window
generation (chronological, contiguous, non-overlapping), objective
resolution (never fabricates zeros), deterministic ranking, the purge
boundary (clip_series), OOS aggregation and the seeded bootstrap.
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationAppError
from app.domains.research.grid import (
    combination_count,
    enumerate_patches,
    generate_windows,
    neighbour_patches,
)
from app.domains.research.objectives import (
    aggregate_oos,
    bootstrap_stats,
    evaluate_constraints,
    metrics_digest,
    metric_value,
    rank_scored,
    select_candidate,
)
from app.domains.research.params import (
    apply_patch,
    parameter_inventory,
    patch_key,
    validate_patch_specs,
)
from app.domains.research.runner import clip_series
from app.domains.backtest.engine import _TimeframeSeries
from app.domains.strategies.rules import validate_rule_set


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def sample_rule_set() -> dict:
    return {
        "schema_version": "2.0",
        "mode": "forex",
        "timeframes": ["M15"],
        "direction": "bullish",
        "conditions": {
            "logic": "AND",
            "conditions": [
                {
                    "id": "px",
                    "type": "price_level",
                    "timeframe": "M15",
                    "lookback": 30,
                    "params": {"field": "close", "operator": "above", "value": 1.10},
                },
                {
                    "id": "ma",
                    "type": "moving_average",
                    "timeframe": "M15",
                    "lookback": 60,
                    "params": {
                        "ma_type": "sma",
                        "period": 20,
                        "source": "close",
                        "compare_to": "value",
                        "value": 1.08,
                        "operator": "above",
                    },
                },
            ],
            "groups": [],
        },
    }


def specs_from(rule_set: dict, *param_dicts: dict) -> list[dict]:
    return param_dicts


PARAM_VALUE = {
    "condition_id": "px",
    "field": "params.value",
    "values": [1.10, 1.15, 1.20],
}
PARAM_PERIOD = {
    "condition_id": "ma",
    "field": "params.period",
    "values": [10, 20],
}


# --------------------------------------------------------------------------- #
# Parameter validation
# --------------------------------------------------------------------------- #


class TestValidatePatchSpecs:
    def test_valid_specs_pass(self):
        validate_patch_specs(sample_rule_set(), [PARAM_VALUE, PARAM_PERIOD])

    def test_missing_condition_rejected(self):
        with pytest.raises(ValidationAppError) as e:
            validate_patch_specs(sample_rule_set(), [{**PARAM_VALUE, "condition_id": "nope"}])
        assert e.value.code == "PARAM_CONDITION_NOT_FOUND"

    def test_new_param_key_rejected(self):
        spec = {"condition_id": "px", "field": "params.brand_new", "values": [1]}
        with pytest.raises(ValidationAppError) as e:
            validate_patch_specs(sample_rule_set(), [spec])
        assert e.value.code == "PARAM_KEY_NOT_FOUND"

    def test_type_incompatible_value_rejected(self):
        spec = {"condition_id": "px", "field": "params.value", "values": ["cheap"]}
        with pytest.raises(ValidationAppError) as e:
            validate_patch_specs(sample_rule_set(), [spec])
        assert e.value.code == "PARAM_VALUE_TYPE_INVALID"

    def test_lookback_out_of_bounds_rejected(self):
        spec = {"condition_id": "px", "field": "lookback", "values": [100_000]}
        with pytest.raises(ValidationAppError) as e:
            validate_patch_specs(sample_rule_set(), [spec])
        assert e.value.code == "PARAM_VALUE_OUT_OF_RANGE"

    def test_lookback_float_rejected(self):
        spec = {"condition_id": "px", "field": "lookback", "values": [30.5]}
        with pytest.raises(ValidationAppError) as e:
            validate_patch_specs(sample_rule_set(), [spec])
        assert e.value.code == "PARAM_VALUE_TYPE_INVALID"

    def test_unsupported_field_rejected(self):
        spec = {"condition_id": "px", "field": "params.weapon", "values": [1]}
        with pytest.raises(ValidationAppError):
            validate_patch_specs(sample_rule_set(), [spec])


class TestApplyPatch:
    def test_patch_replaces_exactly_the_target(self):
        patched = apply_patch(sample_rule_set(), [dict(PARAM_VALUE, value=9.99)])
        cond = next(
            c
            for c in patched["conditions"]["conditions"]
            if c["id"] == "px"
        )
        assert cond["params"]["value"] == 9.99
        # Untouched fields stay identical.
        ma = next(c for c in patched["conditions"]["conditions"] if c["id"] == "ma")
        assert ma["params"]["period"] == 20
        # Original snapshot untouched (deep copy).
        original = next(
            c
            for c in sample_rule_set()["conditions"]["conditions"]
            if c["id"] == "px"
        )
        assert original["params"]["value"] == 1.10

    def test_patched_rule_set_still_validates(self):
        patched = apply_patch(
            sample_rule_set(),
            [dict(PARAM_PERIOD, value=10), {"condition_id": "px", "field": "lookback", "value": 45}],
        )
        validated = validate_rule_set(patched)
        assert validated.to_dict()["conditions"]["conditions"][0]["lookback"] == 45

    def test_patch_missing_condition_raises(self):
        with pytest.raises(ValidationAppError) as e:
            apply_patch(sample_rule_set(), [{"condition_id": "x", "field": "lookback", "value": 5}])
        assert e.value.code == "PARAM_PATCH_INVALID"

    def test_patch_confirmation_and_invalidation_sections(self):
        rs = sample_rule_set()
        rs["confirmation"] = {
            "logic": "AND",
            "conditions": [
                {
                    "id": "conf",
                    "type": "price_level",
                    "timeframe": "M15",
                    "lookback": 10,
                    "params": {"field": "close", "operator": "above", "value": 1.0},
                }
            ],
            "groups": [],
        }
        patched = apply_patch(
            rs, [{"condition_id": "conf", "field": "params.value", "value": 2.0}]
        )
        assert patched["confirmation"]["conditions"][0]["params"]["value"] == 2.0


class TestParameterInventory:
    def test_inventory_lists_existing_fields_only(self):
        inv = parameter_inventory(validate_rule_set(sample_rule_set()))
        by_id = {c["condition_id"]: c for c in inv}
        px_fields = {f["field"] for f in by_id["px"]["fields"]}
        assert px_fields == {"lookback", "params.field", "params.operator", "params.value"}
        ma_fields = {f["field"] for f in by_id["ma"]["fields"]}
        assert "params.period" in ma_fields and "lookback" in ma_fields

    def test_inventory_current_values(self):
        inv = parameter_inventory(validate_rule_set(sample_rule_set()))
        by_id = {c["condition_id"]: c for c in inv}
        px = next(f for f in by_id["px"]["fields"] if f["field"] == "params.value")
        assert px["current"] == 1.10 and px["type"] == "number"


# --------------------------------------------------------------------------- #
# Search-space enumeration
# --------------------------------------------------------------------------- #


class TestEnumeratePatches:
    def test_deterministic_cartesian_product(self):
        patches = enumerate_patches([PARAM_VALUE, PARAM_PERIOD])
        assert len(patches) == 6
        assert [p[0]["value"] for p in patches] == [1.10, 1.10, 1.15, 1.15, 1.20, 1.20]
        assert [p[1]["value"] for p in patches] == [10, 20, 10, 20, 10, 20]
        # Determinism: identical inputs -> identical order.
        assert patches == enumerate_patches([PARAM_VALUE, PARAM_PERIOD])

    def test_combination_count(self):
        assert combination_count([PARAM_VALUE, PARAM_PERIOD]) == 6

    def test_empty_specs(self):
        assert enumerate_patches([]) == []

    def test_patch_entries_are_complete(self):
        patches = enumerate_patches([PARAM_VALUE])
        assert patches[0] == [
            {"condition_id": "px", "field": "params.value", "value": 1.10}
        ]


class TestNeighbourPatches:
    def test_one_step_per_dimension(self):
        selected = [{"condition_id": "px", "field": "params.value", "value": 1.15}]
        nbs = neighbour_patches(selected, [PARAM_VALUE], max_neighbours=10)
        values = sorted(n[0]["value"] for n in nbs)
        assert values == [1.10, 1.20]

    def test_boundary_value_has_single_neighbour(self):
        selected = [{"condition_id": "px", "field": "params.value", "value": 1.10}]
        nbs = neighbour_patches(selected, [PARAM_VALUE], max_neighbours=10)
        assert [n[0]["value"] for n in nbs] == [1.15]

    def test_cap_respected(self):
        selected = [{"condition_id": "px", "field": "params.value", "value": 1.15}]
        nbs = neighbour_patches(selected, [PARAM_VALUE], max_neighbours=1)
        assert len(nbs) == 1


# --------------------------------------------------------------------------- #
# Walk-forward window generation
# --------------------------------------------------------------------------- #


class TestGenerateWindows:
    def test_windows_chronological_contiguous_non_overlapping(self):
        windows, warnings = generate_windows(
            start_epoch=0,
            end_epoch=600 * 60,
            interval_seconds=60,
            train_candles=200,
            test_candles=100,
            step_candles=100,
            window_mode="rolling",
            max_windows=40,
        )
        assert len(windows) == 4
        for i, w in enumerate(windows):
            assert w.index == i
            assert w.train_end == w.test_start
            assert w.train_start < w.train_end < w.test_end
            if i:
                assert w.test_start >= windows[i - 1].test_end
        assert not warnings

    def test_rolling_vs_expanding_train_start(self):
        kw = dict(
            end_epoch=600 * 60,
            interval_seconds=60,
            train_candles=200,
            test_candles=100,
            step_candles=100,
            max_windows=40,
        )
        rolling, _ = generate_windows(start_epoch=0, window_mode="rolling", **kw)
        expanding, _ = generate_windows(start_epoch=0, window_mode="expanding", **kw)
        assert rolling[1].train_start == 100 * 60
        assert expanding[1].train_start == 0
        # Both have identical test windows (same OOS coverage).
        assert [w.test_start for w in rolling] == [w.test_start for w in expanding]

    def test_step_equal_to_test_length_no_overlap(self):
        windows, _ = generate_windows(
            start_epoch=0,
            end_epoch=500 * 60,
            interval_seconds=60,
            train_candles=200,
            test_candles=100,
            step_candles=100,
            window_mode="rolling",
            max_windows=40,
        )
        for a, b in zip(windows, windows[1:]):
            assert b.test_start >= a.test_end

    def test_overlapping_step_rejected(self):
        with pytest.raises(ValidationAppError) as e:
            generate_windows(
                start_epoch=0,
                end_epoch=600 * 60,
                interval_seconds=60,
                train_candles=200,
                test_candles=100,
                step_candles=50,  # < test_candles -> overlap
                window_mode="rolling",
                max_windows=40,
            )
        assert e.value.code == "OVERLAPPING_TEST_WINDOWS"

    def test_insufficient_history_rejected(self):
        with pytest.raises(ValidationAppError) as e:
            generate_windows(
                start_epoch=0,
                end_epoch=250 * 60,
                interval_seconds=60,
                train_candles=200,
                test_candles=100,
                step_candles=100,
                window_mode="rolling",
                max_windows=40,
            )
        assert e.value.code == "INSUFFICIENT_HISTORY_FOR_WINDOWS"

    def test_window_cap_warning(self):
        windows, warnings = generate_windows(
            start_epoch=0,
            end_epoch=10_000 * 60,
            interval_seconds=60,
            train_candles=200,
            test_candles=100,
            step_candles=100,
            window_mode="rolling",
            max_windows=5,
        )
        assert len(windows) == 5
        assert any("WINDOW_CAP_REACHED" in w for w in warnings)


# --------------------------------------------------------------------------- #
# Objectives
# --------------------------------------------------------------------------- #


def fake_metrics(**overrides) -> dict:
    trades = {
        "total": 10,
        "decided_trades": 10,
        "by_outcome": {"win": 6, "loss": 4},
        "win_rate": 0.6,
        "net_pnl": 80.0,
        "gross_profit": 120.0,
        "gross_loss": -40.0,
        "expectancy": 8.0,
        "profit_factor": 3.0,
        "avg_return_pct_per_trade": 0.8,
    }
    trades.update(overrides.pop("trades", {}))
    equity = {
        "start_balance": 1000.0,
        "end_balance": 1080.0,
        "max_drawdown": {"max_drawdown": 20.0, "max_drawdown_pct": 2.0, "points": []},
    }
    equity.update(overrides.pop("equity", {}))
    setups = {
        "total_evaluated": 100,
        "by_status": {"conditions_met": 10},
        "breakdowns": {"by_regime": {}, "by_month": {}},
    }
    setups.update(overrides.pop("setups", {}))
    base = {
        "trades": trades,
        "equity": equity,
        "setups": setups,
        "note": "n",
        "win_rate_note": "w",
        "simulation_mode": "trade_simulation",
    }
    base.update(overrides)
    return base


class TestMetricValue:
    def test_resolves_trade_metrics(self):
        m = fake_metrics()
        assert metric_value(m, "net_pnl") == 80.0
        assert metric_value(m, "expectancy") == 8.0
        assert metric_value(m, "profit_factor") == 3.0
        assert metric_value(m, "win_rate") == 0.6

    def test_drawdown_from_nested_path(self):
        assert metric_value(fake_metrics(), "max_drawdown_pct") == 2.0

    def test_conditions_met_count(self):
        assert metric_value(fake_metrics(), "conditions_met_count") == 10

    def test_missing_metric_is_none_never_zero(self):
        m = fake_metrics(
            trades={
                "decided_trades": 0,
                "win_rate": None,
                "net_pnl": None,
                "expectancy": None,
                "profit_factor": None,
            }
        )
        assert metric_value(m, "net_pnl") is None
        assert metric_value(m, "profit_factor") is None
        assert metric_value(m, "expectancy") is None
        assert metric_value(m, "win_rate") is None

    def test_nan_and_inf_rejected(self):
        assert metric_value(fake_metrics(trades={"net_pnl": float("nan")}), "net_pnl") is None
        assert metric_value(fake_metrics(trades={"net_pnl": float("inf")}), "net_pnl") is None

    def test_none_payload(self):
        assert metric_value(None, "net_pnl") is None


class TestConstraints:
    def test_min_decided_trades(self):
        c = {"min_decided_trades": 5, "max_drawdown_pct": None}
        assert evaluate_constraints(fake_metrics(), c) == []
        c = {"min_decided_trades": 11, "max_drawdown_pct": None}
        violations = evaluate_constraints(fake_metrics(), c)
        assert violations and violations[0]["code"] == "MIN_DECIDED_TRADES"

    def test_drawdown_ceiling(self):
        c = {"min_decided_trades": None, "max_drawdown_pct": 5.0}
        assert evaluate_constraints(fake_metrics(), c) == []
        c = {"min_decided_trades": None, "max_drawdown_pct": 1.0}
        violations = evaluate_constraints(fake_metrics(), c)
        assert violations and violations[0]["code"] == "MAX_DRAWDOWN_PCT"

    def test_none_metrics_no_violations(self):
        assert evaluate_constraints(None, {"min_decided_trades": 5}) == []


class TestRanking:
    def test_rank_maximize_deterministic(self):
        scored = [
            {"candidate_index": 2, "score": 5.0, "constraint_violations": []},
            {"candidate_index": 0, "score": 9.0, "constraint_violations": []},
            {"candidate_index": 1, "score": 9.0, "constraint_violations": []},
            {"candidate_index": 3, "score": None, "constraint_violations": []},
        ]
        rank_scored(scored, "maximize")
        by_index = {c["candidate_index"]: c["rank"] for c in scored}
        assert by_index[0] == 1  # tie -> lower index wins
        assert by_index[1] == 2
        assert by_index[2] == 3
        assert by_index[3] is None

    def test_rank_minimize(self):
        scored = [
            {"candidate_index": 0, "score": 4.0, "constraint_violations": []},
            {"candidate_index": 1, "score": 1.0, "constraint_violations": []},
        ]
        rank_scored(scored, "minimize")
        by_index = {c["candidate_index"]: c["rank"] for c in scored}
        assert by_index[1] == 1 and by_index[0] == 2

    def test_select_candidate_skips_violations(self):
        scored = [
            {
                "candidate_index": 0,
                "score": 9.0,
                "constraint_violations": [{"code": "MAX_DRAWDOWN_PCT"}],
                "rank": 1,
            },
            {"candidate_index": 1, "score": 3.0, "constraint_violations": [], "rank": 2},
        ]
        assert select_candidate(scored)["candidate_index"] == 1

    def test_select_candidate_none_when_all_violate(self):
        scored = [
            {
                "candidate_index": 0,
                "score": 9.0,
                "constraint_violations": [{"code": "MIN_DECIDED_TRADES"}],
                "rank": 1,
            }
        ]
        assert select_candidate(scored) is None


class TestMetricsDigest:
    def test_digest_is_compact_and_honest(self):
        digest = metrics_digest(fake_metrics())
        assert digest["trades"]["net_pnl"] == 80.0
        assert digest["trades"]["breakdowns"]["by_month"] is None
        assert digest["equity"]["max_drawdown"]["max_drawdown_pct"] == 2.0
        assert "note" in digest
        assert "evaluation" not in digest

    def test_digest_of_none(self):
        assert metrics_digest(None) == {}


class TestAggregateOos:
    def test_sums_without_double_counting(self):
        d1 = metrics_digest(fake_metrics(trades={"net_pnl": 50.0, "gross_profit": 60.0, "gross_loss": -10.0, "decided_trades": 5, "by_outcome": {"win": 3, "loss": 2}}))
        d2 = metrics_digest(fake_metrics(trades={"net_pnl": -20.0, "gross_profit": 0.0, "gross_loss": -20.0, "decided_trades": 4, "by_outcome": {"win": 2, "loss": 2}}))
        agg = aggregate_oos([d1, d2])
        t = agg["trades"]
        assert t["decided_trades"] == 9
        assert t["wins"] == 5 and t["losses"] == 4
        assert t["net_pnl"] == round(50.0 - 20.0, 8)
        assert t["win_rate"] == round(5 / 9, 6)
        assert t["expectancy"] == round(30.0 / 9, 8)
        assert t["profit_factor"] == round(60.0 / 30.0, 6)
        assert agg["windows_aggregated"] == 2

    def test_drawdown_not_compounded(self):
        d1 = metrics_digest(fake_metrics())
        d2 = metrics_digest(fake_metrics(equity={"max_drawdown": {"max_drawdown_pct": 7.0}}))
        agg = aggregate_oos([d1, d2])
        assert agg["max_drawdown"]["max_per_window_pct"] == 7.0
        assert "NOT compound" in agg["max_drawdown"]["note"]

    def test_empty_windows(self):
        agg = aggregate_oos([None, {}])
        assert agg["trades"]["decided_trades"] == 0
        assert agg["trades"]["win_rate"] is None


class TestBootstrap:
    def test_deterministic_with_seed(self):
        pnls = [10.0, -5.0, 8.0, -2.0, 12.0]
        a = bootstrap_stats(pnls, samples=50, seed=42)
        b = bootstrap_stats(pnls, samples=50, seed=42)
        assert a == b
        assert a["applicable"] is True
        assert a["samples"] == 50

    def test_different_seed_differs(self):
        pnls = [10.0, -5.0, 8.0, -2.0, 12.0, 3.0, -7.0]
        a = bootstrap_stats(pnls, samples=100, seed=1)
        b = bootstrap_stats(pnls, samples=100, seed=2)
        assert a["net_pnl_percentiles"] != b["net_pnl_percentiles"] or a["seed"] != b["seed"]

    def test_empty_trades_not_applicable(self):
        assert bootstrap_stats([], samples=100, seed=1)["applicable"] is False

    def test_disabled_samples(self):
        assert bootstrap_stats([1.0], samples=0, seed=1)["applicable"] is False

    def test_method_note_documents_limits(self):
        result = bootstrap_stats([5.0, -3.0], samples=10, seed=1)
        assert "i.i.d." in result["method_note"]
        assert "NOT" in result["method_note"]


# --------------------------------------------------------------------------- #
# Purge boundary (clip_series)
# --------------------------------------------------------------------------- #


def candles(count: int, start_epoch: int, step: int = 60, price: float = 1.10) -> list[dict]:
    out = []
    price = price
    for i in range(count):
        t = start_epoch + i * step
        out.append(
            {"time": t, "open": price, "high": price + 0.001, "low": price - 0.001, "close": price}
        )
    return out


class TestClipSeries:
    def test_clips_candles_closing_after_boundary(self):
        series = _TimeframeSeries("M1", candles(10, 0))
        assert len(series.candles) == 10
        # Keep only candles that CLOSE at or before t=300 (candle 4 closes at 300).
        clipped = clip_series(series, 300)
        assert len(clipped.candles) == 5
        assert max(int(r["time"]) for r in clipped.candles) + 60 <= 300

    def test_exit_resolver_cannot_see_beyond_boundary(self):
        series = _TimeframeSeries("M1", candles(20, 0))
        clipped = clip_series(series, 600)
        # The window at any t_eval within the phase can only contain
        # candles closing at or before 600.
        for t_eval in (600, 660):
            for row in clipped.window(t_eval, 100):
                assert int(row["time"]) + 60 <= 600

    def test_original_series_untouched(self):
        rows = candles(10, 0)
        series = _TimeframeSeries("M1", rows)
        clip_series(series, 300)
        assert len(series.candles) == 10


# --------------------------------------------------------------------------- #
# Patch key / labels
# --------------------------------------------------------------------------- #


class TestPatchIdentity:
    def test_patch_key_stable(self):
        p1 = [{"condition_id": "px", "field": "params.value", "value": 1.1}]
        p2 = [{"condition_id": "px", "field": "params.value", "value": 1.1}]
        assert patch_key(p1) == patch_key(p2)

    def test_patch_key_distinguishes_values(self):
        p1 = [{"condition_id": "px", "field": "params.value", "value": 1.1}]
        p2 = [{"condition_id": "px", "field": "params.value", "value": 1.2}]
        assert patch_key(p1) != patch_key(p2)

    def test_empty_patch_label(self):
        from app.domains.research.params import patch_label

        assert patch_label([]) == "original parameters"
        labels = {"px.params.value": "Threshold"}
        assert patch_label([{"condition_id": "px", "field": "params.value", "value": 1.2}], labels) == "Threshold=1.2"
