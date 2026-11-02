"""Unit tests: rule-format whitelist validation."""

from __future__ import annotations

import pytest

from app.domains.strategies.rules import (
    LIMITS,
    RuleSet,
    RuleValidationError,
    validate_rule_set,
)


def valid_rule_set(**overrides: object) -> dict:
    mode = overrides.get("mode", "forex")
    timeframe = "M1" if mode == "binary_options" else "H1"
    base = {
        "schema_version": "1.0",
        "mode": mode,
        "timeframes": [timeframe] if mode == "binary_options" else ["H1", "M15"],
        "direction": "bullish",
        "conditions": {
            "logic": "AND",
            "conditions": [
                {
                    "id": "c1",
                    "type": "price_level",
                    "field": "close",
                    "operator": "above",
                    "value": 1.0850,
                    "timeframe": timeframe,
                    "lookback": 20,
                }
            ],
            "groups": [],
        },
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


class TestHappyPath:
    def test_valid_rule_set_parses(self) -> None:
        rs = validate_rule_set(valid_rule_set())
        assert isinstance(rs, RuleSet)
        assert rs.mode == "forex"
        assert rs.timeframes == ("H1", "M15")
        assert rs.conditions.conditions[0].id == "c1"

    def test_auto_condition_ids_assigned(self) -> None:
        payload = valid_rule_set()
        del payload["conditions"]["conditions"][0]["id"]
        rs = validate_rule_set(payload)
        assert rs.condition_ids == frozenset({"cond_1"})

    def test_binary_options_m1_allowed(self) -> None:
        rs = validate_rule_set(
            valid_rule_set(mode="binary_options", timeframes=["M1"])
        )
        assert rs.mode == "binary_options"

    def test_canonical_roundtrip(self) -> None:
        rs = validate_rule_set(valid_rule_set())
        again = validate_rule_set(rs.to_dict())
        assert again.to_dict() == rs.to_dict()


class TestWhitelists:
    def test_unknown_condition_type_rejected(self) -> None:
        payload = valid_rule_set()
        payload["conditions"]["conditions"][0]["type"] = "ema_divergence"
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert any("Condition type" in e["message"] for e in exc.value.errors)

    def test_unknown_operator_rejected(self) -> None:
        payload = valid_rule_set()
        payload["conditions"]["conditions"][0]["operator"] = "magically_above"
        with pytest.raises(RuleValidationError):
            validate_rule_set(payload)

    def test_unknown_field_rejected(self) -> None:
        payload = valid_rule_set()
        payload["conditions"]["conditions"][0]["field"] = "typical_price"
        with pytest.raises(RuleValidationError):
            validate_rule_set(payload)

    def test_unknown_top_level_key_rejected(self) -> None:
        payload = valid_rule_set(auto_execute=True)
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert any(e["path"] == ".auto_execute" for e in exc.value.errors)

    def test_unknown_group_key_rejected(self) -> None:
        payload = valid_rule_set()
        payload["conditions"]["when"] = "sometime"
        with pytest.raises(RuleValidationError):
            validate_rule_set(payload)

    def test_unsupported_mode_rejected(self) -> None:
        with pytest.raises(RuleValidationError):
            validate_rule_set(valid_rule_set(mode="stocks"))

    def test_unsupported_direction_rejected(self) -> None:
        with pytest.raises(RuleValidationError):
            validate_rule_set(valid_rule_set(direction="sideways"))

    def test_unsupported_timeframe_rejected(self) -> None:
        with pytest.raises(RuleValidationError):
            validate_rule_set(valid_rule_set(timeframes=["W1"]))

    def test_condition_timeframe_must_be_declared(self) -> None:
        payload = valid_rule_set()
        payload["conditions"]["conditions"][0]["timeframe"] = "M5"
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert any(
            e["code"] == "undeclared_timeframe" for e in exc.value.errors
        )

    def test_schema_version_must_match(self) -> None:
        # 2.0 is supported since Phase 3; something far future must fail.
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(valid_rule_set(schema_version="9.9"))
        assert any(
            e["code"] == "unsupported_schema_version" for e in exc.value.errors
        )


class TestModeProfiles:
    def test_binary_options_allows_explicit_higher_timeframes(self) -> None:
        # Phase 3 product rule: Binary Options DEFAULTS to M1, but the user
        # may explicitly configure higher timeframes. The platform must not
        # force them in — but must accept them when declared.
        rs = validate_rule_set(
            valid_rule_set(mode="binary_options", timeframes=["M5", "M1"])
        )
        assert rs.mode == "binary_options"
        assert rs.timeframes == ("M5", "M1")

    def test_binary_options_m1_only_still_valid(self) -> None:
        rs = validate_rule_set(
            valid_rule_set(mode="binary_options", timeframes=["M1"])
        )
        assert rs.mode == "binary_options"


class TestStructure:
    def test_missing_root_group_rejected(self) -> None:
        payload = valid_rule_set()
        del payload["conditions"]
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert any(e["path"] == ".conditions" for e in exc.value.errors)

    def test_empty_group_rejected(self) -> None:
        payload = valid_rule_set()
        payload["conditions"] = {"logic": "AND"}
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert any(e["code"] == "empty_group" for e in exc.value.errors)

    def test_bad_group_logic_rejected(self) -> None:
        payload = valid_rule_set()
        payload["conditions"]["logic"] = "XOR"
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert any("logic" in e["message"] for e in exc.value.errors)

    def test_duplicate_condition_ids_rejected(self) -> None:
        payload = valid_rule_set()
        payload["conditions"]["conditions"].append(
            dict(payload["conditions"]["conditions"][0])
        )
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert any(e["code"] == "duplicate_id" for e in exc.value.errors)

    def test_depth_limit_enforced(self) -> None:
        deep_group: dict = {"logic": "OR", "conditions": [], "groups": []}
        node = deep_group
        for _ in range(LIMITS["max_group_depth"] + 2):
            node["groups"] = [{"logic": "AND", "conditions": [], "groups": []}]
            node = node["groups"][0]
        payload = valid_rule_set()
        payload["conditions"]["groups"] = [deep_group]
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert any(e["code"] == "max_depth_exceeded" for e in exc.value.errors)

    def test_lookback_out_of_range_rejected(self) -> None:
        payload = valid_rule_set()
        payload["conditions"]["conditions"][0]["lookback"] = 10_000
        with pytest.raises(RuleValidationError):
            validate_rule_set(payload)

    def test_nan_value_rejected(self) -> None:
        payload = valid_rule_set()
        payload["conditions"]["conditions"][0]["value"] = float("nan")
        with pytest.raises(RuleValidationError):
            validate_rule_set(payload)

    def test_boolean_value_rejected(self) -> None:
        payload = valid_rule_set()
        payload["conditions"]["conditions"][0]["value"] = True
        with pytest.raises(RuleValidationError):
            validate_rule_set(payload)

    def test_non_object_payload_rejected(self) -> None:
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(["not", "an", "object"])
        assert exc.value.errors[0]["path"] == "$"

    def test_all_errors_collected_not_fail_first(self) -> None:
        payload = valid_rule_set()
        payload["conditions"]["conditions"][0]["operator"] = "bad_op"
        payload["direction"] = "sideways"
        payload["timeframes"] = ["W1"]
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert len(exc.value.errors) >= 3


class TestOptionalBlocks:
    def test_confirmation_and_invalidation_parsed(self) -> None:
        payload = valid_rule_set(
            confirmation={
                "logic": "AND",
                "conditions": [
                    {
                        "id": "k1",
                        "type": "price_level",
                        "field": "close",
                        "operator": "above",
                        "value": 1.0,
                        "timeframe": "H1",
                    }
                ],
            },
            invalidation={
                "logic": "OR",
                "conditions": [
                    {
                        "id": "k2",
                        "type": "price_level",
                        "field": "close",
                        "operator": "below",
                        "value": 0.5,
                        "timeframe": "M15",
                    }
                ],
            },
        )
        rs = validate_rule_set(payload)
        assert rs.confirmation is not None
        assert rs.invalidation is not None
        assert rs.invalidation.logic == "OR"

    def test_condition_in_confirmation_may_use_any_declared_tf(self) -> None:
        payload = valid_rule_set(
            confirmation={
                "logic": "AND",
                "conditions": [
                    {
                        "id": "k1",
                        "type": "price_level",
                        "field": "close",
                        "operator": "above",
                        "value": 1.0,
                        "timeframe": "M15",
                    }
                ],
            }
        )
        rs = validate_rule_set(payload)
        assert rs.confirmation is not None
        assert rs.confirmation.conditions[0].timeframe == "M15"
