"""Research domain (Phase 8): parameter optimization, walk-forward
validation and robustness testing over the EXISTING Phase 6 backtest
engine.

Modules:

* ``schemas``     — validated request/config models (Pydantic).
* ``params``      — parameter inventory + explicit patch application
                    (never invents new rules; revalidates every patch).
* ``grid``        — bounded deterministic search-space enumeration +
                    chronological walk-forward window generation.
* ``objectives``  — objective-function resolution, constraints, ranking,
                    metrics digests, OOS aggregation and the optional
                    seeded bootstrap.
* ``robustness``  — robustness scenarios (cost variants, parameter
                    neighbours, selection stability, bootstrap) built on
                    the same validated simulation services.
* ``runner``      — in-process asyncio execution (mirrors the Phase 6
                    BacktestRunner: semaphore, cooperative cancellation,
                    startup recovery, per-run DB session).
* ``service``     — validation cascade, persistence, owner-scoped queries.

Hard guarantees:

* Reuses ``backtest.engine.run_walk`` — no second backtesting engine.
* The original strategy version is never modified; candidates are
  parameter patches evaluated against the immutable rule-set snapshot.
* Walk-forward selection uses ONLY training-window data; test windows
  are held out (``train_end == test_start``, non-overlapping, purged).
* No automatic order execution, no fabricated market data, no
  guaranteed-profit claims, no automatic publishing of winners.
"""

RESEARCH_RUNNER_VERSION = "8.0.0"

__all__ = ["RESEARCH_RUNNER_VERSION"]
