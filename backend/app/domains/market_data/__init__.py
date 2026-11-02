"""Market data domain: provider abstraction, timeframes, mode profiles.

Phase 1 ships the *abstraction* and honest availability reporting. The
MetaTrader 5 integration itself is Phase 2 — until then, every data request
fails with a clear "unavailable" error instead of fabricating data.

Importing this package registers all built-in providers in the registry
(see ``base.register_provider``).
"""

# Provider self-registration happens at import time.
from app.domains.market_data import mt5_provider  # noqa: F401  (registration)

__all__ = ["mt5_provider"]
