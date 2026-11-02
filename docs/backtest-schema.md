# Phase 6 — Backtest data & result schema

Versioned payloads persisted by the backtesting engine
(`backtest_schema_version: "1.0"`, engine `6.0.0`). The backend is the
single source of truth; the frontend only renders these payloads.

## `BacktestRun.config` (persisted at creation, immutable)

```jsonc
{
  "mode": "forex | binary_options",
  "symbol": "EURUSD",
  "timeframe": "M15",              // must be declared by the strategy version
  "start": "2026-01-01T00:00:00.000+00:00",
  "end":   "2026-02-01T00:00:00.000+00:00",
  "warmup_candles": 150,           // BACKTEST_WARMUP_CANDLES at creation
  "max_candles": 20000,            // BACKTEST_MAX_CANDLES at creation
  "data_source": "postgres_candle_cache",
  "strategy_id": "…", "strategy_name": "…",
  "strategy_version": 2,           // EXPLICIT, immutable version under test
  "strategy_version_status": "published | superseded",
  "rule_set": { /* full validated rule-set snapshot (audit-proof) */ },
  "direction": "bullish | bearish | both",
  "notes": null,
  "forex_execution": {             // null → setup-only run
    "enabled": true,
    "spread_pips": 1.0, "slippage_pips": 0.5,
    "commission_per_lot_per_side": 0.0,
    "stop_loss_pips": 20.0, "take_profit_pips": 40.0,
    "risk_mode": "fixed_fractional", "risk_pct_per_trade": 1.0,
    "pip_value_per_lot": 10.0, "pip_size": null,
    "swap_per_lot_per_night": null,
    "account_start_balance": 10000.0,
    "sl_tp_same_candle_policy": "conservative | optimistic | ambiguous"
  },
  "binary": {                      // binary_options runs always carry it
    "expiry_candles": 1,           // candle-count based (documented)
    "payout_pct": 0.80, "stake": 10.0,
    "tie_policy": "refund | loss", "fees_per_trade": 0.0,
    "account_start_balance": 1000.0
  },
  "simulation_mode": "setup_only | trade_simulation"
}
```

## Evaluation semantics (no look-ahead)

* Walk unit = one CLOSED primary-timeframe candle in `[start, end]`.
* Evaluation instant `t_eval` = the candle's close time.
* A candle on ANY timeframe is visible at `t_eval` only when its close time
  ≤ `t_eval` — higher-timeframe candles still forming are invisible.
* The Phase 4 evaluator runs unchanged on those point-in-time windows
  (one implementation for live analysis and backtests); its wall-clock
  `evaluated_at` is replaced by the simulated instant.
* One evaluation per candle (no duplicate evaluation of the same event);
  Forex entries are suppressed while a position is open.

## `BacktestResult` records

`kind = "setup"` (one per evaluation):

```jsonc
{
  "seq": 17, "kind": "setup",
  "result_time": "2026-01-05T10:15:00.000+00:00",
  "setup_status": "conditions_met | conditions_missing | invalidated | insufficient_data | inconclusive | regime_mismatch",
  "direction": "bullish", "regime": "uptrend",
  "evaluation": { /* full four-state tree + evidence + warnings */ },
  "warnings": [ { "code": "TIMESTAMP_GAPS", "timeframe": "M15", "message": "…" } ],
  "note": null
}
```

`kind = "trade"` (only when execution assumptions were configured):

```jsonc
{
  "seq": 18, "kind": "trade",
  "result_time": "…entry instant…",
  "outcome": "win | loss | tie | open | unresolved | ambiguous | skipped",
  "pnl": 12.5, "return_pct": 0.0125, "equity_after": 10012.5,
  "execution": { /* mode-specific fills + the declared assumptions block */ },
  "note": "…ambiguous-bar / skip explanation when applicable…"
}
```

Outcome rules:

| Outcome | Forex | Binary |
| --- | --- | --- |
| `win` / `loss` | TP/SL fill resolved by candle range | expiry close vs entry close per side |
| `tie` | zero-P&L exit | equal closes → `tie_policy` (refund default) |
| `open` | data ended with the position open (no realized P&L) | — |
| `unresolved` | — | expiry candle beyond available data |
| `ambiguous` | one candle touches both SL and TP under the `ambiguous` policy (excluded from statistics) | — |
| `skipped` | — | strategy direction `both` (no executable side invented) |

## Metrics payload

Derived only from persisted records; `null` means **Not available** (the
UI renders exactly that — never a fabricated zero):

* `setups`: per-status counts, aggregated four-state condition counts,
  breakdowns by regime / direction / month (+ session for Forex).
* `trades`: outcome counts, `win_rate` = wins / (wins + losses) with the
  denominator documented inline, net/gross P&L, avg win/loss, expectancy,
  profit factor (null without both sides), avg R (fixed-fractional runs),
  holding duration, exposure, planned R:R.
* `equity`: closed-trade balance path stats + max drawdown (null without
  ≥ 2 points).

Every payload carries the disclaimer that these are historical simulation
statistics — not predictions, not expected future performance, no
probability of profit — and that MT5 data is not a binary broker's
execution feed.
