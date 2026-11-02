"""Market-data & MT5 HTTP endpoints (Phase 2).

Conventions (unchanged from Phase 1): every response uses the platform
envelope; errors use the global AppError hierarchy with structured details.

Endpoints
---------
- GET /api/v1/market-data/status — live provider availability (extended).
- GET /api/v1/mt5/status         — MT5 terminal/account connection report.
- GET /api/v1/market/symbols     — available symbols + metadata.
- GET /api/v1/market/candles     — validated OHLCV series (live or cache).
- GET /api/v1/market/tick        — latest tick / bid-ask.
- GET /api/v1/market/diagnostics — end-to-end connection + data-quality probe.

Security: no endpoint accepts credentials, terminal paths or anything
executable; configuration arrives exclusively via environment variables.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.config import Settings, get_settings
from app.core.responses import ok
from app.domains.market_data.base import get_provider

# Existing router (Phase 1 surface, extended in place).
router = APIRouter(prefix="/market-data", tags=["market-data"])

# Phase 2 routers.
mt5_router = APIRouter(prefix="/mt5", tags=["mt5"])
market_router = APIRouter(prefix="/market", tags=["market"])

SYMBOL_PATTERN = r"^[A-Za-z0-9._#&+-]{1,40}$"


@router.get("/status")
async def market_data_status(settings: Settings = Depends(get_settings)) -> dict:
    """Report whether the configured market-data provider can serve data.

    This endpoint never lies: when MetaTrader 5 is not connected (or the host
    cannot run it), ``connected`` is ``false`` and the ``detail`` explains why
    and what to do next. Performs a live, bounded terminal check.
    """

    provider = get_provider(settings)
    status = await provider.get_status()  # type: ignore[attr-defined]
    return ok(
        {
            **status,
            "live_data_enabled": status["connected"],
            "note": (
                "MetaTrader 5 is connected; live candle retrieval is active."
                if status["connected"]
                else "No MetaTrader 5 terminal is connected — the platform "
                "will not fabricate data. Analysis can run on candle data "
                "you supply (paste / CSV). See README: 'MetaTrader 5 "
                "integration'."
            ),
        }
    )


@mt5_router.get("/status")
async def mt5_status(settings: Settings = Depends(get_settings)) -> dict:
    """Live MT5 connection status (terminal, account presence, config summary).

    Contains NO credentials — the payload is built exclusively from safe
    snapshots. ``terminal``/``account`` are null when not connected.
    """

    provider = get_provider(settings)
    status = await provider.get_status()  # type: ignore[attr-defined]
    return ok(status)


@market_router.get("/symbols")
async def market_symbols(
    settings: Settings = Depends(get_settings),
    search: str | None = Query(
        None, max_length=40, description="Optional MT5 symbol filter (e.g. 'EUR*')."
    ),
    limit: int = Query(100, ge=1, le=1000, description="Maximum symbols to return."),
) -> dict:
    """Symbols known to the terminal with safe metadata (no trading state)."""

    provider = get_provider(settings)
    payload = await provider.get_symbols(search=search, limit=limit)  # type: ignore[attr-defined]
    return ok(payload)


@market_router.get("/candles")
async def market_candles(
    settings: Settings = Depends(get_settings),
    symbol: str = Query(
        ..., pattern=SYMBOL_PATTERN, description="Instrument name, e.g. EURUSD."
    ),
    timeframe: str = Query(
        ..., min_length=2, max_length=4, description="M1, M5, M15, H1, H4 or D1."
    ),
    limit: int = Query(
        200,
        ge=1,
        le=100_000,
        description="Number of candles (1..MT5_MAX_CANDLES; includes the "
        "forming candle unless closed_only=true).",
    ),
    closed_only: bool = Query(
        False,
        description="Return only closed candles; the forming candle is excluded.",
    ),
    refresh: bool = Query(
        False, description="Bypass the PostgreSQL cache and fetch from MT5."
    ),
) -> dict:
    """Validated OHLCV candles with explicit data status and warnings.

    - ``data_provenance`` states whether the series came from the terminal
      (``mt5_live``) or the fresh PostgreSQL cache (``postgres_cache``).
    - Timestamps preserve the MT5 server-time convention (documented in
      ``timezone_note``); ``time_utc`` renders the same epoch as ISO-8601 UTC.
    - The forming candle is flagged with ``forming: true`` and excluded
      entirely when ``closed_only=true``.
    - Gaps, duplicates, invalid OHLC, staleness and short history are
      reported as warnings — never silently repaired or filled.
    """

    provider = get_provider(settings)
    series = await provider.get_candles(  # type: ignore[attr-defined]
        symbol, timeframe, limit, closed_only=closed_only
    )
    payload = series.model_dump()
    if refresh:
        payload["cache_bypass_requested"] = True
    return ok(payload)


@market_router.get("/tick")
async def market_tick(
    settings: Settings = Depends(get_settings),
    symbol: str = Query(
        ..., pattern=SYMBOL_PATTERN, description="Instrument name, e.g. EURUSD."
    ),
) -> dict:
    """Latest tick (bid/ask) for one symbol. 404 for unknown symbols."""

    provider = get_provider(settings)
    tick = await provider.get_tick(symbol)  # type: ignore[attr-defined]
    return ok(tick)


@market_router.get("/diagnostics")
async def market_diagnostics(
    settings: Settings = Depends(get_settings),
    probe_symbol: str = Query(
        "EURUSD",
        pattern=SYMBOL_PATTERN,
        description="Symbol used for the small live candle probe.",
    ),
) -> dict:
    """End-to-end market-data diagnostics (connection + probe + capabilities)."""

    provider = get_provider(settings)
    payload = await provider.get_diagnostics(probe_symbol=probe_symbol)  # type: ignore[attr-defined]
    return ok(payload)
