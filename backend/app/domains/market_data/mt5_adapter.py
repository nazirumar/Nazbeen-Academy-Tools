"""Low-level MetaTrader 5 adapter — the ONLY module that touches the MT5 API.

Isolation contract
------------------
Every other layer of the application depends on this module's *interface*
(snapshots, dataclasses, exceptions), never on the ``MetaTrader5`` package
itself, so the integration can be tested with a fake module and swapped or
removed without touching the rest of the app.

Reliability contract
--------------------
- All blocking MT5 calls run on a dedicated single-worker thread pool: the
  FastAPI event loop is never blocked and terminal access is serialized
  (the MT5 Python API is terminal-bound and not thread-safe).
- Every call is bounded by ``MT5_TIMEOUT_SECONDS`` (``asyncio.wait_for``).
- Connection loss triggers a bounded reconnect (``MT5_RETRY_ATTEMPTS`` with
  exponential backoff capped at 8× the base interval) — never an unbounded
  loop, never uncontrolled parallel initialization.
- Credentials come exclusively from configuration; they are never logged,
  never stored on the instance beyond the lifetime of a call, and never
  returned by any snapshot.
"""

from __future__ import annotations

import asyncio
import atexit
import functools
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Exceptions (transport-level; the provider maps these onto API error codes)
# --------------------------------------------------------------------------- #
class MT5AdapterError(Exception):
    """Base class for MT5 transport errors."""


class MT5NotAvailableError(MT5AdapterError):
    """The MetaTrader5 Python package is not installed on this host."""


class MT5DisabledError(MT5AdapterError):
    """MT5 integration is disabled by configuration (MT5_ENABLED=false)."""


class MT5ConnectionError(MT5AdapterError):
    """The terminal could not be initialized / reached."""


class MT5TimeoutError(MT5AdapterError):
    """An MT5 call exceeded the configured timeout."""


class MT5SymbolError(MT5AdapterError):
    """The requested symbol is unknown or has no selectable data."""


class MT5DataError(MT5AdapterError):
    """The terminal returned no usable response for a data request."""


# --------------------------------------------------------------------------- #
# Module loading (lazy, defensive, monkeypatch-friendly)
# --------------------------------------------------------------------------- #
def load_mt5_module() -> Any | None:
    """Import ``MetaTrader5`` lazily; return None when unavailable."""

    try:
        import MetaTrader5  # noqa: PLC0415 - intentional lazy import
    except Exception as exc:  # noqa: BLE001 - any import failure = unavailable
        logger.info(
            "mt5_package_unavailable",
            extra={"reason": f"{type(exc).__name__}: {exc}"},
        )
        return None
    return MetaTrader5


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds")


def _coerce_num(value: Any) -> Any:
    """Best-effort numeric coercion; returns the original value on failure.

    Data-quality decisions belong to ``validation.py`` — the adapter only
    bridges numpy records into plain Python structures.
    """

    if value is None:
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return value
    return int(as_float) if as_float.is_integer() and abs(as_float) < 2**53 else as_float


def rates_to_dicts(rates: Any) -> list[dict[str, Any]]:
    """Convert a MetaTrader5 rates result (numpy structured array) to dicts.

    Malformed individual records are passed through with their original
    values; ``validation.py`` flags and reports them. ``None`` results are the
    caller's concern (empty responses are a data-level fact).
    """

    out: list[dict[str, Any]] = []
    for row in rates:
        record: dict[str, Any] = {}
        for name in ("time", "open", "high", "low", "close",
                     "tick_volume", "spread", "real_volume"):
            try:
                record[name] = row[name]
            except (KeyError, IndexError, TypeError, ValueError):
                record[name] = None
        record["time"] = int(record["time"]) if record["time"] is not None else None
        for key in ("open", "high", "low", "close"):
            record[key] = _coerce_num(record[key])
        record["tick_volume"] = _coerce_num(record["tick_volume"])
        record["real_volume"] = _coerce_num(record["real_volume"])
        out.append(record)
    return out


@dataclass
class AccountSnapshot:
    """Safe account summary — NO balances, NO credentials."""

    accessible: bool
    login: int | None = None
    server: str | None = None
    company: str | None = None
    currency: str | None = None
    leverage: int | None = None
    trade_allowed: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accessible": self.accessible,
            "login": self.login,
            "server": self.server,
            "company": self.company,
            "currency": self.currency,
            "leverage": self.leverage,
            "trade_allowed": self.trade_allowed,
        }


@dataclass
class TerminalSnapshot:
    """Safe terminal summary — the terminal path is intentionally excluded."""

    accessible: bool
    name: str | None = None
    build: int | None = None
    broker_connected: bool | None = None
    trade_allowed: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accessible": self.accessible,
            "name": self.name,
            "build": self.build,
            "broker_connected": self.broker_connected,
            "trade_allowed": self.trade_allowed,
        }


@dataclass
class AdapterSnapshot:
    """Thread-safe, honest state of the adapter for status endpoints."""

    enabled: bool
    package_available: bool
    initialized: bool
    connected: bool
    package_version: str | None
    terminal: TerminalSnapshot | None
    account: AccountSnapshot | None
    last_success_at: str | None
    last_error: str | None
    last_error_at: str | None
    reconnect_attempts: int
    detail: str
    checked_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "package_available": self.package_available,
            "initialized": self.initialized,
            "connected": self.connected,
            "package_version": self.package_version,
            "terminal": self.terminal.to_dict() if self.terminal else None,
            "account": self.account.to_dict() if self.account else None,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
            "reconnect_attempts": self.reconnect_attempts,
            "detail": self.detail,
            "checked_at": self.checked_at,
        }


def _last_error_safe(mt5: Any) -> str:
    """Render mt5.last_error() without ever including credential material."""

    try:
        code, description = mt5.last_error()
        return f"MT5 last_error {code}: {description}"
    except Exception:  # noqa: BLE001 - diagnostics must never raise
        return "MT5 last_error unavailable"


class MT5Adapter:
    """Serialized, timeout-bounded wrapper around the MetaTrader5 package."""

    def __init__(
        self,
        settings: Settings,
        *,
        mt5_module: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._module_loader = (
            (lambda: mt5_module) if mt5_module is not None else load_mt5_module
        )
        self._sleep = sleep
        self._clock = clock
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mt5-terminal"
        )
        self._lock = threading.RLock()
        self._mt5: Any | None = None
        self._initialized = False
        self._shutting_down = False
        self._package_version: str | None = None
        self._terminal: TerminalSnapshot | None = None
        self._account: AccountSnapshot | None = None
        self._last_success_at: str | None = None
        self._last_error: str | None = None
        self._last_error_at: str | None = None
        self._reconnect_attempts = 0
        self._last_connect_failure_at: float | None = None
        self._connect_cooldown_seconds = 30.0
        _LIVE_ADAPTERS.add(self)
        _register_adapter_atexit()

    # ------------------------------------------------------------------ core
    @property
    def timeout_seconds(self) -> float:
        return self._settings.mt5_timeout_seconds

    def connect_budget_seconds(self) -> float:
        """Worst-case wall clock for a bounded (re)connect sequence."""

        base = self._settings.mt5_retry_backoff_seconds
        backoff_total = sum(
            min(base * (2**attempt), base * 8)
            for attempt in range(self._settings.mt5_retry_attempts)
        )
        return self._settings.mt5_timeout_seconds + backoff_total + 1.0

    async def run(
        self, fn: Callable[..., Any], *args: Any, timeout: float | None = None, **kwargs: Any
    ) -> Any:
        """Run a blocking adapter method on the dedicated MT5 thread."""

        loop = asyncio.get_running_loop()
        call = functools.partial(fn, *args, **kwargs)
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(self._executor, call),
                timeout=timeout if timeout is not None else self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            self._record_error(
                f"MT5 call timed out after {timeout or self.timeout_seconds:.1f}s."
            )
            # The worker thread may still be wedged: force a full reconnect on
            # the next call instead of trusting a half-dead session.
            with self._lock:
                self._initialized = False
            raise MT5TimeoutError(
                "The MetaTrader 5 terminal did not respond within the "
                f"configured timeout ({timeout or self.timeout_seconds:.1f}s). "
                "The next request will attempt a fresh connection."
            ) from None

    # ------------------------------------------------------------ state utils
    def _record_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message
            self._last_error_at = utc_now_iso()
        logger.warning("mt5_error", extra={"error": message})

    def _record_success(self) -> None:
        with self._lock:
            self._last_success_at = utc_now_iso()

    def _record_terminal(self, mt5: Any) -> TerminalSnapshot | None:
        try:
            info = mt5.terminal_info()
        except Exception as exc:  # noqa: BLE001
            self._record_error(f"terminal_info() failed: {exc}")
            return None
        if info is None:
            return None
        snapshot = TerminalSnapshot(
            accessible=True,
            name=getattr(info, "name", None),
            build=getattr(info, "build", None),
            broker_connected=bool(getattr(info, "connected", False)),
            trade_allowed=bool(getattr(info, "trade_allowed", False)),
        )
        with self._lock:
            self._terminal = snapshot
        return snapshot

    def _record_account(self, mt5: Any) -> AccountSnapshot | None:
        try:
            info = mt5.account_info()
        except Exception as exc:  # noqa: BLE001
            self._record_error(f"account_info() failed: {exc}")
            return None
        if info is None:
            with self._lock:
                self._account = AccountSnapshot(accessible=False)
            return self._account
        snapshot = AccountSnapshot(
            accessible=True,
            login=getattr(info, "login", None),
            server=getattr(info, "server", None),
            company=getattr(info, "company", None),
            currency=getattr(info, "currency", None),
            leverage=getattr(info, "leverage", None),
            trade_allowed=bool(getattr(info, "trade_allowed", False)),
        )
        with self._lock:
            self._account = snapshot
        return snapshot

    # ----------------------------------------------------------- connection
    def _initialize_once(self) -> bool:
        """Single initialize attempt. Returns True on success."""

        mt5 = self._mt5
        kwargs: dict[str, Any] = {}
        if self._settings.mt5_terminal_path:
            kwargs["path"] = self._settings.mt5_terminal_path
        if self._settings.mt5_credentials_configured:
            kwargs["login"] = self._settings.mt5_login
            kwargs["password"] = self._settings.mt5_password
            kwargs["server"] = self._settings.mt5_server
        try:
            ok = bool(mt5.initialize(**kwargs))
        except Exception as exc:  # noqa: BLE001
            self._record_error(f"initialize() raised: {exc}")
            return False
        if not ok:
            self._record_error(_last_error_safe(mt5))
            return False
        return True

    def _ensure_connected(self) -> None:
        """Initialize / reconnect with bounded retries. Runs on the MT5 thread."""

        if self._shutting_down:
            raise MT5ConnectionError("The MT5 adapter is shutting down.")
        with self._lock:
            if self._initialized:
                # Light liveness probe: a stale session dies silently, so
                # verify the terminal still answers before serving data.
                mt5 = self._mt5
                try:
                    terminal = mt5.terminal_info()
                except Exception as exc:  # noqa: BLE001
                    terminal = None
                    self._record_error(f"terminal_info() liveness failed: {exc}")
                if terminal is not None:
                    self._record_terminal(mt5)
                    return
                logger.warning("mt5_session_stale", extra={"detail": "reconnecting"})
                self._initialized = False
            # Cooldown: after a failed (re)connect, do not hammer the terminal
            # on every request — fail fast until the cooldown elapses.
            if self._last_connect_failure_at is not None:
                elapsed = self._clock() - self._last_connect_failure_at
                if elapsed < self._connect_cooldown_seconds:
                    remaining = int(self._connect_cooldown_seconds - elapsed)
                    raise MT5ConnectionError(
                        "Reconnect cooldown active: the last connection attempt "
                        f"failed {int(elapsed)}s ago. Retry in "
                        f"~{max(remaining, 1)}s. Last error: "
                        f"{self._last_error or 'unknown'}"
                    )

        module = self._module_loader()
        if module is None:
            raise MT5NotAvailableError(
                "The MetaTrader5 Python package is not installed on this host. "
                "Install it per README (Windows host with a running MT5 "
                "terminal) — the platform will keep reporting honest, empty "
                "market data until then."
            )
        with self._lock:
            if self._mt5 is None:
                self._mt5 = module
                try:
                    self._package_version = ".".join(
                        str(part) for part in module.version()
                    )
                except Exception:  # noqa: BLE001 - version is cosmetic
                    self._package_version = None

        base = self._settings.mt5_retry_backoff_seconds
        attempts = self._settings.mt5_retry_attempts
        last_error = "terminal initialization failed"
        for attempt in range(attempts):
            with self._lock:
                if self._shutting_down:
                    raise MT5ConnectionError("The MT5 adapter is shutting down.")
            if self._initialize_once():
                mt5 = self._mt5
                terminal = self._record_terminal(mt5)
                if terminal is None:
                    last_error = _last_error_safe(mt5) + " (terminal_info() is None)"
                    try:
                        mt5.shutdown()
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    self._record_account(mt5)
                    with self._lock:
                        self._initialized = True
                        self._last_connect_failure_at = None
                    self._record_success()
                    logger.info(
                        "mt5_connected",
                        extra={
                            "terminal": terminal.name,
                            "build": terminal.build,
                            "broker_connected": terminal.broker_connected,
                            "account_accessible": bool(
                                self._account and self._account.accessible
                            ),
                            "attempt": attempt + 1,
                        },
                    )
                    return
            else:
                last_error = self._last_error or last_error
            if attempt < attempts - 1:
                delay = min(base * (2**attempt), base * 8)
                logger.info(
                    "mt5_reconnect_retry",
                    extra={"attempt": attempt + 1, "delay_seconds": delay},
                )
                self._sleep(delay)
        with self._lock:
            self._reconnect_attempts += attempts
            self._last_connect_failure_at = self._clock()
        raise MT5ConnectionError(
            "Could not connect to a MetaTrader 5 terminal after "
            f"{attempts} attempt(s). Start the MT5 terminal, log in, and make "
            "sure 'Tools → Options → Expert Advisors → Allow automated "
            "trading' is not required for API access. Last error: "
            f"{last_error}"
        )

    # --------------------------------------------------------- public (sync,
    # called via run() from async code)
    def sync_check(self) -> dict[str, Any]:
        """Full connection check; updates and returns the safe snapshot."""

        if not self._settings.mt5_enabled:
            raise MT5DisabledError(
                "MT5 integration is disabled by configuration (MT5_ENABLED=false)."
            )
        self._ensure_connected()
        mt5 = self._mt5
        terminal = self._record_terminal(mt5)
        account = self._record_account(mt5)
        self._record_success()
        with self._lock:
            return {
                "package_version": self._package_version,
                "terminal": terminal.to_dict() if terminal else None,
                "account": account.to_dict() if account else None,
            }

    def sync_shutdown(self) -> None:
        """Release terminal resources cleanly. Idempotent, safe to call twice."""

        with self._lock:
            self._shutting_down = True
            mt5, was_initialized = self._mt5, self._initialized
            self._initialized = False
        if was_initialized and mt5 is not None:
            try:
                mt5.shutdown()
                logger.info("mt5_disconnected")
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                logger.warning("mt5_shutdown_failed", extra={"error": str(exc)})
        with self._lock:
            self._terminal = None
            self._account = None

    def sync_close(self) -> None:
        """sync_shutdown + stop the worker thread (application shutdown)."""

        self.sync_shutdown()
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------- data ops
    def sync_symbols(self, group: str | None = None) -> list[dict[str, Any]]:
        """Available symbols + metadata (safe fields only)."""

        self._ensure_connected()
        mt5 = self._mt5
        try:
            symbols = mt5.symbols_get(group=group) if group else mt5.symbols_get()
        except Exception as exc:  # noqa: BLE001
            raise MT5DataError(f"symbols_get() failed: {exc}") from exc
        self._record_success()
        if symbols is None:
            return []
        safe_fields = (
            "name", "description", "path", "visible", "selectable",
            "trade_mode", "digits", "point", "spread", "tick_value",
            "tick_size", "contract_size", "volume_min", "volume_max",
            "volume_step", "currency_profit", "currency_base",
        )
        out: list[dict[str, Any]] = []
        for row in symbols:
            item: dict[str, Any] = {}
            for field_name in safe_fields:
                try:
                    item[field_name] = _coerce_num(getattr(row, field_name, None))
                except Exception:  # noqa: BLE001
                    item[field_name] = None
            out.append(item)
        return out

    def sync_symbol_info(self, symbol: str) -> dict[str, Any]:
        """Metadata for one symbol; selects it in Market Watch if needed."""

        self._ensure_connected()
        mt5 = self._mt5
        info = self._symbol_info_or_select(mt5, symbol)
        self._record_success()
        safe_fields = (
            "name", "description", "visible", "selectable", "trade_mode",
            "digits", "point", "spread", "spread_float", "tick_value",
            "tick_size", "contract_size", "stops_level", "freeze_level",
            "volume_min", "volume_max", "volume_step", "currency_profit",
            "currency_base", "currency_margin", "filling_mode",
            "trade_calc_mode", "trade_exemode", "session_deals",
        )
        out: dict[str, Any] = {}
        for field_name in safe_fields:
            try:
                out[field_name] = _coerce_num(getattr(info, field_name, None))
            except Exception:  # noqa: BLE001
                out[field_name] = None
        out["chart_mode"] = getattr(info, "chart_mode", None)
        return out

    def _symbol_info_or_select(self, mt5: Any, symbol: str) -> Any:
        try:
            info = mt5.symbol_info(symbol)
        except Exception as exc:  # noqa: BLE001
            raise MT5DataError(f"symbol_info({symbol!r}) failed: {exc}") from exc
        if info is None:
            # Not in Market Watch yet — selecting can make it available.
            try:
                selected = bool(mt5.symbol_select(symbol, True))
            except Exception as exc:  # noqa: BLE001
                raise MT5SymbolError(
                    f"Symbol '{symbol}' could not be selected: {exc}"
                ) from exc
            if selected:
                try:
                    info = mt5.symbol_info(symbol)
                except Exception as exc:  # noqa: BLE001
                    raise MT5DataError(
                        f"symbol_info({symbol!r}) failed after select: {exc}"
                    ) from exc
        if info is None:
            raise MT5SymbolError(
                f"Symbol '{symbol}' is unknown to this terminal (or has no "
                "data in Market Watch). Use GET /api/v1/market/symbols to "
                "list available names."
            )
        return info

    def sync_candles(
        self, symbol: str, timeframe: str, count: int
    ) -> list[dict[str, Any]]:
        """OHLCV candles via copy_rates_from_pos (newest last).

        ``timeframe`` is the canonical platform code (M1/H1/...); the MT5
        constant is resolved here, AFTER a connection exists, so a package
        without the expected constants surfaces as a data error — never as a
        leaked ValueError.
        """

        self._ensure_connected()
        mt5 = self._mt5
        from app.domains.market_data.mt5_timeframes import resolve_mt5_timeframe
        from app.domains.market_data.timeframes import UnsupportedTimeframeError

        try:
            timeframe_value = resolve_mt5_timeframe(mt5, timeframe)
        except UnsupportedTimeframeError as exc:
            raise MT5DataError(
                f"The installed MetaTrader5 package cannot serve timeframe "
                f"{timeframe}: {exc}"
            ) from exc
        # A connected terminal still may not know the symbol — make sure it is
        # selectable before asking for rates.
        self._symbol_info_or_select(mt5, symbol)
        try:
            rates = mt5.copy_rates_from_pos(symbol, timeframe_value, 0, count)
        except Exception as exc:  # noqa: BLE001
            raise MT5DataError(
                f"copy_rates_from_pos({symbol!r}) failed: {exc}"
            ) from exc
        if rates is None:
            raise MT5DataError(
                f"The terminal returned no rate history for '{symbol}' "
                f"({_last_error_safe(mt5)}). The symbol may exist but have "
                "no data for this timeframe."
            )
        records = rates_to_dicts(rates)
        if not records:
            raise MT5DataError(
                f"The terminal returned an EMPTY candle response for "
                f"'{symbol}'. No data will be fabricated."
            )
        self._record_success()
        return records

    def sync_tick(self, symbol: str) -> dict[str, Any]:
        """Latest tick / bid-ask snapshot for one symbol."""

        self._ensure_connected()
        mt5 = self._mt5
        self._symbol_info_or_select(mt5, symbol)
        try:
            tick = mt5.symbol_info_tick(symbol)
        except Exception as exc:  # noqa: BLE001
            raise MT5DataError(f"symbol_info_tick({symbol!r}) failed: {exc}") from exc
        if tick is None:
            raise MT5DataError(
                f"No tick is available yet for '{symbol}' (the market may be "
                "closed or the symbol just selected). "
                f"{_last_error_safe(mt5)}"
            )
        self._record_success()
        return {
            "time": int(getattr(tick, "time", 0) or 0),
            "time_msc": int(getattr(tick, "time_msc", 0) or 0),
            "bid": _coerce_num(getattr(tick, "bid", None)),
            "ask": _coerce_num(getattr(tick, "ask", None)),
            "last": _coerce_num(getattr(tick, "last", None)),
            "volume": _coerce_num(getattr(tick, "volume", None)),
            "flags": getattr(tick, "flags", None),
        }

    # ------------------------------------------------------------- snapshot
    def snapshot(self, detail: str | None = None) -> AdapterSnapshot:
        """Cheap, lock-protected state copy. NEVER touches the terminal."""

        with self._lock:
            package_available = self._mt5 is not None or self._probe_package()
            return AdapterSnapshot(
                enabled=self._settings.mt5_enabled,
                package_available=package_available,
                initialized=self._initialized,
                connected=self._initialized and bool(
                    self._terminal and self._terminal.broker_connected
                ),
                package_version=self._package_version,
                terminal=self._terminal,
                account=self._account,
                last_success_at=self._last_success_at,
                last_error=self._last_error,
                last_error_at=self._last_error_at,
                reconnect_attempts=self._reconnect_attempts,
                detail=detail or self._current_detail(package_available),
                checked_at=utc_now_iso(),
            )

    def _probe_package(self) -> bool:
        try:
            import importlib.util  # noqa: PLC0415

            return importlib.util.find_spec("MetaTrader5") is not None
        except Exception:  # noqa: BLE001
            return False

    def _current_detail(self, package_available: bool) -> str:
        if not self._settings.mt5_enabled:
            return (
                "MT5 integration is disabled by configuration "
                "(MT5_ENABLED=false). Set MT5_ENABLED=true and restart to "
                "enable live market data."
            )
        if not package_available:
            return (
                "The MetaTrader5 Python package is not installed on this "
                "host. Live market data requires a Windows host (or Wine) "
                "with a running MetaTrader 5 terminal — see README "
                "('MetaTrader 5 integration'). Analysis can still run on "
                "user-supplied candles."
            )
        if self._initialized:
            return "Connected to the MetaTrader 5 terminal (cached state)."
        if self._last_error:
            return f"Not connected. Last error: {self._last_error}"
        return (
            "Not connected yet — the adapter connects lazily on the first "
            "data request or via GET /api/v1/mt5/status."
        )


# --------------------------------------------------------------------------- #
# Application-wide singleton (connection state must survive per-request
# provider instantiations)
# --------------------------------------------------------------------------- #
_adapter: MT5Adapter | None = None
_adapter_lock = threading.Lock()

# Executor threads are non-daemon: an adapter whose close() was never called
# would keep the interpreter alive forever. The WeakSet + atexit hook below
# guarantees every surviving adapter is closed at process exit.
_LIVE_ADAPTERS: weakref.WeakSet = weakref.WeakSet()
_atexit_registered = False


def _register_adapter_atexit() -> None:
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(_close_all_adapters)
        _atexit_registered = True


def _close_all_adapters() -> None:
    for adapter in list(_LIVE_ADAPTERS):
        try:
            adapter.sync_close()
        except Exception:  # noqa: BLE001 - interpreter is shutting down
            pass


def get_mt5_adapter(settings: Settings | None = None) -> MT5Adapter:
    global _adapter
    with _adapter_lock:
        if _adapter is None:
            _adapter = MT5Adapter(settings or _settings_for_adapter())
        return _adapter


def reset_mt5_adapter() -> None:
    """Dispose the singleton (tests / shutdown). Calls shutdown if connected."""

    global _adapter
    with _adapter_lock:
        if _adapter is not None:
            _adapter.sync_close()
        _adapter = None


def _settings_for_adapter() -> Settings:
    from app.core.config import get_settings

    return get_settings()
