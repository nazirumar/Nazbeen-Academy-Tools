# Analysis Report Schema (Phase 4) — version `1.0`

Every persisted report produced by `POST /api/v1/analysis/run` carries
`report_schema_version: "1.0"`. The payload is stored verbatim in the
`analysis_reports.report` JSONB column together with indexed columns
(`symbol`, `mode`, `strategy_id`, `strategy_version`, `setup_status`,
`regime`, `evaluated_at`, …). Reports are immutable: a new run always
inserts a NEW row. Candle data is never duplicated — reports carry
references only.

```jsonc
{
  "analysis_id": "uuid",
  "report_schema_version": "1.0",
  "engine_version": "4.0.0",
  "symbol": "EURUSD",
  "mode": "forex",                       // forex | binary_options

  "strategy": {                          // immutable reference + snapshot
    "id": "uuid",
    "name": "...",
    "mode": "forex",
    "direction": "bullish",
    "strategy_status": "active",
    "version": 1,                        // EXACT version evaluated
    "version_status": "published",       // published | draft | superseded
    "immutable_definition": true,
    "rule_set": { }                      // full canonical rule-set snapshot
  },

  "required_timeframes": ["M1"],         // from the strategy rule set
  "evaluated_timeframes": ["M1"],        // sorted by interval

  "data": {
    "provider": "mt5",
    "quality_status": "ok",              // ok | partial | stale | insufficient
    "closed_candles_only": true,         // forming policy actually applied
    "forming_candle_used": false,        // true => provisional results
    "timeframes": [                      // per-timeframe references, NO candles
      {
        "timeframe": "M1",
        "requested_candles": 60,
        "returned_candles": 60,
        "provider_data_status": "ok",
        "provenance": "mt5_live",        // mt5_live | postgres_cache
        "provider_warnings": [],
        "first_time": 1700000000,        // unix seconds
        "last_time": 1700035400,
        "newest_closed_time": 1700035340,
        "stale": false,
        "staleness_seconds": 42,
        "note": "Newest closed candle is 42s old (tolerance 3 × M1 ...).",
        "used_candle_count": 59,         // after forming-candle policy
        "used_first_time": 1700000000,
        "used_last_time": 1700035340
      }
    ],
    "warnings": [ { "code": "FORMING_CANDLE_EXCLUDED", "timeframe": "M1",
                    "message": "..." } ],
    "source_note": "Market data: MetaTrader 5 terminal via ..."
  },

  "evaluated_at": "2026-01-01T00:00:00.000+00:00",

  "regime": {
    "primary_timeframe": "M1",           // highest required timeframe
    "primary": {
      "timeframe": "M1",
      "regime": "uptrend",               // uptrend|downtrend|range|breakout|
                                         // transition|uncertain
      "volatility_state": "normal",      // quiet|normal|volatile|unknown
      "directional_bias": "bullish",
      "applicable_buckets": ["trending"],
      "evidence": { "ema_value": ..., "slope_atr": ..., "box_high": ...,
                    "reason": "...", "criterion": "...", "...": "..." }
    },
    "by_timeframe": [ /* one RegimeResult per required timeframe */ ],
    "agreement": {                       // NEVER forced into agreement
      "status": "agreement",             // agreement|conflict|partial|insufficient
      "detail": "All classified timeframes agree: bullish.",
      "biases": { "M1": "bullish", "H1": "bullish" }
    },
    "applicability": {
      "required": "trending",            // the strategy's market_regime
      "classified": "uptrend",
      "applicable": true,                // false => setup NOT fully satisfied
      "matched_buckets": ["trending"],
      "note": "..."
    },
    "note": "Heuristic classification ... not a forecast ... no confidence."
  },

  "observations": {                      // descriptive feature output
    "by_timeframe": [
      {
        "timeframe": "M1",
        "completeness": { "total": 60, "closed": 59, "forming": 1, "gap_count": 0, ... },
        "latest_candle": { "direction": "bullish", "body_ratio": 0.4, ... },
        "swings": { "strength": 2,
                    "highs": [{ "time": ..., "price": ..., "confirmed_at_time": ... }],
                    "high_structure": { "labels": ["HH"], "structure_trend": "mixed" } },
        "structure_breaks": [ { "time": ..., "side": "bullish", "level": ..., "reference_time": ... } ],
        "zones": [ { "zone_low": ..., "zone_high": ..., "position": "inside",
                     "distance": 0, "touch_count": 3 } ]
      }
    ],
    "note": "..."
  },

  "evaluation": {                        // four-state result tree (see below)
    "direction": "bullish",
    "setup_status": "conditions_met",    // conditions_met | conditions_missing |
                                         // invalidated | regime_mismatch |
                                         // inconclusive | insufficient_data
    "core": { /* NodeResult tree */ },
    "confirmation": null,
    "invalidation": { /* NodeResult tree or null */ },
    "core_status": "met",
    "confirmation_status": null,
    "invalidation_status": "not_met",
    "conditions_summary": { "met": 3, "not_met": 1, "inconclusive": 0, "unsupported": 0 },
    "closed_candles_only": true,
    "forming_candle_used": false,
    "warnings": [],
    "summary": ["Core conditions are met on the provided data.", "..."],
    "evaluated_at": "..."
  },

  "limitations": [                       // ALWAYS present, honest by design
    "This report describes technical conditions on historical market data. It is not a prediction, carries no probability of profit, and is not trading advice.",
    "Regime classification is an explicit, configurable heuristic ...",
    "Only candle data supplied by the connected market-data provider is used ...",
    "All candle-based conditions were evaluated on CLOSED candles only ...",
    "Binary options mode: ... NOT your binary options broker's execution feed ... OTC broker feeds are unavailable and are never simulated."
  ]
}
```

## NodeResult (condition / group tree)

```jsonc
{
  "kind": "condition",                   // condition | group
  "id": "ema-trend",
  "status": "met",                       // met | not_met | inconclusive | unsupported
  "satisfied": true,                     // derived: status == "met"
  "description": "EMA(20) of close above 1.10 on H1 (last 60 candle(s))",
  "evidence": {
    "condition_type": "moving_average",
    "timeframe": "H1",
    "parameters used": "ma_type, period, source, operator, threshold ...",
    "current_value": 1.1134,
    "criterion": "MA on the latest candle must satisfy the comparison",
    "last_candle_time": 1700035340,
    "last_candle_status": "closed",      // closed | forming | no_data
    "forming_candle_used": false,
    "reason": "..."                      // always present for inconclusive/unsupported
  },
  "children": [ /* groups only */ ],
  "logic": "AND",                        // groups only
  "optional": true                       // only when optional
}
```

Status semantics:

| Status | Meaning | AND group effect | OR group effect |
|---|---|---|---|
| `met` | Evaluated; criterion satisfied | required for group `met` | sufficient for group `met` |
| `not_met` | Evaluated; criterion failed | fails the group | insufficient alone |
| `inconclusive` | Could NOT be evaluated (insufficient history, missing reference) | blocks group `met` | surfaces as group `inconclusive` when nothing is met |
| `unsupported` | No evaluator exists for the rule type | blocks group `met` (never success) | surfaces as group `unsupported` when nothing is met |

Optional (`"optional": true`) rules never decide a group outcome but are
always reported with their own state.

## Setup status precedence

1. `insufficient_data` — a required timeframe has no usable candles (the
   platform does not fabricate them).
2. `invalidated` — the invalidation group is satisfied.
3. `regime_mismatch` — the strategy requires a regime the classifier did
   not confirm (an `uncertain` regime never satisfies a requirement).
4. `conditions_met` — core met AND confirmation (if any) met.
5. `conditions_missing` — core or confirmation evaluated and `not_met`.
6. `inconclusive` — core/confirmation `inconclusive` or `unsupported`:
   no setup conclusion is possible.

## Reproducibility & audit

* The rule-set snapshot inside `strategy.rule_set` is the canonical
  definition of the evaluated version — even if the strategy is later
  deleted (reports survive via `ON DELETE SET NULL`; `strategy_name` is
  denormalised).
* Candle references (`requested/returned/used` counts, first/last/newest
  times, provenance, staleness) are enough to re-fetch the same window;
  the candles themselves stay in the `candles` cache table.
* `engine_version` and `report_schema_version` pin the producing code.
