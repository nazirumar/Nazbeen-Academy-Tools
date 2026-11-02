"""Minimal in-process backtest runner (asyncio, no external task queue).

Architecture note (honest by design): this platform is a single-process
FastAPI application. Runs execute as ``asyncio.Task`` objects in the API
process, guarded by a semaphore (BACKTEST_MAX_CONCURRENT). Consequences —
all of them documented rather than hidden:

* The queue/running/completed/failed/cancelled lifecycle is REAL, but
  scoped to this process: a restart marks stale queued/running rows
  ``failed`` with code ``RUNNER_RESTARTED`` (startup recovery).
* Cancellation is COOPERATIVE: the engine checks a cancel event between
  evaluation steps, so a cancel takes effect at the next candle boundary
  (bounded work per step keeps this responsive).
* Each execution owns its DB session (never the request's session) and
  commits results in batches.

No fabricated background-job infrastructure is introduced; no automatic
trading happens anywhere in this module.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.core.logging import get_logger
from app.db.models import BacktestRun
from app.db.session import get_session_factory
from app.domains.backtest.engine import CancelledError, run_backtest
from app.domains.backtest.schemas import BacktestConfigPayload
from app.domains.backtest import service as backtest_service

logger = get_logger(__name__)


class BacktestRunner:
    """Owns execution tasks + cancel events for in-process runs."""

    def __init__(self, max_concurrent: int = 1) -> None:
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}
        self._cancel_events: dict[uuid.UUID, asyncio.Event] = {}

    # ---- submission -------------------------------------------------------

    def submit(self, run_id: uuid.UUID, config: dict[str, Any]) -> None:
        """Schedule execution of a queued run (called after commit)."""

        if run_id in self._tasks:
            return
        event = asyncio.Event()
        self._cancel_events[run_id] = event
        task = asyncio.create_task(self._execute(run_id, config, event))
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._cleanup(run_id))

    def cancel(self, run_id: uuid.UUID) -> bool:
        """Request cooperative cancellation. Returns False when the run is
        not tracked (already finished or not started in this process)."""

        event = self._cancel_events.get(run_id)
        if event is None:
            return False
        event.set()
        return True

    def is_tracked(self, run_id: uuid.UUID) -> bool:
        return run_id in self._tasks

    # ---- execution --------------------------------------------------------

    def _cleanup(self, run_id: uuid.UUID) -> None:
        self._tasks.pop(run_id, None)
        self._cancel_events.pop(run_id, None)

    async def _execute(
        self,
        run_id: uuid.UUID,
        config_payload: dict[str, Any],
        cancel_event: asyncio.Event,
    ) -> None:
        session_factory = get_session_factory()
        async with self._semaphore:
            async with session_factory() as session:
                try:
                    run = await backtest_service.mark_running(session, run_id)
                    await session.commit()

                    def _check() -> None:
                        if cancel_event.is_set():
                            raise CancelledError()

                    config = BacktestConfigPayload.model_validate(config_payload)
                    output = await run_backtest(
                        session, _settings_for(session), config=config, cancel_check=_check
                    )
                    await backtest_service.persist_results(session, run, output)
                    await backtest_service.mark_completed(session, run)
                    await session.commit()
                    logger.info(
                        "backtest_completed",
                        extra={
                            "run_id": str(run_id),
                            "records": output.candles_evaluated,
                        },
                    )
                except CancelledError:
                    await session.rollback()
                    await backtest_service.mark_cancelled(session, run_id)
                    await session.commit()
                    logger.info("backtest_cancelled", extra={"run_id": str(run_id)})
                except asyncio.CancelledError:
                    # Interpreter shutdown / task teardown: persist honest state.
                    await session.rollback()
                    await backtest_service.mark_failed(
                        session,
                        run_id,
                        {
                            "code": "RUN_CANCELLED_PROCESS",
                            "message": "The run was terminated because the "
                            "backend process is shutting down.",
                            "details": [],
                        },
                    )
                    await session.commit()
                    raise
                except Exception as exc:  # noqa: BLE001 - every failure is recorded
                    await session.rollback()
                    await backtest_service.mark_failed(
                        session,
                        run_id,
                        {
                            "code": getattr(exc, "code", None) or "BACKTEST_FAILED",
                            "message": str(exc),
                            "details": getattr(exc, "details", []) or [],
                        },
                    )
                    await session.commit()
                    logger.warning(
                        "backtest_failed",
                        extra={
                            "run_id": str(run_id),
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )


_runner: BacktestRunner | None = None
_runner_loop: asyncio.AbstractEventLoop | None = None


def _settings_for(session) -> Any:  # pragma: no cover - settings are cached
    from app.core.config import get_settings

    return get_settings()


def get_runner() -> BacktestRunner:
    """Process-wide runner, scoped to the RUNNING event loop.

    Phase 8 fix: asyncio primitives bind to their loop on first contended
    use, so a bare module singleton reused across event loops (e.g. test
    loops, or uvicorn reload) would crash with "bound to a different
    event loop" and strand queued runs. Recreating per loop is cheap and
    safe: cancel events of finished runs are irrelevant and startup
    recovery already handles rows stranded by process restarts.
    """

    global _runner, _runner_loop
    loop = asyncio.get_running_loop()
    if _runner is None or _runner_loop is not loop:
        from app.core.config import get_settings

        _runner = BacktestRunner(max_concurrent=get_settings().backtest_max_concurrent)
        _runner_loop = loop
    return _runner


def reset_runner() -> None:
    """Test hook: drop the singleton (settings may have changed)."""

    global _runner, _runner_loop
    _runner = None
    _runner_loop = None


__all__ = ["BacktestRunner", "get_runner", "reset_runner"]
