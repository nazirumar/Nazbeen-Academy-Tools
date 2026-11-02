"""The versioned JSON rule format — validated, whitelisted, never executed.

This module is the single source of truth for what a strategy rule set may
contain. Design principles:

1. **Explicit whitelists.** Condition types (registry), parameters,
   operators, group logic, modes, timeframes and limits are enumerated
   constants. Anything not whitelisted is rejected with a precise error path
   suitable for direct display next to the offending editor field.
2. **No code execution.** A rule set is pure data interpreted by
   ``app.domains.analysis.evaluator``; there is no ``eval``, no templates, no
   user-supplied expressions anywhere in the pipeline.
3. **Versioned.** ``schema_version`` is either ``"2.0"`` (parametric rule
   types) or ``"1.0"`` (legacy ``price_level``-only flat format, kept for
   strategies stored by earlier phases — they must remain evaluatable).
4. **Explainable.** The format separates core conditions, confirmation rules
   and invalidation rules so the evaluator can report each independently.

A ``2.0`` rule set looks like::

    {
      "schema_version": "2.0",
      "mode": "forex",
      "direction": "both",
      "timeframes": ["H1", "M15"],
      "symbols": ["EURUSD"],                      <- optional scope
      "market_regime": "trending",                <- optional applicability
      "evaluation": {"on_close_only": true},      <- optional settings
      "conditions": {                             <- root group (required)
        "logic": "AND",
        "conditions": [
          {"id": "ma-trend", "type": "moving_average", "timeframe": "H1",
           "lookback": 60,
           "params": {"ma_type": "ema", "period": 20, "compare_to": "value",
                       "value": 1.08, "operator": "above"}}
        ],
        "groups": [ {"logic": "OR", "conditions": [...], "groups": []} ]
      },
      "confirmation": {"logic": "AND", "conditions": [...]},   <- optional
      "invalidation": {"logic": "AND", "conditions": [...]},   <- optional
      "notes": "free-form user documentation (optional)"
    }

Legacy ``1.0`` rule sets keep their flat ``price_level`` shape
(``field``/``operator``/``value`` directly on the condition) and are
normalised internally to the parametric form for evaluation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.domains.market_data.timeframes import (
    SUPPORTED_TIMEFRAMES,
    is_timeframe_supported,
    validate_strategy_timeframes,
)
from app.domains.strategies.rule_registry import (
    AVAILABLE_RULE_TYPES,
    is_available_type,
    is_known_type,
    validate_params,
)

RULE_SET_SCHEMA_VERSION = "2.0"
SUPPORTED_SCHEMA_VERSIONS = ("1.0", "2.0")

SUPPORTED_MODES = ("forex", "binary_options")
DIRECTIONS = ("bullish", "bearish", "both")
GROUP_LOGIC = ("AND", "OR")
# Strategy regime applicability vocabulary. Phase 4 adds the fine-grained
# regime names produced by the regime classifier (additive change — the
# coarse Phase 3 buckets remain valid so every stored rule set stays
# loadable). Matching semantics:
#   * "any"                       -> no gate.
#   * fine-grained (uptrend, ...) -> exact match with the classified regime.
#   * coarse (trending, ...)      -> bucket match (see analysis.regime).
# The classifier's "uncertain" regime NEVER satisfies a concrete
# requirement — unknown conditions are never treated as satisfied.
MARKET_REGIMES = (
    "any",
    "trending",
    "ranging",
    "volatile",
    "quiet",
    "uptrend",
    "downtrend",
    "range",
    "breakout",
    "transition",
)

# Condition types that can appear in a VALID rule set (registry "available").
SUPPORTED_CONDITION_TYPES = AVAILABLE_RULE_TYPES
PRICE_FIELDS = ("open", "high", "low", "close")
LEGACY_OPERATORS = ("above", "below", "crosses_above", "crosses_below")
# Backwards-compatible alias (Phase 1/2 name).
SUPPORTED_OPERATORS = LEGACY_OPERATORS

LIMITS = {
    "max_group_depth": 4,
    "max_conditions_total": 100,
    "max_conditions_per_group": 25,
    "lookback_min": 1,
    "lookback_max": 500,
    "default_lookback": 20,
    "id_max_len": 64,
    "notes_max_len": 2000,
    "max_timeframes": 6,
    "max_symbols": 20,
    "symbol_max_len": 32,
}

_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "mode",
        "timeframes",
        "direction",
        "symbols",
        "market_regime",
        "evaluation",
        "conditions",
        "confirmation",
        "invalidation",
        "notes",
    }
)
_GROUP_KEYS = frozenset({"logic", "conditions", "groups"})
_CONDITION_KEYS = frozenset(
    {"id", "type", "timeframe", "lookback", "optional", "params"}
)
# Legacy (schema 1.0) flat parameter keys for price_level conditions.
_LEGACY_CONDITION_KEYS = frozenset({"field", "operator", "value"})
_EVALUATION_KEYS = frozenset({"on_close_only"})


class RuleValidationError(Exception):
    """Aggregated rule-format validation failure with precise error paths."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = errors
        count = len(errors)
        super().__init__(
            f"Rule set validation failed with {count} error"
            f"{'s' if count != 1 else ''}."
        )

    def to_details(self) -> list[dict[str, str]]:
        return self.errors


# --------------------------------------------------------------------------- #
# Parsed models (typed output of validation, consumed by the evaluator)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Condition:
    """A single rule, normalised to the parametric (2.0) representation."""

    id: str
    type: str
    timeframe: str
    params: dict[str, Any]
    lookback: int
    optional: bool = False
    # Schema-version bookkeeping for lossless storage round-trips.
    legacy: bool = False


@dataclass(frozen=True)
class Group:
    logic: str
    conditions: tuple[Condition, ...] = ()
    groups: tuple["Group", ...] = ()


@dataclass(frozen=True)
class RuleSet:
    schema_version: str
    mode: str
    timeframes: tuple[str, ...]
    direction: str
    conditions: Group
    confirmation: Group | None
    invalidation: Group | None
    notes: str | None
    symbols: tuple[str, ...] = ()
    market_regime: str | None = None
    evaluation: dict[str, Any] = field(default_factory=dict)
    condition_ids: frozenset[str] = field(default_factory=frozenset)

    def to_dict(self) -> dict[str, Any]:
        """Canonical (storage/transport) representation for its version."""

        def condition_dict(c: Condition) -> dict[str, Any]:
            if c.legacy:
                # Round-trip the legacy flat shape exactly.
                return {
                    "id": c.id,
                    "type": c.type,
                    "field": c.params["field"],
                    "operator": c.params["operator"],
                    "value": c.params["value"],
                    "timeframe": c.timeframe,
                    "lookback": c.lookback,
                }
            data: dict[str, Any] = {
                "id": c.id,
                "type": c.type,
                "timeframe": c.timeframe,
                "lookback": c.lookback,
                "params": dict(c.params),
            }
            if c.optional:
                data["optional"] = True
            return data

        def group_dict(g: Group) -> dict[str, Any]:
            return {
                "logic": g.logic,
                "conditions": [condition_dict(c) for c in g.conditions],
                "groups": [group_dict(nested) for nested in g.groups],
            }

        data: dict[str, Any] = {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "timeframes": list(self.timeframes),
            "direction": self.direction,
            "conditions": group_dict(self.conditions),
            "confirmation": group_dict(self.confirmation) if self.confirmation else None,
            "invalidation": group_dict(self.invalidation) if self.invalidation else None,
        }
        if self.symbols:
            data["symbols"] = list(self.symbols)
        if self.market_regime is not None:
            data["market_regime"] = self.market_regime
        if self.evaluation:
            data["evaluation"] = dict(self.evaluation)
        if self.notes is not None:
            data["notes"] = self.notes
        return data

    def summary_lines(self) -> list[str]:
        """Human-readable summary (used by the publish preview + tests)."""

        direction_label = {
            "bullish": "Bullish (long setups)",
            "bearish": "Bearish (short setups)",
            "both": "Both directions",
        }[self.direction]
        lines = [
            f"Mode: {self.mode.replace('_', ' ').title()} · "
            f"schema {self.schema_version} · {direction_label}.",
            f"Timeframes: {', '.join(self.timeframes)}.",
        ]
        if self.symbols:
            lines.append(f"Symbols: {', '.join(self.symbols)}.")
        else:
            lines.append("Symbols: any (no restriction).")
        if self.market_regime and self.market_regime != "any":
            lines.append(f"Market regime: {self.market_regime}.")
        if self.evaluation.get("on_close_only"):
            lines.append("Evaluation: closed candles only.")

        def count_group(g: Group) -> tuple[int, int]:  # conditions, groups
            n, gr = len(g.conditions), len(g.groups)
            for nested in g.groups:
                a, b = count_group(nested)
                n, gr = n + a, gr + b
            return n, gr

        core_n, core_g = count_group(self.conditions)
        parts = [f"core: {core_n} rule(s) in {core_g + 1} group(s)"]
        if self.confirmation is not None:
            c_n, c_g = count_group(self.confirmation)
            parts.append(f"confirmation: {c_n} rule(s) in {c_g + 1} group(s)")
        if self.invalidation is not None:
            i_n, i_g = count_group(self.invalidation)
            parts.append(f"invalidation: {i_n} rule(s) in {i_g + 1} group(s)")
        lines.append("Rules — " + "; ".join(parts) + ".")
        return lines


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def validate_rule_set(payload: Any) -> RuleSet:
    """Validate an arbitrary payload against the whitelisted rule format.

    Collects ALL violations (not just the first) and raises
    :class:`RuleValidationError` with precise ``path`` entries suitable for
    direct display in an editor UI.
    """

    errors: list[dict[str, str]] = []

    def err(path: str, message: str, code: str = "invalid_value") -> None:
        errors.append({"path": path, "message": message, "code": code})

    if not isinstance(payload, dict):
        err("$", "Rule set must be a JSON object.", "invalid_type")
        raise RuleValidationError(errors)

    # --- unknown / missing top-level keys -------------------------------------
    for key in sorted(set(payload) - _TOP_LEVEL_KEYS):
        err(
            f".{key}",
            f"Unknown key '{key}'. Allowed keys: {', '.join(sorted(_TOP_LEVEL_KEYS))}.",
            "unknown_key",
        )
    for key in ("schema_version", "mode", "timeframes", "direction", "conditions"):
        if key not in payload:
            err(f".{key}", f"Missing required key '{key}'.", "missing_key")

    if errors:
        raise RuleValidationError(errors)

    # --- schema_version --------------------------------------------------------
    schema_version = payload["schema_version"]
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        err(
            ".schema_version",
            f"Unsupported schema version '{schema_version}'. This build "
            f"supports: {', '.join(SUPPORTED_SCHEMA_VERSIONS)}.",
            "unsupported_schema_version",
        )
        raise RuleValidationError(errors)
    legacy = schema_version == "1.0"

    # --- mode / timeframes / direction ------------------------------------------
    mode = payload["mode"]
    if mode not in SUPPORTED_MODES:
        err(
            ".mode",
            f"Mode '{mode}' is not supported. Supported modes: "
            f"{', '.join(SUPPORTED_MODES)}.",
            "unsupported_mode",
        )

    timeframes = payload["timeframes"]
    if not isinstance(timeframes, list) or not all(isinstance(t, str) for t in timeframes):
        err(".timeframes", "timeframes must be a list of timeframe strings.", "invalid_type")
    elif not timeframes:
        err(".timeframes", "timeframes must not be empty.", "empty_value")
    elif len(timeframes) > LIMITS["max_timeframes"]:
        err(
            ".timeframes",
            f"At most {LIMITS['max_timeframes']} timeframes are allowed.",
            "too_many_items",
        )
    elif mode in SUPPORTED_MODES:
        for tf_err in validate_strategy_timeframes(mode, timeframes):
            err(".timeframes", tf_err, "unsupported_timeframe")
        for t in timeframes:
            if not is_timeframe_supported(t):
                err(
                    ".timeframes",
                    f"Timeframe '{t}' is not supported. Supported: "
                    f"{', '.join(SUPPORTED_TIMEFRAMES)}.",
                    "unsupported_timeframe",
                )

    direction = payload["direction"]
    if direction not in DIRECTIONS:
        err(
            ".direction",
            f"Direction '{direction}' is not supported. "
            f"Supported: {', '.join(DIRECTIONS)}.",
            "unsupported_direction",
        )
    elif legacy and direction == "both":
        err(
            ".direction",
            "Direction 'both' requires schema_version 2.0.",
            "unsupported_direction",
        )

    # --- symbols (optional scope) -------------------------------------------------
    symbols_raw = payload.get("symbols")
    symbols: tuple[str, ...] = ()
    if symbols_raw is not None:
        if legacy:
            err(".symbols", "'symbols' requires schema_version 2.0.", "unknown_key")
        elif not isinstance(symbols_raw, list) or not all(
            isinstance(s, str) for s in symbols_raw
        ):
            err(".symbols", "symbols must be a list of symbol strings.", "invalid_type")
        elif len(symbols_raw) > LIMITS["max_symbols"]:
            err(
                ".symbols",
                f"At most {LIMITS['max_symbols']} symbols are allowed.",
                "too_many_items",
            )
        else:
            cleaned: list[str] = []
            for i, s in enumerate(symbols_raw):
                token = s.strip()
                if not token or len(token) > LIMITS["symbol_max_len"]:
                    err(
                        f".symbols[{i}]",
                        "Each symbol must be a non-empty string of at most "
                        f"{LIMITS['symbol_max_len']} characters.",
                        "invalid_symbol",
                    )
                elif token in cleaned:
                    err(
                        f".symbols[{i}]",
                        f"Duplicate symbol '{token}'.",
                        "duplicate_symbol",
                    )
                else:
                    cleaned.append(token)
            symbols = tuple(cleaned)

    # --- market regime (optional applicability) -----------------------------------
    market_regime = payload.get("market_regime")
    if market_regime is not None:
        if legacy:
            err(
                ".market_regime",
                "'market_regime' requires schema_version 2.0.",
                "unknown_key",
            )
        elif market_regime not in MARKET_REGIMES:
            err(
                ".market_regime",
                f"Market regime '{market_regime}' is not supported. Supported: "
                f"{', '.join(MARKET_REGIMES)}.",
                "unsupported_market_regime",
            )

    # --- evaluation settings (optional) ---------------------------------------------
    evaluation_raw = payload.get("evaluation")
    evaluation: dict[str, Any] = {}
    if evaluation_raw is not None:
        if legacy:
            err(
                ".evaluation",
                "'evaluation' requires schema_version 2.0.",
                "unknown_key",
            )
        elif not isinstance(evaluation_raw, dict):
            err(".evaluation", "evaluation must be a JSON object.", "invalid_type")
        else:
            for key in sorted(set(evaluation_raw) - _EVALUATION_KEYS):
                err(
                    f".evaluation.{key}",
                    f"Unknown evaluation setting '{key}'. Allowed: "
                    f"{', '.join(sorted(_EVALUATION_KEYS))}.",
                    "unknown_key",
                )
            on_close = evaluation_raw.get("on_close_only")
            if on_close is not None and not isinstance(on_close, bool):
                err(
                    ".evaluation.on_close_only",
                    "on_close_only must be a boolean.",
                    "invalid_type",
                )
            evaluation = {
                k: v for k, v in evaluation_raw.items() if k in _EVALUATION_KEYS
            }

    notes = payload.get("notes")
    if notes is not None:
        if not isinstance(notes, str):
            err(".notes", "notes must be a string.", "invalid_type")
        elif len(notes) > LIMITS["notes_max_len"]:
            err(
                ".notes",
                f"notes must not exceed {LIMITS['notes_max_len']} characters.",
                "too_long",
            )

    # --- condition groups ---------------------------------------------------------
    seen_condition_ids: set[str] = set()
    total_conditions = [0]  # boxed counter shared across recursion

    def parse_condition(node: Any, path: str, seen_ids: set[str]) -> Condition | None:
        errors_before = len(errors)
        if not isinstance(node, dict):
            err(path, "Condition must be a JSON object.", "invalid_type")
            return None

        allowed_keys = (
            _CONDITION_KEYS | _LEGACY_CONDITION_KEYS if legacy else _CONDITION_KEYS
        )
        for key in sorted(set(node) - allowed_keys):
            if (
                not legacy
                and key in _LEGACY_CONDITION_KEYS
                and node.get("type") == "price_level"
            ):
                err(
                    f"{path}.{key}",
                    f"'{key}' belongs inside the 'params' object in schema "
                    "version 2.0.",
                    "unknown_key",
                )
            else:
                err(
                    f"{path}.{key}",
                    f"Unknown key '{key}' in condition. Allowed: "
                    f"{', '.join(sorted(allowed_keys))}.",
                    "unknown_key",
                )

        for key in ("type", "timeframe"):
            if key not in node:
                err(f"{path}.{key}", f"Condition requires '{key}'.", "missing_key")

        cond_type = node.get("type")
        if not isinstance(cond_type, str) or not cond_type:
            err(
                f"{path}.type",
                "Condition type must be a non-empty string.",
                "invalid_type",
            )
        elif not is_known_type(cond_type):
            err(
                f"{path}.type",
                f"Condition type '{cond_type}' is not supported. Supported "
                f"types: {', '.join(AVAILABLE_RULE_TYPES)}.",
                "unsupported_condition_type",
            )
        elif not is_available_type(cond_type):
            err(
                f"{path}.type",
                f"Rule type '{cond_type}' exists in the registry but is NOT "
                "operational yet (planned). It cannot be used in strategies "
                "until its semantics and tests are complete.",
                "rule_type_not_available",
            )
        elif legacy and cond_type != "price_level":
            err(
                f"{path}.type",
                f"Schema version 1.0 supports the 'price_level' rule type "
                f"only (got '{cond_type}'). Use schema_version 2.0 for the "
                "extended rule types.",
                "unsupported_condition_type",
            )

        cond_timeframe = node.get("timeframe")
        if isinstance(cond_timeframe, str) and isinstance(timeframes, list):
            if not is_timeframe_supported(cond_timeframe):
                err(
                    f"{path}.timeframe",
                    f"Timeframe '{cond_timeframe}' is not supported.",
                    "unsupported_timeframe",
                )
            elif cond_timeframe not in timeframes:
                err(
                    f"{path}.timeframe",
                    f"Timeframe '{cond_timeframe}' must be declared in the "
                    "rule set's top-level 'timeframes' list first.",
                    "undeclared_timeframe",
                )

        lookback = node.get("lookback")
        default_lookback = 20
        if not legacy and is_known_type(cond_type or ""):
            from app.domains.strategies.rule_registry import get_spec

            default_lookback = get_spec(cond_type).default_lookback
        if lookback is None:
            lookback = default_lookback
        if (
            isinstance(lookback, bool)
            or not isinstance(lookback, int)
            or not (LIMITS["lookback_min"] <= lookback <= LIMITS["lookback_max"])
        ):
            err(
                f"{path}.lookback",
                f"lookback must be an integer between "
                f"{LIMITS['lookback_min']} and {LIMITS['lookback_max']}.",
                "invalid_lookback",
            )
            lookback = default_lookback

        optional = node.get("optional", False)
        if optional is None:
            optional = False
        if not isinstance(optional, bool):
            err(
                f"{path}.optional",
                "optional must be a boolean.",
                "invalid_type",
            )
            optional = False
        elif optional and legacy:
            err(
                f"{path}.optional",
                "'optional' requires schema_version 2.0.",
                "unknown_key",
            )

        # ---- params (2.0) or legacy flat keys (1.0) ------------------------------
        params: dict[str, Any] = {}
        if legacy:
            for key in ("field", "operator", "value"):
                if key not in node:
                    err(
                        f"{path}.{key}",
                        f"Legacy price_level conditions require '{key}'.",
                        "missing_key",
                    )
            if not errors[errors_before:]:
                params = {
                    "field": node.get("field"),
                    "operator": node.get("operator"),
                    "value": node.get("value"),
                }
                from app.domains.strategies.rule_registry import validate_params as _vp

                params, param_errors = _vp("price_level", params, path=path)
                errors.extend(param_errors)
        else:
            raw_params = node.get("params")
            if is_known_type(cond_type or "") and is_available_type(cond_type or ""):
                raw_params = raw_params if raw_params is not None else {}
                params, param_errors = validate_params(
                    cond_type, raw_params, path=f"{path}.params"
                )
                errors.extend(param_errors)
            elif raw_params is not None and not isinstance(raw_params, dict):
                err(f"{path}.params", "params must be a JSON object.", "invalid_type")

        cond_id = node.get("id")
        if cond_id is None:
            cond_id = f"cond_{len(seen_ids) + 1}"
        if not isinstance(cond_id, str) or not cond_id:
            err(f"{path}.id", "id must be a non-empty string.", "invalid_id")
            cond_id = f"cond_invalid_{len(seen_ids) + 1}"
        elif len(cond_id) > LIMITS["id_max_len"]:
            err(
                f"{path}.id",
                f"id must not exceed {LIMITS['id_max_len']} characters.",
                "too_long",
            )
        elif cond_id in seen_ids:
            err(
                f"{path}.id",
                f"Duplicate condition id '{cond_id}'. Condition ids must be "
                "unique within a rule set.",
                "duplicate_id",
            )
        else:
            seen_ids.add(cond_id)

        # Skip the condition only when *its own* subtree produced errors;
        # errors from unrelated parts of the document must not hide it.
        own_errors = any(
            entry["path"].startswith(f"{path}.") for entry in errors[errors_before:]
        )
        if own_errors:
            return None
        return Condition(
            id=cond_id,
            type=str(cond_type or ""),
            timeframe=str(cond_timeframe or ""),
            params=dict(params),
            lookback=int(lookback),
            optional=bool(optional),
            legacy=legacy,
        )

    def parse_group(node: Any, path: str, depth: int) -> Group | None:
        if not isinstance(node, dict):
            err(path, "Condition group must be a JSON object.", "invalid_type")
            return None
        for key in sorted(set(node) - _GROUP_KEYS):
            err(
                f"{path}.{key}",
                f"Unknown key '{key}' in group. Allowed: "
                f"{', '.join(sorted(_GROUP_KEYS))}.",
                "unknown_key",
            )
        if "logic" not in node:
            err(f"{path}.logic", "Group requires a 'logic' key.", "missing_key")
            return None
        logic = node["logic"]
        if logic not in GROUP_LOGIC:
            err(
                f"{path}.logic",
                f"Group logic '{logic}' is not supported. "
                f"Supported: {', '.join(GROUP_LOGIC)}.",
                "unsupported_logic",
            )
            logic = "AND"

        raw_conditions = node.get("conditions", [])
        raw_groups = node.get("groups", [])
        if not isinstance(raw_conditions, list):
            err(f"{path}.conditions", "conditions must be a list.", "invalid_type")
            raw_conditions = []
        if not isinstance(raw_groups, list):
            err(f"{path}.groups", "groups must be a list.", "invalid_type")
            raw_groups = []
        if not raw_conditions and not raw_groups:
            err(
                path,
                "Group must contain at least one condition or nested group.",
                "empty_group",
            )

        if len(raw_conditions) > LIMITS["max_conditions_per_group"]:
            err(
                f"{path}.conditions",
                f"At most {LIMITS['max_conditions_per_group']} conditions per group.",
                "too_many_items",
            )

        conditions: list[Condition] = []
        for i, raw in enumerate(raw_conditions[: LIMITS["max_conditions_per_group"]]):
            cond = parse_condition(raw, f"{path}.conditions[{i}]", seen_condition_ids)
            if cond is not None:
                conditions.append(cond)
                total_conditions[0] += 1
        if depth >= LIMITS["max_group_depth"]:
            err(
                f"{path}.groups",
                f"Nesting exceeds the maximum group depth of "
                f"{LIMITS['max_group_depth']}.",
                "max_depth_exceeded",
            )
            nested: list[Group] = []
        else:
            nested = []
            for i, child in enumerate(raw_groups):
                child_group = parse_group(child, f"{path}.groups[{i}]", depth + 1)
                if child_group is not None:
                    nested.append(child_group)

        return Group(
            logic=logic,
            conditions=tuple(conditions),
            groups=tuple(nested),
        )

    root_group = parse_group(payload.get("conditions"), ".conditions", depth=1)

    confirmation: Group | None = None
    invalidation: Group | None = None
    if payload.get("confirmation") is not None:
        confirmation = parse_group(payload["confirmation"], ".confirmation", depth=1)
    if payload.get("invalidation") is not None:
        invalidation = parse_group(payload["invalidation"], ".invalidation", depth=1)

    if total_conditions[0] > LIMITS["max_conditions_total"]:
        err(
            "$",
            f"Rule set exceeds the maximum of "
            f"{LIMITS['max_conditions_total']} conditions in total.",
            "too_many_conditions",
        )

    if errors:
        raise RuleValidationError(errors)
    if root_group is None:
        err(".conditions", "Root condition group is missing or invalid.", "missing_key")
        raise RuleValidationError(errors)

    return RuleSet(
        schema_version=str(schema_version),
        mode=str(mode),
        timeframes=tuple(timeframes) if isinstance(timeframes, list) else (),
        direction=str(direction),
        conditions=root_group,
        confirmation=confirmation,
        invalidation=invalidation,
        notes=notes if isinstance(notes, str) else None,
        symbols=symbols,
        market_regime=market_regime if isinstance(market_regime, str) else None,
        evaluation=dict(evaluation),
        condition_ids=frozenset(seen_condition_ids),
    )


# Re-exported for convenience by routers / meta endpoint.
__all__ = [
    "RULE_SET_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "SUPPORTED_CONDITION_TYPES",
    "SUPPORTED_MODES",
    "LEGACY_OPERATORS",
    "SUPPORTED_OPERATORS",
    "DIRECTIONS",
    "GROUP_LOGIC",
    "PRICE_FIELDS",
    "MARKET_REGIMES",
    "LIMITS",
    "Condition",
    "Group",
    "RuleSet",
    "RuleValidationError",
    "validate_rule_set",
]
