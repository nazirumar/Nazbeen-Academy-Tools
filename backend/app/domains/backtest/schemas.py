"""Validated backtest configuration models + API request/response schemas.

Design rules:

* The strategy version is EXPLICIT and REQUIRED — the engine never silently
  substitutes a newer (or the published) version. Draft versions are
  rejected: their lifecycle status can still change, so only ``published``
  and ``superseded`` versions (immutable definitions) may be backtested.
* Trade simulation only happens when the caller EXPLICITLY provides the
  mode-specific execution assumptions. Without them the run reports setup
  evaluations only — no fabricated trade records.
* Every assumption the engine cannot derive from data is a declared
  configuration field (spread, payout, tie handling, pip value...), never
  an undocumented constant.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

SYMBOL_PATTERN = r"^[A-Za-z0-9._#&+-]{1,40}$"

RunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
TradeOutcome = Literal[
    "win", "loss", "tie", "open", "unresolved", "ambiguous", "skipped"
]


class ForexExecutionConfig(BaseModel):
    """Explicit Forex simulation assumptions.

    All costs are user-declared — the engine never invents friction, and it
    never assumes frictionless fills silently either (zero-cost assumptions
    are carried into the run payload and surfaced in results).
    """

    model_config = {"extra": "forbid"}

    enabled: bool = Field(
        default=True,
        description="false -> setup-only run (no simulated trades).",
    )
    spread_pips: float = Field(default=0.0, ge=0.0, le=100.0)
    slippage_pips: float = Field(default=0.0, ge=0.0, le=100.0)
    commission_per_lot_per_side: float = Field(default=0.0, ge=0.0, le=1000.0)
    stop_loss_pips: float | None = Field(default=None, gt=0.0, le=10_000.0)
    take_profit_pips: float | None = Field(default=None, gt=0.0, le=10_000.0)
    risk_mode: Literal["fixed_fractional", "fixed_lot"] = "fixed_fractional"
    risk_pct_per_trade: float = Field(default=1.0, gt=0.0, le=100.0)
    fixed_lot_size: float = Field(default=0.01, gt=0.0, le=1_000.0)
    # Account currency per pip per standard lot — an explicit assumption
    # (pairs whose quote currency differs require the user's own value).
    pip_value_per_lot: float = Field(default=10.0, gt=0.0, le=1_000_000.0)
    pip_size: float | None = Field(
        default=None,
        gt=0.0,
        le=10.0,
        description="Override the price increment treated as one pip "
        "(default: 0.01 for *JPY symbols, otherwise 0.0001).",
    )
    swap_per_lot_per_night: float | None = Field(
        default=None,
        ge=-10_000.0,
        le=10_000.0,
        description="Overnight swap in account currency per lot; omitted -> "
        "no swap applied (declared, not assumed).",
    )
    account_start_balance: float = Field(default=10_000.0, gt=0.0)
    # A single candle touching both SL and TP cannot be resolved from OHLC:
    # conservative (default) assumes the stop, optimistic assumes the
    # target, ambiguous records the outcome as 'ambiguous' and excludes it
    # from statistics.
    sl_tp_same_candle_policy: Literal[
        "conservative", "optimistic", "ambiguous"
    ] = "conservative"

    @model_validator(mode="after")
    def _check_sl_tp(self) -> "ForexExecutionConfig":
        if self.enabled and (
            (self.stop_loss_pips is None) != (self.take_profit_pips is None)
        ):
            raise ValueError(
                "stop_loss_pips and take_profit_pips must be provided together "
                "(the simulation needs both to place a managed trade), or "
                "omit both for setup-only evaluation."
            )
        return self


class BinaryOptionsConfig(BaseModel):
    """Explicit Binary Options simulation assumptions."""

    model_config = {"extra": "forbid"}

    expiry_candles: int = Field(
        default=1,
        ge=1,
        description="Expiry measured in COMPLETED evaluation-timeframe "
        "candles after the signal candle (candle-count based; weekend/"
        "session gaps make index-based and wall-clock expiry differ — this "
        "is the documented choice).",
    )
    payout_pct: float = Field(default=0.80, ge=0.0, le=1.0)
    stake: float = Field(default=10.0, gt=0.0, le=1_000_000.0)
    # Equal entry/expiry reference prices: refund (default, honest) or loss.
    tie_policy: Literal["refund", "loss"] = "refund"
    fees_per_trade: float = Field(default=0.0, ge=0.0, le=10_000.0)
    account_start_balance: float = Field(default=1_000.0, gt=0.0)


class BacktestCreateRequest(BaseModel):
    """Create (validate + queue) a backtest run."""

    model_config = {"extra": "forbid"}

    mode: Literal["forex", "binary_options"]
    strategy_id: uuid.UUID
    # Explicit + required: the exact immutable version under test.
    strategy_version: int = Field(ge=1)
    symbol: str = Field(pattern=SYMBOL_PATTERN, min_length=1, max_length=40)
    # None -> the finest timeframe declared by the strategy (M1 for Binary
    # by convention). Must be one of the strategy's declared timeframes.
    timeframe: str | None = Field(default=None, min_length=2, max_length=8)
    start: datetime
    end: datetime
    notes: str | None = Field(default=None, max_length=2000)
    forex_execution: ForexExecutionConfig | None = None
    binary: BinaryOptionsConfig | None = None

    @model_validator(mode="after")
    def _check_window_and_mode(self) -> "BacktestCreateRequest":
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("start/end must be timezone-aware ISO-8601 timestamps.")
        if self.start >= self.end:
            raise ValueError("start must be earlier than end.")
        if self.mode == "binary_options" and self.binary is None:
            # Defaults are documented; the payload still records them.
            self.binary = BinaryOptionsConfig()
        return self


class BacktestConfigPayload(BaseModel):
    """The configuration persisted on the run row (audit-complete)."""

    model_config = {"extra": "forbid"}

    mode: str
    symbol: str
    timeframe: str
    start: str
    end: str
    warmup_candles: int
    max_candles: int
    data_source: str
    strategy_id: str
    strategy_name: str
    strategy_version: int
    strategy_version_status: str
    # Immutable rule-set snapshot: the exact definition that was evaluated.
    rule_set: dict[str, Any]
    direction: str
    notes: str | None = None
    forex_execution: dict[str, Any] | None = None
    binary: dict[str, Any] | None = None
    simulation_mode: Literal["setup_only", "trade_simulation"]


class PaginatedPage(BaseModel):
    page: int
    page_size: int
    total: int
    pages: int
