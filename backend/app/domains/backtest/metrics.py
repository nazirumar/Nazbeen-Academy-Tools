"""Performance metrics derived ONLY from actual simulated records.

Rules:

* A metric that cannot be computed correctly from the run's records is
  ``None`` — the UI renders "Not available". Missing metrics are never
  presented as zero.
* ``win_rate`` denominator = decided trades (wins + losses). Ties, open,
  unresolved, ambiguous and skipped records are excluded and reported
  separately. The denominator is stated in the payload.
* Forex P&L is never inferred from setup counts: without simulated trade
  records (an explicit execution configuration) every financial metric is
  ``None``.
* Historical simulation statistics describe the PAST RUN ONLY. Every
  payload carries a note that these are not expected future performance.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any

_METRIC_NOTE = (
    "Metrics describe this historical simulation only. They are not "
    "predictions, carry no probability of profit, and must not be read as "
    "expected future performance."
)

_WIN_RATE_NOTE = (
    "win_rate = wins / (wins + losses); ties, open, unresolved, ambiguous "
    "and skipped records are excluded from the denominator and reported "
    "separately."
)

_SESSION_BUCKETS: tuple[tuple[str, int, int], ...] = (
    # UTC hour boundaries [start, end)
    ("asian", 0, 7),
    ("london", 7, 13),
    ("new_york", 13, 20),
    ("pacific", 20, 24),
)


def _session_for(epoch: int) -> str:
    hour = datetime.fromtimestamp(epoch, tz=timezone.utc).hour
    for name, lo, hi in _SESSION_BUCKETS:
        if lo <= hour < hi:
            return name
    return "unknown"


def _month_for(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m")


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 8)


def max_drawdown(equity_series: list[float]) -> dict[str, float] | None:
    """Peak-to-trough drawdown over a chronological equity series."""

    if len(equity_series) < 2:
        return None
    peak = equity_series[0]
    max_dd = 0.0
    max_dd_pct = 0.0
    for value in equity_series:
        peak = max(peak, value)
        dd = peak - value
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = (dd / peak * 100.0) if peak > 0 else 0.0
    return {
        "max_drawdown": round(max_dd, 8),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "points": len(equity_series),
    }


def _breakdown(
    records: list[dict[str, Any]], key_fn, metric_fields: list[str]
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        key = key_fn(record)
        if key is None:
            continue
        buckets.setdefault(str(key), []).append(record)
    output: list[dict[str, Any]] = []
    for key in sorted(buckets):
        group = buckets[key]
        entry: dict[str, Any] = {"key": key, "count": len(group)}
        pnls = [r["pnl"] for r in group if isinstance(r.get("pnl"), (int, float))]
        if pnls:
            entry["net_pnl"] = round(sum(pnls), 8)
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            entry["wins"] = len(wins)
            entry["losses"] = len(losses)
            win_rate = len(wins) / (len(wins) + len(losses))
            entry["win_rate"] = round(win_rate, 6) if (wins or losses) else None
        else:
            entry["net_pnl"] = None
            entry["win_rate"] = None
        output.append(entry)
    _ = metric_fields
    return output


def compute_metrics(
    *,
    mode: str,
    setup_records: list[dict[str, Any]],
    trade_records: list[dict[str, Any]],
    total_candles_evaluated: int,
    simulation_mode: str,
    planned_rr: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate metrics over the run's chronological records."""

    status_counts = Counter(
        r.get("setup_status") for r in setup_records if r.get("setup_status")
    )
    conditions_total: Counter[str] = Counter()
    for record in setup_records:
        summary = (record.get("evaluation") or {}).get("conditions_summary") or {}
        for status, count in summary.items():
            if isinstance(count, int):
                conditions_total[status] += count

    realized = [
        r
        for r in trade_records
        if isinstance(r.get("pnl"), (int, float))
        and r.get("outcome") in ("win", "loss", "tie")
    ]
    pnls = [float(r["pnl"]) for r in realized]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    decided = len(wins) + len(losses)
    gross_profit = round(sum(wins), 8)
    gross_loss = round(sum(losses), 8)

    outcome_counts = Counter(
        r.get("outcome") for r in trade_records if r.get("outcome")
    )

    equity_points = [
        {"seq": r["seq"], "time": r["result_time"], "equity": r["equity_after"]}
        for r in trade_records
        if isinstance(r.get("equity_after"), (int, float))
    ]
    equity_series = [p["equity"] for p in equity_points]

    # --- trade-level ratios ---------------------------------------------------
    win_rate = round(len(wins) / decided, 6) if decided else None
    avg_win = _mean(wins)
    avg_loss = _mean(losses)  # negative
    expectancy = _mean(pnls)
    if gross_profit > 0 and gross_loss < 0:
        profit_factor = round(gross_profit / abs(gross_loss), 6)
    else:
        profit_factor = None  # undefined: no losses or no wins — never "inf"

    returns = [
        float(r["return_pct"])
        for r in trade_records
        if isinstance(r.get("return_pct"), (int, float))
        and r.get("outcome") in ("win", "loss", "tie")
    ]
    avg_return_pct = _mean(returns)

    r_multiples: list[float] = []
    for record in realized:
        execution = record.get("execution") or {}
        risk_amount = execution.get("risk_amount")
        if isinstance(risk_amount, (int, float)) and risk_amount > 0:
            r_multiples.append(float(record["pnl"]) / risk_amount)
    avg_r = _mean(r_multiples) if r_multiples else None

    holding: list[int] = []
    for record in realized:
        execution = record.get("execution") or {}
        candles_held = execution.get("candles_held")
        if isinstance(candles_held, int):
            holding.append(candles_held)
    avg_holding_candles = _mean([float(h) for h in holding])

    exposure_pct: float | None = None
    if mode == "forex" and total_candles_evaluated > 0 and holding:
        exposure_pct = round(
            min(1.0, sum(holding) / total_candles_evaluated) * 100.0, 4
        )

    setup_breakdowns = {
        "by_regime": _breakdown(setup_records, lambda r: r.get("regime"), []),
        "by_direction": _breakdown(setup_records, lambda r: r.get("direction"), []),
        "by_month": _breakdown(setup_records, lambda r: _month_for(r["result_time"]), []),
    }
    trade_breakdowns = {
        "by_regime": _breakdown(trade_records, lambda r: r.get("regime"), []),
        "by_direction": _breakdown(trade_records, lambda r: r.get("direction"), []),
        "by_month": _breakdown(trade_records, lambda r: _month_for(r["result_time"]), []),
    }
    if mode == "forex":
        trade_breakdowns["by_session"] = _breakdown(
            trade_records, lambda r: _session_for(r["result_time"]), []
        )
        setup_breakdowns["by_session"] = _breakdown(
            setup_records, lambda r: _session_for(r["result_time"]), []
        )

    metrics: dict[str, Any] = {
        "note": _METRIC_NOTE,
        "win_rate_note": _WIN_RATE_NOTE,
        "simulation_mode": simulation_mode,
        "data": {
            "total_candles_evaluated": total_candles_evaluated,
            "setup_records": len(setup_records),
            "trade_records": len(trade_records),
        },
        "setups": {
            "total_evaluated": len(setup_records),
            "by_status": {status: status_counts.get(status, 0) for status in (
                "conditions_met",
                "conditions_missing",
                "invalidated",
                "insufficient_data",
                "inconclusive",
                "regime_mismatch",
            )},
            "conditions_summary_totals": dict(conditions_total),
            "first_evaluation_time": (
                setup_records[0]["result_time"] if setup_records else None
            ),
            "last_evaluation_time": (
                setup_records[-1]["result_time"] if setup_records else None
            ),
            "breakdowns": setup_breakdowns,
        },
        "trades": {
            "total": len(trade_records),
            "by_outcome": {outcome: outcome_counts.get(outcome, 0) for outcome in (
                "win", "loss", "tie", "open", "unresolved", "ambiguous", "skipped"
            )},
            "decided_trades": decided,
            "win_rate": win_rate,
            "win_rate_denominator": "wins + losses (decided trades)",
            "net_pnl": round(sum(pnls), 8) if pnls else None,
            "gross_profit": gross_profit if wins else None,
            "gross_loss": gross_loss if losses else None,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "expectancy": expectancy,
            "expectancy_note": (
                "Mean realized P&L per decided trade; None when no decided "
                "trades exist."
            ),
            "profit_factor": profit_factor,
            "profit_factor_note": (
                "gross_profit / |gross_loss|; None when either side is "
                "absent (no losing or no winning trades)."
            ),
            "avg_return_pct_per_trade": avg_return_pct,
            "avg_r_multiple": avg_r,
            "avg_r_note": (
                "Mean realized P&L divided by declared risk amount per "
                "trade (Forex, fixed-fractional sizing only)."
            ),
            "avg_holding_candles": avg_holding_candles,
            "exposure_pct": exposure_pct,
            "planned_risk_reward": planned_rr,
            "breakdowns": trade_breakdowns,
        },
        "equity": {
            "points_count": len(equity_points),
            "start_balance": equity_series[0] if equity_series else None,
            "end_balance": equity_series[-1] if equity_series else None,
            "max_drawdown": max_drawdown(equity_series),
            "note": (
                "Equity path from realized (closed) trade balances only; "
                "open positions are not marked to market."
                if equity_series
                else "No financial simulation produced an equity series."
            ),
        },
    }
    # Guard against NaN/Inf leaking into JSON.
    _sanitize(metrics)
    return metrics


def _sanitize(node: Any) -> None:
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                node[key] = None
            else:
                _sanitize(value)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                node[i] = None
            else:
                _sanitize(value)
