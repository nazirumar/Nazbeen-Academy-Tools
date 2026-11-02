"""Research HTTP endpoints (Phase 8).

* ``POST   /research/optimization``                        — validate + queue a run.
* ``GET    /research/optimization``                        — paginated run history.
* ``GET    /research/optimization/parameter-schema``       — optimizable-parameter inventory of a strategy version.
* ``GET    /research/optimization/{run_id}``               — configuration, progress, aggregate.
* ``GET    /research/optimization/{run_id}/candidates``    — ranked candidates (paginated).
* ``GET    /research/optimization/{run_id}/candidates/{index}`` — one candidate (full rule set + digests).
* ``GET    /research/optimization/{run_id}/walk-forward``  — chronological window results.
* ``GET    /research/optimization/{run_id}/robustness``    — robustness scenarios.
* ``POST   /research/optimization/{run_id}/cancel``        — cooperative cancellation.

All endpoints are owner-scoped (``X-User-Id``); foreign runs are 404s.
Responses follow the platform envelope; errors use the shared error model.
No endpoint places orders, fabricates data, or publishes a strategy.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ConflictError
from app.core.ownership import current_owner
from app.core.rate_limit import check_rate_limit
from app.core.responses import created, ok
from app.db.session import get_db_session
from app.domains.research import service as research_service
from app.domains.research.runner import get_runner
from app.domains.research.schemas import ResearchCreateRequest

router = APIRouter(prefix="/research/optimization", tags=["research"])


@router.post("")
async def create_research_run(
    payload: ResearchCreateRequest,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Validate a research configuration and queue the run.

    Validation is complete BEFORE the run is created (strategy version
    immutable, search space bounded, objective computable, windows fit,
    walk budget covered, data coverage checked). The run is executed by
    the in-process runner (queued -> running -> completed | failed |
    cancelled). The best candidate is NEVER published automatically.
    """

    # Per-owner creation rate limit (cheap in-process sliding window):
    # each accepted creation starts a bounded in-process run, so one
    # client must not be able to flood the runner with new runs.
    check_rate_limit(
        f"research-create:{owner}", settings.research_max_creates_per_minute
    )
    run = await research_service.validate_and_create(session, owner, settings, payload)
    await session.commit()  # commit before scheduling (runner owns a session)
    get_runner().submit(run.id)
    return created(research_service.run_summary(run))


@router.get("")
async def list_research_runs(
    mode: str | None = Query(default=None, pattern="^(forex|binary_options)$"),
    strategy_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(
        default=None, pattern="^(queued|running|completed|failed|cancelled)$"
    ),
    validation_mode: str | None = Query(
        default=None, pattern="^(in_sample_only|holdout|walk_forward)$"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Paginated research run history for the requesting owner."""

    items, page_meta = await research_service.list_runs(
        session,
        owner,
        mode=mode,
        strategy_id=strategy_id,
        status=status,
        validation_mode=validation_mode,
        page=page,
        page_size=page_size,
    )
    return ok({"items": items, "page": page_meta})


@router.get("/parameter-schema")
async def parameter_schema(
    strategy_id: uuid.UUID = Query(),
    strategy_version: int = Query(ge=1),
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Optimizable-parameter inventory of an immutable strategy version.

    Lists every condition field the optimizer may vary (lookback or an
    existing params key) with its current value and type. Optimizing a
    parameter that the strategy does not already declare is impossible by
    construction.
    """

    payload = await research_service.parameter_schema(
        session, owner, strategy_id, strategy_version
    )
    return ok(payload)


@router.get("/{run_id}")
async def get_research_run(
    run_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Run configuration, lifecycle status, progress and aggregate."""

    run = await research_service.get_run(session, owner, run_id)
    return ok(research_service.run_summary(run))


@router.get("/{run_id}/candidates")
async def list_research_candidates(
    run_id: uuid.UUID,
    status: str | None = Query(
        default=None, pattern="^(pending|evaluated|invalid|failed)$"
    ),
    sort: str = Query(default="rank", pattern="^(rank|index)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Ranked candidate configurations with sample sizes alongside.

    Walk-forward runs return candidates ordered by candidate index (their
    ``rank`` is intentionally NULL — selection happens per window).
    """

    items, page_meta = await research_service.list_candidates(
        session,
        owner,
        run_id,
        status=status,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return ok({"items": items, "page": page_meta})


@router.get("/{run_id}/candidates/{candidate_index}")
async def get_research_candidate(
    run_id: uuid.UUID,
    candidate_index: int,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """One candidate: full patched rule set + in/out-of-sample digests."""

    return ok(
        await research_service.get_candidate(session, owner, run_id, candidate_index)
    )


@router.get("/{run_id}/walk-forward")
async def list_research_windows(
    run_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Chronological walk-forward windows (training vs testing periods)."""

    items, page_meta = await research_service.list_windows(
        session, owner, run_id, page=page, page_size=page_size
    )
    return ok({"items": items, "page": page_meta})


@router.get("/{run_id}/robustness")
async def list_research_robustness(
    run_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Robustness scenarios with documented assumptions and deltas."""

    items = await research_service.list_scenarios(session, owner, run_id)
    return ok({"items": items})


@router.post("/{run_id}/cancel")
async def cancel_research_run(
    run_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Request cooperative cancellation of a queued/running run.

    The runner honours the request at the next engine step. Runs already
    finished answer 409 — the stored status stays truthful. Results
    computed before the cancellation remain persisted (honestly partial).
    """

    run = await research_service.get_run(session, owner, run_id)
    if run.status in ("completed", "failed", "cancelled"):
        raise ConflictError(
            f"Run is already '{run.status}' and cannot be cancelled.",
            code="RUN_ALREADY_FINISHED",
        )
    runner = get_runner()
    tracked = runner.is_tracked(run_id) and runner.cancel(run_id)
    return ok(
        {
            "run": research_service.run_summary(run),
            "cancel_requested": bool(tracked),
            "note": (
                "Cancellation is cooperative: the runner stops at the next "
                "engine step and the run is stored as 'cancelled'."
                if tracked
                else "The run is not executing in this process (it may "
                "already have finished or the backend restarted); its "
                "stored status is authoritative."
            ),
        }
    )


__all__ = ["router"]
