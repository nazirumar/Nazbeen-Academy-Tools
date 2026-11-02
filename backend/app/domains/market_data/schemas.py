"""Pydantic schemas for market-data endpoints (Phase 2).

All responses reuse the platform envelope ({success, data, error, meta});
these models define the ``data`` payloads and double as OpenAPI docs.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TimezoneConvention = Literal["MT5_SERVER"]
DataStatus = Literal["ok", "partial", "stale", "insufficient", "empty"]
Provenance = Literal["mt5_live", "postgres_cache"]


class CandlePoint(BaseModel):
    """One validated candle. ``time`` is preserved exactly as MT5 reports it."""

    time: int = Field(..., description="Candle open time, epoch seconds (MT5 server-time convention, preserved).")
    time_utc: str = Field(..., description="ISO-8601 UTC rendering of the same epoch.")
    open: float
    high: float
    low: float
    close: float
    volume: float | None = Field(None, description="Real volume when > 0, otherwise tick volume.")
    forming: bool = Field(..., description="True while the candle is still forming (values are NOT final).")


class CandleSeries(BaseModel):
    symbol: str
    timeframe: str
    timezone: TimezoneConvention = "MT5_SERVER"
    timezone_note: str = Field(..., description="Documents the MT5 server-time convention.")
    candles: list[CandlePoint]
    count: int
    requested_count: int
    closed_only: bool
    includes_forming: bool
    first_time: int | None = None
    last_time: int | None = None
    newest_closed_time: int | None = None
    newest_closed_age_seconds: float | None = None
    data_status: DataStatus
    data_provenance: Provenance
    retrieved_at: str = Field(..., description="When the platform retrieved/validated this series (UTC ISO).")
    warnings: list[dict[str, str]] = Field(default_factory=list)


class SymbolSummary(BaseModel):
    name: str
    description: str | None = None
    visible: bool | None = None
    selectable: bool | None = None
    trade_mode: int | None = None
    digits: int | None = None
    point: float | None = None
    spread: int | None = None
    currency_base: str | None = None
    currency_profit: str | None = None


class SymbolDetails(SymbolSummary):
    path: str | None = None
    tick_value: float | None = None
    tick_size: float | None = None
    contract_size: float | None = None
    volume_min: float | None = None
    volume_max: float | None = None
    volume_step: float | None = None
    spread_float: float | None = None
    stops_level: int | None = None
    freeze_level: int | None = None
    trade_calc_mode: int | None = None
    filling_mode: int | None = None


class SymbolList(BaseModel):
    symbols: list[SymbolSummary]
    count: int
    search: str | None = None
    truncated: bool = False


class SpreadInfo(BaseModel):
    points: int | None = Field(None, description="Current spread in points (from symbol properties).")
    from_bid_ask: int | None = Field(None, description="Spread computed from the latest tick bid/ask, when available.")
    floating: bool | None = Field(None, description="True when the symbol uses floating spread.")


class TickPayload(BaseModel):
    symbol: str
    time: int = Field(..., description="Tick time, epoch seconds (MT5 server-time convention).")
    time_utc: str
    time_msc: int | None = None
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    volume: float | None = None
    spread: SpreadInfo | None = None
    symbol_digits: int | None = None
    retrieved_at: str


class MT5StatusPayload(BaseModel):
    """Honest MT5 connection status. Contains NO credentials — ever."""

    provider: str = "mt5"
    enabled: bool
    package_available: bool
    initialized: bool
    connected: bool
    package_version: str | None = None
    terminal: dict[str, Any] | None = None
    account: dict[str, Any] | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    last_error_at: str | None = None
    reconnect_attempts: int = 0
    detail: str
    checked_at: str
    configuration: dict[str, Any] = Field(
        default_factory=dict, description="Safe configuration summary (no secrets)."
    )


class DiagnosticsPayload(BaseModel):
    """End-to-end market-data diagnostics (connection + probe + quality)."""

    provider: str = "mt5"
    status: MT5StatusPayload
    configuration: dict[str, Any] = Field(default_factory=dict)
    probe: dict[str, Any] | None = Field(
        None, description="Result of a small live candle probe (symbol/timeframe/count)."
    )
    capabilities: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
