"""Async SQLAlchemy engine/session management and the DB readiness probe."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(settings: Settings | None = None) -> AsyncEngine:
    """Create (or replace) the global engine. Called at startup and in tests."""

    global _engine, _session_factory
    settings = settings or get_settings()
    _engine = create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        connect_args={"timeout": int(settings.db_connect_timeout_seconds)},
    )
    _session_factory = async_sessionmaker(
        _engine, expire_on_commit=False, autoflush=False
    )
    logger.info(
        "database_engine_initialized",
        extra={"database": settings.database_url_safe()},
    )
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        return init_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        init_engine()
    assert _session_factory is not None
    return _session_factory


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("database_engine_disposed")


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a transactional session."""

    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_database(settings: Settings | None = None) -> dict[str, Any]:
    """Probe PostgreSQL connectivity; never raises.

    Returns ``{"status": "up", "latency_ms": float}`` or
    ``{"status": "down", "error": str}``.
    """

    settings = settings or get_settings()
    started = time.perf_counter()
    try:
        engine = get_engine()
        async with asyncio.timeout(settings.healthcheck_timeout_seconds):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return {
            "status": "up",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception as exc:  # noqa: BLE001 - readiness must not raise
        logger.warning("database_healthcheck_failed", extra={"error": str(exc)})
        return {
            "status": "down",
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
