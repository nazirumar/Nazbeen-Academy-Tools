"""Generate docs/strategy-rule-set.schema.json from the backend registry.

Run from backend/:  python ../scripts/generate_rule_schema.py
Keeps the published JSON Schema in lockstep with the code (single source of
truth: app.domains.strategies.rules + rule_registry).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.domains.market_data.timeframes import SUPPORTED_TIMEFRAMES  # noqa: E402
from app.domains.strategies.rule_registry import registry_summary  # noqa: E402
from app.domains.strategies.rules import (  # noqa: E402
    DIRECTIONS,
    GROUP_LOGIC,
    LIMITS,
    MARKET_REGIMES,
    SUPPORTED_MODES,
    SUPPORTED_SCHEMA_VERSIONS,
)


def condition_schema() -> dict:
    return {
        "type": "object",
        "required": ["id", "type", "timeframe"],
        "properties": {
            "id": {"type": "string", "maxLength": LIMITS["id_max_len"]},
            "type": {
                "type": "string",
                "description": "Must be an AVAILABLE type in the registry "
                "(see /api/v1/rule-types). Planned types are rejected with "
                "rule_type_not_available.",
            },
            "timeframe": {"type": "string", "enum": list(SUPPORTED_TIMEFRAMES)},
            "lookback": {
                "type": "integer",
                "minimum": LIMITS["lookback_min"],
                "maximum": LIMITS["lookback_max"],
            },
            "optional": {"type": "boolean", "default": False},
            "params": {"type": "object", "description": "Per-type parameters."},
        },
        "additionalProperties": True,
    }


def group_schema() -> dict:
    return {
        "type": "object",
        "required": ["logic"],
        "properties": {
            "logic": {"type": "string", "enum": list(GROUP_LOGIC)},
            "conditions": {
                "type": "array",
                "maxItems": LIMITS["max_conditions_per_group"],
                "items": condition_schema(),
            },
            "groups": {
                "type": "array",
                "items": {"$ref": "#/definitions/group"},
                "description": f"Nested groups; max depth {LIMITS['max_group_depth']}.",
            },
        },
        "additionalProperties": False,
    }


def main() -> None:
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": "https://ta-workbench.local/schemas/strategy-rule-set-2.0.json",
        "title": "TA Workbench strategy rule set (schema 2.0)",
        "type": "object",
        "required": ["schema_version", "mode", "timeframes", "direction", "conditions"],
        "properties": {
            "schema_version": {"type": "string", "enum": list(SUPPORTED_SCHEMA_VERSIONS)},
            "mode": {"type": "string", "enum": list(SUPPORTED_MODES)},
            "timeframes": {
                "type": "array",
                "minItems": 1,
                "maxItems": LIMITS["max_timeframes"],
                "items": {"type": "string", "enum": list(SUPPORTED_TIMEFRAMES)},
                "description": "Binary Options defaults to ['M1']; higher "
                "timeframes are allowed only when explicitly declared.",
            },
            "direction": {"type": "string", "enum": list(DIRECTIONS)},
            "symbols": {
                "type": "array",
                "maxItems": LIMITS["max_symbols"],
                "items": {"type": "string", "minLength": 1, "maxLength": LIMITS["symbol_max_len"]},
            },
            "market_regime": {"type": "string", "enum": list(MARKET_REGIMES)},
            "evaluation": {
                "type": "object",
                "properties": {
                    "on_close_only": {"type": "boolean", "default": False}
                },
                "additionalProperties": False,
            },
            "conditions": {"$ref": "#/definitions/group"},
            "confirmation": {"oneOf": [{"$ref": "#/definitions/group"}, {"type": "null"}]},
            "invalidation": {"oneOf": [{"$ref": "#/definitions/group"}, {"type": "null"}]},
            "notes": {"type": "string", "maxLength": LIMITS["notes_max_len"]},
        },
        "additionalProperties": False,
        "definitions": {"group": group_schema()},
        "ta_workbench_rule_types": registry_summary(),
    }

    out = Path(__file__).resolve().parents[1] / "docs" / "strategy-rule-set.schema.json"
    out.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
