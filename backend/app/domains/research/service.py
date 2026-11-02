"""Research persistence + owner-scoped queries (Phase 8).

Creation performs the FULL validation cascade BEFORE anything is queued
(mirroring the Phase 6 backtest service): strategy version exists and is
immutable, modes agree, timeframe is declared by the strategy, symbol is
in scope, stored candles cover the window, the search space is bounded,
the objective is computable for the configured simulation mode, the
walk-forward windows fit into the period, and the total engine-walk
budget covers candidates x phases + robustness scenarios.

Every query is scoped by ``owner_id``; foreign rows answer 404 (no
existence leak). New runs NEVER overwrite previous research results.
"""

from __future__ import annotations

import platform
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.db.models import ResearchCandidate, ResearchRun, ResearchScenario, ResearchWindow
from app.domains.backtest.data import verify_coverage
from app.domains.backtest.schemas import BacktestConfigPayload  # noqa: F401 (docs)
from app.domains.market_data.timeframes import timeframe_seconds
from app.domains.research import RESEARCH_RUNNER_VERSION
from app.domains.backtest import BACKTEST_ENGINE_VERSION
from app.domains.research.grid import combination_count, generate_windows
from app.domains.research.params import validate_patch_specs
from app.domains.research.schemas import (
    OBJECTIVE_NOTES,
    TRADE_LEVEL_METRICS,
    ResearchConfigPayload,
    ResearchCreateRequest,
)
from app.domains.strategies import service as strategy_service
from app.domains.strategies.rules import validate_rule_set

_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 100


# --------------------------------------------------------------------------- #
# Creation + validation cascade
# --------------------------------------------------------------------------- #


def _resolve_timeframe(
    requested: str | None, rule_set_timeframes: tuple[str, ...]
) -> str:
    if requested is not None:
        if requested not in rule_set_timeframes:
            raise ValidationAppError(
                f"Timeframe '{requested}' is not declared by the selected "
                f"strategy version (declared: {', '.join(rule_set_timeframes)}).",
                code="TIMEFRAME_NOT_DECLARED",
            )
        return requested
    return min(rule_set_timeframes, key=timeframe_seconds)


def _walk_budget(
    *,
    combinations: int,
    validation_mode: str,
    windows: int,
    cost_variants: int,
    has_neighbors: bool,
    param_count: int,
    bootstrap_phases: int,
) -> int:
    """Upper bound on engine walks for the configuration (creation check)."""

    if validation_mode == "walk_forward":
        per_candidate = windows
        baseline = windows
        variants = windows * cost_variants
    elif validation_mode == "holdout":
        per_candidate = 1
        baseline = 2  # IS walk + OOS walk of the winner
        variants = 2 * cost_variants
    else:
        per_candidate = 1
        baseline = 1
        variants = cost_variants
    # Neighbour bound: ±1 step per dimension (patched baseline) or every
    # non-current value per dimension (original baseline) — take the max.
    neighbours = (2 * param_count) if has_neighbors else 0
    return combinations * per_candidate + baseline + variants + neighbours + bootstrap_phases


async def validate_and_create(
    session: AsyncSession,
    owner_id: str,
    settings: Settings,
    request: ResearchCreateRequest,
) -> ResearchRun:
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
            "select a published/superseded version) before researching — "
            "drafts can still be replaced, so their lifecycle is not "
            "immutable.",
            code="DRAFT_VERSION_NOT_RESEARCHABLE",
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
            "The stored rule set failed revalidation and cannot be researched.",
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

    if request.mode == "forex" and request.binary is not None:
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
    if request.mode == "forex" and request.binary_cost_variants:
        raise ValidationAppError(
            "binary_cost_variants cannot be combined with a Forex run.",
            code="CONFIG_MISMATCH",
        )
    if request.mode == "binary_options" and request.forex_cost_variants:
        raise ValidationAppError(
            "forex_cost_variants cannot be combined with a Binary Options run.",
            code="CONFIG_MISMATCH",
        )
    if request.mode == "forex" and request.forex_cost_variants and not (
        request.forex_execution and request.forex_execution.enabled
    ):
        raise ValidationAppError(
            "forex_cost_variants require an enabled forex_execution "
            "configuration (cost sensitivity is meaningless for a "
            "setup-only run).",
            code="COST_VARIANTS_REQUIRE_EXECUTION_CONFIG",
        )

    # ---- search space bounds ------------------------------------------------
    from app.domains.research.params import parameter_inventory

    snapshot = rule_set.to_dict()
    validate_patch_specs(
        snapshot, [p.model_dump() for p in request.parameters]
    )
    combos = combination_count([p.model_dump() for p in request.parameters])
    if len(request.parameters) > settings.research_max_parameters:
        raise ValidationAppError(
            f"The run selects {len(request.parameters)} parameters; the "
            f"platform limit is {settings.research_max_parameters}.",
            code="TOO_MANY_PARAMETERS",
        )
    for p in request.parameters:
        if len(p.values) > settings.research_max_values_per_parameter:
            raise ValidationAppError(
                f"Parameter {p.condition_id}.{p.field} lists "
                f"{len(p.values)} values; the limit is "
                f"{settings.research_max_values_per_parameter}.",
                code="TOO_MANY_VALUES",
            )
    if combos > settings.research_max_combinations:
        raise ValidationAppError(
            f"The selected parameters produce {combos} combinations; the "
            f"platform limit is {settings.research_max_combinations}. "
            "Narrow the value lists.",
            code="TOO_MANY_COMBINATIONS",
        )

    # ---- objective applicability -------------------------------------------
    simulation_mode = "setup_only"
    if request.mode == "forex":
        if request.forex_execution is not None and request.forex_execution.enabled:
            simulation_mode = "trade_simulation"
    else:
        simulation_mode = "trade_simulation"
    if simulation_mode == "setup_only" and request.objective.metric in TRADE_LEVEL_METRICS:
        raise ValidationAppError(
            f"Objective '{request.objective.metric}' requires simulated "
            "trades. Provide an enabled forex_execution configuration, or "
            "choose the 'conditions_met_count' objective for a setup-only "
            "research run.",
            code="OBJECTIVE_REQUIRES_TRADE_SIMULATION",
        )

    # ---- period split / windows ---------------------------------------------
    start = request.start.astimezone(timezone.utc)
    end = request.end.astimezone(timezone.utc)
    interval = timeframe_seconds(timeframe)
    start_epoch = int(start.timestamp())
    end_epoch = int(end.timestamp())
    in_sample_end: str | None = None
    windows_count = 0
    wf_warnings: list[str] = []
    if request.validation_mode == "holdout":
        holdout_span = end_epoch - start_epoch
        is_epoch = start_epoch + int(holdout_span * request.holdout.in_sample_pct)
        # Align the boundary to the primary timeframe grid so the split is
        # expressible in whole candles.
        is_epoch -= is_epoch % interval
        if not (start_epoch < is_epoch < end_epoch - interval):
            raise ValidationAppError(
                "The in-sample/out-of-sample split is degenerate for this "
                "period; extend the date range.",
                code="INVALID_SPLIT",
            )
        in_sample_end = datetime.fromtimestamp(is_epoch, tz=timezone.utc).isoformat(
            timespec="milliseconds"
        )
    elif request.validation_mode == "walk_forward":
        windows, wf_warnings = generate_windows(
            start_epoch=start_epoch,
            end_epoch=end_epoch,
            interval_seconds=interval,
            train_candles=request.walk_forward.train_candles,
            test_candles=request.walk_forward.test_candles,
            step_candles=request.walk_forward.step_candles,
            window_mode=request.walk_forward.window_mode,
            max_windows=settings.research_max_windows,
        )
        windows_count = len(windows)
        if windows_count < 2:
            raise ValidationAppError(
                f"The configured period holds only {windows_count} "
                "walk-forward window(s); at least 2 are required for "
                "meaningful walk-forward validation. Extend the date range "
                "or shrink the windows.",
                code="INSUFFICIENT_HISTORY_FOR_WINDOWS",
            )

    # ---- walk budget ---------------------------------------------------------
    cost_variants = len(request.forex_cost_variants) + len(request.binary_cost_variants)
    bootstrap_phases = 0
    if request.run_bootstrap and settings.research_bootstrap_samples > 0:
        bootstrap_phases = 2 if request.validation_mode == "holdout" else 1
    budget = _walk_budget(
        combinations=combos + 1,  # + candidate 0 (original parameterization)
        validation_mode=request.validation_mode,
        windows=windows_count,
        cost_variants=cost_variants,
        has_neighbors=request.param_sensitivity and request.validation_mode != "walk_forward",
        param_count=len(request.parameters),
        bootstrap_phases=bootstrap_phases,
    )
    if budget > settings.research_max_walks:
        raise ValidationAppError(
            f"This configuration needs up to {budget} engine walks (grid + "
            f"windows + robustness scenarios), above the platform limit of "
            f"{settings.research_max_walks}. Reduce combinations, windows "
            "or cost variants.",
            code="RESEARCH_WALK_BUDGET_EXCEEDED",
        )

    # ---- data coverage (pre-run, honest) --------------------------------------
    coverage = await verify_coverage(
        session,
        symbol=symbol,
        primary_timeframe=timeframe,
        start=start,
        end=end,
        max_candles=settings.backtest_max_candles,
    )

    warnings: list[dict[str, Any]] = []
    if request.objective.metric == "win_rate":
        warnings.append(
            {
                "code": "WIN_RATE_OBJECTIVE",
                "message": OBJECTIVE_NOTES["win_rate"],
            }
        )
    if request.objective.metric == "conditions_met_count":
        warnings.append(
            {
                "code": "FREQUENCY_OBJECTIVE",
                "message": OBJECTIVE_NOTES["conditions_met_count"],
            }
        )
    for message in wf_warnings:
        warnings.append({"code": message.split(":")[0], "message": message})

    parameter_labels: dict[str, str] = {}
    for p in request.parameters:
        key = f"{p.condition_id}.{p.field}"
        parameter_labels[key] = p.label or key

    config_payload = ResearchConfigPayload(
        mode=request.mode,
        symbol=symbol,
        timeframe=timeframe,
        validation_mode=request.validation_mode,
        start=start.isoformat(timespec="milliseconds"),
        end=end.isoformat(timespec="milliseconds"),
        in_sample_end=in_sample_end,
        data_source="postgres_candle_cache",
        warmup_candles=settings.backtest_warmup_candles,
        max_candles=settings.backtest_max_candles,
        strategy_id=str(request.strategy_id),
        strategy_name=strategy.name,
        strategy_version=version_row.version,
        strategy_version_status=version_row.status,
        rule_set=snapshot,
        direction=rule_set.direction,
        parameters=[p.model_dump() for p in request.parameters],
        parameter_labels=parameter_labels,
        objective={"metric": request.objective.metric, "note": OBJECTIVE_NOTES[request.objective.metric]},
        objective_direction=request.objective.direction,
        objective_note=OBJECTIVE_NOTES[request.objective.metric],
        objective_constraints=request.objective.constraints.model_dump(),
        holdout=(
            request.holdout.model_dump() if request.validation_mode == "holdout" else None
        ),
        walk_forward=(
            request.walk_forward.model_dump()
            if request.validation_mode == "walk_forward"
            else None
        ),
        forex_execution=(
            request.forex_execution.model_dump() if request.forex_execution else None
        ),
        binary=request.binary.model_dump() if request.binary else None,
        forex_cost_variants=[v.model_dump() for v in request.forex_cost_variants],
        binary_cost_variants=[v.model_dump() for v in request.binary_cost_variants],
        param_sensitivity=request.param_sensitivity,
        run_bootstrap=request.run_bootstrap,
        seed=request.seed,
        combination_count=combos,
        walk_budget=budget,
        limits={
            "max_combinations": settings.research_max_combinations,
            "max_walks": settings.research_max_walks,
            "max_windows": settings.research_max_windows,
            "bootstrap_samples": settings.research_bootstrap_samples,
            "max_cost_scenarios": settings.research_max_cost_scenarios,
        },
        simulation_mode=simulation_mode,
        notes=request.notes,
        warnings=warnings,
        software={
            "research_runner_version": RESEARCH_RUNNER_VERSION,
            "engine_version": BACKTEST_ENGINE_VERSION,
            "python": platform.python_version(),
        },
    )

    if cost_variants > settings.research_max_cost_scenarios:
        raise ValidationAppError(
            f"{cost_variants} cost variants were selected; the platform "
            f"limit is {settings.research_max_cost_scenarios}.",
            code="TOO_MANY_COST_VARIANTS",
        )

    run = ResearchRun(
        owner_id=owner_id,
        mode=request.mode,
        symbol=symbol,
        timeframe=timeframe,
        validation_mode=request.validation_mode,
        strategy_id=request.strategy_id,
        strategy_name=strategy.name,
        strategy_version=version_row.version,
        strategy_version_id=version_row.id,
        status="queued",
        start_time=start,
        end_time=end,
        config=config_payload.model_dump(),
        research_schema_version="1.0",
        research_version=RESEARCH_RUNNER_VERSION,
        engine_version=BACKTEST_ENGINE_VERSION,
        data_quality={"pre_run_coverage": coverage},
    )
    session.add(run)
    await session.flush()
    return run


# --------------------------------------------------------------------------- #
# Lifecycle transitions (runner-facing)
# --------------------------------------------------------------------------- #


async def mark_running(session: AsyncSession, run_id: uuid.UUID) -> ResearchRun:
    run = await session.get(ResearchRun, run_id)
    if run is None:
        raise NotFoundError(f"Research run '{run_id}' was not found.")
    if run.status != "queued":
        raise ConflictError(
            f"Run is already '{run.status}'; it cannot be started again.",
            code="INVALID_RUN_TRANSITION",
        )
    run.status = "running"
    run.started_at = datetime.now(tz=timezone.utc)
    await session.flush()
    return run


async def mark_completed(session: AsyncSession, run: ResearchRun) -> None:
    run.status = "completed"
    run.completed_at = datetime.now(tz=timezone.utc)
    await session.flush()


async def mark_failed(
    session: AsyncSession, run_id: uuid.UUID, error: dict[str, Any]
) -> None:
    run = await session.get(ResearchRun, run_id)
    if run is None:  # pragma: no cover - defensive
        return
    run.status = "failed"
    run.error = error
    run.completed_at = datetime.now(tz=timezone.utc)
    await session.flush()


async def mark_cancelled(session: AsyncSession, run_id: uuid.UUID) -> None:
    run = await session.get(ResearchRun, run_id)
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
    """Startup recovery: queued/running research rows from a previous
    process are marked failed (in-process runs do not survive restarts)."""

    stmt = select(ResearchRun).where(
        ResearchRun.status.in_(("queued", "running")),
        ResearchRun.owner_id.isnot(None),
    )
    rows = list((await session.scalars(stmt)).all())
    for run in rows:
        run.status = "failed"
        run.error = {
            "code": "RUNNER_RESTARTED",
            "message": "The backend process restarted while this research "
            "run was queued/running; in-process runs do not survive "
            "restarts. Create a new run — the configuration below is "
            "retained.",
            "details": [],
        }
        run.completed_at = datetime.now(tz=timezone.utc)
    if rows:
        await session.flush()
    return len(rows)


# --------------------------------------------------------------------------- #
# Owner-scoped reads
# --------------------------------------------------------------------------- #


async def get_run(session: AsyncSession, owner_id: str, run_id: uuid.UUID) -> ResearchRun:
    run = await session.get(ResearchRun, run_id)
    if run is None or run.owner_id != owner_id:
        raise NotFoundError(f"Research run '{run_id}' was not found.")
    return run


def run_summary(run: ResearchRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "owner_id": run.owner_id,
        "mode": run.mode,
        "symbol": run.symbol,
        "timeframe": run.timeframe,
        "validation_mode": run.validation_mode,
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
        "objective": (run.config or {}).get("objective"),
        "combination_count": (run.config or {}).get("combination_count"),
        "walk_budget": (run.config or {}).get("walk_budget"),
        "progress": run.progress,
        "walks_executed": run.walks_executed,
        "aggregate": run.aggregate,
        "data_quality": run.data_quality,
        "error": run.error,
        "config": run.config,
        "research_version": run.research_version,
        "engine_version": run.engine_version,
        "research_schema_version": run.research_schema_version,
        "created_at": run.created_at.isoformat(timespec="milliseconds"),
        "started_at": run.started_at.isoformat(timespec="milliseconds")
        if run.started_at
        else None,
        "completed_at": run.completed_at.isoformat(timespec="milliseconds")
        if run.completed_at
        else None,
    }


def run_list_item(run: ResearchRun) -> dict[str, Any]:
    config = run.config or {}
    aggregate = run.aggregate or {}
    return {
        "id": str(run.id),
        "mode": run.mode,
        "symbol": run.symbol,
        "timeframe": run.timeframe,
        "validation_mode": run.validation_mode,
        "strategy_id": str(run.strategy_id) if run.strategy_id else None,
        "strategy_name": run.strategy_name,
        "strategy_version": run.strategy_version,
        "status": run.status,
        "objective_metric": (config.get("objective") or {}).get("metric"),
        "combination_count": config.get("combination_count"),
        "simulation_mode": config.get("simulation_mode"),
        "start_time": run.start_time.isoformat(timespec="milliseconds"),
        "end_time": run.end_time.isoformat(timespec="milliseconds"),
        "candidates_total": (run.progress or {}).get("candidates_total"),
        "candidates_evaluated": (run.progress or {}).get("candidates_evaluated"),
        "windows_completed": (run.progress or {}).get("windows_completed"),
        "best_candidate": aggregate.get("best_candidate"),
        "error": {"code": run.error.get("code")} if run.error else None,
        "created_at": run.created_at.isoformat(timespec="milliseconds"),
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
    validation_mode: str | None = None,
    page: int = 1,
    page_size: int = _PAGE_SIZE_DEFAULT,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    page = max(1, page)
    page_size = min(max(1, page_size), _PAGE_SIZE_MAX)
    stmt = select(ResearchRun).where(ResearchRun.owner_id == owner_id)
    if mode:
        stmt = stmt.where(ResearchRun.mode == mode)
    if strategy_id is not None:
        stmt = stmt.where(ResearchRun.strategy_id == strategy_id)
    if status:
        stmt = stmt.where(ResearchRun.status == status)
    if validation_mode:
        stmt = stmt.where(ResearchRun.validation_mode == validation_mode)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(
        (
            await session.scalars(
                stmt.order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    total_int = int(total or 0)
    pages = (total_int + page_size - 1) // page_size if page_size else 1
    return (
        [run_list_item(r) for r in rows],
        {"page": page, "page_size": page_size, "total": total_int, "pages": max(1, pages)},
    )


def candidate_summary(row: ResearchCandidate) -> dict[str, Any]:
    in_sample = row.in_sample or {}
    trades = in_sample.get("trades") or {}
    oos = row.out_of_sample or {}
    oos_trades = oos.get("trades") or {}
    return {
        "id": str(row.id),
        "run_id": str(row.run_id),
        "candidate_index": row.candidate_index,
        "status": row.status,
        "params": row.params,
        "score": row.score,
        "rank": row.rank,
        "selectable": row.selectable,
        "constraint_violations": row.constraint_violations or [],
        "rank_note": row.rank_note,
        "times_selected": row.times_selected,
        "in_sample_trades": {
            "decided_trades": trades.get("decided_trades"),
            "win_rate": trades.get("win_rate"),
            "net_pnl": trades.get("net_pnl"),
            "expectancy": trades.get("expectancy"),
            "profit_factor": trades.get("profit_factor"),
        }
        if trades
        else None,
        "out_of_sample_trades": {
            "decided_trades": oos_trades.get("decided_trades"),
            "win_rate": oos_trades.get("win_rate"),
            "net_pnl": oos_trades.get("net_pnl"),
            "expectancy": oos_trades.get("expectancy"),
            "profit_factor": oos_trades.get("profit_factor"),
        }
        if oos
        else None,
        "error": row.error,
    }


def candidate_detail(row: ResearchCandidate) -> dict[str, Any]:
    payload = candidate_summary(row)
    payload["in_sample"] = row.in_sample
    payload["out_of_sample"] = row.out_of_sample
    payload["rule_set"] = row.rule_set
    return payload


async def list_candidates(
    session: AsyncSession,
    owner_id: str,
    run_id: uuid.UUID,
    *,
    status: str | None = None,
    sort: str = "rank",
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    await get_run(session, owner_id, run_id)  # ownership gate
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    stmt = select(ResearchCandidate).where(
        ResearchCandidate.run_id == run_id,
        ResearchCandidate.owner_id == owner_id,
    )
    if status:
        stmt = stmt.where(ResearchCandidate.status == status)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    if sort == "rank":
        # NULLs last, then ascending rank; unranked candidates by index.
        stmt = stmt.order_by(
            ResearchCandidate.rank.asc().nullslast(),
            ResearchCandidate.candidate_index.asc(),
        )
    else:
        stmt = stmt.order_by(ResearchCandidate.candidate_index.asc())
    rows = list(
        (
            await session.scalars(stmt.offset((page - 1) * page_size).limit(page_size))
        ).all()
    )
    total_int = int(total or 0)
    pages = (total_int + page_size - 1) // page_size if page_size else 1
    return (
        [candidate_summary(r) for r in rows],
        {"page": page, "page_size": page_size, "total": total_int, "pages": max(1, pages)},
    )


async def get_candidate(
    session: AsyncSession, owner_id: str, run_id: uuid.UUID, candidate_index: int
) -> dict[str, Any]:
    await get_run(session, owner_id, run_id)
    stmt = (
        select(ResearchCandidate)
        .where(
            ResearchCandidate.run_id == run_id,
            ResearchCandidate.owner_id == owner_id,
            ResearchCandidate.candidate_index == candidate_index,
        )
        .limit(1)
    )
    row = (await session.scalars(stmt)).first()
    if row is None:
        raise NotFoundError(
            f"Candidate {candidate_index} was not found in run '{run_id}'.",
            code="CANDIDATE_NOT_FOUND",
        )
    return candidate_detail(row)


def window_dict(row: ResearchWindow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "run_id": str(row.run_id),
        "window_index": row.window_index,
        "status": row.status,
        "train_start": row.train_start.isoformat(timespec="milliseconds"),
        "train_end": row.train_end.isoformat(timespec="milliseconds"),
        "test_start": row.test_start.isoformat(timespec="milliseconds"),
        "test_end": row.test_end.isoformat(timespec="milliseconds"),
        "train_evaluations": row.train_evaluations,
        "selected_candidate_index": row.selected_candidate_index,
        "selected_params": row.selected_params,
        "selection_score": row.selection_score,
        "test_metrics": row.test_metrics,
        "error": row.error,
        "note": row.note,
    }


def window_summary(row: ResearchWindow) -> dict[str, Any]:
    test = row.test_metrics or {}
    trades = test.get("trades") or {}
    return {
        "id": str(row.id),
        "window_index": row.window_index,
        "status": row.status,
        "train_start": row.train_start.isoformat(timespec="milliseconds"),
        "train_end": row.train_end.isoformat(timespec="milliseconds"),
        "test_start": row.test_start.isoformat(timespec="milliseconds"),
        "test_end": row.test_end.isoformat(timespec="milliseconds"),
        "train_evaluations": row.train_evaluations,
        "selected_candidate_index": row.selected_candidate_index,
        "selected_params": row.selected_params,
        "selection_score": row.selection_score,
        "test_trades": {
            "decided_trades": trades.get("decided_trades"),
            "win_rate": trades.get("win_rate"),
            "net_pnl": trades.get("net_pnl"),
            "expectancy": trades.get("expectancy"),
        },
        "note": row.note,
        "error": {"code": row.error.get("code")} if row.error else None,
    }


async def list_windows(
    session: AsyncSession,
    owner_id: str,
    run_id: uuid.UUID,
    *,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    await get_run(session, owner_id, run_id)
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    stmt = (
        select(ResearchWindow)
        .where(ResearchWindow.run_id == run_id, ResearchWindow.owner_id == owner_id)
        .order_by(ResearchWindow.window_index.asc())
    )
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(
        (
            await session.scalars(stmt.offset((page - 1) * page_size).limit(page_size))
        ).all()
    )
    total_int = int(total or 0)
    pages = (total_int + page_size - 1) // page_size if page_size else 1
    return (
        [window_summary(r) for r in rows],
        {"page": page, "page_size": page_size, "total": total_int, "pages": max(1, pages)},
    )


def scenario_dict(row: ResearchScenario) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "run_id": str(row.run_id),
        "scenario_index": row.scenario_index,
        "category": row.category,
        "label": row.label,
        "assumptions": row.assumptions,
        "metrics": row.metrics,
        "delta": row.delta,
        "status": row.status,
        "note": row.note,
        "error": row.error,
    }


async def list_scenarios(
    session: AsyncSession, owner_id: str, run_id: uuid.UUID
) -> list[dict[str, Any]]:
    await get_run(session, owner_id, run_id)
    stmt = (
        select(ResearchScenario)
        .where(ResearchScenario.run_id == run_id, ResearchScenario.owner_id == owner_id)
        .order_by(ResearchScenario.scenario_index.asc())
    )
    rows = list((await session.scalars(stmt)).all())
    return [scenario_dict(r) for r in rows]


async def parameter_schema(
    session: AsyncSession,
    owner_id: str,
    strategy_id: uuid.UUID,
    version_number: int,
) -> dict[str, Any]:
    """Optimizable-parameter inventory of an immutable strategy version."""

    from app.domains.research.params import parameter_inventory

    version_row = await strategy_service.get_version(
        session, owner_id, strategy_id, version_number
    )
    try:
        rule_set = validate_rule_set(version_row.rule_set)
    except Exception as exc:
        raise ValidationAppError(
            "The stored rule set failed revalidation.", code="RULE_SET_INVALID"
        ) from exc
    return {
        "strategy_id": str(strategy_id),
        "strategy_version": version_row.version,
        "strategy_version_status": version_row.status,
        "mode": rule_set.mode,
        "direction": rule_set.direction,
        "timeframes": list(rule_set.timeframes),
        "parameters": parameter_inventory(rule_set),
        "note": (
            "Only fields the strategy version already declares are "
            "optimizable; the optimizer never introduces new rules or "
            "parameters."
        ),
    }


__all__ = [
    "get_candidate",
    "get_run",
    "list_candidates",
    "list_runs",
    "list_scenarios",
    "list_windows",
    "mark_cancelled",
    "mark_completed",
    "mark_failed",
    "mark_running",
    "parameter_schema",
    "recover_interrupted",
    "run_summary",
    "validate_and_create",
]
