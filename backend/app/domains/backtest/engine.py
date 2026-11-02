"""Deterministic backtest engine — chronological walk with zero look-ahead.

Guarantees (tested in backend/tests/unit/test_backtest_engine.py):

* Candles are processed STRICTLY in chronological order; the evaluation of
  candle ``i`` receives only candles whose CLOSE time is <= the close time
  of candle ``i`` (the evaluation instant ``t_eval``). Higher-timeframe
  candles that are still forming at ``t_eval`` are INVISIBLE — a swing or
  structure break they would confirm can never influence earlier
  evaluations.
* The evaluation instant is the close time of the primary candle: the
  first moment a real trader could act on a closed candle.
* Every evaluation reuses the Phase 4 rule evaluator on the point-in-time
  window (one implementation, one semantics). The evaluator's wall-clock
  ``evaluated_at`` stamp is REPLACED with the simulated instant so runs are
  byte-for-byte deterministic.
* Duplicate evaluation of the same candle is impossible by construction
  (one evaluation per closed candle); Forex entries are suppressed while a
  position is open (documented policy).
* Nothing is invented: no candles, no outcomes, no probabilities.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ValidationAppError
from app.domains.analysis import regime as rg
from app.domains.analysis.evaluator import (
    evaluate_rule_set,
    _required_count_for_timeframe,
)
from app.domains.backtest import binary as binary_sim
from app.domains.backtest import forex as forex_sim
from app.domains.backtest import metrics as metrics_mod
from app.domains.backtest.data import (
    InsufficientHistoryError,
    assess_series_quality,
    warmup_start,
)
from app.domains.backtest.schemas import (
    BacktestConfigPayload,
    BinaryOptionsConfig,
    ForexExecutionConfig,
)
from app.domains.market_data.store import load_candles_range
from app.domains.market_data.timeframes import timeframe_seconds
from app.domains.strategies.rules import RuleSet, validate_rule_set

_REGIME_MIN_CANDLES = 60
_WINDOW_MARGIN = 5


class CancelledError(Exception):
    """Raised when cooperative cancellation is requested mid-walk."""


@dataclass
class EngineOutput:
    """Everything the runner persists for a completed run."""

    records: list[dict[str, Any]]
    data_quality: dict[str, Any]
    metrics: dict[str, Any]
    candles_evaluated: int


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    )


def _row_valid(row: dict[str, Any]) -> bool:
    try:
        o, h, low, c = (
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
        )
    except (KeyError, TypeError, ValueError):
        return False
    return h >= low and min(o, h, low, c) > 0


class _TimeframeSeries:
    """Ascending closed-candle series with O(log n) point-in-time windows.

    ``close_times`` is precomputed once; the number of candles VISIBLE at
    ``t_eval`` is ``bisect_right(close_times, t_eval)`` — a candle is
    visible exactly when it has CLOSED at or before the evaluation instant.
    """

    __slots__ = (
        "timeframe",
        "interval",
        "candles",
        "close_times",
        "duplicate_count",
    )

    def __init__(self, timeframe: str, rows: list[dict[str, Any]]) -> None:
        self.timeframe = timeframe
        self.interval = timeframe_seconds(timeframe)
        cleaned = sorted(
            (dict(r) for r in rows if r.get("forming") is not True and _row_valid(r)),
            key=lambda r: int(r["time"]),
        )
        deduped: list[dict[str, Any]] = []
        self.duplicate_count = 0
        for row in cleaned:
            if deduped and int(row["time"]) == int(deduped[-1]["time"]):
                self.duplicate_count += 1
                continue  # duplicate timestamps: keep first occurrence
            deduped.append(row)
        self.candles = deduped
        self.close_times = [int(r["time"]) + self.interval for r in deduped]

    def window(self, t_eval: int, size: int) -> list[dict[str, Any]]:
        """Candles VISIBLE at ``t_eval`` (closed), tail-capped to ``size``."""

        k = bisect_right(self.close_times, t_eval)
        if k <= 0:
            return []
        lo = max(0, k - size)
        return self.candles[lo:k]

    def visible_count(self, t_eval: int) -> int:
        return bisect_right(self.close_times, t_eval)


def build_data_quality(
    series_by_tf: dict[str, _TimeframeSeries],
    requested_timeframes: list[str],
) -> dict[str, Any]:
    """Honest per-timeframe quality summary over the LOADED history."""

    per_tf: list[dict[str, Any]] = []
    for tf in requested_timeframes:
        series = series_by_tf.get(tf)
        if series is None or not series.candles:
            per_tf.append(
                {
                    "timeframe": tf,
                    "candles_available": 0,
                    "warnings": [
                        {
                            "code": "NO_CANDLES_IN_RANGE",
                            "timeframe": tf,
                            "message": "No stored closed candles were loaded "
                            "for this timeframe; evaluations that require it "
                            "report insufficient data.",
                        }
                    ],
                }
            )
            continue
        loaded_start = datetime.fromtimestamp(series.candles[0]["time"], tz=timezone.utc)
        loaded_end = datetime.fromtimestamp(
            series.candles[-1]["time"] + series.interval, tz=timezone.utc
        )
        quality = assess_series_quality(series.candles, tf, loaded_start, loaded_end)
        quality_dict = quality.to_dict()
        if series.duplicate_count:
            quality_dict["duplicate_count"] = series.duplicate_count
            quality_dict["warnings"].append(
                {
                    "code": "DUPLICATE_TIMESTAMPS",
                    "timeframe": tf,
                    "message": f"{series.duplicate_count} duplicate timestamp(s) "
                    "in the stored series were removed (first occurrence "
                    "kept) before the walk.",
                }
            )
        per_tf.append(quality_dict)
    overall = "ok"
    for entry in per_tf:
        codes = {w["code"] for w in entry.get("warnings", [])}
        if "NO_CANDLES_IN_RANGE" in codes:
            overall = "insufficient"
            break
        if codes - {"DATA_GAPS"}:
            overall = "partial" if overall == "ok" else overall
    return {
        "source": "postgres_candle_cache",
        "source_note": (
            "Backtests run on the platform's stored historical candle cache "
            "(synced from the MetaTrader 5 terminal by the Phase 2 "
            "integration). Data is never fabricated, filled or extrapolated."
        ),
        "overall_status": overall,
        "timeframes": per_tf,
        "forming_candles_used": False,
        "closed_candles_only": True,
        "note": (
            "Counts include warmup history loaded before the requested "
            "start. All evaluations use CLOSED candles only; a candle "
            "becomes visible to the walk at its close time."
        ),
    }


def _window_size(rule_set: RuleSet, timeframe: str) -> int:
    required = _required_count_for_timeframe(rule_set, timeframe)
    return max(required, _REGIME_MIN_CANDLES) + _WINDOW_MARGIN


def run_walk(
    *,
    config: BacktestConfigPayload,
    rule_set: RuleSet,
    series_by_tf: dict[str, _TimeframeSeries],
    cancel_check: Callable[[], None] | None = None,
) -> EngineOutput:
    """Execute the deterministic chronological walk (pure, no I/O).

    ``cancel_check`` (optional) is called before every evaluation step and
    raises :class:`CancelledError` when the run should stop.
    """

    primary = config.timeframe
    primary_series = series_by_tf.get(primary)
    if primary_series is None or not primary_series.candles:
        raise InsufficientHistoryError(
            f"No closed candles available for {config.symbol} {primary} in "
            "the loaded history; the walk cannot start.",
            code="INSUFFICIENT_HISTORY",
        )
    start_epoch = int(datetime.fromisoformat(config.start).timestamp())
    end_epoch = int(datetime.fromisoformat(config.end).timestamp())

    open_times = [int(r["time"]) for r in primary_series.candles]
    first_idx = bisect_left(open_times, start_epoch)
    last_idx_excl = bisect_right(open_times, end_epoch)  # open time <= end
    if first_idx >= last_idx_excl:
        raise InsufficientHistoryError(
            "The requested window contains no closed primary-timeframe "
            "candles.",
            code="INSUFFICIENT_HISTORY",
        )
    if last_idx_excl - first_idx > config.max_candles:
        raise ValidationAppError(
            f"The window contains {last_idx_excl - first_idx} primary "
            f"candles, above the run limit of {config.max_candles}.",
            code="RANGE_TOO_LARGE",
        )

    requested_timeframes = sorted(set(rule_set.timeframes), key=timeframe_seconds)
    for tf in requested_timeframes:
        if tf not in series_by_tf:
            raise ValidationAppError(
                f"Required timeframe '{tf}' was not loaded for the run.",
                code="MISSING_TIMEFRAME_DATA",
            )
    window_sizes = {tf: _window_size(rule_set, tf) for tf in requested_timeframes}
    interval = primary_series.interval
    classifier = rg.DefaultRegimeClassifier()

    direction = rule_set.direction
    forex_cfg = None
    binary_cfg = None
    if config.mode == "forex" and config.forex_execution is not None:
        forex_cfg = ForexExecutionConfig.model_validate(config.forex_execution)
    if config.mode == "binary_options" and config.binary is not None:
        binary_cfg = BinaryOptionsConfig.model_validate(config.binary)
    simulating = (forex_cfg is not None and forex_cfg.enabled) or binary_cfg is not None
    pip_size = None
    if forex_cfg is not None:
        pip_size = forex_sim.infer_pip_size(config.symbol, forex_cfg.pip_size)

    records: list[dict[str, Any]] = []
    setup_metric_rows: list[dict[str, Any]] = []
    trade_metric_rows: list[dict[str, Any]] = []
    seq = 0
    start_balance = 0.0
    if forex_cfg is not None:
        start_balance = forex_cfg.account_start_balance
    elif binary_cfg is not None:
        start_balance = binary_cfg.account_start_balance
    balance = start_balance
    position_open: forex_sim.ForexTrade | None = None

    for idx in range(first_idx, last_idx_excl):
        if cancel_check is not None:
            cancel_check()
        signal = primary_series.candles[idx]
        t_eval = int(signal["time"]) + interval

        # ---- point-in-time windows (closed candles only) -------------------
        windows = {
            tf: series_by_tf[tf].window(t_eval, window_sizes[tf])
            for tf in requested_timeframes
        }

        # ---- regime (mirrors run_analysis: per timeframe, gate decided on
        #      the HIGHEST timeframe, only from visible candles) -------------
        regime_results = [
            classifier.classify(windows.get(tf, []), tf)
            for tf in requested_timeframes
        ]
        primary_timeframe = requested_timeframes[-1]
        primary_regime = next(
            r for r in regime_results if r.timeframe == primary_timeframe
        )
        regime_check = rg.regime_applicability(rule_set.market_regime, primary_regime)

        # ---- evaluation (shared Phase 4 evaluator) --------------------------
        evaluation = evaluate_rule_set(rule_set, windows, regime_check=regime_check)
        evaluation_dict = evaluation.to_dict()
        evaluation_dict["evaluated_at"] = _iso(t_eval)  # determinism override

        records.append(
            {
                "seq": seq,
                "kind": "setup",
                "result_time": t_eval,
                "result_time_iso": _iso(t_eval),
                "symbol": config.symbol,
                "timeframe": primary,
                "setup_status": evaluation_dict["setup_status"],
                "direction": evaluation_dict["direction"],
                "regime": primary_regime.regime,
                "evaluation": evaluation_dict,
                "warnings": evaluation_dict.get("warnings", []),
                "note": None,
            }
        )
        setup_metric_rows.append(
            {
                "seq": seq,
                "result_time": t_eval,
                "setup_status": evaluation_dict["setup_status"],
                "direction": evaluation_dict["direction"],
                "regime": primary_regime.regime,
                "evaluation": {
                    "conditions_summary": evaluation_dict.get("conditions_summary"),
                },
            }
        )
        seq += 1

        # ---- trade simulation (only with explicit configuration) ------------
        if not simulating or evaluation_dict["setup_status"] != "conditions_met":
            continue

        if forex_cfg is not None and forex_cfg.enabled:
            if position_open is not None or direction not in ("bullish", "bearish"):
                continue
            trade = forex_sim.open_trade(
                forex_cfg,
                seq=seq,
                direction="long" if direction == "bullish" else "short",
                signal_candle=signal,
                entry_time=t_eval,
                balance=balance,
                pip_size=pip_size,
            )
            if trade.lots <= 0:
                continue
            trade = forex_sim.resolve_exit(
                trade, primary_series.candles, idx + 1, forex_cfg, pip_size
            )
            # Phase 8 fix: realized P&L was never computed because
            # ``settle`` was not called after exit resolution — every
            # resolved trade stayed at pnl None / outcome "tie" and the
            # equity path never moved. settle() uses ONLY the declared
            # cost assumptions (no new data, no fabrication).
            trade = forex_sim.settle(trade, forex_cfg, pip_size, balance)
            outcome = trade.outcome
            if outcome in ("win", "loss", "tie") and trade.pnl is not None:
                balance = round(balance + trade.pnl, 8)
                trade.equity_after = balance
            trade_exec = forex_sim.execution_payload(trade, forex_cfg, pip_size)
            return_pct = (
                round(trade.pnl / trade.risk_amount, 8)
                if trade.pnl is not None and trade.risk_amount and trade.risk_amount > 0
                else None
            )
            records.append(
                {
                    "seq": seq,
                    "kind": "trade",
                    "result_time": trade.entry_time,
                    "result_time_iso": _iso(trade.entry_time),
                    "symbol": config.symbol,
                    "timeframe": primary,
                    "setup_status": evaluation_dict["setup_status"],
                    "direction": direction,
                    "regime": primary_regime.regime,
                    "outcome": outcome,
                    "pnl": trade.pnl,
                    "return_pct": return_pct,
                    "equity_after": (
                        trade.equity_after if outcome in ("win", "loss", "tie") else None
                    ),
                    "evaluation": None,
                    "execution": trade_exec,
                    "warnings": [],
                    "note": (
                        "Stop-loss and take-profit were both inside the exit "
                        "candle's range; OHLC data cannot resolve the order. "
                        "Recorded as ambiguous under the configured policy "
                        "and excluded from statistics."
                        if outcome == "ambiguous"
                        else None
                    ),
                }
            )
            trade_metric_rows.append(
                {
                    "seq": seq,
                    "result_time": trade.entry_time,
                    "outcome": outcome,
                    "pnl": trade.pnl,
                    "return_pct": return_pct,
                    "regime": primary_regime.regime,
                    "direction": direction,
                    "execution": trade_exec,
                    "equity_after": (
                        trade.equity_after if outcome in ("win", "loss", "tie") else None
                    ),
                }
            )
            seq += 1
            position_open = None if outcome in ("win", "loss", "tie", "ambiguous") else trade
            continue

        # Binary: one trade per conditions_met setup (distinct events).
        if binary_cfg is not None:
            trade = binary_sim.evaluate_trade(
                binary_cfg,
                seq=seq,
                direction=direction,
                signal_candle=signal,
                interval=interval,
                candles=primary_series.candles,
                signal_index=idx,
            )
            outcome = trade.outcome
            realized = outcome in ("win", "loss", "tie") and trade.pnl is not None
            if realized:
                balance = round(balance + trade.pnl, 8)
            trade_exec = binary_sim.execution_payload(trade, binary_cfg)
            return_pct = (
                round(trade.pnl / trade.stake, 8)
                if trade.pnl is not None and trade.stake > 0
                else None
            )
            records.append(
                {
                    "seq": seq,
                    "kind": "trade",
                    "result_time": trade.entry_time,
                    "result_time_iso": _iso(trade.entry_time),
                    "symbol": config.symbol,
                    "timeframe": primary,
                    "setup_status": evaluation_dict["setup_status"],
                    "direction": direction,
                    "regime": primary_regime.regime,
                    "outcome": outcome,
                    "pnl": trade.pnl if realized else None,
                    "return_pct": return_pct if realized else None,
                    "equity_after": balance if realized else None,
                    "evaluation": None,
                    "execution": trade_exec,
                    "warnings": [],
                    "note": trade.skip_reason,
                }
            )
            trade_metric_rows.append(
                {
                    "seq": seq,
                    "result_time": trade.entry_time,
                    "outcome": outcome,
                    "pnl": trade.pnl if realized else None,
                    "return_pct": return_pct if realized else None,
                    "regime": primary_regime.regime,
                    "direction": direction,
                    "execution": trade_exec,
                    "equity_after": balance if realized else None,
                }
            )
            seq += 1

    candles_evaluated = last_idx_excl - first_idx
    planned_rr = (
        forex_sim.trade_limit_payload(forex_cfg)
        if forex_cfg is not None and forex_cfg.enabled
        else None
    )
    data_quality = build_data_quality(series_by_tf, requested_timeframes)
    metrics = metrics_mod.compute_metrics(
        mode=config.mode,
        setup_records=setup_metric_rows,
        trade_records=trade_metric_rows,
        total_candles_evaluated=candles_evaluated,
        simulation_mode=config.simulation_mode,
        planned_rr=planned_rr,
    )
    return EngineOutput(
        records=records,
        data_quality=data_quality,
        metrics=metrics,
        candles_evaluated=candles_evaluated,
    )


async def run_backtest(
    session: AsyncSession,
    settings: Settings,
    *,
    config: BacktestConfigPayload,
    cancel_check: Callable[[], None] | None = None,
) -> EngineOutput:
    """Load history from the candle store and execute the walk.

    Deterministic: identical stored candles + identical configuration +
    identical strategy version produce identical records, metrics and
    data-quality payloads.
    """

    try:
        rule_set = validate_rule_set(config.rule_set)
    except Exception as exc:  # RuleValidationError — defensive for snapshots
        raise ValidationAppError(
            "The stored strategy definition failed revalidation and cannot "
            "be backtested.",
            code="RULE_SET_INVALID",
        ) from exc

    requested_timeframes = sorted(set(rule_set.timeframes), key=timeframe_seconds)
    start = datetime.fromisoformat(config.start)
    end = datetime.fromisoformat(config.end)

    # Warmup window: history before `start` so early evaluations have their
    # lookback context. Candles INSIDE [start, end] still only become
    # visible at their close times.
    warmup = settings.backtest_warmup_candles
    rows_by_tf: dict[str, list[dict[str, Any]]] = {}
    for tf in requested_timeframes:
        tf_lo = warmup_start(start, tf, warmup)
        tf_hi = end + timedelta(seconds=timeframe_seconds(tf))
        rows = await load_candles_range(
            session, config.symbol, tf, tf_lo, tf_hi, settings.backtest_max_candles * 4
        )
        if len(rows) >= settings.backtest_max_candles * 4:
            raise ValidationAppError(
                f"Stored history for {config.symbol} {tf} exceeds the read "
                "limit; narrow the date range.",
                code="RANGE_TOO_LARGE",
            )
        rows_by_tf[tf] = rows

    series_by_tf = {
        tf: _TimeframeSeries(tf, rows_by_tf.get(tf, []))
        for tf in requested_timeframes
    }
    return run_walk(
        config=config,
        rule_set=rule_set,
        series_by_tf=series_by_tf,
        cancel_check=cancel_check,
    )
