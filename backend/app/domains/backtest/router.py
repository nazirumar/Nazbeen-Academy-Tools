"""Backtest HTTP endpoints (Phase 6).

* ``POST   /backtests``                      — validate + queue a run.
* ``GET    /backtests``                      — paginated run history (owner-scoped).
* ``GET    /backtests/data-coverage``        — pre-run candle availability check.
* ``GET    /backtests/{run_id}``             — configuration, status, metrics.
* ``GET    /backtests/{run_id}/results``     — paginated chronological records.
* ``GET    /backtests/{run_id}/results/{seq}`` — one record (full evaluation tree).
* ``GET    /backtests/{run_id}/equity``      — realized equity path (when it exists).
* ``POST   /backtests/{run_id}/cancel``      — cooperative cancellation.
* ``GET    /backtests/{run_id}/export``      — CSV export (results | summary).

All endpoints are owner-scoped (``X-User-Id``); foreign runs are 404s.
Responses follow the platform envelope; errors use the shared error model.
No endpoint fabricates data, and none places orders — ever.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.ownership import current_owner
from app.core.responses import created, ok
from app.db.session import get_db_session
from app.domains.backtest import csv_export
from app.domains.backtest import service as backtest_service
from app.domains.backtest.data import verify_coverage
from app.domains.backtest.runner import get_runner
from app.domains.backtest.schemas import BacktestCreateRequest
from app.domains.market_data.timeframes import is_timeframe_supported

router = APIRouter(prefix="/backtests", tags=["backtests"])


@router.get("/data-coverage")
async def data_coverage(
    symbol: str = Query(min_length=1, max_length=40),
    timeframe: str = Query(min_length=2, max_length=8),
    start: datetime = Query(),
    end: datetime = Query(),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Pre-run availability check for the configuration panel.

    Reports how many candles exist for the window (never fabricates data
    availability). Fails 422 when the window is empty or too large —
    exactly like run creation.
    """

    if not is_timeframe_supported(timeframe):
        return _validation_error("Unsupported timeframe.", "UNSUPPORTED_TIMEFRAME")
    if start.tzinfo is None or end.tzinfo is None:
        return _validation_error(
            "start/end must be timezone-aware ISO-8601 timestamps.",
            "INVALID_TIMEWINDOW",
        )
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    if start >= end:
        return _validation_error("start must be earlier than end.", "INVALID_TIMEWINDOW")
    coverage = await verify_coverage(
        session,
        symbol=symbol.strip().upper(),
        primary_timeframe=timeframe,
        start=start,
        end=end,
        max_candles=settings.backtest_max_candles,
    )
    return ok(coverage)


def _validation_error(message: str, code: str) -> dict:
    from app.core.errors import ValidationAppError

    raise ValidationAppError(message, code=code)


@router.post("")
async def create_backtest(
    payload: BacktestCreateRequest,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Validate a backtest configuration and queue the run.

    Validation is complete BEFORE the run is created: strategy version must
    exist and be immutable (published/superseded), modes must agree, the
    timeframe must be declared by the strategy, the symbol must be in
    scope, and the stored candle cache must cover the window. The run is
    then executed by the in-process runner (status: queued -> running ->
    completed | failed | cancelled) — the platform's documented, minimal
    execution architecture.
    """

    run = await backtest_service.validate_and_create(session, owner, settings, payload)
    # Commit before scheduling so the runner (own session) sees the row.
    await session.commit()
    get_runner().submit(run.id, run.config)
    run_dict = backtest_service.run_summary(run)
    return created(run_dict)


@router.get("")
async def list_backtests(
    mode: str | None = Query(default=None, pattern="^(forex|binary_options)$"),
    strategy_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(
        default=None,
        pattern="^(queued|running|completed|failed|cancelled)$",
    ),
    symbol: str | None = Query(default=None, max_length=40),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Paginated run history for the requesting owner (newest first)."""

    items, page_meta = await backtest_service.list_runs(
        session,
        owner,
        mode=mode,
        strategy_id=strategy_id,
        status=status,
        symbol=symbol,
        page=page,
        page_size=page_size,
    )
    return ok({"items": items, "page": page_meta})


@router.get("/{run_id}")
async def get_backtest(
    run_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Run configuration, lifecycle status and aggregate metrics."""

    run = await backtest_service.get_run(session, owner, run_id)
    return ok(backtest_service.run_summary(run))


@router.get("/{run_id}/results")
async def list_backtest_results(
    run_id: uuid.UUID,
    kind: str | None = Query(default=None, pattern="^(setup|trade)$"),
    setup_status: str | None = Query(default=None, max_length=24),
    outcome: str | None = Query(default=None, max_length=16),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Paginated chronological records (ascending by sequence number)."""

    items, page_meta = await backtest_service.list_results(
        session,
        owner,
        run_id,
        kind=kind,
        setup_status=setup_status,
        outcome=outcome,
        page=page,
        page_size=page_size,
    )
    return ok({"items": items, "page": page_meta})


@router.get("/{run_id}/results/{seq}")
async def get_backtest_result(
    run_id: uuid.UUID,
    seq: int,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """One record with its FULL explainable evaluation tree."""

    return ok(await backtest_service.get_result(session, owner, run_id, seq))


@router.get("/{run_id}/equity")
async def backtest_equity(
    run_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Realized equity path (closed-trade balances) when the run has one."""

    return ok(await backtest_service.equity_series(session, owner, run_id))


@router.post("/{run_id}/cancel")
async def cancel_backtest(
    run_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Request cooperative cancellation of a queued/running run.

    The engine honours the request at the next evaluation boundary. A run
    already finished (or not tracked by this process) answers 409 — the
    stored status stays truthful.
    """

    run = await backtest_service.get_run(session, owner, run_id)
    if run.status in ("completed", "failed", "cancelled"):
        from app.core.errors import ConflictError

        raise ConflictError(
            f"Run is already '{run.status}' and cannot be cancelled.",
            code="RUN_ALREADY_FINISHED",
        )
    runner = get_runner()
    tracked = runner.is_tracked(run_id) and runner.cancel(run_id)
    summary = backtest_service.run_summary(run)
    return ok(
        {
            "run": summary,
            "cancel_requested": bool(tracked),
            "note": (
                "Cancellation is cooperative: the engine stops at the next "
                "evaluation boundary and the run is stored as 'cancelled'."
                if tracked
                else "The run is not executing in this process (it may "
                "already have finished or the backend restarted); its "
                "stored status is authoritative."
            ),
        }
    )


@router.get("/{run_id}/export")
async def export_backtest(
    run_id: uuid.UUID,
    scope: str = Query(default="results", pattern="^(results|summary)$"),
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """CSV export (results or summary) with formula-injection protection.

    The export leads with a declared assumptions block (strategy version,
    window, data source, simulation assumptions) and an explicit
    disclaimer: these are simulations over stored MT5 candles — not broker
    execution records and not performance guarantees.
    """

    run, records = await backtest_service.export_rows(session, owner, run_id)
    payload = backtest_service.run_summary(run)
    payload["config"] = run.config or {}
    payload["exported_at"] = datetime.now(tz=timezone.utc).isoformat(
        timespec="milliseconds"
    )
    if scope == "summary":
        text = csv_export.summary_csv(payload)
        filename = f"backtest-{run_id}-summary.csv"
    else:
        text = csv_export.results_csv(payload, records)
        filename = f"backtest-{run_id}-results.csv"
    return Response(
        content=text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


__all__ = ["router"]
