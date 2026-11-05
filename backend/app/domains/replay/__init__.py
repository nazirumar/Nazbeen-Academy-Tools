"""Historical market replay domain (Phase 11).

The replay engine is a deterministic cursor over stored, closed candles.
It shares the platform's data-quality assessment and never exposes a candle
whose close time is after the simulated replay time. Simulated order
execution is intentionally not part of this first vertical slice; the
existing Phase 6 execution modules remain the single source of trade
accounting for backtests.
"""

from __future__ import annotations

REPLAY_ENGINE_VERSION = "1.0.0"

__all__ = ["REPLAY_ENGINE_VERSION"]
