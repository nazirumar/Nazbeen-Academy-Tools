"""Unit tests: deterministic evaluator semantics + data-quality warnings."""

from __future__ import annotations

from app.domains.analysis.evaluator import evaluate_rule_set, evaluate_group
from app.domains.analysis.quality import assess_candle_series
from app.domains.strategies.rules import validate_rule_set


def candles(*closes: float, start: int = 1_700_000_000, step: int = 60) -> list[dict]:
    out = []
    for i, close in enumerate(closes):
        out.append(
            {
                "time": start + i * step,
                "open": close - 0.5,
                "high": close + 0.5,
                "low": close - 1.0,
                "close": close,
                "volume": None,
            }
        )
    return out


def rule(conditions: list[dict], logic: str = "AND", **kw) -> dict:
    base = {
        "schema_version": "1.0",
        "mode": "forex",
        "timeframes": ["M1"],
        "direction": "bullish",
        "conditions": {"logic": logic, "conditions": conditions, "groups": []},
    }
    base.update(kw)
    return base


def cond(operator: str, value: float, lookback: int = 5, cid: str = "c1") -> dict:
    return {
        "id": cid,
        "type": "price_level",
        "field": "close",
        "operator": operator,
        "value": value,
        "timeframe": "M1",
        "lookback": lookback,
    }


class TestConditionSemantics:
    def test_above_satisfied_on_latest_candle(self) -> None:
        rs = validate_rule_set(rule([cond("above", 100.0)]))
        result = evaluate_rule_set(rs, {"M1": candles(99, 100, 101)})
        assert result.setup_status == "conditions_met"
        leaf = result.core.children[0]
        assert leaf.satisfied is True
        assert leaf.evidence["current_value"] == 101
        assert leaf.evidence["matched_count"] == 1  # only 101 > 100

    def test_above_fails_when_latest_drops_below(self) -> None:
        rs = validate_rule_set(rule([cond("above", 100.0)]))
        result = evaluate_rule_set(rs, {"M1": candles(101, 100, 99)})
        assert result.setup_status == "conditions_missing"
        assert result.core.children[0].satisfied is False

    def test_below_semantics(self) -> None:
        rs = validate_rule_set(rule([cond("below", 100.0)]))
        result = evaluate_rule_set(rs, {"M1": candles(101, 99)})
        assert result.core.children[0].satisfied is True

    def test_crosses_above_requires_fresh_cross(self) -> None:
        rs = validate_rule_set(rule([cond("crosses_above", 100.0)]))
        # crossed two candles ago but latest candle is NOT a cross
        result = evaluate_rule_set(rs, {"M1": candles(99, 101, 102)})
        assert result.core.children[0].satisfied is False
        assert result.core.children[0].evidence["total_crosses_in_window"] == 1
        # fresh cross on the latest candle
        result2 = evaluate_rule_set(rs, {"M1": candles(102, 99, 101)})
        assert result2.core.children[0].satisfied is True

    def test_crosses_below_semantics(self) -> None:
        rs = validate_rule_set(rule([cond("crosses_below", 100.0)]))
        result = evaluate_rule_set(rs, {"M1": candles(99, 101, 99.5)})
        assert result.core.children[0].satisfied is True

    def test_lookback_window_limits_history(self) -> None:
        rs = validate_rule_set(rule([cond("above", 100.0, lookback=3)]))
        data = candles(*[101.0] * 5 + [99.0] * 2)  # older candles all above
        leaf = evaluate_rule_set(rs, {"M1": data}).core.children[0]
        assert leaf.satisfied is False
        assert leaf.evidence["candles_evaluated"] == 3


class TestGroupLogic:
    def test_and_requires_all(self) -> None:
        rs = validate_rule_set(
            rule([cond("above", 100.0, cid="a"), cond("below", 200.0, cid="b")])
        )
        result = evaluate_rule_set(rs, {"M1": candles(150)})
        assert result.core.satisfied is True

    def test_and_fails_if_one_fails(self) -> None:
        rs = validate_rule_set(
            rule([cond("above", 100.0, cid="a"), cond("above", 200.0, cid="b")])
        )
        result = evaluate_rule_set(rs, {"M1": candles(150)})
        assert result.core.satisfied is False

    def test_or_needs_one(self) -> None:
        rs = validate_rule_set(
            rule([cond("above", 500.0, cid="a"), cond("below", 200.0, cid="b")], logic="OR")
        )
        result = evaluate_rule_set(rs, {"M1": candles(150)})
        assert result.core.satisfied is True

    def test_nested_groups(self) -> None:
        payload = rule([cond("above", 100.0, cid="a")])
        payload["conditions"]["groups"] = [
            {
                "logic": "OR",
                "conditions": [
                    cond("above", 500.0, cid="x"),
                    cond("below", 200.0, cid="y"),
                ],
                "groups": [],
            }
        ]
        rs = validate_rule_set(payload)
        result = evaluate_rule_set(rs, {"M1": candles(150)})
        assert result.core.satisfied is True


class TestConfirmationInvalidation:
    def test_confirmation_blocks_setup(self) -> None:
        payload = rule(
            [cond("above", 100.0, cid="a")],
            confirmation={
                "logic": "AND",
                "conditions": [cond("above", 500.0, cid="conf")],
                "groups": [],
            },
        )
        rs = validate_rule_set(payload)
        result = evaluate_rule_set(rs, {"M1": candles(150)})
        assert result.setup_status == "conditions_missing"

    def test_invalidation_overrides_core(self) -> None:
        payload = rule(
            [cond("above", 100.0, cid="a")],
            invalidation={
                "logic": "AND",
                "conditions": [cond("below", 50.0, cid="inv")],
                "groups": [],
            },
        )
        rs = validate_rule_set(payload)
        result = evaluate_rule_set(rs, {"M1": candles(40)})
        assert result.setup_status == "invalidated"

    def test_no_invalidation_is_reported(self) -> None:
        rs = validate_rule_set(rule([cond("above", 100.0)]))
        result = evaluate_rule_set(rs, {"M1": candles(150)})
        assert result.invalidation is None


class TestMissingData:
    def test_missing_timeframe_reported_not_fabricated(self) -> None:
        payload = rule(
            [cond("above", 100.0, cid="a")],
            timeframes=["M1", "M5"],
        )
        payload["conditions"]["groups"] = [
            {
                "logic": "AND",
                "conditions": [dict(cond("above", 100.0, cid="m5"), timeframe="M5")],
                "groups": [],
            }
        ]
        rs = validate_rule_set(payload)
        result = evaluate_rule_set(rs, {"M1": candles(150)})  # M5 missing
        assert result.setup_status == "insufficient_data"
        assert "M5" in result.core.evidence["missing_timeframes"]

    def test_insufficient_candles_warning(self) -> None:
        rs = validate_rule_set(rule([cond("above", 100.0, lookback=50)]))
        result = evaluate_rule_set(rs, {"M1": candles(150)})
        codes = {w["code"] for w in result.warnings}
        assert "INSUFFICIENT_CANDLES" in codes


class TestDataQuality:
    def test_unsorted_input_sorted_with_warning(self) -> None:
        data = list(reversed(candles(100, 101, 102)))
        warnings, rows = assess_candle_series(data, "M1", required_count=3)
        codes = {w["code"] for w in warnings}
        assert "TIMESTAMPS_UNSORTED" in codes
        assert [r["time"] for r in rows] == sorted(r["time"] for r in rows)

    def test_duplicate_timestamps_warned(self) -> None:
        data = candles(100, 101, 101)
        data[2]["time"] = data[1]["time"]
        warnings, _ = assess_candle_series(data, "M1", required_count=3)
        assert any(w["code"] == "DUPLICATE_TIMESTAMPS" for w in warnings)

    def test_gap_detection(self) -> None:
        data = candles(100, 101, 102)
        data[2]["time"] += 3600  # 1-hour hole in M1 data
        warnings, _ = assess_candle_series(data, "M1", required_count=3)
        assert any(w["code"] == "TIMESTAMP_GAPS" for w in warnings)

    def test_d1_weekend_gap_not_warned(self) -> None:
        data = candles(100, 101, 102, step=86400)
        data[2]["time"] += 2 * 86400  # weekend: Fri -> Mon
        warnings, _ = assess_candle_series(data, "D1", required_count=3)
        assert not any(w["code"] == "TIMESTAMP_GAPS" for w in warnings)

    def test_invalid_ohlc_warned_and_excluded(self) -> None:
        data = candles(100, 101, 102)
        data[1]["high"], data[1]["low"] = data[1]["low"], data[1]["high"]
        warnings, _ = assess_candle_series(data, "M1", required_count=3)
        assert any(w["code"] == "INVALID_OHLC" for w in warnings)

    def test_input_not_mutated(self) -> None:
        data = list(reversed(candles(100, 101)))
        assess_candle_series(data, "M1", required_count=2)
        assert data[0]["time"] > data[1]["time"]  # still reversed


class TestExplainability:
    def test_summary_mentions_direction(self) -> None:
        rs = validate_rule_set(rule([cond("above", 100.0)]))
        result = evaluate_rule_set(rs, {"M1": candles(150)})
        assert any("BULLISH" in line for line in result.summary)

    def test_deterministic_evaluation(self) -> None:
        rs = validate_rule_set(rule([cond("crosses_above", 100.0)]))
        data = {"M1": candles(99, 101, 100.5)}
        r1 = evaluate_rule_set(rs, data)
        r2 = evaluate_rule_set(rs, data)
        assert r1.to_dict()["core"] == r2.to_dict()["core"]
        assert r1.setup_status == r2.setup_status
