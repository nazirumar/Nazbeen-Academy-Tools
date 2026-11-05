"""FastAPI application factory.

Wires together: configuration, structured logging, request correlation,
consistent response/error envelopes, database lifecycle and the v1 API.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app as app_package
from app.api.v1 import api_v1_router, health_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import (
    RequestIdMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.responses import ok
from app.db.session import dispose_engine, get_session_factory, init_engine

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application (kept deterministic for tests)."""

    settings = settings or get_settings()
    configure_logging(level=settings.log_level, log_format=settings.log_format)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Fail fast on unknown provider configuration (never mid-request).
        from app.domains.market_data.base import get_provider

        get_provider(settings)
        init_engine(settings)

        # Warm up the MT5 connection in the background only when explicitly
        # enabled. The adapter connects lazily anyway; a wedged terminal must
        # never block the HTTP server during startup.
        warmup_task: asyncio.Task | None = None
        if settings.mt5_enabled and settings.mt5_warmup_on_startup:
            from app.domains.market_data.mt5_adapter import get_mt5_adapter

            adapter = get_mt5_adapter(settings)

            async def _warmup() -> None:
                try:
                    await adapter.run(
                        adapter.sync_check, timeout=adapter.connect_budget_seconds()
                    )
                    logger.info("mt5_warmup_connected")
                except Exception as exc:  # noqa: BLE001 - startup must not fail
                    logger.info(
                        "mt5_warmup_unavailable",
                        extra={"reason": f"{type(exc).__name__}: {exc}"},
                    )

            warmup_task = asyncio.create_task(_warmup())

        logger.info(
            "application_started",
            extra={
                "app": settings.app_name,
                "version": settings.app_version,
                "env": settings.app_env,
                "database": settings.database_url_safe(),
                "market_data_provider": settings.market_data_provider,
                "mt5_enabled": settings.mt5_enabled,
                "docs_enabled": settings.enable_docs,
                "auth": settings.auth_config_summary(),
            },
        )
        # Phase 9: be loud (but honest) when an explicitly multi-user or
        # internet-facing environment runs without API authentication.
        if not settings.api_auth_enabled and settings.app_env in (
            "development",
            "staging",
        ):
            logger.warning(
                "api_auth_disabled",
                extra={
                    "env": settings.app_env,
                    "hint": "Set API_AUTH_TOKEN or API_AUTH_TOKENS to require "
                    "bearer authentication on /api/v1 (mandatory in production).",
                },
            )

        # Phase 6: honest startup recovery — runs left queued/running by a
        # previous process are marked failed (in-process runs do not
        # survive restarts; the platform never fakes completion).
        try:
            from app.domains.backtest import service as backtest_service

            async with get_session_factory()() as session:
                recovered = await backtest_service.recover_interrupted(session)
                await session.commit()
            if recovered:
                logger.info("backtest_runs_recovered", extra={"count": recovered})
        except Exception as exc:  # noqa: BLE001 - startup must not fail
            logger.warning(
                "backtest_recovery_failed",
                extra={"reason": f"{type(exc).__name__}: {exc}"},
            )

        # Phase 8: same honest recovery for research/optimization runs.
        try:
            from app.domains.research import service as research_service

            async with get_session_factory()() as session:
                recovered_research = await research_service.recover_interrupted(session)
                await session.commit()
            if recovered_research:
                logger.info(
                    "research_runs_recovered", extra={"count": recovered_research}
                )
        except Exception as exc:  # noqa: BLE001 - startup must not fail
            logger.warning(
                "research_recovery_failed",
                extra={"reason": f"{type(exc).__name__}: {exc}"},
            )

        yield
        if warmup_task is not None and not warmup_task.done():
            warmup_task.cancel()
        # Release terminal resources + worker thread cleanly.
        from app.domains.market_data.mt5_adapter import reset_mt5_adapter

        reset_mt5_adapter()
        await dispose_engine()
        logger.info("application_stopped")

    app = FastAPI(
        title=settings.app_name,
        version=app_package.__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )

    # Middleware order: last added runs first (outermost). CORS must see the
    # request first; security headers wrap everything; logging next;
    # request-id is innermost.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RequestIdMiddleware)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID"],
        )

    # Small guard against oversized payloads (DoS / accidental multi-MB pastes).
    limit = settings.request_max_body_bytes

    @app.middleware("http")
    async def body_size_limit(request, call_next):  # type: ignore[no-untyped-def]
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > limit:
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=413,
                content={
                    "success": False,
                    "data": None,
                    "error": {
                        "code": "PAYLOAD_TOO_LARGE",
                        "message": f"Request body exceeds the "
                        f"{limit // (1024 * 1024)} MB limit.",
                        "details": [],
                    },
                    "meta": {"request_id": None, "timestamp": None, "api_version": "v1"},
                },
            )
        return await call_next(request)

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(api_v1_router, prefix=settings.api_v1_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return ok(
            {
                "service": settings.app_name,
                "version": app_package.__version__,
                "environment": settings.app_env,
                "api_prefix": settings.api_v1_prefix,
                "health": {"liveness": "/health/live", "readiness": "/health/ready"},
                "docs": "/docs" if settings.enable_docs else None,
            }
        )

    return app


# Module-level ASGI application for `uvicorn app.main:app` (docker, scripts).
# Construction validates configuration and fails fast on invalid settings.
app = create_app()
