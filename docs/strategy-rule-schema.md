# Strategy Rule Set Schema — version 2.0

This document is the authoritative, versioned specification of the JSON
format that defines a strategy's rule set. The backend validator
(`backend/app/domains/strategies/rules.py`), the rule-type registry
(`rule_registry.py`), the evaluator (`app/domains/analysis/evaluator.py`)
and the frontend Strategy Builder all follow this document. A machine
readable JSON-Schema draft is provided next to this file:
[`strategy-rule-set.schema.json`](./strategy-rule-set.schema.json).

Core guarantees:

1. **Pure data.** A rule set contains numbers, strings, booleans, enums and
   nested groups only. Arbitrary code (Python/JavaScript/SQL/shell) can never
   be expressed or executed — anything outside the whitelist is rejected with
   a precise `path`.
2. **Versioned.** `schema_version` is `"2.0"` (current) or `"1.0"` (legacy,
   read-only: still valid and evaluatable, but new features require 2.0).
3. **Immutable once saved.** Saving a rule set creates a NEW draft version;
   publishing freezes it. Stored definitions are never mutated.

---

## 1. Top-level object

| Key | Type | Required | Constraints |
|---|---|---|---|
| `schema_version` | string | ✔ | `"2.0"` (or `"1.0"` legacy) |
| `mode` | string | ✔ | `"forex"` \| `"binary_options"` |
| `timeframes` | string[] | ✔ | 1–6 items from `M1, M5, M15, H1, H4, D1`; no duplicates. Every condition's `timeframe` must be declared here. Forex defaults: `D1,H4,H1,M15,M5,M1`. **Binary Options defaults to `["M1"]`; higher timeframes are allowed only when the user explicitly declares them — the platform never forces them in.** |
| `direction` | string | ✔ | `"bullish"` \| `"bearish"` \| `"both"` (`"both"` requires schema 2.0) |
| `symbols` | string[] | — | Up to 20 non-empty symbols (≤ 32 chars). Empty/absent = applies to any symbol. |
| `market_regime` | string | — | `"any"` \| `"trending"` \| `"ranging"` \| `"volatile"` \| `"quiet"` (Phase 3 buckets) \| fine-grained Phase 4 names: `"uptrend"` \| `"downtrend"` \| `"range"` \| `"breakout"` \| `"transition"` — `uncertain` never satisfies a requirement |
| `evaluation` | object | — | `{ "on_close_only": true|false }` — declares that the strategy is meant to be evaluated on closed candles. Unknown keys are rejected. |
| `conditions` | group | ✔ | Root condition group (see §2). |
| `confirmation` | group | — | Optional extra rules that must ALSO hold. |
| `invalidation` | group | — | If satisfied, the setup is invalid regardless of core rules. |
| `notes` | string | — | ≤ 2000 chars, free-form documentation. |

## 2. Condition groups (AND / OR)

```jsonc
{
  "logic": "AND",            // "AND" = every required child must hold
                             // "OR"  = at least one child must hold
  "conditions": [ /* rule objects, §3 */ ],
  "groups":     [ /* nested groups; max depth 4 */ ]
}
```

* A group must contain at least one condition or nested group.
* Limits: ≤ 25 conditions per group, ≤ 100 per rule set.
* **Rule order** is the array order. Conditions are evaluated and reported
  in the declared order; the builder exposes move-up/move-down controls.
* **Optional rules**: a condition with `"optional": true` never fails an
  `AND` group when unmet (it is still reported, and can still satisfy an
  `OR` group). Summaries distinguish unmet *required* rules from unmet
  *optional* ones.

## 3. Conditions (rules)

Schema 2.0 — parametric form:

```jsonc
{
  "id": "trend-hold",        // unique within the rule set, ≤ 64 chars
  "type": "moving_average",  // must be an AVAILABLE type in the registry
  "timeframe": "H1",         // declared in top-level timeframes
  "lookback": 60,            // 1–500; defaults to the type's default
  "optional": false,         // optional flag (default false)
  "params": { /* per-type, validated against the registry schema */ }
}
```

Legacy 1.0 form (still valid for `price_level` only):

```jsonc
{ "id": "c1", "type": "price_level", "field": "close",
  "operator": "above", "value": 1.0850, "timeframe": "H1", "lookback": 20 }
```

**Planned rule types are rejected.** A type that is in the registry with
`available: false` (e.g. `mss_choch`, `liquidity_sweep`, `fvg`,
`order_block`, `breaker_block`, `support_resistance_reaction`) fails
validation with code `rule_type_not_available`. The UI shows them disabled
with a “planned” badge; they are never evaluated and never reported as
satisfied. Query `GET /api/v1/rule-types` for the live registry.

## 4. Available rule types (operational)

Full semantics are served by the API; the essential definitions:

| Type | Params (required in bold) | “Satisfied” means |
|---|---|---|
| `price_level` | **field, operator, value** | `above/below`: latest candle of the window satisfies the comparison. `crosses_above/below`: latest candle completes the cross. |
| `candle_property` | **property, operator, value** | Derived property (open/high/low/close/body/range/upper_wick/lower_wick) of the latest candle vs threshold. |
| `candle_pattern` | **pattern**, max_body_ratio (default 0.1) | Classic pattern on the latest 1–2 candles: bullish/bearish engulfing, hammer, shooting star, doji, inside bar. |
| `swing_point` | **kind (high/low), strength (1–10)** | A swing high/low *confirmed* inside the window. Confirmation needs `strength` candles after the swing **within the supplied data** — no look-ahead. |
| `structure_pattern` | **pattern (higher_highs/higher_lows/lower_highs/lower_lows)**, count 2–5, strength (default 2) | The most recent `count` confirmed swings are strictly rising / falling (HH / HL / LH / LL). |
| `market_structure_break` | **side (bullish/bearish), strength (default 2)** | Latest close is beyond the most recent confirmed swing high (bullish) / swing low (bearish) inside the window. Generic, precisely-defined BOS. |
| `moving_average` | **ma_type (sma/ema), period 2–200, compare_to (value/price_field), operator** + value or price_field; source (default close) | SMA/EMA on the latest candle above/below the target; `crosses_*` require the latest candle to complete the cross. EMA is SMA-seeded (documented). |
| `price_zone` | **zone_low, zone_high, operator (touched/closed_inside/rejected)** | Interaction with the user-defined `[zone_low, zone_high]` zone: any candle overlaps it / latest close inside (inclusive) / latest candle touched it but closed outside. |

## 5. Evaluation semantics (no look-ahead bias)

* All evaluators are **pure functions of the candles supplied at evaluation
  time**. A condition never uses information that was unavailable when the
  last supplied candle closed. Swing confirmation is the clearest example:
  a swing candidate near the right edge is reported as pending and never
  counts until enough subsequent candles exist *in the data*.
* Group logic: `AND` — every required child satisfied; `OR` — at least one
  child satisfied. Optional unmet children never block an `AND` group.
* Overall status:
  * `invalidated` — the invalidation group is satisfied;
  * `conditions_met` — core (and confirmation, if defined) satisfied;
  * `conditions_missing` — required rules unmet, no invalidation;
  * `insufficient_data` — a referenced timeframe has no usable candles.
    The platform **never fabricates or silently back-fills** candles.
* Evidence per rule reports numeric values, timestamps, counts and the
  criterion string, so every decision can be audited.

## 6. Versioning & workflows

* `POST /strategies/{id}/versions` validates and stores a **draft** version
  (number = previous + 1). Previous versions are never touched.
* `POST /strategies/{id}/versions/{n}/publish` marks version *n* as
  **published** (the strategy's current definition; strategy becomes
  `active`). The previously published version becomes **superseded** — kept
  forever for historical analyses, never mutated, never re-publishable.
* Editing a published strategy therefore always produces a new draft; the
  definition used by historical analyses remains stable.
* Validation runs are persisted per strategy (`strategy_validation_results`)
  with schema version, outcome and errors.

## 7. Worked example (schema 2.0)

```jsonc
{
  "schema_version": "2.0",
  "mode": "forex",
  "direction": "both",
  "timeframes": ["H1", "M15"],
  "symbols": ["EURUSD"],
  "market_regime": "trending",
  "evaluation": { "on_close_only": true },
  "conditions": {
    "logic": "AND",
    "conditions": [
      {
        "id": "trend-hold",
        "type": "moving_average",
        "timeframe": "H1",
        "lookback": 60,
        "params": { "ma_type": "ema", "period": 20, "source": "close",
                     "compare_to": "value", "value": 1.08, "operator": "above" }
      },
      {
        "id": "hh",
        "type": "structure_pattern",
        "timeframe": "M15",
        "optional": true,
        "params": { "pattern": "higher_highs", "count": 2, "strength": 2 }
      }
    ],
    "groups": []
  },
  "invalidation": {
    "logic": "OR",
    "conditions": [
      { "id": "bos-down", "type": "market_structure_break", "timeframe": "M15",
        "params": { "side": "bearish", "strength": 2 } }
    ],
    "groups": []
  },
  "notes": "Example for documentation purposes."
}
```
