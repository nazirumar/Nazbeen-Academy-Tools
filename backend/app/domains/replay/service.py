"""Market replay session lifecycle (Phase 11).

A session is a persisted, deterministic cursor over stored closed candles.
The service exposes only cursor transitions; it never fetches live data and
never returns a candle after the cursor.

Lifecycle::

    create -> paused -> (resume) active -> (pause) paused
                         |                  |
                         +---- step --------+
                         +---- seek --------+
                         +---- reset -------> paused
                         +---- end ----------> completed

``step`` is deliberately allowed from paused state so a user can advance one
bar without starting the timer. Playback speed is presentation-only.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.db.models.replay import ReplaySession
from app.domains.market_data.timeframes import (
    is_timeframe_supported,
    timeframe_seconds,
)
from app.domains.replay import REPLAY_ENGINE_VERSION
from app.domains.replay.data import (
    count_closed,
    coverage_payload,
    latest_at_or_before,
    load_next_closed,
    load_visible,
    load_window,
)
from app.domains.replay.schemas import (
    ReplayCreateRequest,
    ReplaySeekRequest,
    ReplayStepRequest,
    ReplayUpdateRequest,
)

_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 100
_MAX_STEP = 100


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="milliseconds")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def session_summary(session: ReplaySession) -> dict[str, Any]:
    """The API representation of a replay session (no candle payloads)."""

    interval = timeframe_seconds(session.timeframe)
    cursor = _utc(session.cursor_time)
    current_time = cursor + timedelta(seconds=interval)
    total = max(1, int(session.total_candles))
    visible = min(max(1, int(session.visible_candles)), total)
    progress = round(visible / total, 6)
    at_end = visible >= total
    return {
        "id": str(session.id),
        "owner_id": session.owner_id,
        "mode": session.mode,
        "symbol": session.symbol,
        "timeframe": session.timeframe,
        "status": session.status,
        "start_time": _iso(_utc(session.start_time)),
        "end_time": _iso(_utc(session.end_time)),
        "first_candle_time": _iso(_utc(session.first_candle_time)),
        "last_candle_time": _iso(_utc(session.last_candle_time)),
        "cursor_time": _iso(cursor),
        "current_time": _iso(current_time),
        "current_time_unix": int(current_time.timestamp()),
        "visible_candles": visible,
        "total_candles": total,
        "remaining_candles": max(0, total - visible),
        "cursor_index": visible - 1,
        "progress": progress,
        "progress_pct": round(progress * 100.0, 1),
        "at_end": at_end,
        "playback_speed": float(session.playback_speed),
        "data_quality": session.data_quality,
        "config": session.config,
        "session_schema_version": session.session_schema_version,
        "engine_version": REPLAY_ENGINE_VERSION,
        "created_at": _iso(session.created_at),
        "last_advanced_at": _iso(session.last_advanced_at),
        "no_lookahead": True,
        "note": (
            "The replay clock is the close time of the newest visible candle. "
            "Chart reads are bounded by cursor_time; future candles are never "
            "returned. Playback speed is presentation-only."
        ),
    }


def _validate_window_fields(payload: ReplayCreateRequest) -> tuple[str, datetime, datetime]:
    try:
        payload.validate_window()
    except ValueError as exc:
        raise ValidationAppError(str(exc), code="INVALID_TIMEWINDOW") from exc

    symbol = payload.symbol.strip().upper()
    if not symbol:
        raise ValidationAppError("symbol must not be empty.", code="INVALID_SYMBOL")
    if not is_timeframe_supported(payload.timeframe):
        raise ValidationAppError(
            f"Timeframe '{payload.timeframe}' is not supported. "
            "Use one of the timeframes advertised by GET /api/v1/meta.",
            code="UNSUPPORTED_TIMEFRAME",
        )
    if payload.mode == "binary_options" and payload.timeframe != "M1":
        raise ValidationAppError(
            "Binary Options replay uses the M1 timeframe only (product rule).",
            code="MODE_TIMEFRAME_MISMATCH",
        )
    return symbol, _utc(payload.start), _utc(payload.end)


async def create_session(
    session: AsyncSession,
    owner_id: str,
    settings: Settings,
    payload: ReplayCreateRequest,
) -> ReplaySession:
    """Create a paused replay session over a validated stored window."""

    symbol, start, end = _validate_window_fields(payload)
    window = await load_window(
        session,
        symbol=symbol,
        timeframe=payload.timeframe,
        start=start,
        end=end,
        max_candles=settings.replay_max_candles,
    )
    first = window.first_time
    last = window.last_time
    row = ReplaySession(
        owner_id=owner_id,
        mode=payload.mode,
        symbol=symbol,
        timeframe=payload.timeframe,
        status="paused",
        start_time=start,
        end_time=end,
        first_candle_time=first,
        last_candle_time=last,
        cursor_time=first,
        total_candles=len(window.candles),
        visible_candles=1,
        playback_speed=float(payload.playback_speed),
        config={
            "data_source": "postgres_candle_cache",
            "data_source_note": (
                "Closed candles loaded from the platform's PostgreSQL MT5 "
                "cache. Replay never fetches live data during playback."
            ),
            "requested_window": {
                "start": _iso(start),
                "end": _iso(end),
                "mode": payload.mode,
                "symbol": symbol,
                "timeframe": payload.timeframe,
            },
            "resolved_window": {
                "first_candle_time": _iso(first),
                "last_candle_time": _iso(last),
                "candles": len(window.candles),
            },
            "assumptions": {
                "closed_candles_only": True,
                "forming_candles_used": False,
                "gaps_filled": False,
                "candles_invented": False,
                "playback_speed_changes_chronology": False,
                "execution_enabled": False,
            },
            "engine_version": REPLAY_ENGINE_VERSION,
        },
        data_quality=window.quality,
    )
    session.add(row)
    await session.flush()
    return row


async def coverage(
    session: AsyncSession,
    settings: Settings,
    *,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Read-only data-coverage check for the replay configuration panel."""

    symbol = symbol.strip().upper()
    if not symbol:
        raise ValidationAppError("symbol must not be empty.", code="INVALID_SYMBOL")
    if not is_timeframe_supported(timeframe):
        raise ValidationAppError(
            f"Timeframe '{timeframe}' is not supported.",
            code="UNSUPPORTED_TIMEFRAME",
        )
    start_utc = _utc(start)
    end_utc = _utc(end)
    if start_utc >= end_utc:
        raise ValidationAppError(
            "start must be earlier than end.", code="INVALID_TIMEWINDOW"
        )
    window = await load_window(
        session,
        symbol=symbol,
        timeframe=timeframe,
        start=start_utc,
        end=end_utc,
        max_candles=settings.replay_max_candles,
    )
    return coverage_payload(
        window,
        symbol=symbol,
        timeframe=timeframe,
        start=start_utc,
        end=end_utc,
    )


async def get_session(
    session: AsyncSession,
    owner_id: str,
    session_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> ReplaySession:
    stmt = select(ReplaySession).where(
        ReplaySession.id == session_id, ReplaySession.owner_id == owner_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = (await session.scalars(stmt)).first()
    if row is None:
        # Foreign and missing sessions are intentionally indistinguishable.
        raise NotFoundError(f"Replay session '{session_id}' was not found.")
    return row


def _advance_clock(row: ReplaySession, new_cursor: datetime, consumed: int) -> None:
    row.cursor_time = new_cursor
    row.visible_candles = min(
        int(row.total_candles), int(row.visible_candles) + max(0, consumed)
    )
    row.last_advanced_at = datetime.now(tz=timezone.utc)
    if row.visible_candles >= int(row.total_candles):
        row.status = "completed"


async def step_session(
    session: AsyncSession,
    owner_id: str,
    session_id: uuid.UUID,
    payload: ReplayStepRequest,
) -> ReplaySession:
    """Advance by up to ``payload.count`` closed candles, never past the end."""

    row = await get_session(session, owner_id, session_id, for_update=True)
    if row.visible_candles >= row.total_candles:
        row.status = "completed"
        row.last_advanced_at = datetime.now(tz=timezone.utc)
        await session.flush()
        return row

    remaining = int(row.total_candles) - int(row.visible_candles)
    wanted = min(max(1, int(payload.count)), _MAX_STEP, remaining)
    batch = await load_next_closed(
        session,
        symbol=row.symbol,
        timeframe=row.timeframe,
        after=_utc(row.cursor_time),
        end=_utc(row.end_time),
        limit=wanted,
    )
    if not batch:
        # The stored series changed underneath the session or the window has
        # no further closed candles. We do not invent one; close honestly.
        row.status = "completed"
        row.last_advanced_at = datetime.now(tz=timezone.utc)
        await session.flush()
        return row

    used = batch[:remaining]
    last = used[-1]
    _advance_clock(
        row,
        datetime.fromtimestamp(int(last["time"]), tz=timezone.utc),
        len(used),
    )
    await session.flush()
    return row


async def seek_session(
    session: AsyncSession,
    owner_id: str,
    session_id: uuid.UUID,
    payload: ReplaySeekRequest,
) -> ReplaySession:
    """Jump to the latest closed candle at or before ``target_time``."""

    row = await get_session(session, owner_id, session_id, for_update=True)
    target = _utc(payload.target_time)
    if target < _utc(row.first_candle_time):
        raise ValidationAppError(
            "target_time is before the first candle in this replay window.",
            code="SEEK_OUT_OF_WINDOW",
        )
    if target > _utc(row.last_candle_time):
        target = _utc(row.last_candle_time)

    found = await latest_at_or_before(
        session,
        symbol=row.symbol,
        timeframe=row.timeframe,
        first=_utc(row.first_candle_time),
        target=target,
    )
    if found is None:
        raise ValidationAppError(
            "No closed candle exists at or before target_time in this window.",
            code="SEEK_OUT_OF_WINDOW",
        )

    cursor = datetime.fromtimestamp(int(found["time"]), tz=timezone.utc)
    visible = await count_closed(
        session,
        symbol=row.symbol,
        timeframe=row.timeframe,
        start=_utc(row.first_candle_time),
        end=cursor,
    )
    row.cursor_time = cursor
    row.visible_candles = min(int(row.total_candles), max(1, int(visible)))
    row.status = (
        "completed" if row.visible_candles >= int(row.total_candles) else "paused"
    )
    row.last_advanced_at = datetime.now(tz=timezone.utc)
    await session.flush()
    return row


async def resume_session(
    session: AsyncSession, owner_id: str, session_id: uuid.UUID
) -> ReplaySession:
    row = await get_session(session, owner_id, session_id, for_update=True)
    if row.status == "completed" or row.visible_candles >= row.total_candles:
        raise ConflictError(
            "The replay has reached the end of its window. Reset it to replay again.",
            code="REPLAY_AT_END",
        )
    row.status = "active"
    await session.flush()
    return row


async def pause_session(
    session: AsyncSession, owner_id: str, session_id: uuid.UUID
) -> ReplaySession:
    row = await get_session(session, owner_id, session_id, for_update=True)
    if row.status == "completed":
        raise ConflictError(
            "The replay has already completed.", code="REPLAY_COMPLETED"
        )
    row.status = "paused"
    await session.flush()
    return row


async def reset_session(
    session: AsyncSession, owner_id: str, session_id: uuid.UUID
) -> ReplaySession:
    row = await get_session(session, owner_id, session_id, for_update=True)
    row.cursor_time = _utc(row.first_candle_time)
    row.visible_candles = 1
    row.status = "paused"
    row.last_advanced_at = datetime.now(tz=timezone.utc)
    await session.flush()
    return row


async def update_session(
    session: AsyncSession,
    owner_id: str,
    session_id: uuid.UUID,
    payload: ReplayUpdateRequest,
) -> ReplaySession:
    row = await get_session(session, owner_id, session_id, for_update=True)
    row.playback_speed = float(payload.playback_speed)
    await session.flush()
    return row


async def delete_session(
    session: AsyncSession, owner_id: str, session_id: uuid.UUID
) -> None:
    row = await get_session(session, owner_id, session_id, for_update=True)
    await session.delete(row)
    await session.flush()


async def session_candles(
    session: AsyncSession,
    owner_id: str,
    session_id: uuid.UUID,
    *,
    limit: int,
) -> dict[str, Any]:
    """Return only candles at or before the cursor (newest ``limit``)."""

    row = await get_session(session, owner_id, session_id)
    candles = await load_visible(
        session,
        symbol=row.symbol,
        timeframe=row.timeframe,
        first=_utc(row.first_candle_time),
        cursor=_utc(row.cursor_time),
        limit=limit,
    )
    if candles and int(candles[-1]["time"]) > int(row.cursor_time.timestamp()):
        # Defensive assertion for the single most important replay invariant.
        raise ConflictError(
            "Replay invariant violated: a future candle was selected.",
            code="REPLAY_LOOKAHEAD_GUARD",
        )
    summary = session_summary(row)
    return {
        "session_id": str(row.id),
        "symbol": row.symbol,
        "timeframe": row.timeframe,
        "status": row.status,
        "timezone": "UTC",
        "candles": candles,
        "count": len(candles),
        "cursor_time": summary["cursor_time"],
        "current_time": summary["current_time"],
        "current_time_unix": summary["current_time_unix"],
        "visible_candles": summary["visible_candles"],
        "total_candles": summary["total_candles"],
        "progress": summary["progress"],
        "data_quality": row.data_quality,
        "no_lookahead": True,
        "future_candles_excluded": True,
        "note": (
            "Every returned candle has an open time <= the session cursor. "
            "The newest returned candle is the current replay candle; its "
            "close time is the simulated now."
        ),
    }


async def list_sessions(
    session: AsyncSession,
    owner_id: str,
    *,
    mode: str | None = None,
    status: str | None = None,
    symbol: str | None = None,
    page: int = 1,
    page_size: int = _PAGE_SIZE_DEFAULT,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    page = max(1, int(page))
    page_size = min(max(1, int(page_size)), _PAGE_SIZE_MAX)
    stmt = select(ReplaySession).where(ReplaySession.owner_id == owner_id)
    if mode:
        stmt = stmt.where(ReplaySession.mode == mode)
    if status:
        stmt = stmt.where(ReplaySession.status == status)
    if symbol:
        stmt = stmt.where(ReplaySession.symbol == symbol.strip().upper())
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(
        (
            await session.scalars(
                stmt.order_by(ReplaySession.created_at.desc(), ReplaySession.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    total_int = int(total or 0)
    pages = math.ceil(total_int / page_size) if page_size else 1
    items = [
        {
            "id": str(row.id),
            "mode": row.mode,
            "symbol": row.symbol,
            "timeframe": row.timeframe,
            "status": row.status,
            "start_time": _iso(_utc(row.start_time)),
            "end_time": _iso(_utc(row.end_time)),
            "cursor_time": _iso(_utc(row.cursor_time)),
            "visible_candles": int(row.visible_candles),
            "total_candles": int(row.total_candles),
            "progress": round(int(row.visible_candles) / max(1, int(row.total_candles)), 6),
            "playback_speed": float(row.playback_speed),
            "data_quality_status": (row.data_quality or {}).get("overall_status"),
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ]
    return items, {
        "page": page,
        "page_size": page_size,
        "total": total_int,
        "pages": max(1, pages),
    }


__all__ = [
    "coverage",
    "create_session",
    "delete_session",
    "get_session",
    "list_sessions",
    "pause_session",
    "reset_session",
    "resume_session",
    "seek_session",
    "session_candles",
    "session_summary",
    "step_session",
    "update_session",
]
