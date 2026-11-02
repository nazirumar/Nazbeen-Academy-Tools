"""Validated research request + persisted configuration models.

Design rules (mirroring the Phase 6 backtest schemas):

* The strategy version under test is EXPLICIT and REQUIRED and must be
  immutable (published/superseded). Drafts are rejected.
* The search space is fully EXPLICIT: every optimizable parameter is one
  existing field of an existing rule-set condition (or its lookback) with
  a user-selected list of allowed values. New rules/parameters can never
  be introduced by optimization.
* Robustness cost scenarios are explicit field overrides of the declared
  execution assumptions — spread/slippage/commission/swap for Forex,
  payout/fees/tie-policy for Binary Options. Nothing else is perturbed.
* The objective metric is chosen from metrics the existing engine
  actually produces, with a FIXED direction per metric (documented).
  Win rate is allowed but carries an explicit misuse warning.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Union

from pydantic import BaseModel, Field, model_validator

from app.domains.backtest.schemas import (
    SYMBOL_PATTERN,
    BinaryOptionsConfig,
    ForexExecutionConfig,
)

ParameterValueType = Union[int, float, str, bool]

# A parameter addresses an EXISTING condition field: the condition's
# lookback, or an EXISTING key inside its params object. Anything else is
# rejected — optimization can never invent new rule structure.
PARAMETER_FIELD_PATTERN = r"^(lookback|params\.[A-Za-z_][A-Za-z0-9_]*)$"

ObjectiveMetric = Literal[
    "net_pnl",
    "expectancy",
    "profit_factor",
    "win_rate",
    "avg_return_pct_per_trade",
    "max_drawdown_pct",
    "conditions_met_count",
]

# Fixed, documented direction per supported objective metric.
OBJECTIVE_DIRECTIONS: dict[str, str] = {
    "net_pnl": "maximize",
    "expectancy": "maximize",
    "profit_factor": "maximize",
    "win_rate": "maximize",
    "avg_return_pct_per_trade": "maximize",
    "max_drawdown_pct": "minimize",
    "conditions_met_count": "maximize",
}

# Metrics that need simulated trades (setup-only runs cannot produce them).
TRADE_LEVEL_METRICS = frozenset(
    {
        "net_pnl",
        "expectancy",
        "profit_factor",
        "win_rate",
        "avg_return_pct_per_trade",
        "max_drawdown_pct",
    }
)

OBJECTIVE_NOTES: dict[str, str] = {
    "net_pnl": "Simulated net P&L after the configured costs (account currency).",
    "expectancy": "Mean P&L per decided trade (account currency).",
    "profit_factor": "Gross profit / gross loss; undefined when either side is absent.",
    "win_rate": (
        "wins / (wins + losses). Warning: ranking on win rate alone ignores "
        "risk and reward sizes — prefer expectancy or net P&L when trades exist."
    ),
    "avg_return_pct_per_trade": "Mean per-trade return in percent of risk/stake.",
    "max_drawdown_pct": (
        "Largest peak-to-trough decline of the realized equity path, in "
        "percent of its peak (lower is better)."
    ),
    "conditions_met_count": (
        "Number of setups whose conditions were met (trigger frequency). "
        "This is NOT a performance metric — use only for setup-only research."
    ),
}


class OptimizationParameter(BaseModel):
    """One optimizable dimension: an existing condition field + allowed values."""

    model_config = {"extra": "forbid"}

    condition_id: str = Field(min_length=1, max_length=64)
    # "lookback" or "params.<existing key>" — pattern-enforced.
    field: str = Field(pattern=PARAMETER_FIELD_PATTERN, min_length=1, max_length=80)
    label: str | None = Field(default=None, max_length=120)
    values: list[ParameterValueType] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _check_values(self) -> "OptimizationParameter":
        if len(set(map(repr, self.values))) != len(self.values):
            raise ValueError("Parameter values must be unique (no duplicates).")
        kinds = {type(v) for v in self.values}
        # int and float are the same numeric family; str/bool must not mix.
        if bool in kinds and len(kinds) > 1:
            raise ValueError("Boolean parameter values cannot be mixed with other types.")
        if str in kinds and len(kinds) > 1:
            raise ValueError("String parameter values cannot be mixed with other types.")
        for v in self.values:
            if isinstance(v, float):
                if v != v or v in (float("inf"), float("-inf")):
                    raise ValueError("Parameter values must be finite numbers.")
        return self


class ObjectiveConstraints(BaseModel):
    """Selection constraints checked on the SELECTION window metrics."""

    model_config = {"extra": "forbid"}

    min_decided_trades: int | None = Field(default=None, ge=0, le=100_000)
    max_drawdown_pct: float | None = Field(default=None, ge=0.0, le=100.0)


class ObjectiveSpec(BaseModel):
    """Optimization objective: metric (fixed documented direction) + constraints."""

    model_config = {"extra": "forbid"}

    metric: ObjectiveMetric
    constraints: ObjectiveConstraints = Field(default_factory=ObjectiveConstraints)

    @property
    def direction(self) -> str:
        return OBJECTIVE_DIRECTIONS[self.metric]

    @property
    def note(self) -> str:
        return OBJECTIVE_NOTES[self.metric]


class HoldoutSplit(BaseModel):
    """Single in-sample / out-of-sample split (holdout validation)."""

    model_config = {"extra": "forbid"}

    # The in-sample portion as a fraction of [start, end]; the remainder is
    # held out. 0.5..0.9 keeps both periods meaningful.
    in_sample_pct: float = Field(default=0.7, ge=0.5, le=0.9)


class WalkForwardConfig(BaseModel):
    """Walk-forward validation settings (candle counts on the primary TF)."""

    model_config = {"extra": "forbid"}

    train_candles: int = Field(ge=20)
    test_candles: int = Field(ge=5)
    # Step between successive windows; MUST be >= test_candles so test
    # windows never overlap (no double-counted out-of-sample observations).
    step_candles: int = Field(ge=5)
    window_mode: Literal["rolling", "expanding"] = "rolling"

    @model_validator(mode="after")
    def _check_no_test_overlap(self) -> "WalkForwardConfig":
        if self.step_candles < self.test_candles:
            raise ValueError(
                "step_candles must be >= test_candles: overlapping test "
                "windows would double-count out-of-sample observations."
            )
        return self


class ForexCostOverride(BaseModel):
    """EXPLICIT execution-cost perturbation for a Forex robustness scenario.

    Only cost fields may vary; SL/TP, sizing and pip-value assumptions stay
    fixed so the scenario isolates execution friction.
    """

    model_config = {"extra": "forbid"}

    spread_pips: float | None = Field(default=None, ge=0.0, le=100.0)
    slippage_pips: float | None = Field(default=None, ge=0.0, le=100.0)
    commission_per_lot_per_side: float | None = Field(default=None, ge=0.0, le=1000.0)
    swap_per_lot_per_night: float | None = Field(default=None, ge=-10_000.0, le=10_000.0)


class BinaryCostOverride(BaseModel):
    """EXPLICIT payout/fee perturbation for a Binary Options scenario."""

    model_config = {"extra": "forbid"}

    payout_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    fees_per_trade: float | None = Field(default=None, ge=0.0, le=10_000.0)
    tie_policy: Literal["refund", "loss"] | None = None


class ResearchCreateRequest(BaseModel):
    """Create (validate + queue) a research/optimization run."""

    model_config = {"extra": "forbid"}

    mode: Literal["forex", "binary_options"]
    strategy_id: uuid.UUID
    # Explicit + required: the exact immutable version under test.
    strategy_version: int = Field(ge=1)
    symbol: str = Field(pattern=SYMBOL_PATTERN, min_length=1, max_length=40)
    timeframe: str | None = Field(default=None, min_length=2, max_length=8)
    start: datetime
    end: datetime
    notes: str | None = Field(default=None, max_length=2000)

    # Search space (explicit, bounded; candidate 0 = original params).
    parameters: list[OptimizationParameter] = Field(min_length=1, max_length=32)

    objective: ObjectiveSpec
    validation_mode: Literal["in_sample_only", "holdout", "walk_forward"] = "holdout"
    holdout: HoldoutSplit = Field(default_factory=HoldoutSplit)
    walk_forward: WalkForwardConfig | None = None

    # Execution assumptions (identical semantics to the backtest API).
    forex_execution: ForexExecutionConfig | None = None
    binary: BinaryOptionsConfig | None = None

    # Robustness scenario toggles + variants.
    forex_cost_variants: list[ForexCostOverride] = Field(default_factory=list, max_length=24)
    binary_cost_variants: list[BinaryCostOverride] = Field(default_factory=list, max_length=24)
    param_sensitivity: bool = True
    run_bootstrap: bool = True
    # Seed for the optional randomized stress test (reproducible).
    seed: int = Field(default=7, ge=0, le=2**31 - 1)

    @model_validator(mode="after")
    def _check_window_and_mode(self) -> "ResearchCreateRequest":
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("start/end must be timezone-aware ISO-8601 timestamps.")
        if self.start >= self.end:
            raise ValueError("start must be earlier than end.")
        if self.mode == "binary_options" and self.binary is None:
            self.binary = BinaryOptionsConfig()
        if self.validation_mode == "walk_forward" and self.walk_forward is None:
            raise ValueError(
                "walk_forward settings are required when validation_mode is "
                "'walk_forward'."
            )
        if self.validation_mode != "walk_forward" and self.walk_forward is not None:
            raise ValueError(
                "walk_forward settings are only valid when validation_mode "
                "is 'walk_forward'."
            )
        if self.mode == "forex" and self.binary is not None and (
            self.binary.model_dump() != BinaryOptionsConfig().model_dump()
        ):
            # A defaulted binary config is auto-filled for binary mode only;
            # an explicit one on a Forex run is a configuration mistake.
            raise ValueError(
                "binary configuration cannot be combined with a Forex run."
            )
        return self

    @model_validator(mode="after")
    def _check_mode_config_consistency(self) -> "ResearchCreateRequest":
        if self.mode == "forex":
            if self.binary_cost_variants:
                raise ValueError(
                    "binary_cost_variants cannot be combined with a Forex run."
                )
            if self.binary is not None and self.binary.model_dump() != BinaryOptionsConfig().model_dump():
                raise ValueError(
                    "binary configuration cannot be combined with a Forex run."
                )
        else:
            if self.forex_cost_variants:
                raise ValueError(
                    "forex_cost_variants cannot be combined with a Binary "
                    "Options run."
                )
            if self.forex_execution is not None:
                raise ValueError(
                    "forex_execution configuration cannot be combined with a "
                    "Binary Options run."
                )
        # Duplicate-variant guard (identical assumptions twice would only
        # burn walk budget without adding information).
        def _dup(items: list[dict[str, Any]]) -> bool:
            seen = [tuple(sorted(d.items(), key=lambda kv: kv[0])) for d in items]
            return len(seen) != len(set(seen))

        if _dup([v.model_dump() for v in self.forex_cost_variants]):
            raise ValueError("forex_cost_variants contains duplicate assumptions.")
        if _dup([v.model_dump() for v in self.binary_cost_variants]):
            raise ValueError("binary_cost_variants contains duplicate assumptions.")
        return self


class ResearchConfigPayload(BaseModel):
    """The reproducible configuration persisted on the run row."""

    model_config = {"extra": "forbid"}

    mode: str
    symbol: str
    timeframe: str
    validation_mode: str
    start: str
    end: str
    # Resolved in-sample end for holdout runs (ISO); None otherwise.
    in_sample_end: str | None = None

    data_source: str
    warmup_candles: int
    max_candles: int

    strategy_id: str
    strategy_name: str
    strategy_version: int
    strategy_version_status: str
    # Immutable rule-set snapshot: the baseline every patch applies to.
    rule_set: dict[str, Any]
    direction: str

    parameters: list[dict[str, Any]]
    parameter_labels: dict[str, str]
    objective: dict[str, Any]
    objective_direction: str
    objective_note: str
    objective_constraints: dict[str, Any]

    holdout: dict[str, Any] | None = None
    walk_forward: dict[str, Any] | None = None

    forex_execution: dict[str, Any] | None = None
    binary: dict[str, Any] | None = None
    forex_cost_variants: list[dict[str, Any]] = []
    binary_cost_variants: list[dict[str, Any]] = []
    param_sensitivity: bool = True
    run_bootstrap: bool = True
    seed: int

    combination_count: int
    walk_budget: int
    limits: dict[str, int]

    simulation_mode: Literal["setup_only", "trade_simulation"]
    notes: str | None = None
    warnings: list[dict[str, Any]] = []
    software: dict[str, Any] = {}


__all__ = [
    "BinaryCostOverride",
    "ForexCostOverride",
    "HoldoutSplit",
    "OBJECTIVE_DIRECTIONS",
    "OBJECTIVE_NOTES",
    "TRADE_LEVEL_METRICS",
    "OptimizationParameter",
    "ObjectiveConstraints",
    "ObjectiveSpec",
    "PARAMETER_FIELD_PATTERN",
    "ResearchConfigPayload",
    "ResearchCreateRequest",
    "WalkForwardConfig",
]
