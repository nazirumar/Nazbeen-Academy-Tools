"""Unit tests: Phase 4 evaluation semantics — four-state results,
AND/OR behavior with unknown/unsupported conditions, forming-candle
policy, regime gating and conditions summary."""

from __future__ import annotations

import pytest

from app.domains.analysis.evaluator import (
    STATUS_INCONCLUSIVE,
    STATUS_MET,
    STATUS_NOT_MET,
    STATUS_UNSUPPORTED,
    evaluate_group,
    evaluate_rule_set,
)
from app.domains.analysis.features import confirmed_swings
from app.domains.strategies.rules import Condition, Group, RuleSet, validate_rule_set

START = 1_700_000_000
STEP = 60
TF = "M1"


def candles(*closes: float, start: int = START, step: int = STEP, forming_last: bool = False) -> list[dict]:
    out = []
    price = 100.0
    for i, c in enumerate(closes):
        o = price
        out.append(
            {
                "time": start + i * step,
                "open": o,
                "high": max(o, c) + 0.5,
                "low": min(o, c) - 0.5,
                "close": c,
                "volume": None,
            }
        )
        price = c
    if forming_last and out:
        out[-1]["forming"] = True
    return out


def rule(conditions: list[dict], logic: str = "AND", **kw) -> dict:
    base = {
        "schema_version": "2.0",
        "mode": "forex",
        "timeframes": [TF],
        "direction": "bullish",
        "conditions": {"logic": logic, "conditions": conditions, "groups": []},
    }
    base.update(kw)
    return base


def price_cond(operator: str, value: float, lookback: int = 5, cid: str = "c1", optional: bool = False) -> dict:
    data = {
        "id": cid,
        "type": "price_level",
        "timeframe": TF,
        "lookback": lookback,
        "params": {"field": "close", "operator": operator, "value": value},
    }
    if optional:
        data["optional"] = True
    return data


def run(payload: dict, data: dict[str, list[dict]], **kw):
    return evaluate_rule_set(validate_rule_set(payload), data, **kw)


def find_condition(node: dict, cid: str) -> dict:
    if node["kind"] == "condition" and node["id"] == cid:
        return node
    for child in node.get("children", []):
        found = find_condition(child, cid)
        if found:
            return found
    raise AssertionError(f"condition {cid} not found")


# --------------------------------------------------------------------------- #
# Four-state condition results
# --------------------------------------------------------------------------- #


class TestConditionStates:
    def test_met_and_not_met_are_definitive(self) -> None:
        result = run(rule([price_cond("above", 100.0, cid="a")]), {TF: candles(105)})
        assert result.core_status == "met"
        node = find_condition(result.core.to_dict(), "a")
        assert node["status"] == "met"
        assert node["satisfied"] is True

        result = run(rule([price_cond("above", 200.0, cid="a")]), {TF: candles(105)})
        node = find_condition(result.core.to_dict(), "a")
        assert node["status"] == "not_met"
        assert node["satisfied"] is False

    def test_insufficient_history_is_inconclusive_not_failed(self) -> None:
        # A moving_average(20) with 3 candles CANNOT be evaluated.
        payload = rule([
            {
                "id": "ma",
                "type": "moving_average",
                "timeframe": TF,
                "lookback": 20,
                "params": {"ma_type": "sma", "period": 20, "compare_to": "value",
                           "value": 100.0, "operator": "above"},
            }
        ])
        result = run(payload, {TF: candles(100, 101, 102)})
        node = find_condition(result.core.to_dict(), "ma")
        assert node["status"] == "inconclusive"
        assert "needs at least" in node["evidence"]["reason"]
        # AND must not turn unknown into success...
        assert result.core_status == "inconclusive"
        # ...and the setup must be honestly inconclusive, not "missing".
        assert result.setup_status == "inconclusive"

    def test_inconclusive_and_missing_are_distinct(self) -> None:
        ok_payload = rule([price_cond("above", 100.0, cid="a")])
        # Evaluated but failed -> conditions_missing.
        missing = run(ok_payload, {TF: candles(50)})
        assert missing.setup_status == "conditions_missing"
        # Cannot evaluate (empty series is caught earlier as insufficient,
        # so use an over-long lookback need) -> inconclusive.
        ma_payload = rule([
            {
                "id": "ma",
                "type": "moving_average",
                "timeframe": TF,
                "lookback": 50,
                "params": {"ma_type": "ema", "period": 50, "compare_to": "value",
                           "value": 100.0, "operator": "above"},
            }
        ])
        inconclusive = run(ma_payload, {TF: candles(100, 101)})
        assert inconclusive.setup_status == "inconclusive"

    def test_unsupported_type_reported_by_group(self) -> None:
        # Built directly (validation would reject the type) to exercise the
        # defensive evaluator path.
        condition = Condition(
            id="ob", type="order_block", timeframe=TF, params={}, lookback=10
        )
        group = Group(logic="AND", conditions=(condition,), groups=())
        node = evaluate_group(group, {TF: candles(105)})
        assert node.status == STATUS_UNSUPPORTED
        child = node.children[0]
        assert child.status == STATUS_UNSUPPORTED
        assert child.satisfied is False

    def test_candle_status_evidence(self) -> None:
        result = run(rule([price_cond("above", 100.0, cid="a")]), {TF: candles(105)})
        node = find_condition(result.core.to_dict(), "a")
        assert node["evidence"]["last_candle_status"] == "closed"
        assert node["evidence"]["forming_candle_used"] is False

        # Last candle forming + opt-in -> forming status recorded.
        payload = rule(
            [price_cond("above", 100.0, cid="a")],
            evaluation={"on_close_only": False},
        )
        result = run(payload, {TF: candles(100, 105, forming_last=True)})
        node = find_condition(result.core.to_dict(), "a")
        assert node["evidence"]["last_candle_status"] == "forming"
        assert node["evidence"]["forming_candle_used"] is True


# --------------------------------------------------------------------------- #
# AND/OR with unknown / unsupported
# --------------------------------------------------------------------------- #


class TestGroupSemantics:
    def test_and_never_converts_unknown_into_success(self) -> None:
        ma_payload = rule([
            price_cond("above", 100.0, cid="a"),
            {
                "id": "ma",
                "type": "moving_average",
                "timeframe": TF,
                "lookback": 50,
                "params": {"ma_type": "sma", "period": 50, "compare_to": "value",
                           "value": 100.0, "operator": "above"},
            },
        ])
        result = run(ma_payload, {TF: candles(105)})
        statuses = {c["id"]: c["status"] for c in result.core.to_dict()["children"]}
        assert statuses["a"] == "met"
        assert statuses["ma"] == "inconclusive"
        assert result.core_status == "inconclusive"
        assert result.setup_status == "inconclusive"

    def test_and_with_definitive_failure_reports_not_met(self) -> None:
        payload = rule([
            price_cond("above", 100.0, cid="a"),
            price_cond("above", 900.0, cid="b"),
        ])
        result = run(payload, {TF: candles(105)})
        assert result.core_status == "not_met"
        assert result.setup_status == "conditions_missing"

    def test_or_does_not_conceal_unavailable_conditions(self) -> None:
        ma_payload = rule(
            [
                price_cond("above", 900.0, cid="a"),
                {
                    "id": "ma",
                    "type": "moving_average",
                    "timeframe": TF,
                    "lookback": 50,
                    "params": {"ma_type": "sma", "period": 50, "compare_to": "value",
                               "value": 100.0, "operator": "above"},
                },
            ],
            logic="OR",
        )
        result = run(ma_payload, {TF: candles(105)})
        tree = result.core.to_dict()
        statuses = {c["id"]: c["status"] for c in tree["children"]}
        assert statuses["a"] == "not_met"
        assert statuses["ma"] == "inconclusive"
        assert tree["status"] == "inconclusive"
        assert result.setup_status == "inconclusive"
        # The unavailable child stays visible in the report.
        assert len(tree["children"]) == 2

    def test_or_with_supported_met_child_is_met(self) -> None:
        payload = rule(
            [price_cond("above", 900.0, cid="a"), price_cond("above", 100.0, cid="b")],
            logic="OR",
        )
        result = run(payload, {TF: candles(105)})
        assert result.core_status == "met"

    def test_or_all_evaluated_none_met(self) -> None:
        payload = rule(
            [price_cond("above", 900.0, cid="a"), price_cond("below", 50.0, cid="b")],
            logic="OR",
        )
        result = run(payload, {TF: candles(105)})
        assert result.core_status == "not_met"

    def test_conditions_summary_counts(self) -> None:
        payload = rule([
            price_cond("above", 100.0, cid="a"),
            price_cond("above", 900.0, cid="b"),
        ])
        result = run(payload, {TF: candles(105)})
        assert result.conditions_summary == {
            "met": 1,
            "not_met": 1,
            "inconclusive": 0,
            "unsupported": 0,
        }


# --------------------------------------------------------------------------- #
# Forming-candle policy
# --------------------------------------------------------------------------- #


class TestFormingCandlePolicy:
    def test_forming_candle_excluded_by_default(self) -> None:
        data = {TF: candles(100, 101, 102, 103, forming_last=True)}
        result = run(rule([price_cond("above", 101.0, cid="a")]), data)
        assert result.closed_candles_only is True
        assert result.forming_candle_used is False
        codes = {w["code"] for w in result.warnings}
        assert "FORMING_CANDLE_EXCLUDED" in codes
        node = find_condition(result.core.to_dict(), "a")
        assert node["evidence"]["candles_evaluated"] == 3  # forming excluded

    def test_forming_candle_opt_in_is_provisional(self) -> None:
        payload = rule(
            [price_cond("above", 101.0, cid="a")],
            evaluation={"on_close_only": False},
        )
        data = {TF: candles(100, 101, 102, 103, forming_last=True)}
        result = run(payload, data)
        assert result.closed_candles_only is False
        assert result.forming_candle_used is True
        codes = {w["code"] for w in result.warnings}
        assert "PROVISIONAL_FORMING_CANDLE" in codes
        assert any("PROVISIONAL" in line for line in result.summary)
        node = find_condition(result.core.to_dict(), "a")
        assert node["evidence"]["candles_evaluated"] == 4
        assert node["evidence"]["last_candle_status"] == "forming"

    def test_inline_candles_without_forming_flag_unaffected(self) -> None:
        result = run(
            rule([price_cond("above", 101.0, cid="a")]),
            {TF: candles(100, 101, 102, 103, 102)},
        )
        assert result.warnings == []


# --------------------------------------------------------------------------- #
# Regime gating
# --------------------------------------------------------------------------- #


class TestRegimeGate:
    def test_regime_mismatch_blocks_full_satisfaction(self) -> None:
        result = run(
            rule([price_cond("above", 100.0, cid="a")]),
            {TF: candles(105)},
            regime_check={
                "required": "trending",
                "classified": "range",
                "applicable": False,
                "matched_buckets": ["ranging"],
                "note": "not applicable",
            },
        )
        assert result.setup_status == "regime_mismatch"
        # Conditions still evaluated and visible.
        assert find_condition(result.core.to_dict(), "a")["status"] == "met"
        assert any("regime" in line.lower() for line in result.summary)

    def test_regime_match_allows_full_satisfaction(self) -> None:
        result = run(
            rule([price_cond("above", 100.0, cid="a")]),
            {TF: candles(105)},
            regime_check={
                "required": "trending",
                "classified": "uptrend",
                "applicable": True,
                "matched_buckets": ["trending"],
                "note": "ok",
            },
        )
        assert result.setup_status == "conditions_met"

    def test_invalidation_still_wins_over_regime(self) -> None:
        payload = rule(
            [price_cond("above", 100.0, cid="a")],
            invalidation={"logic": "AND", "conditions": [price_cond("below", 50.0, cid="i")], "groups": []},
        )
        result = run(
            payload,
            {TF: candles(105, 40)},
            regime_check={"required": "trending", "classified": "range",
                          "applicable": False, "matched_buckets": [], "note": ""},
        )
        assert result.setup_status == "invalidated"

    def test_regime_mismatch_reported_even_when_conditions_missing(self) -> None:
        result = run(
            rule([price_cond("above", 900.0, cid="a")]),
            {TF: candles(105)},
            regime_check={"required": "trending", "classified": "range",
                          "applicable": False, "matched_buckets": [], "note": ""},
        )
        assert result.setup_status == "regime_mismatch"


# --------------------------------------------------------------------------- #
# Swing confirmation timing through the shared feature library
# --------------------------------------------------------------------------- #


class TestSwingTimingViaEvaluator:
    def test_swing_point_rule_respects_confirmation(self) -> None:
        payload = rule([
            {
                "id": "sw",
                "type": "swing_point",
                "timeframe": TF,
                "lookback": 50,
                "params": {"kind": "high", "strength": 2},
            }
        ])
        # Series with a swing high at index 5 confirmed at index 7.
        data = [dict(c) for c in candles(*([100.0] * 5 + [105.0] + [100.0] * 8))]
        data[5] = {**data[5], "high": 106.0}
        for i in (6, 7):
            data[i] = {**data[i], "high": 101.0}
        result = run(payload, {TF: data})
        node = find_condition(result.core.to_dict(), "sw")
        assert node["status"] == "met"
        assert node["evidence"]["swings"][0]["time"] == data[5]["time"]

        # Truncate at the candle BEFORE confirmation: not yet known.
        truncated = data[:7]
        result = run(payload, {TF: truncated})
        node = find_condition(result.core.to_dict(), "sw")
        assert node["status"] == "not_met"
        assert node["evidence"]["swings_found"] == 0
