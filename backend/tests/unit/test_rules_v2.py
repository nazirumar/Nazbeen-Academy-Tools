"""Unit tests: rule-set schema 2.0 (parametric conditions, v1.0 compat)."""

from __future__ import annotations

import pytest

from app.domains.strategies.rules import (
    LIMITS,
    RULE_SET_SCHEMA_VERSION,
    RuleValidationError,
    validate_rule_set,
)


def v2_rule_set(**overrides: object) -> dict:
    base = {
        "schema_version": "2.0",
        "mode": "forex",
        "timeframes": ["H1", "M15"],
        "direction": "bullish",
        "conditions": {
            "logic": "AND",
            "conditions": [
                {
                    "id": "ma-trend",
                    "type": "moving_average",
                    "timeframe": "H1",
                    "lookback": 60,
                    "params": {
                        "ma_type": "ema",
                        "period": 20,
                        "compare_to": "value",
                        "value": 1.08,
                        "operator": "above",
                    },
                }
            ],
            "groups": [],
        },
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


def v1_rule_set(**overrides: object) -> dict:
    base = {
        "schema_version": "1.0",
        "mode": "forex",
        "timeframes": ["H1"],
        "direction": "bullish",
        "conditions": {
            "logic": "AND",
            "conditions": [
                {
                    "id": "c1",
                    "type": "price_level",
                    "field": "close",
                    "operator": "above",
                    "value": 1.08,
                    "timeframe": "H1",
                    "lookback": 20,
                }
            ],
            "groups": [],
        },
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


class TestSchema2HappyPath:
    def test_current_version_constant(self) -> None:
        assert RULE_SET_SCHEMA_VERSION == "2.0"

    def test_parametric_condition_parses(self) -> None:
        rs = validate_rule_set(v2_rule_set())
        cond = rs.conditions.conditions[0]
        assert cond.type == "moving_average"
        assert cond.params["ma_type"] == "ema"
        assert cond.params["source"] == "close"  # documented default applied
        assert cond.optional is False

    def test_canonical_roundtrip(self) -> None:
        rs = validate_rule_set(v2_rule_set())
        again = validate_rule_set(rs.to_dict())
        assert again.to_dict() == rs.to_dict()

    def test_legacy_roundtrip_preserved(self) -> None:
        rs = validate_rule_set(v1_rule_set())
        d = rs.to_dict()
        # Legacy flat shape is preserved for storage.
        assert d["schema_version"] == "1.0"
        assert d["conditions"]["conditions"][0]["field"] == "close"
        assert "params" not in d["conditions"]["conditions"][0]
        again = validate_rule_set(d)
        assert again.to_dict() == d

    def test_direction_both_allowed_in_v2(self) -> None:
        rs = validate_rule_set(v2_rule_set(direction="both"))
        assert rs.direction == "both"

    def test_direction_both_rejected_in_v1(self) -> None:
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(v1_rule_set(direction="both"))
        assert any(e["path"] == ".direction" for e in exc.value.errors)


class TestStrategyScopeSettings:
    def test_symbols_scope(self) -> None:
        rs = validate_rule_set(v2_rule_set(symbols=["EURUSD", "XAUUSD"]))
        assert rs.symbols == ("EURUSD", "XAUUSD")

    def test_symbols_duplicate_rejected(self) -> None:
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(v2_rule_set(symbols=["EURUSD", "EURUSD"]))
        assert any(e["code"] == "duplicate_symbol" for e in exc.value.errors)

    def test_symbols_empty_string_rejected(self) -> None:
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(v2_rule_set(symbols=["  "]))
        assert any(e["code"] == "invalid_symbol" for e in exc.value.errors)

    def test_symbols_require_v2(self) -> None:
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(v1_rule_set(symbols=["EURUSD"]))
        assert any(e["path"] == ".symbols" for e in exc.value.errors)

    def test_market_regime_accepted(self) -> None:
        rs = validate_rule_set(v2_rule_set(market_regime="trending"))
        assert rs.market_regime == "trending"

    def test_market_regime_whitelist(self) -> None:
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(v2_rule_set(market_regime="crash"))
        assert any(e["code"] == "unsupported_market_regime" for e in exc.value.errors)

    def test_evaluation_settings(self) -> None:
        rs = validate_rule_set(v2_rule_set(evaluation={"on_close_only": True}))
        assert rs.evaluation == {"on_close_only": True}

    def test_evaluation_unknown_setting_rejected(self) -> None:
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(
                v2_rule_set(evaluation={"auto_trade": True, "on_close_only": True})
            )
        assert any(e["path"] == ".evaluation.auto_trade" for e in exc.value.errors)

    def test_optional_rules_parsed(self) -> None:
        payload = v2_rule_set()
        payload["conditions"]["conditions"][0]["optional"] = True
        rs = validate_rule_set(payload)
        assert rs.conditions.conditions[0].optional is True
        assert rs.to_dict()["conditions"]["conditions"][0]["optional"] is True


class TestParamValidation:
    def test_missing_params_object_defaults_applied(self) -> None:
        payload = v2_rule_set()
        payload["conditions"]["conditions"][0]["type"] = "swing_point"
        payload["conditions"]["conditions"][0]["params"] = {"kind": "high", "strength": 2}
        rs = validate_rule_set(payload)
        assert rs.conditions.conditions[0].params == {"kind": "high", "strength": 2}

    def test_missing_required_param_rejected_with_path(self) -> None:
        payload = v2_rule_set()
        payload["conditions"]["conditions"][0]["params"] = {"ma_type": "sma"}
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert any(
            e["path"] == ".conditions.conditions[0].params.period"
            for e in exc.value.errors
        )

    def test_unknown_param_rejected_with_path(self) -> None:
        payload = v2_rule_set()
        payload["conditions"]["conditions"][0]["params"]["magic"] = 1
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert any(
            e["path"].endswith(".params.magic") and e["code"] == "unknown_param"
            for e in exc.value.errors
        )

    def test_param_enum_violation_rejected(self) -> None:
        payload = v2_rule_set()
        payload["conditions"]["conditions"][0]["params"]["ma_type"] = "hwma"
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert any(
            e["path"].endswith(".params.ma_type")
            and e["code"] == "invalid_param_value"
            for e in exc.value.errors
        )

    def test_zone_inverted_bounds_rejected(self) -> None:
        payload = v2_rule_set()
        payload["conditions"]["conditions"][0] = {
            "id": "z1",
            "type": "price_zone",
            "timeframe": "M15",
            "params": {"zone_low": 1.10, "zone_high": 1.05, "operator": "touched"},
        }
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert any(e["path"].endswith(".params.zone_high") for e in exc.value.errors)

    def test_flat_keys_rejected_in_v2_with_hint(self) -> None:
        payload = v2_rule_set()
        c = payload["conditions"]["conditions"][0]
        c["type"] = "price_level"
        c["params"] = {"field": "close", "operator": "above", "value": 1.1}
        c["field"] = "close"  # legacy location
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert any(
            e["path"] == ".conditions.conditions[0].field"
            and "params" in e["message"]
            for e in exc.value.errors
        )


class TestRuleTypeAvailability:
    def test_planned_type_rejected(self) -> None:
        # order_block remains a PLANNED type in Phase 4 (mss_choch, bos,
        # liquidity_sweep and fvg were promoted to operational).
        payload = v2_rule_set()
        payload["conditions"]["conditions"][0] = {
            "id": "ob1",
            "type": "order_block",
            "timeframe": "M15",
            "params": {},
        }
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert any(
            e["code"] == "rule_type_not_available" and "order_block" in e["message"]
            for e in exc.value.errors
        )

    def test_every_planned_type_rejected(self) -> None:
        from app.domains.strategies.rule_registry import PLANNED_RULE_TYPES

        for planned in PLANNED_RULE_TYPES:
            payload = v2_rule_set()
            payload["conditions"]["conditions"][0] = {
                "id": "x1",
                "type": planned,
                "timeframe": "M15",
                "params": {},
            }
            with pytest.raises(RuleValidationError) as exc:
                validate_rule_set(payload)
            assert any(
                e["code"] == "rule_type_not_available" for e in exc.value.errors
            ), planned

    def test_unknown_type_rejected(self) -> None:
        payload = v2_rule_set()
        payload["conditions"]["conditions"][0]["type"] = "astro_alignment"
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert any(
            e["code"] == "unsupported_condition_type" for e in exc.value.errors
        )

    def test_v1_rejects_non_price_level_types(self) -> None:
        payload = v1_rule_set()
        payload["conditions"]["conditions"][0] = {
            "id": "c1",
            "type": "moving_average",
            "timeframe": "H1",
            "params": {"ma_type": "sma", "period": 5, "compare_to": "value", "value": 1, "operator": "above"},
        }
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert any("price_level" in e["message"] for e in exc.value.errors)


class TestSummaryLines:
    def test_summary_covers_scope_and_rules(self) -> None:
        rs = validate_rule_set(
            v2_rule_set(
                symbols=["EURUSD"],
                market_regime="trending",
                evaluation={"on_close_only": True},
                direction="both",
                confirmation={
                    "logic": "AND",
                    "conditions": [
                        {
                            "id": "sw",
                            "type": "swing_point",
                            "timeframe": "M15",
                            "params": {"kind": "low", "strength": 2},
                        }
                    ],
                    "groups": [],
                },
            )
        )
        lines = rs.summary_lines()
        joined = "\n".join(lines)
        assert "Both directions" in joined
        assert "EURUSD" in joined
        assert "trending" in joined
        assert "closed candles only" in joined
        assert "confirmation" in joined

    def test_schema_2_0_default_lookback_from_registry(self) -> None:
        payload = v2_rule_set()
        del payload["conditions"]["conditions"][0]["lookback"]
        rs = validate_rule_set(payload)
        assert rs.conditions.conditions[0].lookback == 20  # moving_average default


class TestLimitsStillEnforced:
    def test_too_many_symbols_rejected(self) -> None:
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(
                v2_rule_set(symbols=[f"S{i}" for i in range(LIMITS["max_symbols"] + 1)])
            )
        assert any(e["code"] == "too_many_items" for e in exc.value.errors)
