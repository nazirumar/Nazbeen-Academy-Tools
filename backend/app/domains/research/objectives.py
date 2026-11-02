"""Objective functions, constraints, ranking and result aggregation.

Rules (matching the platform's honesty contract):

* A metric the engine could not compute is ``None`` — the candidate is
  "not rankable" with an explicit reason, never silently scored as zero.
* Ranking is deterministic: direction-adjusted score first, then the
  candidate index (candidate 0 = the original parameterization wins
  ties against identical patches).
* Constraints (minimum decided trades, drawdown ceiling) are evaluated
  on the SELECTION window only and mark a candidate unselectable — they
  never fabricate a score.
* Out-of-sample aggregation sums decided-trade statistics across
  chronological windows and recomputes ratios; it refuses to invent a
  cross-window equity curve (max drawdown is reported per window).
* The optional bootstrap is a SEEDED, BOUNDED i.i.d. resampling of the
  recorded trade P&L list. It documents its i.i.d. assumption and is
  NOT a statistical significance test.
"""

from __future__ import annotations

import math
import random
from typing import Any

# Objective metric -> (section, key path within metrics, direction).
_METRIC_PATHS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "net_pnl": ("trades", ("net_pnl",), "maximize"),
    "expectancy": ("trades", ("expectancy",), "maximize"),
    "profit_factor": ("trades", ("profit_factor",), "maximize"),
    "win_rate": ("trades", ("win_rate",), "maximize"),
    "avg_return_pct_per_trade": ("trades", ("avg_return_pct_per_trade",), "maximize"),
    "max_drawdown_pct": ("equity", ("max_drawdown", "max_drawdown_pct"), "minimize"),
    "conditions_met_count": ("setups", ("by_status", "conditions_met"), "maximize"),
}

_RANK_NOTE_UNDEFINED = (
    "Objective metric is undefined for this candidate on the selection "
    "window (e.g. no decided trades); it cannot be ranked."
)


def metric_value(metrics: dict[str, Any] | None, metric: str) -> float | None:
    """Resolve an objective metric from an engine metrics payload.

    Returns ``None`` when the metric is absent or not a finite number —
    the engine stores ``None`` for "not available" and this module never
    substitutes a fabricated zero.
    """

    if not metrics or metric not in _METRIC_PATHS:
        return None
    section, path, _direction = _METRIC_PATHS[metric]
    node = metrics.get(section)
    if not isinstance(node, dict):
        return None
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
        if node is None:
            return None
    if isinstance(node, bool) or not isinstance(node, (int, float)):
        return None
    value = float(node)
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def evaluate_constraints(
    metrics: dict[str, Any] | None,
    constraints: dict[str, Any],
) -> list[dict[str, str]]:
    """Check selection constraints on the SELECTION-window metrics."""

    violations: list[dict[str, str]] = []
    if metrics is None:
        return violations
    min_trades = constraints.get("min_decided_trades")
    if min_trades is not None:
        decided = (metrics.get("trades") or {}).get("decided_trades")
        if decided is None or decided < min_trades:
            violations.append(
                {
                    "code": "MIN_DECIDED_TRADES",
                    "message": (
                        f"Decided trades {decided if decided is not None else 'N/A'} "
                        f"below the required minimum of {min_trades}."
                    ),
                }
            )
    max_dd = constraints.get("max_drawdown_pct")
    if max_dd is not None:
        dd = ((metrics.get("equity") or {}).get("max_drawdown") or {}).get(
            "max_drawdown_pct"
        )
        if dd is not None and dd > max_dd:
            violations.append(
                {
                    "code": "MAX_DRAWDOWN_PCT",
                    "message": (
                        f"Max drawdown {dd}% exceeds the configured ceiling "
                        f"of {max_dd}%."
                    ),
                }
            )
    return violations


def rank_scored(
    scored: list[dict[str, Any]],
    direction: str,
) -> None:
    """Assign ``rank`` (1-based) + ``rank_note`` on scored candidate dicts.

    Each input dict needs ``candidate_index``, ``score`` and
    ``constraint_violations``. Candidates without a finite score keep
    ``rank = None``. Ties resolve to the LOWER candidate index. Mutates
    the dicts in place.
    """

    direction = "minimize" if direction == "minimize" else "maximize"

    def sort_key(c: dict[str, Any]) -> tuple[int, float, int]:
        score = c.get("score")
        if score is None or isinstance(score, float) and (math.isnan(score) or math.isinf(score)):
            return (1, 0.0, c["candidate_index"])
        return (0, -float(score) if direction == "maximize" else float(score), c["candidate_index"])

    ordered = sorted(scored, key=sort_key)
    rank = 0
    for candidate in ordered:
        if candidate.get("score") is None:
            candidate.setdefault("rank_note", _RANK_NOTE_UNDEFINED)
            candidate["rank"] = None
            continue
        rank += 1
        candidate["rank"] = rank
        if candidate.get("constraint_violations"):
            candidate.setdefault(
                "rank_note",
                "Ranked on the objective, but selection constraints are "
                "violated (see constraint_violations).",
            )
        else:
            candidate.setdefault("rank_note", None)


def select_candidate(scored: list[dict[str, Any]]) -> dict[str, Any] | None:
    """First selectable candidate by rank (scored + constraint-clean)."""

    eligible = [
        c
        for c in scored
        if c.get("score") is not None
        and not c.get("constraint_violations")
        and c.get("rank") is not None
    ]
    if not eligible:
        return None
    return min(eligible, key=lambda c: c["rank"])


def metrics_digest(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Compact, honest metrics subset persisted per candidate/window."""

    if not metrics:
        return {}
    setups = metrics.get("setups") or {}
    trades = metrics.get("trades") or {}
    equity = metrics.get("equity") or {}
    breakdowns = trades.get("breakdowns") or {}
    setup_breakdowns = setups.get("breakdowns") or {}
    return {
        "setups": {
            "total_evaluated": setups.get("total_evaluated"),
            "by_status": setups.get("by_status"),
            "first_evaluation_time": setups.get("first_evaluation_time"),
            "last_evaluation_time": setups.get("last_evaluation_time"),
        },
        "trades": {
            "total": trades.get("total"),
            "decided_trades": trades.get("decided_trades"),
            "by_outcome": trades.get("by_outcome"),
            "win_rate": trades.get("win_rate"),
            "net_pnl": trades.get("net_pnl"),
            "gross_profit": trades.get("gross_profit"),
            "gross_loss": trades.get("gross_loss"),
            "expectancy": trades.get("expectancy"),
            "profit_factor": trades.get("profit_factor"),
            "avg_return_pct_per_trade": trades.get("avg_return_pct_per_trade"),
            "avg_holding_candles": trades.get("avg_holding_candles"),
            "breakdowns": {
                "by_month": breakdowns.get("by_month"),
                "by_regime": breakdowns.get("by_regime"),
            },
        },
        "equity": {
            "start_balance": equity.get("start_balance"),
            "end_balance": equity.get("end_balance"),
            "max_drawdown": equity.get("max_drawdown"),
        },
        "note": metrics.get("note"),
        "win_rate_note": metrics.get("win_rate_note"),
        "simulation_mode": metrics.get("simulation_mode"),
    }


def aggregate_oos(window_digests: list[dict[str, Any] | None]) -> dict[str, Any]:
    """Aggregate chronological out-of-sample window digests.

    Adds counts and money; recomputes win_rate / expectancy / profit
    factor from the sums. Max drawdown CANNOT be compounded honestly from
    per-window curves — the worst per-window drawdown is reported with an
    explicit note. Windows without trades contribute nothing; skipped
    windows must not be passed in.
    """

    decided = 0
    wins = 0
    losses = 0
    net_pnl = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    trade_total = 0
    windows_with_trades = 0
    drawdowns: list[float] = []
    per_window: list[dict[str, Any]] = []
    for digest in window_digests:
        if not digest:
            continue
        trades = digest.get("trades") or {}
        equity = digest.get("equity") or {}
        dd = (equity.get("max_drawdown") or {}).get("max_drawdown_pct")
        if isinstance(dd, (int, float)):
            drawdowns.append(float(dd))
        by_outcome = trades.get("by_outcome") or {}
        w = by_outcome.get("win") or 0
        l = by_outcome.get("loss") or 0
        d = trades.get("decided_trades")
        t = trades.get("total")
        pnl = trades.get("net_pnl")
        gp = trades.get("gross_profit")
        gl = trades.get("gross_loss")
        per_window.append(
            {
                "net_pnl": pnl,
                "win_rate": trades.get("win_rate"),
                "decided_trades": d,
                "expectancy": trades.get("expectancy"),
                "max_drawdown_pct": dd,
            }
        )
        if t is None and d is None:
            continue
        trade_total += int(t or 0)
        if d:
            decided += int(d)
            wins += int(w or 0)
            losses += int(l or 0)
            windows_with_trades += 1
        if pnl is not None:
            net_pnl += float(pnl)
        if gp is not None:
            gross_profit += float(gp)
        if gl is not None:
            gross_loss += float(gl)

    win_rate = (wins / (wins + losses)) if (wins + losses) > 0 else None
    expectancy = (net_pnl / decided) if decided > 0 else None
    # Engine semantics: gross_loss is NEGATIVE; profit_factor undefined when
    # either side is absent (never "inf").
    profit_factor = None
    if gross_profit > 0 and gross_loss < 0:
        profit_factor = round(gross_profit / abs(gross_loss), 6)
    worst_dd = max(drawdowns) if drawdowns else None
    return {
        "windows_aggregated": len([d for d in window_digests if d]),
        "windows_with_trades": windows_with_trades,
        "trades": {
            "total": trade_total,
            "decided_trades": decided,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 6) if win_rate is not None else None,
            "net_pnl": round(net_pnl, 8) if decided else None,
            "gross_profit": round(gross_profit, 8) if gross_profit else None,
            "gross_loss": round(gross_loss, 8) if gross_loss else None,
            "expectancy": round(expectancy, 8) if expectancy is not None else None,
            "profit_factor": profit_factor,
        },
        "max_drawdown": {
            "max_per_window_pct": worst_dd,
            "note": (
                "Drawdown is reported per window; the walk-forward "
                "procedure does NOT compound a cross-window equity curve, "
                "so no single aggregate drawdown is claimed."
                if drawdowns
                else None
            ),
        },
        "per_window": per_window,
        "note": (
            "Out-of-sample windows are chronological and non-overlapping "
            "(step >= test length); counts are summed without "
            "double-counting observations."
        ),
    }


def bootstrap_stats(
    trade_pnls: list[float],
    *,
    samples: int,
    seed: int,
) -> dict[str, Any]:
    """Seeded i.i.d. bootstrap of the recorded trade P&L list.

    Resamples decided-trade P&L with replacement to show how sensitive
    net P&L / expectancy / drawdown-of-resampled-path are to the ORDER
    and SELECTION of the very trades the simulation produced. What this
    CANNOT establish (documented on every result): it assumes trades are
    independent and identically distributed (real trades cluster), it
    never generates new prices, and its percentiles are NOT significance
    tests or probability-of-profit statements.
    """

    n = len(trade_pnls)
    if n == 0 or samples <= 0:
        return {
            "applicable": False,
            "reason": "no decided trades" if n == 0 else "bootstrap disabled",
            "samples": 0,
        }
    rng = random.Random(seed)
    totals: list[float] = []
    means: list[float] = []
    drawdowns: list[float] = []
    for _ in range(samples):
        pick = [trade_pnls[rng.randrange(n)] for _ in range(n)]
        total = math.fsum(pick)
        totals.append(total)
        means.append(total / n)
        peak = pick[0]
        worst = 0.0
        equity = 0.0
        for pnl in pick:
            equity += pnl
            peak = max(peak, equity)
            worst = max(worst, peak - equity)
        drawdowns.append(worst)

    def _pct(values: list[float], q: float) -> float:
        ordered = sorted(values)
        idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
        return round(ordered[idx], 8)

    historical_total = math.fsum(trade_pnls)
    return {
        "applicable": True,
        "decided_trades": n,
        "samples": samples,
        "seed": seed,
        "historical_net_pnl": round(historical_total, 8),
        "net_pnl_percentiles": {
            "p5": _pct(totals, 0.05),
            "p25": _pct(totals, 0.25),
            "p50": _pct(totals, 0.50),
            "p75": _pct(totals, 0.75),
            "p95": _pct(totals, 0.95),
        },
        "expectancy_percentiles": {
            "p5": _pct(means, 0.05),
            "p50": _pct(means, 0.50),
            "p95": _pct(means, 0.95),
        },
        "resampled_path_drawdown_percentiles": {
            "p50": _pct(drawdowns, 0.50),
            "p95": _pct(drawdowns, 0.95),
        },
        "method_note": (
            "i.i.d. resampling with replacement of the simulated trade "
            "P&L list (random.Random, fixed seed). Chronological "
            "clustering of trades is NOT preserved; the drawdown view "
            "comes from resampled paths, not the historical sequence. "
            "Percentiles are NOT statistical significance tests and imply "
            "nothing about future profitability."
        ),
    }


__all__ = [
    "aggregate_oos",
    "bootstrap_stats",
    "evaluate_constraints",
    "metrics_digest",
    "metric_value",
    "rank_scored",
    "select_candidate",
]
