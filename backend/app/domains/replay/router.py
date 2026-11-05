"""Market replay HTTP endpoints (Phase 11).

* ``GET    /replay/data-coverage``           - read-only window inventory.
* ``POST   /replay/sessions``                - create a paused saved session.
* ``GET    /replay/sessions``                - owner-scoped history.
* ``GET    /replay/sessions/{id}``           - saved cursor + data quality.
* ``GET    /replay/sessions/{id}/candles``   - ONLY candles <= cursor.
* ``POST   /replay/sessions/{id}/step``      - advance N closed candles.
* ``POST   /replay/sessions/{id}/seek``      - jump to a permitted timestamp.
* ``POST   /replay/sessions/{id}/resume``    - start presentation timer.
* ``POST   /replay/sessions/{id}/pause``     - stop presentation timer.
* ``POST   /replay/sessions/{id}/reset``     - return to the first candle.
* ``PATCH  /replay/sessions/{id}``           - presentation speed.
* ``DELETE /replay/sessions/{id}``           - delete this owner's session.

Every route is owner-scoped (``X-User-Id``); foreign sessions return the same
404 as missing ones. No route fetches market data, places orders or exposes a
candle after the cursor.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.ownership import current_owner
from app.core.rate_limit import check_rate_limit
from app.core.responses import created, ok
from app.db.session import get_db_session
from app.domains.replay import service as replay_service
from app.domains.replay.schemas import (
    ReplayCreateRequest,
    ReplaySeekRequest,
    ReplayStepRequest,
    ReplayUpdateRequest,
)

router = APIRouter(prefix="/replay", tags=["replay"])


@router.get("/data-coverage")
async def replay_coverage(
    symbol: str = Query(min_length=1, max_length=40),
    timeframe: str = Query(min_length=2, max_length=8),
    start: datetime = Query(),
    end: datetime = Query(),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Check stored closed-candle coverage before creating a session."""

    payload = await replay_service.coverage(
        session,
        settings,
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
    )
    return ok(payload)


@router.post("/sessions")
async def create_replay_session(
    payload: ReplayCreateRequest,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Validate the window and create a paused, resumable replay session."""

    check_rate_limit(
        f"replay-create:{owner}", settings.replay_max_creates_per_minute
    )
    row = await replay_service.create_session(session, owner, settings, payload)
    return created(replay_service.session_summary(row))


@router.get("/sessions")
async def list_replay_sessions(
    mode: str | None = Query(default=None, pattern="^(forex|binary_options)$"),
    status: str | None = Query(default=None, pattern="^(active|paused|completed)$"),
    symbol: str | None = Query(default=None, max_length=40),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    items, page_meta = await replay_service.list_sessions(
        session,
        owner,
        mode=mode,
        status=status,
        symbol=symbol,
        page=page,
        page_size=page_size,
    )
    return ok({"items": items, "page": page_meta})


@router.get("/sessions/{session_id}")
async def get_replay_session(
    session_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    row = await replay_service.get_session(session, owner, session_id)
    return ok(replay_service.session_summary(row))


@router.get("/sessions/{session_id}/candles")
async def get_replay_candles(
    session_id: uuid.UUID,
    limit: int = Query(default=500, ge=1, le=20_000),
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Return the newest visible candles, bounded by the persisted cursor."""

    capped = min(int(limit), int(settings.replay_max_candles_returned))
    payload = await replay_service.session_candles(
        session, owner, session_id, limit=capped
    )
    payload["requested_limit"] = int(limit)
    payload["returned_limit"] = capped
    return ok(payload)


@router.post("/sessions/{session_id}/step")
async def step_replay_session(
    session_id: uuid.UUID,
    payload: ReplayStepRequest,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    row = await replay_service.step_session(session, owner, session_id, payload)
    return ok(replay_service.session_summary(row))


@router.post("/sessions/{session_id}/seek")
async def seek_replay_session(
    session_id: uuid.UUID,
    payload: ReplaySeekRequest,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    row = await replay_service.seek_session(session, owner, session_id, payload)
    return ok(replay_service.session_summary(row))


@router.post("/sessions/{session_id}/resume")
async def resume_replay_session(
    session_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    row = await replay_service.resume_session(session, owner, session_id)
    return ok(replay_service.session_summary(row))


@router.post("/sessions/{session_id}/pause")
async def pause_replay_session(
    session_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    row = await replay_service.pause_session(session, owner, session_id)
    return ok(replay_service.session_summary(row))


@router.post("/sessions/{session_id}/reset")
async def reset_replay_session(
    session_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    row = await replay_service.reset_session(session, owner, session_id)
    return ok(replay_service.session_summary(row))


@router.patch("/sessions/{session_id}")
async def update_replay_session(
    session_id: uuid.UUID,
    payload: ReplayUpdateRequest,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    row = await replay_service.update_session(session, owner, session_id, payload)
    return ok(replay_service.session_summary(row))


@router.delete("/sessions/{session_id}")
async def delete_replay_session(
    session_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    await replay_service.delete_session(session, owner, session_id)
    return ok({"id": str(session_id), "deleted": True})


__all__ = ["router"]
