"""Analysis report persistence (owner-scoped, immutable after creation).

Reports are never mutated: a new analysis run always inserts a NEW row.
Every payload is self-contained for audit (rule-set snapshot included)
while candle data is referenced, not duplicated.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.models import AnalysisReport

_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 100


async def save_report(
    session: AsyncSession, owner_id: str, report: dict[str, Any]
) -> AnalysisReport:
    """Persist one analysis report and return the row.

    The row id equals the payload's ``analysis_id`` so the id returned by
    ``POST /analysis/run`` can be used directly for retrieval (and by the
    Phase 7 AI explanation endpoint) — one id everywhere, no drift.
    """

    strategy_id = report["strategy"]["id"]
    evaluated_at = datetime.fromisoformat(report["evaluated_at"])
    row = AnalysisReport(
        id=uuid.UUID(report["analysis_id"]),
        owner_id=owner_id,
        symbol=report["symbol"],
        mode=report["mode"],
        strategy_id=uuid.UUID(strategy_id) if strategy_id else None,
        strategy_name=report["strategy"]["name"],
        strategy_version=report["strategy"]["version"],
        setup_status=report["evaluation"]["setup_status"],
        direction=report["strategy"]["direction"],
        regime=report["regime"]["primary"]["regime"],
        report_schema_version=report["report_schema_version"],
        engine_version=report["engine_version"],
        evaluated_at=evaluated_at,
        report=report,
    )
    session.add(row)
    await session.flush()
    return row


async def get_report(
    session: AsyncSession, owner_id: str, report_id: uuid.UUID
) -> AnalysisReport:
    """Fetch one report (owner-scoped; foreign owners get 404)."""

    row = await session.get(AnalysisReport, report_id)
    if row is None or row.owner_id != owner_id:
        raise NotFoundError(f"Analysis report '{report_id}' was not found.")
    return row


async def list_reports(
    session: AsyncSession,
    owner_id: str,
    *,
    symbol: str | None = None,
    strategy_id: uuid.UUID | None = None,
    mode: str | None = None,
    setup_status: str | None = None,
    page: int = 1,
    page_size: int = _PAGE_SIZE_DEFAULT,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Paginated history (owner-scoped), newest first. Returns light
    summaries (not the full payloads) plus page metadata."""

    page = max(1, page)
    page_size = min(max(1, page_size), _PAGE_SIZE_MAX)

    stmt = select(AnalysisReport).where(AnalysisReport.owner_id == owner_id)
    if symbol:
        stmt = stmt.where(AnalysisReport.symbol == symbol.upper())
    if strategy_id is not None:
        stmt = stmt.where(AnalysisReport.strategy_id == strategy_id)
    if mode:
        stmt = stmt.where(AnalysisReport.mode == mode)
    if setup_status:
        stmt = stmt.where(AnalysisReport.setup_status == setup_status)

    total = await session.scalar(
        select(func.count()).select_from(stmt.subquery())
    )
    rows = list(
        (
            await session.scalars(
                stmt.order_by(AnalysisReport.created_at.desc(), AnalysisReport.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    total_int = int(total or 0)
    pages = (total_int + page_size - 1) // page_size if page_size else 1
    items = [
        {
            "id": str(row.id),
            "symbol": row.symbol,
            "mode": row.mode,
            "strategy_id": str(row.strategy_id) if row.strategy_id else None,
            "strategy_name": row.strategy_name,
            "strategy_version": row.strategy_version,
            "setup_status": row.setup_status,
            "direction": row.direction,
            "regime": row.regime,
            "report_schema_version": row.report_schema_version,
            "engine_version": row.engine_version,
            "evaluated_at": row.evaluated_at.isoformat(timespec="milliseconds"),
            "created_at": row.created_at.isoformat(timespec="milliseconds"),
        }
        for row in rows
    ]
    return items, {
        "page": page,
        "page_size": page_size,
        "total": total_int,
        "pages": max(1, pages),
    }


__all__ = ["get_report", "list_reports", "save_report"]
