"""Unit tests: evaluators for the Phase 3 rule types.

All fixtures are deterministic synthetic candles (clearly labelled in the
helpers). Every test documents the expected semantics — the same semantics
published at /api/v1/rule-types. No look-ahead bias: evaluators must only
use information contained in the supplied candles.
"""

from __future__ import annotations

import math

from app.domains.analysis.evaluator import (
    evaluate_candle_pattern,
    evaluate_candle_property,
    evaluate_market_structure_break,
    evaluate_moving_average,
    evaluate_price_level_condition,
    evaluate_price_zone,
    evaluate_rule_set,
    evaluate_swing_point,
    evaluate_structure_pattern,
)
from app.domains.strategies.rules import Condition, validate_rule_set

TF = "M1"


def candle(
    i: int,
    o: float,
    h: float,
    l: float,
    c: float,
    start: int = 1_700_000_000,
    step: int = 60,
) -> dict:
    return {
        "time": start + i * step,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": None,
    }


def cond(type_: str, params: dict, lookback: int = 100, cid: str = "c1", optional: bool = False) -> Condition:
    return Condition(
        id=cid,
        type=type_,
        timeframe=TF,
        params=params,
        lookback=lookback,
        optional=optional,
        legacy=False,
    )


def rule(conditions: list[dict], logic: str = "AND", **kw) -> dict:
    base = {
        "schema_version": "2.0",
        "mode": "forex",
        "timeframes": ["M1"],
        "direction": "bullish",
        "conditions": {"logic": logic, "conditions": conditions, "groups": []},
    }
    base.update(kw)
    return base


def price_cond(operator: str, value: float, lookback: int = 5, cid: str = "c1", optional: bool = False) -> dict:
    return {
        "id": cid,
        "type": "price_level",
        "timeframe": TF,
        "lookback": lookback,
        "optional": optional,
        "params": {"field": "close", "operator": operator, "value": value},
    }


# --------------------------------------------------------------------------- #
# candle_property
# --------------------------------------------------------------------------- #


class TestCandleProperty:
    def test_body_above(self) -> None:
        c = cond("candle_property", {"property": "body", "operator": "above", "value": 1.5})
        candles = [candle(0, 100, 102.5, 99.5, 102)]
        node = evaluate_candle_property(c, candles)
        assert node.satisfied is True
        assert node.evidence["current_value"] == 2.0

    def test_body_below(self) -> None:
        c = cond("candle_property", {"property": "body", "operator": "below", "value": 1.0})
        candles = [candle(0, 100, 102.5, 99.5, 102)]
        assert evaluate_candle_property(c, candles).satisfied is False

    def test_upper_wick(self) -> None:
        c = cond("candle_property", {"property": "upper_wick", "operator": "above", "value": 2.0})
        candles = [candle(0, 100, 103, 99.5, 102)]  # upper wick = 1
        assert evaluate_candle_property(c, candles).satisfied is False

    def test_no_candles_unsatisfied_with_reason(self) -> None:
        c = cond("candle_property", {"property": "body", "operator": "above", "value": 1.0})
        node = evaluate_candle_property(c, [])
        assert node.satisfied is False
        assert "reason" in node.evidence

    def test_lower_wick(self) -> None:
        c = cond("candle_property", {"property": "lower_wick", "operator": "above", "value": 4.0})
        candles = [candle(0, 100, 100.5, 95.0, 100)]  # lower wick = 5
        assert evaluate_candle_property(c, candles).satisfied is True


# --------------------------------------------------------------------------- #
# candle_pattern
# --------------------------------------------------------------------------- #


class TestCandlePattern:
    def test_bullish_engulfing(self) -> None:
        c = cond("candle_pattern", {"pattern": "bullish_engulfing"})
        candles = [
            candle(0, 105, 105.5, 99.5, 100),   # bearish body 105->100
            candle(1, 99.5, 106.5, 99.0, 106),  # bullish body 99.5->106 engulfs
        ]
        assert evaluate_candle_pattern(c, candles).satisfied is True

    def test_bullish_engulfing_requires_opposite_colour(self) -> None:
        c = cond("candle_pattern", {"pattern": "bullish_engulfing"})
        candles = [
            candle(0, 100, 105.5, 99.5, 105),   # bullish previous
            candle(1, 99.5, 106.5, 99.0, 106),
        ]
        assert evaluate_candle_pattern(c, candles).satisfied is False

    def test_inside_bar(self) -> None:
        c = cond("candle_pattern", {"pattern": "inside_bar"})
        candles = [
            candle(0, 100, 110, 90, 105),
            candle(1, 102, 105, 95, 103),
        ]
        assert evaluate_candle_pattern(c, candles).satisfied is True

    def test_doji_tolerance(self) -> None:
        c = cond("candle_pattern", {"pattern": "doji"})
        candles = [candle(0, 100, 105, 95, 100.05)]  # body 0.05 of range 10
        assert evaluate_candle_pattern(c, candles).satisfied is True

    def test_doji_strict_tolerance(self) -> None:
        c = cond("candle_pattern", {"pattern": "doji", "max_body_ratio": 0.001})
        candles = [candle(0, 100, 105, 95, 100.05)]
        assert evaluate_candle_pattern(c, candles).satisfied is False

    def test_hammer(self) -> None:
        c = cond("candle_pattern", {"pattern": "hammer"})
        candles = [candle(0, 100, 101.5, 95.0, 101)]
        node = evaluate_candle_pattern(c, candles)
        assert node.satisfied is True

    def test_shooting_star(self) -> None:
        c = cond("candle_pattern", {"pattern": "shooting_star"})
        candles = [candle(0, 100, 106.5, 100.0, 100.5)]
        assert evaluate_candle_pattern(c, candles).satisfied is True

    def test_two_candle_pattern_needs_two(self) -> None:
        c = cond("candle_pattern", {"pattern": "inside_bar"})
        node = evaluate_candle_pattern(c, [candle(0, 100, 110, 90, 105)])
        assert node.satisfied is False
        assert "2 candles" in node.evidence["reason"]

    def test_zero_range_candle_is_doji(self) -> None:
        c = cond("candle_pattern", {"pattern": "doji"})
        candles = [candle(0, 100, 100, 100, 100)]
        assert evaluate_candle_pattern(c, candles).satisfied is True


# --------------------------------------------------------------------------- #
# swing_point
# --------------------------------------------------------------------------- #


class TestSwingPoint:
    def test_confirmed_swing_high(self) -> None:
        c = cond("swing_point", {"kind": "high", "strength": 1})
        candles = [candle(i, 10, h, 9, 10) for i, h in enumerate([10, 12, 9, 8, 7])]
        node = evaluate_swing_point(c, candles)
        assert node.satisfied is True
        assert node.evidence["swings_found"] == 1
        assert node.evidence["swings"][0]["price"] == 12

    def test_no_lookahead_swing_at_edge_never_confirmed(self) -> None:
        # The spike is the LATEST candle: confirming it would need future
        # candles that do not exist — the rule must stay unsatisfied.
        c = cond("swing_point", {"kind": "high", "strength": 1})
        candles = [candle(i, 10, h, 9, 10) for i, h in enumerate([10, 9, 8, 30])]
        node = evaluate_swing_point(c, candles)
        assert node.satisfied is False

    def test_strength_requires_subsequent_candles(self) -> None:
        c = cond("swing_point", {"kind": "high", "strength": 2})
        # swing candidate at index 2 with only 2 candles after it: index
        # window [0..4]; strength 2 needs indices 0,1 AND 3,4 -> confirmed.
        candles = [candle(i, 10, h, 9, 10) for i, h in enumerate([10, 11, 14, 9, 8])]
        assert evaluate_swing_point(c, candles).satisfied is True

        # Same candidate but one fewer candle: confirmation impossible.
        c2 = cond("swing_point", {"kind": "high", "strength": 2})
        candles_short = candles[:4]
        node = evaluate_swing_point(c2, candles_short)
        assert node.satisfied is False

    def test_swing_low(self) -> None:
        c = cond("swing_point", {"kind": "low", "strength": 1})
        candles = [candle(i, 20, 21, l, 20) for i, l in enumerate([20, 15, 22, 23, 24])]
        node = evaluate_swing_point(c, candles)
        assert node.satisfied is True
        assert node.evidence["swings"][0]["price"] == 15

    def test_minimum_candles_guard(self) -> None:
        c = cond("swing_point", {"kind": "high", "strength": 3})
        candles = [candle(i, 10, 10, 9, 10) for i in range(5)]
        node = evaluate_swing_point(c, candles)
        assert node.satisfied is False
        assert "at least" in node.evidence["reason"]


# --------------------------------------------------------------------------- #
# structure_pattern (HH / HL / LH / LL)
# --------------------------------------------------------------------------- #


class TestStructurePattern:
    def test_higher_highs(self) -> None:
        c = cond("structure_pattern", {"pattern": "higher_highs", "count": 2, "strength": 1})
        highs = [10, 11, 14, 9, 13, 16, 12]
        candles = [candle(i, 10, h, 9, 10) for i, h in enumerate(highs)]
        node = evaluate_structure_pattern(c, candles)
        assert node.satisfied is True
        assert node.evidence["swing_prices"] == [14, 16]

    def test_lower_highs_fails_higher_highs(self) -> None:
        c = cond("structure_pattern", {"pattern": "higher_highs", "count": 2, "strength": 1})
        highs = [10, 11, 16, 9, 13, 14, 12]
        candles = [candle(i, 10, h, 9, 10) for i, h in enumerate(highs)]
        assert evaluate_structure_pattern(c, candles).satisfied is False

    def test_higher_lows(self) -> None:
        c = cond("structure_pattern", {"pattern": "higher_lows", "count": 2, "strength": 1})
        lows = [20, 15, 22, 17, 23, 19, 24]
        candles = [candle(i, 20, 23, l, 20) for i, l in enumerate(lows)]
        node = evaluate_structure_pattern(c, candles)
        assert node.satisfied is True
        # Confirmed swing lows: 15, 17, 19 — the LAST 2 must be rising.
        assert node.evidence["swing_prices"] == [17, 19]

    def test_insufficient_swings_reported(self) -> None:
        c = cond("structure_pattern", {"pattern": "higher_highs", "count": 3, "strength": 1})
        candles = [candle(i, 10, 10, 9, 10) for i in range(8)]
        node = evaluate_structure_pattern(c, candles)
        assert node.satisfied is False
        assert "confirmed swing" in node.evidence["reason"]


# --------------------------------------------------------------------------- #
# market_structure_break
# --------------------------------------------------------------------------- #


class TestMarketStructureBreak:
    def test_bullish_break(self) -> None:
        c = cond("market_structure_break", {"side": "bullish", "strength": 1})
        candles = [
            candle(0, 13, 13.5, 12.5, 13),
            candle(1, 13, 14.5, 13.5, 14),   # swing high 14.5 (confirmed by 2,3)
            candle(2, 14, 14.2, 13.0, 13.5),
            candle(3, 13.5, 14.0, 12.8, 13.2),
            candle(4, 13.2, 15.8, 13.1, 15.5),  # close 15.5 breaks 14.5
        ]
        node = evaluate_market_structure_break(c, candles)
        assert node.satisfied is True
        assert node.evidence["reference_swing_price"] == 14.5
        assert node.evidence["current_close"] == 15.5

    def test_no_break_when_close_below_reference(self) -> None:
        c = cond("market_structure_break", {"side": "bullish", "strength": 1})
        candles = [
            candle(0, 13, 13.5, 12.5, 13),
            candle(1, 13, 14.5, 13.5, 14),
            candle(2, 14, 14.2, 13.0, 13.5),
            candle(3, 13.5, 14.0, 12.8, 13.2),
            candle(4, 13.2, 14.1, 13.1, 14.0),  # close 14.0 < 14.5
        ]
        assert evaluate_market_structure_break(c, candles).satisfied is False

    def test_bearish_break(self) -> None:
        c = cond("market_structure_break", {"side": "bearish", "strength": 1})
        candles = [
            candle(0, 11, 11.5, 10.5, 11),
            candle(1, 11, 11.2, 9.5, 10),    # swing low 9.5
            candle(2, 10, 10.4, 9.8, 10.2),
            candle(3, 10.2, 10.6, 10.0, 10.4),
            candle(4, 10.4, 10.5, 8.8, 9.0),  # close 9.0 breaks 9.5
        ]
        node = evaluate_market_structure_break(c, candles)
        assert node.satisfied is True
        assert node.evidence["reference_swing_price"] == 9.5

    def test_latest_candle_cannot_be_its_own_reference(self) -> None:
        # Huge spike on the latest candle: the swing it would create is NOT
        # confirmable yet, so there is no reference -> unsatisfied, no
        # look-ahead.
        c = cond("market_structure_break", {"side": "bullish", "strength": 1})
        candles = [
            candle(0, 10, 10.5, 9.5, 10),
            candle(1, 10, 10.4, 9.6, 10),
            candle(2, 10, 10.6, 9.4, 10),
            candle(3, 10, 30.0, 9.5, 29.0),
        ]
        node = evaluate_market_structure_break(c, candles)
        assert node.satisfied is False

    def test_no_reference_swing(self) -> None:
        c = cond("market_structure_break", {"side": "bullish", "strength": 2})
        candles = [candle(i, 10, 11, 9, 10) for i in range(4)]
        node = evaluate_market_structure_break(c, candles)
        assert node.satisfied is False
        assert "reason" in node.evidence  # minimum-candles guard


# --------------------------------------------------------------------------- #
# moving_average
# --------------------------------------------------------------------------- #


class TestMovingAverage:
    def test_sma_above_threshold(self) -> None:
        c = cond("moving_average", {
            "ma_type": "sma", "period": 3, "compare_to": "value",
            "value": 9.0, "operator": "above",
        })
        candles = [candle(i, 10, 10.5, 9.5, 10) for i in range(5)]
        node = evaluate_moving_average(c, candles)
        assert node.satisfied is True
        assert node.evidence["ma_value"] == 10.0

    def test_sma_below_threshold(self) -> None:
        c = cond("moving_average", {
            "ma_type": "sma", "period": 3, "compare_to": "value",
            "value": 11.0, "operator": "below",
        })
        candles = [candle(i, 10, 10.5, 9.5, 10) for i in range(5)]
        assert evaluate_moving_average(c, candles).satisfied is True

    def test_sma_insufficient_candles(self) -> None:
        c = cond("moving_average", {
            "ma_type": "sma", "period": 10, "compare_to": "value",
            "value": 9.0, "operator": "above",
        })
        candles = [candle(i, 10, 10.5, 9.5, 10) for i in range(5)]
        node = evaluate_moving_average(c, candles)
        assert node.satisfied is False
        assert "at least 10" in node.evidence["reason"]

    def test_ema_seed_and_value(self) -> None:
        c = cond("moving_average", {
            "ma_type": "ema", "period": 2, "compare_to": "value",
            "value": 14.0, "operator": "above",
        })
        candles = [candle(i, 10, 20.5, 9.5, c_) for i, c_ in enumerate([10, 20])]
        node = evaluate_moving_average(c, candles)
        # EMA seed = SMA(2) = 15 > 14
        assert node.satisfied is True
        assert node.evidence["ma_value"] == 15.0

    def test_crosses_above_fresh_only(self) -> None:
        c = cond("moving_average", {
            "ma_type": "sma", "period": 2, "compare_to": "value",
            "value": 10.5, "operator": "crosses_above",
        })
        # closes: 10, 10, 12 -> MA [10, 11]; cross on the latest candle
        candles = [candle(i, 10, 12.5, 9.5, c_) for i, c_ in enumerate([10, 10, 12])]
        node = evaluate_moving_average(c, candles)
        assert node.satisfied is True

        # Cross happened earlier; latest candle is not a cross.
        candles_late = [candle(i, 10, 12.5, 9.5, c_) for i, c_ in enumerate([10, 10, 12, 12])]
        node_late = evaluate_moving_average(c, candles_late)
        assert node_late.satisfied is False
        assert node_late.evidence["total_crosses_in_window"] == 1

    def test_ma_vs_price_field(self) -> None:
        c = cond("moving_average", {
            "ma_type": "sma", "period": 2, "compare_to": "price_field",
            "price_field": "close", "operator": "below",
        })
        # closes 8, 12 -> MA=10 < close=12 on latest candle
        candles = [candle(i, 10, 12.5, 7.5, c_) for i, c_ in enumerate([8, 12])]
        node = evaluate_moving_average(c, candles)
        assert node.satisfied is True
        assert node.evidence["ma_value"] == 10.0
        assert node.evidence["target_value"] == 12.0

    def test_ma_vs_price_field_cross(self) -> None:
        c = cond("moving_average", {
            "ma_type": "sma", "period": 2, "compare_to": "price_field",
            "price_field": "close", "operator": "crosses_below",
        })
        # closes 20, 8, 12: MA [14, 10]; target [8, 12]: MA(14) >= 8, MA(10) < 12
        # -> cross below on latest candle
        candles = [candle(i, 10, 20.5, 7.5, c_) for i, c_ in enumerate([20, 8, 12])]
        node = evaluate_moving_average(c, candles)
        assert node.satisfied is True


# --------------------------------------------------------------------------- #
# price_zone
# --------------------------------------------------------------------------- #


class TestPriceZone:
    def test_touched(self) -> None:
        c = cond("price_zone", {"zone_low": 100, "zone_high": 102, "operator": "touched"})
        candles = [
            candle(0, 105, 106, 104, 105),   # no touch
            candle(1, 103, 104, 101.5, 104),  # low 101.5 <= 102 -> touch
        ]
        node = evaluate_price_zone(c, candles)
        assert node.satisfied is True
        assert node.evidence["touches"] == 1

    def test_not_touched(self) -> None:
        c = cond("price_zone", {"zone_low": 100, "zone_high": 102, "operator": "touched"})
        candles = [candle(0, 105, 106, 104, 105)]
        assert evaluate_price_zone(c, candles).satisfied is False

    def test_closed_inside(self) -> None:
        c = cond("price_zone", {"zone_low": 100, "zone_high": 102, "operator": "closed_inside"})
        candles = [candle(0, 100, 103, 99, 101.5)]
        assert evaluate_price_zone(c, candles).satisfied is True

    def test_closed_inside_inclusive_bounds(self) -> None:
        c = cond("price_zone", {"zone_low": 100, "zone_high": 102, "operator": "closed_inside"})
        candles = [candle(0, 100, 103, 99, 102)]
        assert evaluate_price_zone(c, candles).satisfied is True

    def test_rejected(self) -> None:
        c = cond("price_zone", {"zone_low": 100, "zone_high": 102, "operator": "rejected"})
        candles = [candle(0, 103, 103.5, 100.5, 103.2)]  # touched, closed above
        assert evaluate_price_zone(c, candles).satisfied is True

    def test_no_rejection_when_closed_inside(self) -> None:
        c = cond("price_zone", {"zone_low": 100, "zone_high": 102, "operator": "rejected"})
        candles = [candle(0, 103, 103.5, 100.5, 101.5)]
        assert evaluate_price_zone(c, candles).satisfied is False


# --------------------------------------------------------------------------- #
# Optional rules + integration through evaluate_rule_set
# --------------------------------------------------------------------------- #


def run(payload: dict, candles_by_tf: dict[str, list[dict]]):
    return evaluate_rule_set(validate_rule_set(payload), candles_by_tf)


class TestOptionalSemantics:
    def test_optional_rule_does_not_block_and_group(self) -> None:
        payload = rule(
            [
                price_cond("below", 200.0, cid="required1"),
                price_cond("above", 500.0, cid="nice_to_have", optional=True),
            ]
        )
        result = run(payload, {"M1": [candle(0, 149.5, 150.5, 149, 150)]})
        # required1 satisfied; optional unmet -> group still satisfied
        assert result.core.satisfied is True
        assert result.setup_status == "conditions_met"
        optional_node = result.core.children[1]
        assert optional_node.optional is True
        assert optional_node.satisfied is False

    def test_optional_unmet_counted_in_summary(self) -> None:
        payload = rule(
            [
                price_cond("above", 500.0, cid="required1"),
                price_cond("above", 500.0, cid="nice_to_have", optional=True),
            ]
        )
        result = run(payload, {"M1": [candle(0, 149.5, 150.5, 149, 150)]})
        # status = conditions_missing (required rule failed); the summary
        # must distinguish required failures from optional ones.
        assert any("required condition(s)/group(s)" in line for line in result.summary)
        assert any("optional rule(s) are unmet" in line for line in result.summary)

    def test_required_rule_still_blocks(self) -> None:
        payload = rule(
            [
                price_cond("above", 500.0, cid="required1"),
                price_cond("above", 100.0, cid="nice_to_have", optional=True),
            ]
        )
        result = run(payload, {"M1": [candle(0, 149.5, 150.5, 149, 150)]})
        assert result.core.satisfied is False
        assert result.setup_status == "conditions_missing"

    def test_optional_group_to_dict(self) -> None:
        payload = rule([price_cond("above", 500.0, cid="x", optional=True)])
        result = run(payload, {"M1": [candle(0, 149.5, 150.5, 149, 150)]})
        dumped = result.core.to_dict()
        assert dumped["children"][0]["optional"] is True

    def test_or_group_counts_optional(self) -> None:
        payload = rule(
            [
                price_cond("above", 500.0, cid="a"),
                price_cond("below", 200.0, cid="b", optional=True),
            ],
            logic="OR",
        )
        result = run(payload, {"M1": [candle(0, 149.5, 150.5, 149, 150)]})
        assert result.core.satisfied is True  # optional child satisfies OR


class TestDirectionBoth:
    def test_both_label_in_summary(self) -> None:
        payload = rule([price_cond("above", 100.0)], direction="both")
        result = run(payload, {"M1": [candle(0, 149.5, 150.5, 149, 150)]})
        assert any("BOTH DIRECTIONS" in line for line in result.summary)

    def test_both_still_evaluates_normally(self) -> None:
        payload = rule([price_cond("above", 100.0)], direction="both")
        result = run(payload, {"M1": [candle(0, 149.5, 150.5, 149, 150)]})
        assert result.setup_status == "conditions_met"


class TestEndToEndNewTypes:
    def test_multi_rule_rule_set_evaluates(self) -> None:
        payload = rule(
            [
                {
                    "id": "trend",
                    "type": "moving_average",
                    "timeframe": "M1",
                    "params": {
                        "ma_type": "sma", "period": 3, "compare_to": "value",
                        "value": 100.0, "operator": "above",
                    },
                },
                {
                    "id": "zone",
                    "type": "price_zone",
                    "timeframe": "M1",
                    "params": {
                        "zone_low": 100, "zone_high": 110, "operator": "closed_inside",
                    },
                },
            ],
            invalidation={
                "logic": "AND",
                "conditions": [
                    {
                        "id": "inv",
                        "type": "price_level",
                        "timeframe": "M1",
                        "params": {"field": "close", "operator": "below", "value": 90},
                    }
                ],
                "groups": [],
            },
        )
        candles = [candle(i, 104.5 + i, 106 + i, 104 + i, 105 + i) for i in range(4)]
        result = run(payload, {"M1": candles})
        # SMA(3)=107 > 100 and close 108 inside [100, 110]; invalidation
        # (close < 90) is NOT triggered -> setup met.
        assert result.core.satisfied is True
        assert result.setup_status == "conditions_met"

    def test_group_logic_or_with_new_types(self) -> None:
        payload = rule(
            [
                {
                    "id": "hammer",
                    "type": "candle_pattern",
                    "timeframe": "M1",
                    "params": {"pattern": "hammer"},
                },
                {
                    "id": "swing",
                    "type": "swing_point",
                    "timeframe": "M1",
                    "params": {"kind": "low", "strength": 1},
                },
            ],
            logic="OR",
        )
        candles = [
            candle(0, 21.5, 22.0, 21.0, 21.7),
            candle(1, 16.5, 17.0, 15.0, 16.2),   # confirmed swing low (15)
            candle(2, 19.5, 20.9, 18.9, 20.0),   # NOT a hammer (small lower wick)
        ]
        result = run(payload, {"M1": candles})
        assert result.core.satisfied is True
        assert result.core.children[0].satisfied is False
        assert result.core.children[1].satisfied is True

    def test_nan_never_leaks_into_evidence(self) -> None:
        payload = rule([price_cond("above", 100.0)])
        result = run(payload, {"M1": [candle(0, 10, 10.5, 9.5, 150)]})

        def check(node: dict) -> None:
            for key, value in node["evidence"].items():
                if isinstance(value, float):
                    assert not math.isnan(value), key
                if isinstance(value, dict):
                    check({"evidence": value})
            for child in node.get("children", []):
                check(child)

        check(result.core.to_dict())


class TestPriceLevelRegression:
    """Guards the Phase 1 semantics that must never change."""

    def test_legacy_condition_via_validator(self) -> None:
        payload = {
            "schema_version": "1.0",
            "mode": "forex",
            "timeframes": ["M1"],
            "direction": "bullish",
            "conditions": {
                "logic": "AND",
                "conditions": [
                    {
                        "id": "c1",
                        "type": "price_level",
                        "field": "close",
                        "operator": "crosses_above",
                        "value": 100.0,
                        "timeframe": "M1",
                    }
                ],
                "groups": [],
            },
        }
        # Fresh cross on latest candle
        rs = validate_rule_set(payload)
        result = evaluate_rule_set(
            rs,
            {"M1": [candle(i, c - 1, c + 1, c - 2, c) for i, c in enumerate([102, 99, 101])]},
        )
        assert result.core.children[0].satisfied is True
        # Stale cross
        result2 = evaluate_rule_set(
            rs,
            {"M1": [candle(i, c - 1, c + 1, c - 2, c) for i, c in enumerate([99, 101, 102])]},
        )
        assert result2.core.children[0].satisfied is False


_ = (evaluate_price_level_condition,)  # imported for regression clarity
