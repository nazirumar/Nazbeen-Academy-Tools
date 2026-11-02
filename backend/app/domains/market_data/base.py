"""Market-data provider abstraction.

Providers are selected by name via the MARKET_DATA_PROVIDER setting. A
provider that cannot supply data (e.g. MetaTrader 5 terminal not connected)
must raise ``MarketDataUnavailableError`` — the platform NEVER falls back to
synthetic data, because presenting fabricated prices as analysis input would
be actively dangerous for trading decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, TypedDict

from app.core.config import ConfigError, Settings, get_settings

# Canonical definition lives with the other API errors; re-exported here so
# provider modules have a single import site.
from app.core.errors import MarketDataUnavailableError  # noqa: F401

__all__ = [
    "Candle",
    "MarketDataProvider",
    "MarketDataUnavailableError",
    "ProviderStatus",
    "get_provider",
    "register_provider",
]


class Candle(TypedDict):
    """A single OHLCV candle. ``time`` is a unix timestamp in **seconds**."""

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float | None


@dataclass(frozen=True)
class ProviderStatus:
    """Honest, explainable provider availability report."""

    provider: str
    available: bool
    connected: bool
    detail: str
    checked_at: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat(
            timespec="milliseconds"
        )
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "available": self.available,
            "connected": self.connected,
            "detail": self.detail,
            "checked_at": self.checked_at,
        }


class MarketDataProvider(Protocol):
    """Contract every market-data backend must fulfil.

    Phase 2: ``get_candles`` returns a validated, explainable
    ``CandleSeries`` payload (schemas.py) — never a bare list, so status,
    provenance and warnings travel with the data.
    """

    name: str

    def status(self) -> ProviderStatus:
        """Report availability WITHOUT throwing (cheap, cached snapshot)."""
        ...

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        *,
        closed_only: bool = False,
    ) -> Any:
        """Fetch + validate candles or raise an AppError.

        Raises ``MarketDataUnavailableError`` when the backend is not
        connected; ``SymbolNotFoundError`` for unknown symbols;
        ``MarketDataError`` for unusable upstream responses.
        """
        ...


_REGISTRY: dict[str, type] = {}


def register_provider(provider_cls: type) -> type:
    _REGISTRY[provider_cls.name] = provider_cls
    return provider_cls


def get_provider(settings: Settings | None = None) -> Any:
    """Instantiate the configured provider. Unknown names fail fast."""

    settings = settings or get_settings()
    provider_name = settings.market_data_provider
    try:
        provider_cls = _REGISTRY[provider_name]
    except KeyError as exc:
        raise ConfigError(
            f"Unknown MARKET_DATA_PROVIDER '{provider_name}'. "
            f"Registered providers: {', '.join(sorted(_REGISTRY)) or 'none'}."
        ) from exc
    return provider_cls()
