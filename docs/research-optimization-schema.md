# Research & Optimization Schema (Phase 8)

Contract for the strategy optimization, walk-forward validation and
robustness workspace. The research layer reuses the Phase 6 backtest
engine (`backtest.engine.run_walk`) — it does **not** contain a second
simulation implementation — and stores every run so it stays
reproducible and auditable.

Hard guarantees, enforced end to end:

* **No order execution.** Research runs are historical simulations over
  the stored candle cache. Nothing anywhere places orders.
* **No fabricated data.** Metrics that cannot be computed are `null`
  ("Not available"), never zero. Failed/invalid candidates are recorded,
  never silently discarded.
* **Immutable versions.** Runs reference an explicit `strategy_version`
  (published/superseded only) and store the immutable rule-set snapshot
  inside the run config. Later strategy edits can never alter an earlier
  run's meaning.
* **No automatic publishing.** The best candidate is never saved,
  published or executed automatically. Saving a candidate as a new
  *draft* strategy version is an explicit user action through the
  existing Strategy Builder endpoint.
* **No profit claims.** Every aggregate carries explicit disclaimers.

---

## 1. Run creation — `POST /api/v1/research/optimization`

Request (all fields validated server-side; unknown fields rejected):

```jsonc
{
  "mode": "forex",                          // or "binary_options"
  "strategy_id": "uuid",
  "strategy_version": 2,                    // immutable (published/superseded)
  "symbol": "EURUSD",
  "timeframe": "M15",                       // optional; default = strategy's finest
  "start": "2025-01-01T00:00:00Z",          // tz-aware ISO-8601
  "end": "2025-03-01T00:00:00Z",
  "parameters": [                           // 1..RESEARCH_MAX_PARAMETERS
    {
      "condition_id": "px",
      "field": "params.value",              // "lookback" or "params.<existing key>"
      "label": "Breakout threshold",
      "values": [1.10, 1.15, 1.20]          // explicit allowed values
    }
  ],
  "objective": {
    "metric": "net_pnl",
    "constraints": {
      "min_decided_trades": 30,
      "max_drawdown_pct": 25.0
    }
  },
  "validation_mode": "holdout",             // "in_sample_only" | "holdout" | "walk_forward"
  "holdout": { "in_sample_pct": 0.7 },      // holdout only
  "walk_forward": {                          // walk_forward only
    "train_candles": 200,
    "test_candles": 100,
    "step_candles": 100,                     // MUST be >= test_candles
    "window_mode": "rolling"                 // or "expanding"
  },
  "forex_execution": { "...": "same schema as the backtest API" },
  "binary": { "...": "same schema as the backtest API" },
  "forex_cost_variants": [                   // robustness B (Forex)
    { "spread_pips": 2.0 },                  // only cost fields may vary
    { "commission_per_lot_per_side": 5.0 }
  ],
  "binary_cost_variants": [                  // robustness B (Binary)
    { "payout_pct": 0.65 },
    { "tie_policy": "loss" }
  ],
  "param_sensitivity": true,                 // robustness A
  "run_bootstrap": true,                     // robustness F (seeded, bounded)
  "seed": 7,                                 // recorded; used by the bootstrap
  "notes": "optional free text"
}
```

### Parameter addressing

A parameter varies ONE existing field of ONE existing condition of the
immutable rule-set snapshot:

* `lookback` — the condition's lookback (bounded by the rule format's
  global limits), or
* `params.<key>` — an existing key of that condition's params object.

The patch validator rejects unknown conditions, unknown param keys,
type-incompatible values and out-of-range lookbacks. Every candidate
patch is applied to a deep copy and REVALIDATED with the standard
`validate_rule_set` before any walk — optimization can never introduce
new rules, new parameters or arbitrary user code.

### Objective metrics

| metric | direction | requires |
|---|---|---|
| `net_pnl` | maximize | simulated trades |
| `expectancy` | maximize | simulated trades |
| `profit_factor` | maximize | simulated trades |
| `win_rate` | maximize (warning attached) | simulated trades |
| `avg_return_pct_per_trade` | maximize | simulated trades |
| `max_drawdown_pct` | minimize | simulated trades |
| `conditions_met_count` | maximize | setup evaluations only |

Directions are fixed and documented; a user cannot "maximize drawdown".
A trade-level objective on a setup-only run is rejected
(`OBJECTIVE_REQUIRES_TRADE_SIMULATION`). `win_rate` and
`conditions_met_count` carry explicit misuse warnings in the stored
config. Ranking on win rate alone is discouraged in the UI.

### Creation-time bounds (env-tunable)

| setting | default | meaning |
|---|---|---|
| `RESEARCH_MAX_PARAMETERS` | 8 | parameter dimensions per run |
| `RESEARCH_MAX_VALUES_PER_PARAMETER` | 12 | allowed values per dimension |
| `RESEARCH_MAX_COMBINATIONS` | 200 | distinct candidates per run |
| `RESEARCH_MAX_WALKS` | 1500 | total engine walks (grid × phases + scenarios) |
| `RESEARCH_MAX_WINDOWS` | 40 | walk-forward windows per run |
| `RESEARCH_MAX_COST_SCENARIOS` | 6 | execution-cost variants |
| `RESEARCH_BOOTSTRAP_SAMPLES` | 200 | bootstrap resamples (0 disables) |
| `RESEARCH_MAX_CONCURRENT` | 1 | in-process research runs |

Violations answer `422` with codes `TOO_MANY_PARAMETERS`,
`TOO_MANY_VALUES`, `TOO_MANY_COMBINATIONS`,
`RESEARCH_WALK_BUDGET_EXCEEDED`, `OVERLAPPING_TEST_WINDOWS`,
`INSUFFICIENT_HISTORY_FOR_WINDOWS`, `TOO_MANY_COST_VARIANTS`,
`COST_VARIANTS_REQUIRE_EXECUTION_CONFIG`, `DRAFT_VERSION_NOT_RESEARCHABLE`,
`MODE_MISMATCH`, `SYMBOL_NOT_IN_SCOPE`, `TIMEFRAME_NOT_DECLARED`,
`PARAM_CONDITION_NOT_FOUND`, `PARAM_KEY_NOT_FOUND`,
`PARAM_VALUE_TYPE_INVALID`, `PARAM_VALUE_OUT_OF_RANGE`,
`OBJECTIVE_REQUIRES_TRADE_SIMULATION`, `INVALID_SPLIT`.

---

## 2. Validation modes and leakage prevention

### in_sample_only

Every candidate (including candidate 0 — the ORIGINAL, unmodified
parameterization) is evaluated once over the full window. Ranking is an
explicit in-sample fit; the run's config, warnings and disclaimers say
so (`IN_SAMPLE_ONLY`).

### holdout

* The period splits at `start + in_sample_pct × span`, aligned to the
  primary timeframe grid. The in-sample end is stored in the config.
* All candidates are evaluated on the IN-SAMPLE window ONLY; scores,
  ranks and constraint checks come from those walks.
* The out-of-sample window is walked EXACTLY ONCE, for the in-sample
  winner, with FROZEN parameters. No other candidate is ever walked on
  the out-of-sample period (no OOS-based cherry-picking).
* Boundary exclusion: the OOS walk starts one primary interval AFTER
  the in-sample end, so no candle is evaluated in both periods.

### walk_forward

* Windows are generated chronologically from the configured period:
  each window is `[train_start, train_end)` training followed by
  `[train_end, train_end + test_len)` testing (`train_end == test_start`:
  contiguous, no gap, no overlap). Successive windows advance by
  `step_candles ≥ test_candles`, so test windows never overlap and
  out-of-sample observations are never double-counted.
* For every window: all candidates are evaluated on the TRAINING window
  only; the winner is selected by objective + constraints; the frozen
  winner is walked once on the test window. Per-candidate training
  evaluations live on the window row; the test digest lives on the
  window too. Candidate rows carry `times_selected` and an average
  TRAINING score; `rank` stays `NULL` (there is no global ranking).
* Selection happens per window on training data only — the API layer
  has no path that could rank candidates on test data.
* Windows with fewer stored candles than the configured minimum are
  marked `skipped` with `INSUFFICIENT_WINDOW_DATA` and excluded from
  the aggregate (documented in the run aggregate).

### Purge policy (all modes)

Every walk executes against the loaded series **clipped at the walk's
phase end**: candles closing after the phase boundary are removed, so
neither the evaluator nor the Forex exit resolver can consult data
beyond the phase. Trades that cannot settle inside their phase become
`open` / `unresolved` and are excluded from decided-trade statistics.
This is a deliberately conservative purge: selection-window metrics
never include trades whose outcome required future candles.

Candles are loaded ONCE per run (plus warmup before the configured
start) and shared by every candidate/window — candidates can never see
different data. The engine's own zero-look-ahead guarantees (Phase 6)
are unchanged and still covered by its test suite.

---

## 3. Robustness scenarios

Persisted on `research_scenarios` with explicit assumptions, deltas and
a note about what each scenario can and cannot establish.

| category | what it does | what it cannot establish |
|---|---|---|
| `cost_variant` | Re-runs the configured evaluation with EXPLICIT execution-cost overrides (Forex: spread/slippage/commission/swap; Binary: payout/fees/tie policy). Walk-forward runs re-run the whole window procedure per variant. | Robustness; a surviving scenario is not evidence of profitability. |
| `param_neighbor` | Re-evaluates the SELECTION window for nearby permitted values (one step per dimension; from the original parameterization: every allowed value that differs from the current one). | Out-of-sample robustness; smooth neighbours ≠ safety. |
| `bootstrap` | OPTIONAL: seeded i.i.d. resampling (with replacement) of the recorded win/loss trade P&L list; percentile bands for net P&L / expectancy / resampled-path drawdown. Bounded by `RESEARCH_BOOTSTRAP_SAMPLES`. | Anything about future profitability; it assumes i.i.d. trades (real trades cluster), never generates prices, and is NOT a significance test. |
| `selection_stability` | Walk-forward only: how consistently the optimizer picked the same parameters across training windows. | Stability ≠ profitability. |

Derived (no extra walks) stability views are stored in the run
aggregate: time-period stability (monthly breakdowns / per-window OOS
series), market-regime stability (from the deterministic classifier's
own labels) and data-quality sensitivity (warnings + their meaning).

---

## 4. Persistence

* `research_runs` — configuration (JSONB, incl. seed, limits, software
  versions, immutable rule-set snapshot), lifecycle
  (`queued → running → completed | failed | cancelled`), progress,
  data-quality, aggregate, error.
* `research_candidates` — one row per explicitly selected combination
  (candidate 0 = original), with the patch, the FULL patched rule set,
  score / rank / constraint violations and in/out-of-sample digests.
* `research_windows` — walk-forward windows with exact period
  boundaries, per-candidate training evaluations, frozen selections and
  test digests.
* `research_scenarios` — robustness scenarios with assumptions, metrics
  and deltas.

All rows are owner-scoped (`X-User-Id`); foreign rows answer 404 (no
existence leak). Candidate and window listings are paginated. New runs
never overwrite previous results. Startup recovery marks stale
`queued`/`running` rows `failed` (`RUNNER_RESTARTED`) — in-process runs
do not survive restarts and the platform never fakes completion.

## 5. Endpoints

```
POST   /api/v1/research/optimization                        validate + queue
GET    /api/v1/research/optimization                        paginated history (filters: mode, strategy_id, status, validation_mode)
GET    /api/v1/research/optimization/parameter-schema       optimizable-parameter inventory of a strategy version
GET    /api/v1/research/optimization/{run_id}               config, progress, aggregate
GET    /api/v1/research/optimization/{run_id}/candidates    ranked candidates (paginated; ?sort=rank|index)
GET    /api/v1/research/optimization/{run_id}/candidates/{index}   one candidate (full patched rule set + digests)
GET    /api/v1/research/optimization/{run_id}/walk-forward  chronological windows (paginated)
GET    /api/v1/research/optimization/{run_id}/robustness    robustness scenarios
POST   /api/v1/research/optimization/{run_id}/cancel        cooperative cancellation
```

Saving a candidate as a draft version reuses the existing
`POST /api/v1/strategies/{id}/versions` endpoint — deliberately, so the
candidate can never bypass the Strategy Builder's validation and
draft/publish lifecycle.

## 6. Known limits (documented, not hidden)

* In-process execution only: runs do not survive restarts (startup
  recovery marks them failed). No external task queue is introduced.
* Binary options results are candle-based historical simulations with
  the configured payout/expiry/tie assumptions — not broker execution
  results. MT5 candles are not another broker's feed; OTC prices are
  never simulated.
* The walk-forward aggregate does NOT compound a cross-window equity
  curve; drawdown is reported per window.
* The bootstrap assumes i.i.d. trades and is not a significance test.
* Foreach mode, metrics and the objective can only be as good as the
  declared assumptions; the platform never invents friction or fills.
