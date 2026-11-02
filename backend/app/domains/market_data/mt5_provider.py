"""MetaTrader 5 market-data provider (Phase 2).

This module is the application-facing face of the MT5 integration. It
composes:

- ``mt5_adapter``  — the ONLY module that talks to the MetaTrader5 package
                     (dedicated thread, serialized, timeout-bounded);
- ``mt5_timeframes`` — extensible timeframe → MT5-constant mapping;
- ``validation``   — data-quality gate (OHLC, duplicates, gaps, forming vs
                     closed, staleness); nothing invalid reaches the API;
- ``store``        — PostgreSQL historical cache (dedup + freshness policy).

Guarantees
----------
- The provider NEVER fabricates, interpolates or fills candles. When MT5 is
  unavailable it raises ``MarketDataUnavailableError`` (HTTP 503) and the UI
  shows an honest empty state.
- Credentials never appear in responses or logs; statuses carry safe
  snapshots only.
- Every blocking call is executed on the adapter's dedicated worker thread
  with a bounded timeout — the FastAPI event loop is never blocked.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from app.core.config import Settings, get_settings
from app.core.errors import (
    MarketDataError,
    MarketDataUnavailableError,
    SymbolNotFoundError,
    ValidationAppError,
)
from app.core.logging import get_logger
from app.domains.market_data.base import (
    ProviderStatus,
    register_provider,
)
from app.domains.market_data.mt5_adapter import (
    MT5AdapterError,
    MT5ConnectionError,
    MT5DataError,
    MT5DisabledError,
    MT5NotAvailableError,
    MT5SymbolError,
    MT5TimeoutError,
    get_mt5_adapter,
)
from app.domains.market_data.mt5_timeframes import (
    PLATFORM_MT5_TIMEFRAMES,
)
from app.domains.market_data.schemas import (
    CandlePoint,
    CandleSeries,
)
from app.domains.market_data.timeframes import is_timeframe_supported
from app.domains.market_data.validation import TIMEZONE_CONVENTION, validate_candles

logger = get_logger(__name__)


def _utc_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds")


@register_provider
class MT5Provider:
    name = "mt5"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ status
    def status(self) -> ProviderStatus:
        """Cheap, honest status from the adapter's cached snapshot.

        NEVER blocks and NEVER touches the terminal: safe for /health and
        request hot paths. Use ``await get_status()`` for a live check.
        """

        adapter = get_mt5_adapter(self.settings)
        snap = adapter.snapshot()
        enabled = self.settings.mt5_enabled
        return ProviderStatus(
            provider=self.name,
            available=snap.package_available and enabled,
            connected=snap.connected and enabled,
            detail=snap.detail,
        )

    async def get_status(self) -> dict[str, Any]:
        """LIVE connection check (bounded); used by GET /api/v1/mt5/status."""

        adapter = get_mt5_adapter(self.settings)
        if not self.settings.mt5_enabled:
            return self._status_payload(adapter, "MT5 integration is disabled by configuration.")
        try:
            await adapter.run(adapter.sync_check, timeout=adapter.connect_budget_seconds())
        except MT5TimeoutError as exc:
            logger.warning("mt5_status_check_timeout", extra={"error": str(exc)})
        except MT5AdapterError as exc:
            # Expected failure modes are recorded on the snapshot already.
            logger.info("mt5_status_check_failed", extra={"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - status must never 500
            logger.exception("mt5_status_check_unexpected")
            adapter._record_error(f"Unexpected status-check failure: {exc}")
        return self._status_payload(adapter, None)

    def _status_payload(self, adapter: Any, detail_override: str | None) -> dict[str, Any]:
        snap = adapter.snapshot(detail=detail_override)
        payload = snap.to_dict()
        # Request-scoped configuration wins for identity/enablement: the
        # adapter singleton may predate a configuration change.
        payload["provider"] = self.name
        payload["enabled"] = self.settings.mt5_enabled
        payload["configuration"] = self.settings.mt5_config_summary()
        return payload

    # ----------------------------------------------------------------- candles
    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        *,
        closed_only: bool = False,
    ) -> CandleSeries:
        """Validated candle series (live MT5 or fresh PostgreSQL cache)."""

        symbol = self._validate_symbol(symbol)
        timeframe = self._validate_timeframe(timeframe)
        limit = self._validate_limit(limit)

        adapter = get_mt5_adapter(self.settings)
        self._ensure_enabled(adapter)

        # ---- 1. try the fresh PostgreSQL cache --------------------------------
        if self.settings.mt5_cache_enabled:
            cached = await self._serve_from_cache(
                symbol, timeframe, limit, closed_only
            )
            if cached is not None:
                return cached

        # ---- 2. fetch from the terminal ----------------------------------------
        try:
            records = await adapter.run(adapter.sync_candles, symbol, timeframe, limit)
        except MT5SymbolError as exc:
            raise SymbolNotFoundError(str(exc)) from exc
        except (MT5DisabledError, MT5NotAvailableError, MT5ConnectionError, MT5TimeoutError) as exc:
            raise MarketDataUnavailableError(
                f"Market data is unavailable: {exc}",
                details=[{"path": "mt5", "code": type(exc).__name__}],
            ) from exc
        except MT5DataError as exc:
            raise MarketDataError(str(exc)) from exc

        # ---- 3. validate (never trust transport) -------------------------------
        validation = validate_candles(
            records,
            timeframe,
            closed_only=closed_only,
            requested_count=limit,
            stale_after_intervals=self.settings.mt5_stale_after_intervals,
        )
        if validation.data_status == "empty":
            raise MarketDataError(
                "The terminal returned no usable candles for "
                f"'{symbol}' ({timeframe}). See warnings for the reason.",
                details=validation.warning_dicts,
            )

        # ---- 4. persist to the historical cache (best effort) -------------------
        warnings = validation.warning_dicts
        if self.settings.mt5_cache_enabled and validation.candles:
            cache_warning = await self._persist(
                symbol, timeframe, validation.candles
            )
            if cache_warning:
                warnings.append(cache_warning)

        return self._build_series(
            symbol=symbol,
            timeframe=timeframe,
            validation=validation,
            provenance="mt5_live",
            extra_warnings=warnings,
            limit=limit,
            closed_only=closed_only,
        )

    async def _serve_from_cache(
        self, symbol: str, timeframe: str, limit: int, closed_only: bool
    ) -> CandleSeries | None:
        """Serve from PostgreSQL when the series is fresh and long enough."""

        from app.db.session import get_session_factory

        try:
            factory = get_session_factory()
            async with factory() as session:
                from app.domains.market_data.store import (
                    is_series_fresh,
                    load_candles,
                    series_state,
                )

                state = await series_state(session, symbol, timeframe)
                if not state or state["cached_rows"] < limit:
                    return None
                if not is_series_fresh(
                    state,
                    timeframe,
                    cache_max_age_seconds=self.settings.mt5_cache_max_age_seconds,
                ):
                    return None
                candles = await load_candles(
                    session, symbol, timeframe, limit, closed_only=closed_only
                )
        except Exception as exc:  # noqa: BLE001 - cache is an optimization
            logger.warning(
                "cache_read_failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return None

        if not candles:
            return None
        validation = validate_candles(
            candles,
            timeframe,
            closed_only=closed_only,
            requested_count=limit,
            stale_after_intervals=self.settings.mt5_stale_after_intervals,
        )
        if validation.data_status == "empty":
            return None
        return self._build_series(
            symbol=symbol,
            timeframe=timeframe,
            validation=validation,
            provenance="postgres_cache",
            extra_warnings=validation.warning_dicts,
            limit=limit,
            closed_only=closed_only,
        )

    async def _persist(
        self, symbol: str, timeframe: str, candles: list[dict[str, Any]]
    ) -> dict[str, str] | None:
        """Upsert validated candles; failure degrades to a warning."""

        from app.db.session import get_session_factory

        try:
            factory = get_session_factory()
            async with factory() as session:
                from app.domains.market_data.store import upsert_candles

                written = await upsert_candles(session, symbol, timeframe, candles)
                await session.commit()
            logger.info(
                "candles_persisted",
                extra={"symbol": symbol, "timeframe": timeframe, "rows": written},
            )
            return None
        except Exception as exc:  # noqa: BLE001 - persistence must not break serving
            logger.warning(
                "cache_write_failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return {
                "code": "CACHE_WRITE_FAILED",
                "severity": "warning",
                "message": "Candles could not be persisted to the PostgreSQL "
                "cache (the series is still served live from MT5).",
            }

    def _build_series(
        self,
        *,
        symbol: str,
        timeframe: str,
        validation: Any,
        provenance: str,
        extra_warnings: list[dict[str, str]],
        limit: int,
        closed_only: bool,
    ) -> CandleSeries:
        points = [
            CandlePoint(
                time=row["time"],
                time_utc=row["time_utc"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                forming=row["forming"],
            )
            for row in validation.candles
        ]
        closed_times = [p.time for p in points if not p.forming]
        newest_closed = closed_times[-1] if closed_times else None
        return CandleSeries(
            symbol=symbol,
            timeframe=timeframe,
            timezone="MT5_SERVER",
            timezone_note=TIMEZONE_CONVENTION,
            candles=points,
            count=len(points),
            requested_count=limit,
            closed_only=closed_only,
            includes_forming=validation.forming_count > 0 and not closed_only,
            first_time=points[0].time if points else None,
            last_time=points[-1].time if points else None,
            newest_closed_time=newest_closed,
            newest_closed_age_seconds=(
                round(validation.newest_closed_age_seconds, 3)
                if validation.newest_closed_age_seconds is not None
                else None
            ),
            data_status=validation.data_status,
            data_provenance=provenance,
            retrieved_at=_utc_iso(),
            warnings=extra_warnings,
        )

    # ---------------------------------------------------------------- symbols
    async def get_symbols(
        self, search: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        adapter = get_mt5_adapter(self.settings)
        self._ensure_enabled(adapter)
        try:
            symbols = await adapter.run(adapter.sync_symbols, search)
        except (MT5DisabledError, MT5NotAvailableError, MT5ConnectionError, MT5TimeoutError) as exc:
            raise MarketDataUnavailableError(f"Market data is unavailable: {exc}") from exc
        except MT5DataError as exc:
            raise MarketDataError(str(exc)) from exc
        truncated = len(symbols) > limit
        return {
            "symbols": symbols[:limit],
            "count": len(symbols[:limit]),
            "search": search,
            "truncated": truncated,
        }

    async def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        symbol = self._validate_symbol(symbol)
        adapter = get_mt5_adapter(self.settings)
        self._ensure_enabled(adapter)
        try:
            return await adapter.run(adapter.sync_symbol_info, symbol)
        except MT5SymbolError as exc:
            raise SymbolNotFoundError(str(exc)) from exc
        except (MT5DisabledError, MT5NotAvailableError, MT5ConnectionError, MT5TimeoutError) as exc:
            raise MarketDataUnavailableError(f"Market data is unavailable: {exc}") from exc
        except MT5DataError as exc:
            raise MarketDataError(str(exc)) from exc

    # ------------------------------------------------------------------- tick
    async def get_tick(self, symbol: str) -> dict[str, Any]:
        symbol = self._validate_symbol(symbol)
        adapter = get_mt5_adapter(self.settings)
        self._ensure_enabled(adapter)
        try:
            raw_tick = await adapter.run(adapter.sync_tick, symbol)
        except MT5SymbolError as exc:
            raise SymbolNotFoundError(str(exc)) from exc
        except (MT5DisabledError, MT5NotAvailableError, MT5ConnectionError, MT5TimeoutError) as exc:
            raise MarketDataUnavailableError(f"Market data is unavailable: {exc}") from exc
        except MT5DataError as exc:
            raise MarketDataError(str(exc)) from exc

        spread = None
        symbol_digits = None
        if raw_tick.get("bid") and raw_tick.get("ask"):
            try:
                info = await adapter.run(adapter.sync_symbol_info, symbol)
                symbol_digits = info.get("digits")
                point = info.get("point")
                if point:
                    spread = {
                        "points": int(round((raw_tick["ask"] - raw_tick["bid"]) / point)),
                        "from_bid_ask": int(round((raw_tick["ask"] - raw_tick["bid"]) / point)),
                        "floating": bool(info.get("spread_float")),
                    }
                else:
                    spread = {"points": info.get("spread")}
            except MT5AdapterError:  # spread metadata is optional sugar
                spread = None
        from datetime import datetime as _dt

        return {
            "symbol": symbol,
            "time": raw_tick["time"],
            "time_utc": _dt.fromtimestamp(raw_tick["time"], tz=timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "time_msc": raw_tick.get("time_msc"),
            "bid": raw_tick.get("bid"),
            "ask": raw_tick.get("ask"),
            "last": raw_tick.get("last"),
            "volume": raw_tick.get("volume"),
            "spread": spread,
            "symbol_digits": symbol_digits,
            "retrieved_at": _utc_iso(),
        }

    # ------------------------------------------------------------- diagnostics
    async def get_diagnostics(self, probe_symbol: str = "EURUSD") -> dict[str, Any]:
        """End-to-end diagnostics: connection, config summary, live probe."""

        status = await self.get_status()
        notes: list[str] = [
            "Diagnostics perform a LIVE terminal check plus a small candle "
            "probe; failures are reported here instead of being hidden.",
            "The MetaTrader 5 terminal must run on the same host as this "
            "backend (Windows, or Wine); the terminal itself must never be "
            "exposed to the public internet.",
        ]
        probe: dict[str, Any] | None = None
        snap = status
        if snap.get("enabled") and snap.get("connected"):
            try:
                series = await self.get_candles(
                    probe_symbol, "M1", min(10, self.settings.mt5_default_candles)
                )
                probe = {
                    "symbol": series.symbol,
                    "timeframe": series.timeframe,
                    "count": series.count,
                    "data_status": series.data_status,
                    "data_provenance": series.data_provenance,
                    "first_time": series.first_time,
                    "last_time": series.last_time,
                    "warnings": series.warnings[:5],
                }
            except Exception as exc:  # noqa: BLE001 - diagnostics report, never hide
                probe = {
                    "symbol": probe_symbol,
                    "timeframe": "M1",
                    "error": str(exc),
                    "code": getattr(exc, "code", type(exc).__name__),
                }
        else:
            probe = {
                "skipped": True,
                "reason": "MT5 is not connected; no probe was attempted.",
            }
        return {
            "status": status,
            "configuration": self.settings.mt5_config_summary(),
            "probe": probe,
            "capabilities": {
                "timeframes": list(PLATFORM_MT5_TIMEFRAMES),
                "max_candles": self.settings.mt5_max_candles,
                "default_candles": self.settings.mt5_default_candles,
                "cache_enabled": self.settings.mt5_cache_enabled,
                "binary_options_timeframes": ["M1"],
                "order_execution": False,
                "note": "Market data only — the platform never places orders.",
            },
            "notes": notes,
        }

    # -------------------------------------------------------------- validation
    @staticmethod
    def _validate_symbol(symbol: str) -> str:
        cleaned = (symbol or "").strip()
        if not cleaned:
            raise ValidationAppError(
                "Symbol must not be empty.",
                details=[{"path": "symbol", "code": "required"}],
            )
        if len(cleaned) > 40:
            raise ValidationAppError(
                "Symbol is too long (max 40 characters).",
                details=[{"path": "symbol", "code": "max_length", "input": cleaned[:8]}],
            )
        allowed_extra = set("._#&+-")
        if not all(ch.isalnum() or ch in allowed_extra for ch in cleaned):
            raise ValidationAppError(
                "Symbol contains invalid characters (allowed: letters, digits, . _ # & + -).",
                details=[{"path": "symbol", "code": "invalid_characters"}],
            )
        return cleaned.upper()

    @staticmethod
    def _validate_timeframe(timeframe: str) -> str:
        tf = (timeframe or "").strip().upper()
        if not is_timeframe_supported(tf):
            raise ValidationAppError(
                f"Timeframe '{timeframe}' is not supported. Supported: "
                f"{', '.join(PLATFORM_MT5_TIMEFRAMES)}.",
                details=[{"path": "timeframe", "code": "unsupported_timeframe"}],
            )
        return tf

    def _validate_limit(self, limit: int) -> int:
        if limit is None or limit < 1:
            raise ValidationAppError(
                "Candle count must be >= 1.",
                details=[{"path": "limit", "code": "minimum"}],
            )
        if limit > self.settings.mt5_max_candles:
            raise ValidationAppError(
                f"Candle count exceeds the maximum of "
                f"{self.settings.mt5_max_candles}.",
                details=[
                    {
                        "path": "limit",
                        "code": "maximum",
                        "constraint": self.settings.mt5_max_candles,
                    }
                ],
            )
        return int(limit)

    def _ensure_enabled(self, adapter: Any) -> None:
        if not self.settings.mt5_enabled:
            raise MarketDataUnavailableError(
                "MT5 integration is disabled by configuration "
                "(MT5_ENABLED=false). The platform will not fabricate data — "
                "enable MT5 or run analysis on user-supplied candles."
            )
        adapter_snapshot_probe = getattr(adapter, "_module_loader", None)
        if adapter_snapshot_probe is not None and adapter_snapshot_probe() is None:
            raise MarketDataUnavailableError(
                "The MetaTrader5 Python package is not installed on this "
                "host. Live market data requires a Windows host (or Wine) "
                "with a running MetaTrader 5 terminal — see README.",
            )
