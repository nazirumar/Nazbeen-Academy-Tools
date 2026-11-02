"""Rule-type registry — the single source of truth for condition types.

Every condition type that a strategy rule set may reference is registered
here with:

* a machine-readable ``params`` schema (enums, numeric bounds, defaults);
* human-readable semantics (what "satisfied" means, which candles are used);
* availability metadata. Types marked ``available=False`` are **planned**
  extensions: the UI may show them, but validation rejects them with
  ``rule_type_not_available`` and the evaluator never runs them.

Design rules (Phase 3):

1. A rule type is "supported" only when it appears here with
   ``available=True`` AND has an evaluator in
   ``app.domains.analysis.evaluator.CONDITION_EVALUATORS`` AND has tests.
2. Adding a new rule type must not require changes to the strategy builder:
   the frontend renders parameter forms from ``params_schema``.
3. No rule definition can execute code — parameters are pure data
   (numbers, enums, strings) validated against the schemas below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# Reusable param-schema fragments
# --------------------------------------------------------------------------- #

PRICE_FIELD_SCHEMA: dict[str, Any] = {
    "type": "string",
    "enum": ["open", "high", "low", "close"],
    "description": "Price field of a candle.",
}

COMPARISON_OPERATORS = ("above", "below", "crosses_above", "crosses_below")

_BOOL_PROPERTIES = ("bullish", "bearish")


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RuleTypeSpec:
    """Metadata + parameter schema for one condition type."""

    type: str
    title: str
    description: str
    semantics: str
    params_schema: dict[str, Any] = field(default_factory=dict)
    required_params: tuple[str, ...] = ()
    available: bool = True
    status: str = "operational"  # operational | planned
    supported_modes: tuple[str, ...] = ("forex", "binary_options")
    default_lookback: int = 20
    # Human note about how much history the rule ideally receives.
    data_note: str = ""


def _num(description: str, *, minimum: float | None = None, maximum: float | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "number", "description": description}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


def _int(description: str, *, minimum: int | None = None, maximum: int | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer", "description": description}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


def _enum(description: str, values: tuple[str, ...]) -> dict[str, Any]:
    return {"type": "string", "enum": list(values), "description": description}


SPECS: tuple[RuleTypeSpec, ...] = (
    RuleTypeSpec(
        type="price_level",
        title="Price vs level",
        description=(
            "Compares a candle price series (open/high/low/close) against a "
            "fixed numeric threshold supplied by the user."
        ),
        semantics=(
            "above/below: the LATEST candle in the window must satisfy the "
            "comparison. crosses_above/crosses_below: the latest candle must "
            "complete the cross relative to the previous candle. Evidence "
            "reports every cross in the window. Uses only candles supplied "
            "at evaluation time — no look-ahead."
        ),
        params_schema={
            "field": PRICE_FIELD_SCHEMA,
            "operator": _enum("Comparison operator.", COMPARISON_OPERATORS),
            "value": _num("Threshold to compare against."),
        },
        required_params=("field", "operator", "value"),
    ),
    RuleTypeSpec(
        type="candle_property",
        title="Candle property vs threshold",
        description=(
            "Compares a derived property of the latest closed candle (body, "
            "range, wick sizes or a raw price field) against a numeric "
            "threshold."
        ),
        semantics=(
            "The property is computed on the LATEST candle of the provided "
            "window and compared with the threshold (above/below). body = "
            "|close - open|, range = high - low, upper_wick = high - "
            "max(open, close), lower_wick = min(open, close) - low. Uses "
            "only data available at evaluation time."
        ),
        params_schema={
            "property": _enum(
                "Candle property to measure.",
                ("open", "high", "low", "close", "body", "range", "upper_wick", "lower_wick"),
            ),
            "operator": _enum("Comparison operator.", ("above", "below")),
            "value": _num("Threshold to compare against.", minimum=0),
        },
        required_params=("property", "operator", "value"),
        default_lookback=1,
    ),
    RuleTypeSpec(
        type="candle_pattern",
        title="Candlestick pattern",
        description=(
            "Detects a classic candlestick pattern on the latest closed "
            "candle(s) of the window."
        ),
        semantics=(
            "bullish/bearish_engulfing: the latest candle's body fully "
            "engulfs the previous candle's body (opposite colours). "
            "hammer: small body near the top, lower wick >= 2x body, "
            "closing in the upper half. shooting_star: mirror of the "
            "hammer (upper wick >= 2x body, close in the lower half). "
            "doji: |close - open| <= max_body_ratio * range. inside_bar: "
            "latest high/low fully inside the previous candle's range. "
            "Patterns need 1-2 candles; otherwise the rule is unsatisfied "
            "with an explicit evidence reason."
        ),
        params_schema={
            "pattern": _enum(
                "Pattern to detect.",
                (
                    "bullish_engulfing",
                    "bearish_engulfing",
                    "hammer",
                    "shooting_star",
                    "doji",
                    "inside_bar",
                ),
            ),
            "max_body_ratio": _num(
                "Doji only: maximum body size as a fraction of the candle "
                "range.",
                minimum=0.0,
                maximum=0.5,
            ),
        },
        required_params=("pattern",),
        default_lookback=2,
        data_note="Engulfing and inside_bar need at least 2 candles.",
    ),
    RuleTypeSpec(
        type="swing_point",
        title="Swing high / swing low",
        description=(
            "Checks whether a confirmed swing high or swing low of the "
            "given strength exists inside the lookback window."
        ),
        semantics=(
            "A candle is a swing high when its high is strictly greater "
            "than the highs of ``strength`` candles on BOTH sides inside "
            "the supplied window; a swing low is the mirror image. A swing "
            "is CONFIRMED only once ``strength`` subsequent candles exist, "
            "so confirmation always uses data available at evaluation time "
            "(no look-ahead: the swing near the right edge that still "
            "needs future bars is reported as 'pending' and never counts)."
        ),
        params_schema={
            "kind": _enum("Swing direction to detect.", ("high", "low")),
            "strength": _int(
                "Bars on each side required to confirm the swing.",
                minimum=1,
                maximum=10,
            ),
        },
        required_params=("kind", "strength"),
        data_note=(
            "Detecting swings of strength N near the end of the window "
            "needs N subsequent candles — request enough history."
        ),
    ),
    RuleTypeSpec(
        type="structure_pattern",
        title="Market structure (HH / HL / LH / LL)",
        description=(
            "Checks the sequence of the last confirmed swing highs or "
            "swing lows: strictly rising (higher highs / higher lows) or "
            "strictly falling (lower highs / lower lows)."
        ),
        semantics=(
            "Confirms swing points with the given strength inside the "
            "window, takes the most recent ``count`` swings of the kind "
            "implied by the pattern (highs for higher_highs/lower_highs, "
            "lows for higher_lows/lower_lows) and requires each to be "
            "strictly greater (rising) / smaller (falling) than its "
            "predecessor. Fewer than ``count`` confirmed swings => "
            "unsatisfied with evidence. No look-ahead: only swings already "
            "confirmed by subsequent candles count."
        ),
        params_schema={
            "pattern": _enum(
                "Structure sequence to require.",
                ("higher_highs", "higher_lows", "lower_highs", "lower_lows"),
            ),
            "count": _int("Swings that must form the sequence.", minimum=2, maximum=5),
            "strength": _int("Swing confirmation strength.", minimum=1, maximum=10),
        },
        required_params=("pattern",),
        default_lookback=60,
    ),
    RuleTypeSpec(
        type="market_structure_break",
        title="Market structure break (generic BOS)",
        description=(
            "The latest candle's close breaks beyond the most recent "
            "confirmed swing high (bullish) or swing low (bearish) inside "
            "the window."
        ),
        semantics=(
            "bullish: close of the latest candle > the most recent "
            "confirmed swing high in the window (and that swing must not "
            "BE the latest candle). bearish: close < the most recent "
            "confirmed swing low. The break is evaluated on the latest "
            "candle only — the rule is a snapshot, not a memory of past "
            "breaks. No look-ahead: swing levels come from candles at or "
            "before the break candle."
        ),
        params_schema={
            "side": _enum("Direction of the break.", ("bullish", "bearish")),
            "strength": _int("Swing confirmation strength.", minimum=1, maximum=10),
        },
        required_params=("side", "strength"),
        default_lookback=40,
        data_note=(
            "This is the generic, precisely-defined building block. "
            "MSS/CHOCH-specific semantics remain a planned rule type."
        ),
    ),
    RuleTypeSpec(
        type="moving_average",
        title="Moving average comparison",
        description=(
            "Computes an SMA or EMA over a price series and compares it "
            "with a numeric threshold or with a candle price field."
        ),
        semantics=(
            "above/below: the MA value on the LATEST candle must satisfy "
            "the comparison. crosses_above/crosses_below: the latest "
            "candle must complete the cross (MA vs the target series). "
            "SMA = arithmetic mean of the last ``period`` values; EMA is "
            "seeded with the SMA of the first ``period`` values and then "
            "smoothed with alpha = 2/(period+1). At least ``period`` "
            "candles are required; otherwise unsatisfied with evidence."
        ),
        params_schema={
            "ma_type": _enum("Moving-average type.", ("sma", "ema")),
            "period": _int("Averaging period.", minimum=2, maximum=200),
            "source": {**PRICE_FIELD_SCHEMA, "description": "Price field the MA is computed from."},
            "compare_to": _enum(
                "What the MA is compared with.", ("value", "price_field")
            ),
            "value": _num("Threshold (required when compare_to = value)."),
            "price_field": {
                **PRICE_FIELD_SCHEMA,
                "description": "Candle field (required when compare_to = price_field).",
            },
            "operator": _enum("Comparison operator.", COMPARISON_OPERATORS),
        },
        required_params=("ma_type", "period", "compare_to", "operator"),
    ),
    RuleTypeSpec(
        type="price_zone",
        title="Price reaching a user-defined zone",
        description=(
            "Defines a zone [zone_low, zone_high] and checks how price "
            "interacted with it inside the lookback window."
        ),
        semantics=(
            "touched: any candle in the window overlaps the zone "
            "(low <= zone_high and high >= zone_low). closed_inside: the "
            "LATEST candle's close lies inside the zone (inclusive). "
            "rejected: the latest candle touched the zone intrabar but "
            "closed outside it. All variants use only supplied candles."
        ),
        params_schema={
            "zone_low": _num("Lower boundary of the zone."),
            "zone_high": _num("Upper boundary of the zone."),
            "operator": _enum("Zone interaction to require.", ("touched", "closed_inside", "rejected")),
        },
        required_params=("zone_low", "zone_high", "operator"),
    ),
    # ---- Planned (NOT operational) ---------------------------------------- #
    RuleTypeSpec(
        type="mss_choch",
        title="MSS / CHoCH (market structure shift)",
        description=(
            "Change of character: a close beyond the opposing confirmed "
            "swing that REQUIRES prior opposite structure (two falling "
            "swing highs + two falling swing lows for a bullish shift), "
            "with an optional displacement filter."
        ),
        semantics=(
            "Bullish: the last TWO confirmed swing highs are strictly "
            "falling AND the last TWO confirmed swing lows strictly "
            "falling (bearish character), then the LATEST candle's close "
            "is above the most recent confirmed swing high. Bearish "
            "mirrors. displacement=true additionally requires the breaking "
            "candle body >= displacement_min_atr * ATR(14). If the prior "
            "character cannot be established the rule is INCONCLUSIVE — a "
            "change of character needs a character to change from. This "
            "is our precise definition, not a universal ICT standard; "
            "swings confirm with `strength` subsequent candles (no "
            "look-ahead)."
        ),
        params_schema={
            "direction": _enum("Direction of the shift.", ("bullish", "bearish")),
            "strength": _int("Swing confirmation strength.", minimum=1, maximum=10),
            "displacement": {
                "type": "boolean",
                "description": "Require displacement (body vs ATR) on the breaking candle.",
            },
            "displacement_min_atr": _num(
                "Minimum breaking-candle body in ATR(14) units when displacement is true.",
                minimum=0.1,
                maximum=10.0,
            ),
        },
        required_params=("direction",),
        default_lookback=60,
        data_note=(
            "Needs at least two confirmed swings of each kind plus the "
            "breaking candle (~2*strength+3 candles); request enough history."
        ),
    ),
    RuleTypeSpec(
        type="bos",
        title="Break of structure (continuation)",
        description=(
            "Break of structure as CONTINUATION: close beyond the most "
            "recent confirmed swing in the direction of already-aligned "
            "structure (HH+HL for bullish, LH+LL for bearish)."
        ),
        semantics=(
            "Bullish: the last TWO confirmed swing highs are strictly "
            "rising AND the last TWO confirmed swing lows strictly rising "
            "(established uptrend character), then the LATEST candle's "
            "close is above the most recent confirmed swing high. Bearish "
            "mirrors. Without the aligned character the rule is "
            "INCONCLUSIVE. The context-free snapshot break is the separate "
            "market_structure_break type. Our definition; swings confirm "
            "with `strength` subsequent candles (no look-ahead)."
        ),
        params_schema={
            "direction": _enum("Direction of the continuation break.", ("bullish", "bearish")),
            "strength": _int("Swing confirmation strength.", minimum=1, maximum=10),
        },
        required_params=("direction",),
        default_lookback=60,
        data_note="Needs ~2*strength+3 candles (two swings per kind + break).",
    ),
    RuleTypeSpec(
        type="liquidity_sweep",
        title="Liquidity sweep",
        description=(
            "The latest candle wicks STRICTLY beyond the most recent "
            "confirmed swing high/low but CLOSES back on the origin side."
        ),
        semantics=(
            "kind=high (buy-side): latest high > most recent confirmed "
            "swing high AND latest close < that swing high. kind=low "
            "(sell-side): mirror. Wick-through with close-back — a CLOSE "
            "beyond the swing would be a structure break, not a sweep. "
            "Equal touches do not sweep (strict inequality). Snapshot: "
            "only the latest candle can sweep; the reference swing must "
            "have confirmed strictly before it (no look-ahead)."
        ),
        params_schema={
            "kind": _enum("Which liquidity pool to test.", ("high", "low")),
            "strength": _int("Swing confirmation strength.", minimum=1, maximum=10),
        },
        required_params=("kind",),
        default_lookback=40,
        data_note="Needs ~2*strength+2 candles (swing confirmation + sweep).",
    ),
    RuleTypeSpec(
        type="fvg",
        title="Fair Value Gap",
        description=(
            "Three-candle imbalance: bullish when low[i+1] > high[i-1], "
            "bearish when high[i+1] < low[i-1], with an explicit "
            "mitigation requirement."
        ),
        semantics=(
            "requirement=exists: any FVG of the direction in the window. "
            "exists_unfilled: at least one FVG never fully filled by a "
            "later candle (partial mitigation allowed). "
            "exists_untouched: at least one FVG no later candle has traded "
            "into. Fill/mitigation is measured ONLY on candles after the "
            "gap completes (created at candle i+1's close) — no look-ahead. "
            "Our mitigation taxonomy: full (traded through the entire gap), "
            "partial (entered but not through), none."
        ),
        params_schema={
            "direction": _enum("Gap direction.", ("bullish", "bearish")),
            "requirement": _enum(
                "Mitigation state to require.",
                ("exists", "exists_unfilled", "exists_untouched"),
            ),
        },
        required_params=("direction", "requirement"),
        default_lookback=60,
    ),
    RuleTypeSpec(
        type="order_block",
        title="Order block (planned)",
        description="Last opposite candle before displacement.",
        semantics=(
            "PLANNED — not operational. Order-block qualification rules "
            "(which displacement counts, mitigation/inversion semantics, "
            "exactness of the last-opposite-candle choice) are not "
            "finalised. Validation rejects this type."
        ),
        available=False,
        status="planned",
    ),
    RuleTypeSpec(
        type="breaker_block",
        title="Breaker block (planned)",
        description="Failed order block used in the opposite direction.",
        semantics=(
            "PLANNED — not operational. Validation rejects this type."
        ),
        available=False,
        status="planned",
    ),
    RuleTypeSpec(
        type="support_resistance_reaction",
        title="Support/resistance reaction (planned)",
        description="User-drawn level with expected reaction behaviour.",
        semantics=(
            "PLANNED — not operational. Level management (persistence, "
            "break vs reaction) is not finalised. Validation rejects this "
            "type."
        ),
        available=False,
        status="planned",
    ),
)

REGISTRY: dict[str, RuleTypeSpec] = {spec.type: spec for spec in SPECS}

AVAILABLE_RULE_TYPES: tuple[str, ...] = tuple(
    spec.type for spec in SPECS if spec.available
)
PLANNED_RULE_TYPES: tuple[str, ...] = tuple(
    spec.type for spec in SPECS if not spec.available
)

# Defaults for optional params (documented in params_schema descriptions).
_PARAM_DEFAULTS: dict[str, dict[str, Any]] = {
    "candle_pattern": {"max_body_ratio": 0.1},
    "structure_pattern": {"strength": 2},
    "market_structure_break": {"strength": 2},
    "mss_choch": {"strength": 2, "displacement": False, "displacement_min_atr": 1.0},
    "bos": {"strength": 2},
    "liquidity_sweep": {"strength": 2},
    "moving_average": {"source": "close"},
    "swing_point": {},
    "price_zone": {},
    "candle_property": {},
    "price_level": {},
    "fvg": {},
}


class UnknownRuleTypeError(KeyError):
    """Raised when a type is not in the registry at all."""


def get_spec(rule_type: str) -> RuleTypeSpec:
    try:
        return REGISTRY[rule_type]
    except KeyError as exc:
        raise UnknownRuleTypeError(rule_type) from exc


def is_known_type(rule_type: str) -> bool:
    return rule_type in REGISTRY


def is_available_type(rule_type: str) -> bool:
    return rule_type in AVAILABLE_RULE_TYPES


def registry_summary() -> list[dict[str, Any]]:
    """Serialisable registry description for the API ``/rule-types``."""

    return [
        {
            "type": spec.type,
            "title": spec.title,
            "description": spec.description,
            "semantics": spec.semantics,
            "available": spec.available,
            "status": spec.status,
            "supported_modes": list(spec.supported_modes),
            "default_lookback": spec.default_lookback,
            "data_note": spec.data_note,
            "required_params": list(spec.required_params),
            "params_schema": spec.params_schema,
        }
        for spec in SPECS
    ]


# --------------------------------------------------------------------------- #
# Parameter validation (pure data checks — never executes anything)
# --------------------------------------------------------------------------- #

_NUMERIC_TYPES = ("number", "integer")


def _validate_param_value(
    rule_type: str,
    key: str,
    schema: dict[str, Any],
    value: Any,
    path: str,
    errors: list[dict[str, str]],
) -> None:
    expected = schema.get("type")
    if expected == "boolean":
        if not isinstance(value, bool):
            errors.append(
                {
                    "path": f"{path}.{key}",
                    "message": f"Parameter '{key}' must be a boolean.",
                    "code": "invalid_param_type",
                }
            )
        return
    if expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(
                {
                    "path": f"{path}.{key}",
                    "message": f"Parameter '{key}' must be a number.",
                    "code": "invalid_param_type",
                }
            )
            return
        import math

        if not math.isfinite(float(value)):
            errors.append(
                {
                    "path": f"{path}.{key}",
                    "message": f"Parameter '{key}' must be a finite number.",
                    "code": "invalid_param_type",
                }
            )
            return
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and float(value) < minimum:
            errors.append(
                {
                    "path": f"{path}.{key}",
                    "message": f"Parameter '{key}' must be >= {minimum}.",
                    "code": "param_out_of_range",
                }
            )
        if maximum is not None and float(value) > maximum:
            errors.append(
                {
                    "path": f"{path}.{key}",
                    "message": f"Parameter '{key}' must be <= {maximum}.",
                    "code": "param_out_of_range",
                }
            )
    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(
                {
                    "path": f"{path}.{key}",
                    "message": f"Parameter '{key}' must be an integer.",
                    "code": "invalid_param_type",
                }
            )
            return
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            errors.append(
                {
                    "path": f"{path}.{key}",
                    "message": f"Parameter '{key}' must be >= {minimum}.",
                    "code": "param_out_of_range",
                }
            )
        if maximum is not None and value > maximum:
            errors.append(
                {
                    "path": f"{path}.{key}",
                    "message": f"Parameter '{key}' must be <= {maximum}.",
                    "code": "param_out_of_range",
                }
            )
    elif expected == "string":
        if not isinstance(value, str):
            errors.append(
                {
                    "path": f"{path}.{key}",
                    "message": f"Parameter '{key}' must be a string.",
                    "code": "invalid_param_type",
                }
            )
            return
        enum = schema.get("enum")
        if enum is not None and value not in enum:
            errors.append(
                {
                    "path": f"{path}.{key}",
                    "message": (
                        f"Parameter '{key}' must be one of: {', '.join(enum)}."
                    ),
                    "code": "invalid_param_value",
                }
            )


def validate_params(
    rule_type: str,
    params: Any,
    *,
    path: str = ".params",
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Validate the ``params`` object of a condition against the registry.

    Returns ``(normalized_params, errors)``. Normalisation applies
    documented defaults for optional parameters. Never raises for content
    problems — errors carry precise paths so the editor can place them
    next to the offending field.
    """

    errors: list[dict[str, str]] = []
    spec = REGISTRY.get(rule_type)
    if spec is None:
        # Caller (rules.py) already reports the unknown/unavailable type.
        return {}, errors

    if params is None:
        params = {}
    if not isinstance(params, dict):
        errors.append(
            {
                "path": path,
                "message": "params must be a JSON object.",
                "code": "invalid_param_type",
            }
        )
        return {}, errors

    unknown = sorted(set(params) - set(spec.params_schema))
    for key in unknown:
        errors.append(
            {
                "path": f"{path}.{key}",
                "message": (
                    f"Unknown parameter '{key}' for rule type '{rule_type}'. "
                    f"Allowed: {', '.join(sorted(spec.params_schema))}."
                ),
                "code": "unknown_param",
            }
        )

    normalized: dict[str, Any] = {}
    for key in sorted(spec.params_schema):
        if key in params:
            _validate_param_value(
                rule_type, key, spec.params_schema[key], params[key], path, errors
            )
            normalized[key] = params[key]
        elif key in _PARAM_DEFAULTS.get(rule_type, {}):
            normalized[key] = _PARAM_DEFAULTS[rule_type][key]
        elif key in spec.required_params:
            errors.append(
                {
                    "path": f"{path}.{key}",
                    "message": (
                        f"Missing required parameter '{key}' for rule type "
                        f"'{rule_type}'."
                    ),
                    "code": "missing_param",
                }
            )

    # ---- cross-field rules -------------------------------------------------
    if rule_type == "price_zone":
        low = normalized.get("zone_low")
        high = normalized.get("zone_high")
        if (
            isinstance(low, (int, float))
            and not isinstance(low, bool)
            and isinstance(high, (int, float))
            and not isinstance(high, bool)
            and float(high) < float(low)
        ):
            errors.append(
                {
                    "path": f"{path}.zone_high",
                    "message": "zone_high must be >= zone_low.",
                    "code": "invalid_param_value",
                }
            )
    elif rule_type == "moving_average":
        compare_to = normalized.get("compare_to")
        if compare_to == "value" and "value" not in params:
            errors.append(
                {
                    "path": f"{path}.value",
                    "message": (
                        "compare_to='value' requires the numeric 'value' "
                        "parameter."
                    ),
                    "code": "missing_param",
                }
            )
        if compare_to == "price_field" and "price_field" not in params:
            errors.append(
                {
                    "path": f"{path}.price_field",
                    "message": (
                        "compare_to='price_field' requires the 'price_field' "
                        "parameter."
                    ),
                    "code": "missing_param",
                }
            )

    return normalized, errors


__all__ = [
    "REGISTRY",
    "AVAILABLE_RULE_TYPES",
    "PLANNED_RULE_TYPES",
    "RuleTypeSpec",
    "UnknownRuleTypeError",
    "get_spec",
    "is_known_type",
    "is_available_type",
    "registry_summary",
    "validate_params",
    "PRICE_FIELD_SCHEMA",
]
