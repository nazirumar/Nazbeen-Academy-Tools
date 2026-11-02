"""Health & readiness endpoints (unversioned, infrastructure surface)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.responses import envelope
from app.db.session import check_database
from app.domains.market_data.base import get_provider

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def liveness() -> dict:
    """Liveness: the process is up. Checks no dependencies."""
    return envelope({"status": "live"})


@router.get("/health/ready")
async def readiness(
    response: Response,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Readiness: PostgreSQL reachable + market-data provider status.

    - HTTP 200 when PostgreSQL is reachable (the platform is usable).
    - HTTP 503 when PostgreSQL is down, with the underlying error surfaced.
    - Market data being unavailable does NOT make the platform unready
      (Phase 1 supports user-supplied candles) — it is reported transparently.
    """

    db_check = await check_database(settings)
    try:
        provider = get_provider(settings)
        provider_status = provider.status().to_dict()
    except Exception as exc:  # noqa: BLE001 - misconfiguration is reported, not hidden
        provider_status = {
            "provider": settings.market_data_provider,
            "available": False,
            "connected": False,
            "detail": f"Provider configuration error: {exc}",
            "checked_at": None,
        }

    ready = db_check["status"] == "up"
    if not ready:
        response.status_code = 503

    return envelope(
        {
            "status": "ready" if ready else "not_ready",
            "checks": {
                "database": {
                    **db_check,
                    "engine": "postgresql",
                },
                "market_data": {
                    **provider_status,
                    "blocks_readiness": False,
                },
            },
        }
    )


@router.get("/health/db", include_in_schema=False)
async def db_ping(settings: Settings = Depends(get_settings)) -> dict:
    """Quick DB-only probe used by docker healthchecks and operators."""
    result = await check_database(settings)
    if result["status"] != "up":
        return envelope(result)
    # Also confirm the schema is migrated (alembic_version present).
    from app.db.session import get_engine

    async with get_engine().connect() as conn:
        has_version = (
            await conn.execute(
                text("SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                     "WHERE table_name = 'alembic_version')")
            )
        ).scalar()
    result["migrated"] = bool(has_version)
    return envelope(result)
