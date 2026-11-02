"""Unit tests: rule-type registry (availability, params validation)."""

from __future__ import annotations

import pytest

from app.domains.analysis.evaluator import CONDITION_EVALUATORS
from app.domains.strategies import rule_registry
from app.domains.strategies.rule_registry import (
    AVAILABLE_RULE_TYPES,
    PLANNED_RULE_TYPES,
    REGISTRY,
    get_spec,
    is_available_type,
    is_known_type,
    registry_summary,
    validate_params,
)


class TestRegistryIntegrity:
    def test_every_available_type_has_an_evaluator(self) -> None:
        missing = [
            t for t in AVAILABLE_RULE_TYPES if t not in CONDITION_EVALUATORS
        ]
        assert missing == [], (
            "Registry marks these types operational but no evaluator exists: "
            f"{missing}"
        )

    def test_every_evaluator_has_a_registry_entry(self) -> None:
        unknown = [t for t in CONDITION_EVALUATORS if t not in REGISTRY]
        assert unknown == []

    def test_planned_types_have_no_evaluator(self) -> None:
        # The platform must never pretend a planned type is operational.
        for t in PLANNED_RULE_TYPES:
            assert t not in CONDITION_EVALUATORS
            assert REGISTRY[t].available is False
            assert REGISTRY[t].status == "planned"

    def test_required_params_are_present_in_schema(self) -> None:
        for spec in REGISTRY.values():
            for key in spec.required_params:
                assert key in spec.params_schema, spec.type

    def test_expected_operational_set(self) -> None:
        # Phase 4 promoted mss_choch, bos, liquidity_sweep and fvg to
        # operational (each has a precise documented definition, evaluator
        # and tests). Phase 3's eight types remain operational.
        assert set(AVAILABLE_RULE_TYPES) == {
            "price_level",
            "candle_property",
            "candle_pattern",
            "swing_point",
            "structure_pattern",
            "market_structure_break",
            "moving_average",
            "price_zone",
            "mss_choch",
            "bos",
            "liquidity_sweep",
            "fvg",
        }

    def test_expected_planned_set(self) -> None:
        assert set(PLANNED_RULE_TYPES) == {
            "order_block",
            "breaker_block",
            "support_resistance_reaction",
        }

    def test_unknown_type_helpers(self) -> None:
        assert not is_known_type("moon_phase")
        assert not is_available_type("moon_phase")
        with pytest.raises(rule_registry.UnknownRuleTypeError):
            get_spec("moon_phase")

    def test_registry_summary_shape(self) -> None:
        summary = registry_summary()
        types = [entry["type"] for entry in summary]
        assert len(types) == len(REGISTRY)
        entry = next(e for e in summary if e["type"] == "moving_average")
        assert entry["available"] is True
        assert "period" in entry["params_schema"]
        assert entry["semantics"]


class TestValidateParams:
    def test_unknown_param_rejected_with_path(self) -> None:
        _, errors = validate_params("price_level", {"field": "close", "operator": "above", "value": 1, "magic": 1})
        assert any(
            e["code"] == "unknown_param" and e["path"].endswith(".magic")
            for e in errors
        )

    def test_missing_required_param_rejected(self) -> None:
        _, errors = validate_params("swing_point", {"kind": "high"})
        assert any(e["code"] == "missing_param" and e["path"].endswith(".strength") for e in errors)

    def test_wrong_type_rejected(self) -> None:
        _, errors = validate_params("moving_average", {
            "ma_type": "sma", "period": "twenty", "compare_to": "value",
            "value": 1.0, "operator": "above",
        })
        assert any(e["code"] == "invalid_param_type" and e["path"].endswith(".period") for e in errors)

    def test_enum_violation_rejected(self) -> None:
        _, errors = validate_params("candle_pattern", {"pattern": "harami"})
        assert any(e["code"] == "invalid_param_value" for e in errors)

    def test_numeric_bounds_enforced(self) -> None:
        _, errors = validate_params("moving_average", {
            "ma_type": "sma", "period": 1, "compare_to": "value",
            "value": 1.0, "operator": "above",
        })
        assert any(e["code"] == "param_out_of_range" and e["path"].endswith(".period") for e in errors)

    def test_defaults_applied(self) -> None:
        normalized, errors = validate_params("moving_average", {
            "ma_type": "sma", "period": 20, "compare_to": "value",
            "value": 1.0, "operator": "above",
        })
        assert errors == []
        assert normalized["source"] == "close"

    def test_doji_tolerance_default(self) -> None:
        normalized, errors = validate_params("candle_pattern", {"pattern": "doji"})
        assert errors == []
        assert normalized["max_body_ratio"] == 0.1

    def test_zone_cross_field_rule(self) -> None:
        _, errors = validate_params("price_zone", {
            "zone_low": 1.10, "zone_high": 1.05, "operator": "touched",
        })
        assert any(e["path"].endswith(".zone_high") for e in errors)

    def test_ma_conditional_requirement_value(self) -> None:
        _, errors = validate_params("moving_average", {
            "ma_type": "ema", "period": 20, "compare_to": "value", "operator": "above",
        })
        assert any(e["code"] == "missing_param" and e["path"].endswith(".value") for e in errors)

    def test_ma_conditional_requirement_price_field(self) -> None:
        _, errors = validate_params("moving_average", {
            "ma_type": "ema", "period": 20, "compare_to": "price_field", "operator": "above",
        })
        assert any(e["code"] == "missing_param" and e["path"].endswith(".price_field") for e in errors)

    def test_non_object_params_rejected(self) -> None:
        _, errors = validate_params("price_level", [1, 2])
        assert any(e["code"] == "invalid_param_type" for e in errors)

    def test_unknown_rule_type_returns_no_extra_errors(self) -> None:
        # rules.py reports the unknown type itself; validate_params stays quiet.
        normalized, errors = validate_params("moon_phase", {"x": 1})
        assert normalized == {}
        assert errors == []

    def test_boolean_number_rejected(self) -> None:
        _, errors = validate_params("price_level", {
            "field": "close", "operator": "above", "value": True,
        })
        assert any(e["code"] == "invalid_param_type" for e in errors)
