"""Analysis DTOs (request/response contracts for explainable evaluation)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field, model_validator

MAX_CANDLES_PER_TIMEFRAME = 5000
MAX_TIMEFRAMES_PER_REQUEST = 6


class CandleRow(BaseModel):
    """A single OHLCV candle supplied by the caller.

    ``time`` is a unix timestamp in SECONDS. Pydantic rejects NaN/Infinity
    because ``allow_inf_nan`` defaults to False for constrained floats.
    """

    model_config = {"extra": "forbid"}

    time: int = Field(gt=0, le=4102444800)  # up to year 2100
    open: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)
    low: float = Field(gt=0, allow_inf_nan=False)
    close: float = Field(gt=0, allow_inf_nan=False)
    volume: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class EvaluateRequest(BaseModel):
    """Evaluate a rule set against caller-supplied candle data.

    Exactly one of ``strategy_id`` (stored, versioned rule set) or inline
    ``rule_set`` must be provided. ``candles`` maps timeframe -> candle list
    and must cover every timeframe referenced by the rule set.
    """

    strategy_id: uuid.UUID | None = None
    version: int | None = Field(default=None, ge=1)
    rule_set: dict[str, Any] | None = None
    candles: dict[str, list[CandleRow]]

    @model_validator(mode="after")
    def _check_exclusive_source(self) -> "EvaluateRequest":
        if self.strategy_id is None and self.rule_set is None:
            raise ValueError(
                "Provide either 'strategy_id' (to evaluate a stored version) "
                "or an inline 'rule_set'."
            )
        if self.strategy_id is not None and self.rule_set is not None:
            raise ValueError(
                "Provide only one of 'strategy_id' or inline 'rule_set', not both."
            )
        if self.version is not None and self.strategy_id is None:
            raise ValueError("'version' can only be used together with 'strategy_id'.")
        if not self.candles:
            raise ValueError(
                "'candles' must contain at least one timeframe with candles. "
                "The platform does not fabricate market data."
            )
        if len(self.candles) > MAX_TIMEFRAMES_PER_REQUEST:
            raise ValueError(
                f"At most {MAX_TIMEFRAMES_PER_REQUEST} timeframes per request."
            )
        for tf, rows in self.candles.items():
            if len(rows) > MAX_CANDLES_PER_TIMEFRAME:
                raise ValueError(
                    f"Timeframe '{tf}' has {len(rows)} candles; the limit is "
                    f"{MAX_CANDLES_PER_TIMEFRAME}."
                )
        return self


SYMBOL_PATTERN = r"^[A-Za-z0-9._#&+-]{1,40}$"


class RunAnalysisRequest(BaseModel):
    """Run the analysis engine against a stored strategy version.

    The engine fetches REAL candles from the configured market-data
    provider for every timeframe the strategy requires. It refuses to run
    when the provider cannot supply data (no synthetic fallback, no OTC
    simulation).
    """

    strategy_id: uuid.UUID
    # Omitted -> published version, else latest saved version.
    version: int | None = Field(default=None, ge=1)
    symbol: str = Field(pattern=SYMBOL_PATTERN, min_length=1, max_length=40)
    # None -> the strategy's evaluation.on_close_only (default: closed only).
    # true -> INCLUDE the forming candle; results are labelled provisional.
    include_forming: bool | None = None
    # Optional per-timeframe history override (bounded by the provider cap).
    candle_limit: int | None = Field(default=None, ge=30, le=5000)
