"""Backtest domain (Phase 6): deterministic historical strategy evaluation.

Modules:

* ``schemas``    — validated configuration models (Pydantic) + API payloads.
* ``data``       — historical candle loading from the existing store +
                   honest data-quality assessment (never fills gaps).
* ``engine``     — deterministic chronological walk; point-in-time windows;
                   zero look-ahead; reuses the Phase 4 rule evaluator.
* ``forex``      — Forex simulation (spread/commission/slippage/SL/TP/
                   sizing/swap) with an explicit ambiguous-bar policy.
* ``binary``     — Binary Options simulation (expiry, payout, tie rules).
* ``metrics``    — metrics derived ONLY from actual simulated records;
                   missing data yields ``None`` ("Not available").
* ``service``    — persistence + owner-scoped queries.
* ``runner``     — minimal in-process asyncio execution with cooperative
                   cancellation and startup recovery (no external task queue).
* ``csv_export`` — CSV export with spreadsheet formula-injection protection.
"""

BACKTEST_ENGINE_VERSION = "6.0.0"

__all__ = ["BACKTEST_ENGINE_VERSION"]
