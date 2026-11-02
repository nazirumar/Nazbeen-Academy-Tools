"""Forex simulation — explicit-assumption trade accounting.

Declared assumptions (all recorded in run payloads and CSV exports):

* Candles are BID-side OHLC (MetaTrader convention). A long enters at
  ``close + (spread + slippage)`` (paying the ask plus adverse slippage);
  a short enters at ``close - slippage`` (selling the bid into slippage).
* Take-profit exits fill AT the level (passive limit, no slippage declared).
  Stop-loss exits fill BEYOND the level by the declared slippage.
* A candle whose range touches BOTH the stop and the target cannot be
  resolved from OHLC data: the configured policy decides — ``conservative``
  (default) books the stop, ``optimistic`` books the target, ``ambiguous``
  records the outcome as ``ambiguous`` and excludes it from statistics.
  Nothing is ever assumed silently favourable.
* Position sizing: fixed-fractional risk on the realized balance, or a
  fixed lot. Lots are rounded down to 0.01 (declared).
* Swap: when ``swap_per_lot_per_night`` is configured, nights are counted
  as UTC date boundaries between entry and exit (approximation, declared).
* A trade still open when the data ends is reported as ``open`` with no
  realized P&L; it never contributes to wins, losses or the equity path.

The simulator never invents execution prices: every fill is derived from
stored candle prices plus the declared, persisted cost assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domains.backtest.schemas import ForexExecutionConfig

MIN_LOT_STEP = 0.01


def infer_pip_size(symbol: str, override: float | None) -> float:
    """Documented default pip heuristic with explicit override."""

    if override is not None:
        return override
    return 0.01 if "JPY" in symbol.upper() else 0.0001


@dataclass
class ForexTrade:
    seq: int
    direction: str  # "long" | "short"
    entry_time: int
    entry_price: float
    signal_time: int
    reference_close: float
    stop_price: float
    target_price: float
    lots: float
    risk_amount: float

    exit_time: int | None = None
    exit_price: float | None = None
    exit_reason: str | None = None  # stop_loss | take_profit | open_at_data_end
    pnl: float | None = None
    commission: float = 0.0
    swap: float = 0.0
    equity_after: float | None = None
    ambiguous: bool = False
    candles_held: int = 0

    @property
    def outcome(self) -> str:
        if self.exit_reason is None:
            return "open"
        if self.ambiguous:
            return "ambiguous"
        return "win" if (self.pnl or 0.0) > 0 else ("loss" if (self.pnl or 0.0) < 0 else "tie")


def _lots_for(
    cfg: ForexExecutionConfig, balance: float, stop_pips: float
) -> tuple[float, float]:
    """Return (lots, risk_amount) under the configured sizing mode."""

    if cfg.risk_mode == "fixed_lot":
        return round(max(cfg.fixed_lot_size, MIN_LOT_STEP), 2), 0.0
    risk_amount = balance * (cfg.risk_pct_per_trade / 100.0)
    per_lot_risk = stop_pips * cfg.pip_value_per_lot
    lots = risk_amount / per_lot_risk if per_lot_risk > 0 else 0.0
    lots = max(MIN_LOT_STEP, (int(lots * 100) / 100))  # floor to 0.01
    return round(lots, 2), round(lots * per_lot_risk, 8)


def open_trade(
    cfg: ForexExecutionConfig,
    *,
    seq: int,
    direction: str,
    signal_candle: dict[str, Any],
    entry_time: int,
    balance: float,
    pip_size: float,
) -> ForexTrade:
    """Create a managed trade from a signal candle close (declared fills).

    ``entry_time`` is the close time of the signal candle — the first
    instant the strategy could act on it (no same-candle foresight).
    """

    close = float(signal_candle["close"])
    slip = cfg.slippage_pips * pip_size
    spread = cfg.spread_pips * pip_size
    if direction == "long":
        entry = close + spread + slip
        stop = entry - cfg.stop_loss_pips * pip_size
        target = entry + cfg.take_profit_pips * pip_size
    else:
        entry = close - slip
        stop = entry + cfg.stop_loss_pips * pip_size
        target = entry - cfg.take_profit_pips * pip_size
    lots, risk_amount = _lots_for(cfg, balance, cfg.stop_loss_pips)
    return ForexTrade(
        seq=seq,
        direction=direction,
        entry_time=entry_time,
        entry_price=round(entry, 10),
        signal_time=int(signal_candle["time"]),
        reference_close=close,
        stop_price=round(stop, 10),
        target_price=round(target, 10),
        lots=lots,
        risk_amount=round(risk_amount, 8),
    )


def resolve_exit(
    trade: ForexTrade,
    candles: list[dict[str, Any]],
    start_index: int,
    cfg: ForexExecutionConfig,
    pip_size: float,
) -> ForexTrade:
    """Walk forward chronologically until SL/TP fills or the data ends.

    Uses ONLY candles after the entry; each candle is examined once
    (high/low against stop/target). Ambiguous same-candle cases follow the
    declared policy. ``trade.exit_time`` is the close time of the exit
    candle when resolved.
    """

    sl_pips = cfg.stop_loss_pips
    slip = cfg.slippage_pips * pip_size
    for i in range(start_index, len(candles)):
        candle = candles[i]
        high = float(candle["high"])
        low = float(candle["low"])
        trade.candles_held += 1
        if trade.direction == "long":
            hit_sl = low <= trade.stop_price
            hit_tp = high >= trade.target_price
        else:
            hit_sl = high >= trade.stop_price
            hit_tp = low <= trade.target_price
        if hit_sl and hit_tp:
            if cfg.sl_tp_same_candle_policy == "ambiguous":
                trade.ambiguous = True
                trade.exit_reason = "stop_loss"
                trade.exit_time = int(candle["time"]) + 1
                trade.exit_price = None
                return trade
            chosen_stop = cfg.sl_tp_same_candle_policy == "conservative"
        else:
            chosen_stop = hit_sl
        if chosen_stop and hit_sl:
            trade.exit_reason = "stop_loss"
            trade.exit_time = int(candle["time"]) + 1
            if trade.direction == "long":
                trade.exit_price = round(trade.stop_price - slip, 10)
            else:
                trade.exit_price = round(trade.stop_price + slip, 10)
            return trade
        if (not chosen_stop) and hit_tp:
            trade.exit_reason = "take_profit"
            trade.exit_time = int(candle["time"]) + 1
            trade.exit_price = trade.target_price  # passive limit: no slippage
            return trade
    trade.exit_reason = None  # data ended with the position open
    trade.exit_time = None
    trade.exit_price = None
    return trade


def settle(
    trade: ForexTrade,
    cfg: ForexExecutionConfig,
    pip_size: float,
    balance: float,
) -> ForexTrade:
    """Compute realized P&L for a resolved exit (declared costs only)."""

    if trade.exit_time is None or trade.exit_price is None:
        return trade  # open at data end: nothing realized
    pips = (
        (trade.exit_price - trade.entry_price) / pip_size
        if trade.direction == "long"
        else (trade.entry_price - trade.exit_price) / pip_size
    )
    gross = pips * trade.lots * cfg.pip_value_per_lot
    commission = cfg.commission_per_lot_per_side * trade.lots * 2
    swap = 0.0
    if cfg.swap_per_lot_per_night is not None:
        nights = _nights_between(trade.entry_time, trade.exit_time)
        swap = cfg.swap_per_lot_per_night * trade.lots * nights
    trade.commission = round(commission, 8)
    trade.swap = round(swap, 8)
    # swap is SIGNED (negative = cost); commission is always a cost.
    trade.pnl = round(gross + swap - commission, 8)
    trade.equity_after = round(balance + trade.pnl, 8)
    return trade


def _nights_between(entry_epoch: int, exit_epoch: int) -> int:
    from datetime import datetime, timezone

    entry_day = datetime.fromtimestamp(entry_epoch, tz=timezone.utc).date()
    exit_day = datetime.fromtimestamp(exit_epoch, tz=timezone.utc).date()
    return max(0, (exit_day - entry_day).days)


def execution_payload(trade: ForexTrade, cfg: ForexExecutionConfig, pip_size: float) -> dict[str, Any]:
    """Execution details persisted on the trade record (audit trail)."""

    return {
        "mode": "forex",
        "direction": trade.direction,
        "entry_time": trade.entry_time,
        "entry_price": trade.entry_price,
        "reference_close": trade.reference_close,
        "signal_time": trade.signal_time,
        "stop_price": trade.stop_price,
        "target_price": trade.target_price,
        "exit_time": trade.exit_time,
        "exit_price": trade.exit_price,
        "exit_reason": trade.exit_reason,
        "lots": trade.lots,
        "risk_amount": trade.risk_amount or None,
        "commission": trade.commission,
        "swap": trade.swap,
        "pnl": trade.pnl,
        "equity_after": trade.equity_after,
        "candles_held": trade.candles_held,
        "assumptions": {
            "spread_pips": cfg.spread_pips,
            "slippage_pips": cfg.slippage_pips,
            "commission_per_lot_per_side": cfg.commission_per_lot_per_side,
            "pip_size": pip_size,
            "pip_value_per_lot": cfg.pip_value_per_lot,
            "swap_per_lot_per_night": cfg.swap_per_lot_per_night,
            "sl_tp_same_candle_policy": cfg.sl_tp_same_candle_policy,
            "candle_side": "bid",
            "note": (
                "Candles are BID-side OHLC. Long entries pay the declared "
                "spread + slippage; stop fills slip adversely; take-profit "
                "fills at the level (passive limit)."
            ),
        },
    }


def trade_limit_payload(cfg: ForexExecutionConfig) -> dict[str, Any] | None:
    """Stop/target reference for metrics (avg R computations)."""

    if cfg.stop_loss_pips is None or cfg.take_profit_pips is None:
        return None
    return {
        "stop_loss_pips": cfg.stop_loss_pips,
        "take_profit_pips": cfg.take_profit_pips,
        "planned_rr": round(cfg.take_profit_pips / cfg.stop_loss_pips, 6),
    }
