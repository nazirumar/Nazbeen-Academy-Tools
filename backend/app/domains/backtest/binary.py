"""Binary Options simulation — explicit expiry/payout/tie rules.

Declared semantics (mirrored in run payloads and the UI):

* Entry reference price = CLOSE of the signal candle (the candle whose
  close triggered ``conditions_met``) — the first price an M1 trader could
  act on. No intra-candle entry is assumed.
* Expiry reference price = CLOSE of the candle ``expiry_candles`` positions
  AFTER the signal candle (candle-count based, documented: session/weekend
  gaps make index-based expiry differ from wall-clock expiry).
* Side: ``bullish`` strategy direction -> CALL (win when expiry > entry),
  ``bearish`` -> PUT (win when expiry < entry). Strategies with direction
  ``both`` do NOT define an executable side — those setups are recorded
  with outcome ``skipped`` and are never traded (no fabricated side).
* Equal entry/expiry prices follow the configured tie policy: ``refund``
  (default — stake returned, P&L 0) or ``loss``.
* Payout applies to the stake on wins; declared per-trade fees are always
  subtracted. Outcomes are win / loss / tie only — no probabilities, no
  confidence, no guarantees.

These are simulations over stored MT5 candles. MT5 data is NOT a binary
broker's execution feed, and OTC feeds are never simulated; reports state
this without exception.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domains.backtest.schemas import BinaryOptionsConfig


@dataclass
class BinaryTrade:
    seq: int
    side: str  # "call" | "put"
    direction: str  # strategy direction ("bullish"/"bearish")
    signal_time: int  # open time of the signal candle
    entry_time: int  # close time of the signal candle
    entry_price: float
    expiry_candle_time: int | None  # open time of the expiry candle
    expiry_time: int | None  # close time of the expiry candle
    expiry_price: float | None
    stake: float
    payout_pct: float
    fees: float

    pnl: float | None = None
    tie: bool = False
    skipped: bool = False
    skip_reason: str | None = None

    @property
    def outcome(self) -> str:
        if self.skipped:
            return "skipped"
        if self.expiry_price is None:
            return "unresolved"
        if self.tie:
            return "tie"
        return "win" if self.pnl is not None and self.pnl > 0 else "loss"


def evaluate_trade(
    cfg: BinaryOptionsConfig,
    *,
    seq: int,
    direction: str,
    signal_candle: dict[str, Any],
    interval: int,
    candles: list[dict[str, Any]],
    signal_index: int,
) -> BinaryTrade:
    """Classify one binary outcome under the explicit rules.

    ``signal_index`` points at the signal candle inside ``candles``; the
    expiry candle is at ``signal_index + expiry_candles``. When that candle
    is not in the stored data the outcome is ``unresolved`` (excluded from
    statistics) — never extrapolated.
    """

    entry_price = float(signal_candle["close"])
    signal_time = int(signal_candle["time"])
    entry_time = signal_time + interval
    if direction not in ("bullish", "bearish"):
        return BinaryTrade(
            seq=seq,
            side="none",
            direction=direction,
            signal_time=signal_time,
            entry_time=entry_time,
            entry_price=entry_price,
            expiry_candle_time=None,
            expiry_time=None,
            expiry_price=None,
            stake=cfg.stake,
            payout_pct=cfg.payout_pct,
            fees=cfg.fees_per_trade,
            skipped=True,
            skip_reason=(
                f"Strategy direction '{direction}' does not define an "
                "executable side (CALL/PUT); the setup is recorded but not "
                "traded."
            ),
        )
    side = "call" if direction == "bullish" else "put"
    trade = BinaryTrade(
        seq=seq,
        side=side,
        direction=direction,
        signal_time=signal_time,
        entry_time=entry_time,
        entry_price=entry_price,
        expiry_candle_time=None,
        expiry_time=None,
        expiry_price=None,
        stake=cfg.stake,
        payout_pct=cfg.payout_pct,
        fees=cfg.fees_per_trade,
    )
    expiry_index = signal_index + cfg.expiry_candles
    if expiry_index >= len(candles):
        return trade  # expiry beyond available data -> unresolved
    expiry_candle = candles[expiry_index]
    trade.expiry_candle_time = int(expiry_candle["time"])
    trade.expiry_time = trade.expiry_candle_time + interval
    trade.expiry_price = float(expiry_candle["close"])
    diff = trade.expiry_price - trade.entry_price
    if diff == 0:
        trade.tie = True
        trade.pnl = 0.0 if cfg.tie_policy == "refund" else -cfg.stake
    elif (side == "call" and diff > 0) or (side == "put" and diff < 0):
        trade.pnl = round(cfg.stake * cfg.payout_pct - cfg.fees_per_trade, 8)
    else:
        trade.pnl = round(-cfg.stake - cfg.fees_per_trade, 8)
    return trade


def execution_payload(trade: BinaryTrade, cfg: BinaryOptionsConfig) -> dict[str, Any]:
    """Execution details persisted on the trade record (audit trail)."""

    return {
        "mode": "binary_options",
        "side": trade.side,
        "strategy_direction": trade.direction,
        "signal_time": trade.signal_time,
        "entry_time": trade.entry_time,
        "entry_reference_price": trade.entry_price,
        "expiry_candle_time": trade.expiry_candle_time,
        "expiry_time": trade.expiry_time,
        "expiry_reference_price": trade.expiry_price,
        "expiry_candles": cfg.expiry_candles,
        "stake": trade.stake,
        "payout_pct": cfg.payout_pct,
        "tie_policy": cfg.tie_policy,
        "fees_per_trade": cfg.fees_per_trade,
        "pnl": trade.pnl,
        "skip_reason": trade.skip_reason,
        "note": (
            "Entry/expiry reference prices are CLOSES of stored MT5 "
            "candles. This simulates outcomes on MT5 data; it is NOT a "
            "record of execution on any binary options broker feed."
        ),
    }
