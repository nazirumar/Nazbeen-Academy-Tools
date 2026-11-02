"""Parameter inventory + explicit patch application (Phase 8).

The optimizer can ONLY vary fields that already exist in the immutable
rule-set snapshot:

* a condition's ``lookback`` (bounded by the rule format's limits), or
* an EXISTING key of that condition's ``params`` object.

A patch is a list of ``{condition_id, field, value}`` entries. Applying a
patch deep-copies the snapshot, replaces exactly those values and
REVALIDATES the whole rule set with the existing Phase 3 validator —
so a candidate can never introduce new rules, new parameters, invalid
types or out-of-range lookbacks. No code is ever generated or executed.
"""

from __future__ import annotations

import copy
from typing import Any

from app.core.errors import ValidationAppError
from app.domains.strategies.rules import LIMITS, RuleSet

_FIELD_LOOKBACK = "lookback"
_PARAMS_PREFIX = "params."


def _iter_conditions(group: Any) -> list[dict[str, Any]]:
    """Depth-first condition list of a validated rule-set group dict."""

    out: list[dict[str, Any]] = []
    if not isinstance(group, dict):
        return out
    for cond in group.get("conditions", []) or []:
        if isinstance(cond, dict):
            out.append(cond)
    for nested in group.get("groups", []) or []:
        out.extend(_iter_conditions(nested))
    return out


def conditions_index(rule_set_dict: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map condition_id -> condition dict across core/confirmation/invalidation."""

    index: dict[str, dict[str, Any]] = {}
    for section in ("conditions", "confirmation", "invalidation"):
        group = rule_set_dict.get(section)
        if group is None:
            continue
        for cond in _iter_conditions(group):
            cid = cond.get("id")
            if isinstance(cid, str):
                # First occurrence wins; validation forbids duplicate ids.
                index.setdefault(cid, cond)
    return index


def _scalar_type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def _value_type_compatible(existing: Any, new: Any) -> bool:
    if isinstance(existing, bool) or isinstance(new, bool):
        return isinstance(existing, bool) and isinstance(new, bool)
    if isinstance(existing, (int, float)) and isinstance(new, (int, float)):
        return True
    return type(existing) is type(new)


def parameter_inventory(rule_set: RuleSet) -> list[dict[str, Any]]:
    """Optimizable-parameter inventory for the configuration UI.

    Returns one entry per condition with its optimizable fields and the
    current value/type of each. Deliberately conservative: only fields the
    strategy already declares are offered — the optimizer cannot invent
    new rule structure.
    """

    snapshot = rule_set.to_dict()
    inv: list[dict[str, Any]] = []
    for cid, cond in conditions_index(snapshot).items():
        fields: list[dict[str, Any]] = []
        lookback = cond.get("lookback")
        if isinstance(lookback, int):
            fields.append(
                {
                    "field": _FIELD_LOOKBACK,
                    "current": lookback,
                    "type": "integer",
                    "min": LIMITS["lookback_min"],
                    "max": LIMITS["lookback_max"],
                }
            )
        params = cond.get("params") or {}
        if isinstance(params, dict):
            for name in sorted(params):
                current = params[name]
                if not _scalar_type_name(current) in ("boolean", "integer", "number", "string"):
                    continue  # nested/object params are not optimizable
                fields.append(
                    {
                        "field": f"{_PARAMS_PREFIX}{name}",
                        "current": current,
                        "type": _scalar_type_name(current),
                    }
                )
        if fields:
            inv.append(
                {
                    "condition_id": cid,
                    "condition_type": cond.get("type"),
                    "timeframe": cond.get("timeframe"),
                    "fields": fields,
                }
            )
    return inv


def validate_patch_specs(
    rule_set_dict: dict[str, Any],
    specs: list[dict[str, Any]],
) -> None:
    """Validate parameter definitions against the snapshot (creation time).

    Raises :class:`ValidationAppError` with a precise code when a
    parameter addresses a missing condition/field or its allowed values
    are type-incompatible. ``lookback`` values must stay inside the rule
    format's global bounds.
    """

    index = conditions_index(rule_set_dict)
    for spec in specs:
        cid = spec["condition_id"]
        field = spec["field"]
        cond = index.get(cid)
        if cond is None:
            raise ValidationAppError(
                f"Parameter '{field}' addresses condition '{cid}', which does "
                "not exist in the selected strategy version.",
                code="PARAM_CONDITION_NOT_FOUND",
            )
        if field == _FIELD_LOOKBACK:
            for value in spec["values"]:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValidationAppError(
                        f"Parameter '{field}' on condition '{cid}' accepts "
                        "integer lookback values only.",
                        code="PARAM_VALUE_TYPE_INVALID",
                    )
                if not (LIMITS["lookback_min"] <= value <= LIMITS["lookback_max"]):
                    raise ValidationAppError(
                        f"Lookback value {value} for condition '{cid}' is "
                        f"outside the allowed range "
                        f"{LIMITS['lookback_min']}..{LIMITS['lookback_max']}.",
                        code="PARAM_VALUE_OUT_OF_RANGE",
                    )
            continue
        if not field.startswith(_PARAMS_PREFIX):
            raise ValidationAppError(
                f"Unsupported parameter field '{field}'.",
                code="PARAM_FIELD_UNSUPPORTED",
            )
        param_name = field[len(_PARAMS_PREFIX):]
        params = cond.get("params") or {}
        if param_name not in params:
            raise ValidationAppError(
                f"Condition '{cid}' has no existing parameter '{param_name}'. "
                "Optimization can only vary parameters the strategy already "
                "defines — it never introduces new ones.",
                code="PARAM_KEY_NOT_FOUND",
            )
        existing = params[param_name]
        for value in spec["values"]:
            if not _value_type_compatible(existing, value):
                raise ValidationAppError(
                    f"Value {value!r} is not compatible with the existing "
                    f"type of '{field}' on condition '{cid}' "
                    f"(current: {existing!r}).",
                    code="PARAM_VALUE_TYPE_INVALID",
                )


def apply_patch(rule_set_dict: dict[str, Any], patch: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a deep-copied rule set with the patch values applied.

    Raises ``PARAM_PATCH_INVALID`` if the patch addresses a missing
    condition or field (defensive: specs were validated at creation, but
    the runner never trusts cached state).
    """

    patched = copy.deepcopy(rule_set_dict)
    index = conditions_index(patched)
    for entry in patch:
        cid = entry["condition_id"]
        field = entry["field"]
        cond = index.get(cid)
        if cond is None:
            raise ValidationAppError(
                f"Patch addresses missing condition '{cid}'.",
                code="PARAM_PATCH_INVALID",
            )
        if field == _FIELD_LOOKBACK:
            cond["lookback"] = entry["value"]
            continue
        if not field.startswith(_PARAMS_PREFIX):
            raise ValidationAppError(
                f"Unsupported patch field '{field}'.",
                code="PARAM_PATCH_INVALID",
            )
        param_name = field[len(_PARAMS_PREFIX):]
        params = cond.get("params")
        if not isinstance(params, dict) or param_name not in params:
            raise ValidationAppError(
                f"Patch addresses missing parameter '{param_name}' of "
                f"condition '{cid}'.",
                code="PARAM_PATCH_INVALID",
            )
        params[param_name] = entry["value"]
    return patched


def patch_label(patch: list[dict[str, Any]], labels: dict[str, str] | None = None) -> str:
    """Human-readable one-line description of a patch (for rankings)."""

    if not patch:
        return "original parameters"
    labels = labels or {}
    parts = []
    for entry in patch:
        key = f"{entry['condition_id']}.{entry['field']}"
        parts.append(f"{labels.get(key, key)}={entry['value']}")
    return ", ".join(parts)


def patch_key(patch: list[dict[str, Any]]) -> str:
    """Stable identity of a patch (used to find neighbours' siblings)."""

    return "|".join(f"{e['condition_id']}::{e['field']}::{e['value']!r}" for e in patch)


__all__ = [
    "apply_patch",
    "conditions_index",
    "parameter_inventory",
    "patch_key",
    "patch_label",
    "validate_patch_specs",
]
