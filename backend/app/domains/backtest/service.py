"""Backtest persistence + owner-scoped queries.

Every query is scoped by ``owner_id`` (from the ``X-User-Id`` header —
foreign owners get 404, no existence leak). Run creation performs the full
validation cascade BEFORE anything is queued: strategy version exists and
is immutable (published/superseded), modes agree, timeframe is declared by
the strategy, symbol is in scope, and the stored candle cache covers the
requested window. A run that cannot be validated is never created.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.db.models import BacktestResult, BacktestRun
from app.domains.backtest import BACKTEST_ENGINE_VERSION
from app.domains.backtest.data import verify_coverage
from app.domains.backtest.schemas import BacktestCreateRequest, BacktestConfigPayload
from app.domains.market_data.timeframes import timeframe_seconds
from app.domains.strategies import service as strategy_service
from app.domains.strategies.rules import validate_rule_set

_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 100
_RESULT_BATCH_SIZE = 500


# --------------------------------------------------------------------------- #
# Creation + validation cascade
# --------------------------------------------------------------------------- #


def _resolve_timeframe(
    requested: str | None, rule_set_timeframes: tuple[str, ...]
) -> str:
    """Default = the strategy's finest timeframe (never invented)."""

    if requested is not None:
        if requested not in rule_set_timeframes:
            raise ValidationAppError(
                f"Timeframe '{requested}' is not declared by the selected "
                f"strategy version (declared: {', '.join(rule_set_timeframes)}).",
                code="TIMEFRAME_NOT_DECLARED",
            )
        return requested
    return min(rule_set_timeframes, key=timeframe_seconds)


async def validate_and_create(
    session: AsyncSession,
    owner_id: str,
    settings: Settings,
    request: BacktestCreateRequest,
) -> BacktestRun:
    """Validate everything, then persist the run as ``queued``."""

    version_row = await strategy_service.get_version(
        session, owner_id, request.strategy_id, request.strategy_version
    )
    strategy = await strategy_service.get_strategy(
        session, owner_id, request.strategy_id
    )
    if version_row.status == "draft":
        raise ValidationAppError(
            f"Version {request.strategy_version} is a draft. Publish it (or "
            "select a published/superseded version) before backtesting — "
            "drafts can still be replaced, so their lifecycle is not "
            "immutable.",
            code="DRAFT_VERSION_NOT_BACKTESTABLE",
        )
    if strategy.mode != request.mode:
        raise ValidationAppError(
            f"Strategy mode '{strategy.mode}' does not match the requested "
            f"mode '{request.mode}'.",
            code="MODE_MISMATCH",
        )
    try:
        rule_set = validate_rule_set(version_row.rule_set)
    except Exception as exc:
        raise ValidationAppError(
            "The stored rule set failed revalidation and cannot be backtested.",
            code="RULE_SET_INVALID",
        ) from exc
    if rule_set.mode != request.mode:
        raise ValidationAppError(
            f"Rule set mode '{rule_set.mode}' does not match the requested "
            f"mode '{request.mode}'.",
            code="MODE_MISMATCH",
        )
    symbol = request.symbol.strip().upper()
    if rule_set.symbols and symbol not in rule_set.symbols:
        raise ValidationAppError(
            f"Symbol '{symbol}' is outside the strategy's symbol scope "
            f"({', '.join(rule_set.symbols)}).",
            code="SYMBOL_NOT_IN_SCOPE",
        )
    timeframe = _resolve_timeframe(request.timeframe, rule_set.timeframes)

    if request.mode == "forex" and request.forex_execution is not None:
        if not request.forex_execution.enabled and request.binary is not None:
            raise ValidationAppError(
                "binary configuration cannot be combined with a Forex run.",
                code="CONFIG_MISMATCH",
            )
    if request.mode == "binary_options" and request.forex_execution is not None:
        raise ValidationAppError(
            "forex_execution configuration cannot be combined with a Binary "
            "Options run.",
            code="CONFIG_MISMATCH",
        )
    if request.binary is not None and request.mode == "forex":
        raise ValidationAppError(
            "binary configuration cannot be combined with a Forex run.",
            code="CONFIG_MISMATCH",
        )

    simulation_mode = "setup_only"
    if request.mode == "forex":
        if request.forex_execution is not None and request.forex_execution.enabled:
            simulation_mode = "trade_simulation"
    else:
        simulation_mode = "trade_simulation"

    # ---- historical data availability (before anything is queued) ----------
    start = request.start.astimezone(timezone.utc)
    end = request.end.astimezone(timezone.utc)
    coverage = await verify_coverage(
        session,
        symbol=symbol,
        primary_timeframe=timeframe,
        start=start,
        end=end,
        max_candles=settings.backtest_max_candles,
    )

    config_payload = BacktestConfigPayload(
        mode=request.mode,
        symbol=symbol,
        timeframe=timeframe,
        start=start.isoformat(timespec="milliseconds"),
        end=end.isoformat(timespec="milliseconds"),
        warmup_candles=settings.backtest_warmup_candles,
        max_candles=settings.backtest_max_candles,
        data_source="postgres_candle_cache",
        strategy_id=str(request.strategy_id),
        strategy_name=strategy.name,
        strategy_version=version_row.version,
        strategy_version_status=version_row.status,
        rule_set=rule_set.to_dict(),
        direction=rule_set.direction,
        notes=request.notes,
        forex_execution=(
            request.forex_execution.model_dump() if request.forex_execution else None
        ),
        binary=request.binary.model_dump() if request.binary else None,
        simulation_mode=simulation_mode,
    )

    run = BacktestRun(
        owner_id=owner_id,
        mode=request.mode,
        symbol=symbol,
        timeframe=timeframe,
        strategy_id=request.strategy_id,
        strategy_name=strategy.name,
        strategy_version=version_row.version,
        strategy_version_id=version_row.id,
        status="queued",
        start_time=start,
        end_time=end,
        config=config_payload.model_dump(),
        backtest_schema_version="1.0",
        engine_version=BACKTEST_ENGINE_VERSION,
        data_quality={"pre_run_coverage": coverage},
    )
    session.add(run)
    await session.flush()
    return run


# --------------------------------------------------------------------------- #
# Lifecycle transitions (runner-facing)
# --------------------------------------------------------------------------- #


async def mark_running(session: AsyncSession, run_id: uuid.UUID) -> BacktestRun:
    run = await session.get(BacktestRun, run_id)
    if run is None:
        raise NotFoundError(f"Backtest run '{run_id}' was not found.")
    if run.status != "queued":
        raise ConflictError(
            f"Run is already '{run.status}'; it cannot be started again.",
            code="INVALID_RUN_TRANSITION",
        )
    run.status = "running"
    run.started_at = datetime.now(tz=timezone.utc)
    await session.flush()
    return run


async def persist_results(
    session: AsyncSession, run: BacktestRun, output
) -> None:
    """Insert chronological result records in transactional batches."""

    from app.db.models.backtest import BACKTEST_SCHEMA_VERSION as SCHEMA_V

    owner_id = run.owner_id
    for start in range(0, len(output.records), _RESULT_BATCH_SIZE):
        batch = output.records[start : start + _RESULT_BATCH_SIZE]
        rows = []
        for record in batch:
            rows.append(
                BacktestResult(
                    run_id=run.id,
                    owner_id=owner_id,
                    seq=record["seq"],
                    kind=record["kind"],
                    result_time=datetime.fromtimestamp(
                        record["result_time"], tz=timezone.utc
                    ),
                    setup_status=record.get("setup_status"),
                    direction=record.get("direction"),
                    regime=record.get("regime"),
                    outcome=record.get("outcome"),
                    pnl=record.get("pnl"),
                    return_pct=record.get("return_pct"),
                    equity_after=record.get("equity_after"),
                    evaluation=record.get("evaluation"),
                    execution=record.get("execution"),
                    warnings=record.get("warnings") or None,
                    note=record.get("note"),
                )
            )
        session.add_all(rows)
        await session.flush()
    run.candles_evaluated = output.candles_evaluated
    run.records_total = len(output.records)
    run.data_quality = {**(run.data_quality or {}), **output.data_quality}
    run.metrics = output.metrics
    run.backtest_schema_version = run.backtest_schema_version or SCHEMA_V
    await session.flush()


async def mark_completed(session: AsyncSession, run: BacktestRun) -> None:
    run.status = "completed"
    run.completed_at = datetime.now(tz=timezone.utc)
    await session.flush()


async def mark_failed(
    session: AsyncSession,
    run_id: uuid.UUID,
    error: dict[str, Any],
) -> None:
    run = await session.get(BacktestRun, run_id)
    if run is None:  # pragma: no cover - defensive
        return
    run.status = "failed"
    run.error = error
    run.completed_at = datetime.now(tz=timezone.utc)
    await session.flush()


async def mark_cancelled(session: AsyncSession, run_id: uuid.UUID) -> None:
    run = await session.get(BacktestRun, run_id)
    if run is None:  # pragma: no cover - defensive
        return
    run.status = "cancelled"
    run.completed_at = datetime.now(tz=timezone.utc)
    run.error = {
        "code": "CANCELLED",
        "message": "The run was cancelled by its owner before completion.",
        "details": [],
    }
    await session.flush()


async def recover_interrupted(session: AsyncSession) -> int:
    """Startup recovery: runs left queued/running by a previous process are
    marked failed with an explicit reason (the platform never pretends a
    vanished process completed its work)."""

    stmt = select(BacktestRun).where(
        BacktestRun.status.in_(("queued", "running")),
        BacktestRun.owner_id.isnot(None),
    )
    rows = list((await session.scalars(stmt)).all())
    for run in rows:
        run.status = "failed"
        run.error = {
            "code": "RUNNER_RESTARTED",
            "message": "The backend process restarted while this run was "
            "queued/running; in-process runs do not survive restarts. "
            "Create a new run — the configuration below is retained.",
            "details": [],
        }
        run.completed_at = datetime.now(tz=timezone.utc)
    if rows:
        await session.flush()
    return len(rows)


# --------------------------------------------------------------------------- #
# Owner-scoped reads
# --------------------------------------------------------------------------- #


async def get_run(session: AsyncSession, owner_id: str, run_id: uuid.UUID) -> BacktestRun:
    run = await session.get(BacktestRun, run_id)
    if run is None or run.owner_id != owner_id:
        raise NotFoundError(f"Backtest run '{run_id}' was not found.")
    return run


def run_summary(run: BacktestRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "owner_id": run.owner_id,
        "mode": run.mode,
        "symbol": run.symbol,
        "timeframe": run.timeframe,
        "strategy_id": str(run.strategy_id) if run.strategy_id else None,
        "strategy_name": run.strategy_name,
        "strategy_version": run.strategy_version,
        "strategy_version_id": (
            str(run.strategy_version_id) if run.strategy_version_id else None
        ),
        "status": run.status,
        "start_time": run.start_time.isoformat(timespec="milliseconds"),
        "end_time": run.end_time.isoformat(timespec="milliseconds"),
        "simulation_mode": (run.config or {}).get("simulation_mode"),
        "candles_evaluated": run.candles_evaluated,
        "records_total": run.records_total,
        "engine_version": run.engine_version,
        "backtest_schema_version": run.backtest_schema_version,
        "error": run.error,
        "metrics": run.metrics,
        "data_quality": run.data_quality,
        "config": run.config,
        "created_at": run.created_at.isoformat(timespec="milliseconds"),
        "started_at": run.started_at.isoformat(timespec="milliseconds")
        if run.started_at
        else None,
        "completed_at": run.completed_at.isoformat(timespec="milliseconds")
        if run.completed_at
        else None,
    }


async def list_runs(
    session: AsyncSession,
    owner_id: str,
    *,
    mode: str | None = None,
    strategy_id: uuid.UUID | None = None,
    status: str | None = None,
    symbol: str | None = None,
    page: int = 1,
    page_size: int = _PAGE_SIZE_DEFAULT,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    page = max(1, page)
    page_size = min(max(1, page_size), _PAGE_SIZE_MAX)
    stmt = select(BacktestRun).where(BacktestRun.owner_id == owner_id)
    if mode:
        stmt = stmt.where(BacktestRun.mode == mode)
    if strategy_id is not None:
        stmt = stmt.where(BacktestRun.strategy_id == strategy_id)
    if status:
        stmt = stmt.where(BacktestRun.status == status)
    if symbol:
        stmt = stmt.where(BacktestRun.symbol == symbol.upper())
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(
        (
            await session.scalars(
                stmt.order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    total_int = int(total or 0)
    pages = (total_int + page_size - 1) // page_size if page_size else 1
    light = [
        {
            "id": str(row.id),
            "mode": row.mode,
            "symbol": row.symbol,
            "timeframe": row.timeframe,
            "strategy_id": str(row.strategy_id) if row.strategy_id else None,
            "strategy_name": row.strategy_name,
            "strategy_version": row.strategy_version,
            "status": row.status,
            "simulation_mode": (row.config or {}).get("simulation_mode"),
            "start_time": row.start_time.isoformat(timespec="milliseconds"),
            "end_time": row.end_time.isoformat(timespec="milliseconds"),
            "candles_evaluated": row.candles_evaluated,
            "records_total": row.records_total,
            "win_rate": ((row.metrics or {}).get("trades") or {}).get("win_rate")
            if row.metrics
            else None,
            "net_pnl": ((row.metrics or {}).get("trades") or {}).get("net_pnl")
            if row.metrics
            else None,
            "conditions_met": ((row.metrics or {}).get("setups") or {}).get(
                "by_status", {}
            ).get("conditions_met")
            if row.metrics
            else None,
            "error": {"code": row.error.get("code")} if row.error else None,
            "created_at": row.created_at.isoformat(timespec="milliseconds"),
            "completed_at": row.completed_at.isoformat(timespec="milliseconds")
            if row.completed_at
            else None,
        }
        for row in rows
    ]
    return light, {"page": page, "page_size": page_size, "total": total_int, "pages": max(1, pages)}


def result_row_dict(row: BacktestResult) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "run_id": str(row.run_id),
        "seq": row.seq,
        "kind": row.kind,
        "result_time": row.result_time.isoformat(timespec="milliseconds"),
        "result_time_unix": int(row.result_time.timestamp()),
        "setup_status": row.setup_status,
        "direction": row.direction,
        "regime": row.regime,
        "outcome": row.outcome,
        "pnl": row.pnl,
        "return_pct": row.return_pct,
        "equity_after": row.equity_after,
        "evaluation": row.evaluation,
        "execution": row.execution,
        "warnings": row.warnings or [],
        "note": row.note,
    }


def result_summary_dict(row: BacktestResult) -> dict[str, Any]:
    """Light projection for paginated table listings."""

    evaluation = row.evaluation or {}
    execution = row.execution or {}
    return {
        "id": str(row.id),
        "run_id": str(row.run_id),
        "seq": row.seq,
        "kind": row.kind,
        "result_time": row.result_time.isoformat(timespec="milliseconds"),
        "result_time_unix": int(row.result_time.timestamp()),
        "setup_status": row.setup_status,
        "direction": row.direction,
        "regime": row.regime,
        "outcome": row.outcome,
        "pnl": row.pnl,
        "return_pct": row.return_pct,
        "equity_after": row.equity_after,
        "conditions_summary": evaluation.get("conditions_summary"),
        "execution_summary": {
            key: execution.get(key)
            for key in (
                "mode",
                "side",
                "direction",
                "entry_time",
                "entry_price",
                "expiry_time",
                "expiry_reference_price",
                "exit_time",
                "exit_price",
                "exit_reason",
                "lots",
                "stake",
                "pnl",
                "equity_after",
            )
            if key in execution
        }
        or None,
        "warning_count": len(row.warnings or []),
        "note": (row.note[:280] if row.note else None),
    }


async def list_results(
    session: AsyncSession,
    owner_id: str,
    run_id: uuid.UUID,
    *,
    kind: str | None = None,
    setup_status: str | None = None,
    outcome: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    await get_run(session, owner_id, run_id)  # ownership gate
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    stmt = select(BacktestResult).where(
        BacktestResult.run_id == run_id, BacktestResult.owner_id == owner_id
    )
    if kind:
        stmt = stmt.where(BacktestResult.kind == kind)
    if setup_status:
        stmt = stmt.where(BacktestResult.setup_status == setup_status)
    if outcome:
        stmt = stmt.where(BacktestResult.outcome == outcome)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(
        (
            await session.scalars(
                stmt.order_by(BacktestResult.seq.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    total_int = int(total or 0)
    pages = (total_int + page_size - 1) // page_size if page_size else 1
    return (
        [result_summary_dict(row) for row in rows],
        {"page": page, "page_size": page_size, "total": total_int, "pages": max(1, pages)},
    )


async def get_result(
    session: AsyncSession, owner_id: str, run_id: uuid.UUID, seq: int
) -> dict[str, Any]:
    await get_run(session, owner_id, run_id)
    stmt = (
        select(BacktestResult)
        .where(
            BacktestResult.run_id == run_id,
            BacktestResult.owner_id == owner_id,
            BacktestResult.seq == seq,
        )
        .limit(1)
    )
    row = (await session.scalars(stmt)).first()
    if row is None:
        raise NotFoundError(
            f"Result {seq} was not found in run '{run_id}'.", code="RESULT_NOT_FOUND"
        )
    return result_row_dict(row)


async def equity_series(
    session: AsyncSession, owner_id: str, run_id: uuid.UUID
) -> dict[str, Any]:
    """Chronological realized-equity path (closed-trade balance points)."""

    run = await get_run(session, owner_id, run_id)
    stmt = (
        select(BacktestResult)
        .where(
            BacktestResult.run_id == run_id,
            BacktestResult.owner_id == owner_id,
            BacktestResult.equity_after.isnot(None),
        )
        .order_by(BacktestResult.seq.asc())
    )
    rows = list((await session.scalars(stmt)).all())
    points = [
        {
            "seq": row.seq,
            "time": row.result_time.isoformat(timespec="milliseconds"),
            "time_unix": int(row.result_time.timestamp()),
            "equity": row.equity_after,
            "pnl": row.pnl,
            "outcome": row.outcome,
        }
        for row in rows
    ]
    return {
        "run_id": str(run_id),
        "simulation_mode": (run.config or {}).get("simulation_mode"),
        "points": points,
        "note": (
            "Realized closed-trade balance path; open positions are not "
            "marked to market."
            if points
            else "No equity series exists: the run produced no settled "
            "trades (setup-only runs never have one)."
        ),
    }


async def export_rows(
    session: AsyncSession, owner_id: str, run_id: uuid.UUID
) -> tuple[BacktestRun, list[dict[str, Any]]]:
    run = await get_run(session, owner_id, run_id)
    stmt = (
        select(BacktestResult)
        .where(
            BacktestResult.run_id == run_id, BacktestResult.owner_id == owner_id
        )
        .order_by(BacktestResult.seq.asc())
    )
    rows = list((await session.scalars(stmt)).all())
    return run, [result_row_dict(row) for row in rows]


__all__ = [
    "BACKTEST_ENGINE_VERSION",
    "equity_series",
    "export_rows",
    "get_result",
    "get_run",
    "list_results",
    "list_runs",
    "mark_cancelled",
    "mark_completed",
    "mark_failed",
    "mark_running",
    "persist_results",
    "recover_interrupted",
    "run_summary",
    "validate_and_create",
]
