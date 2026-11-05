"""Pytest fixtures: real PostgreSQL (pgserver) + ASGI test client.

Integration tests run against a REAL PostgreSQL server provided by the
``pgserver`` package (bundled binaries, no root required). If pgserver is
not installed, integration tests skip with a clear message instead of
silently faking the database.

On platforms where pgserver is unavailable (it publishes no Windows wheels),
set ``TA_TEST_DATABASE_URL`` to a disposable PostgreSQL database whose name
contains ``test``. The fixture applies migrations to it and the suite
truncates its tables before every test. Any other database name is rejected
so a developer cannot point the destructive fixtures at real data.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]

# Test configuration must be set BEFORE app modules are imported.
os.environ["APP_ENV"] = "test"
os.environ["LOG_FORMAT"] = "console"
os.environ.setdefault("CORS_ORIGINS", "")
# Hermetic test environment: never attach to a paid LLM provider from an
# ordinary test run, even when the developer's machine (or backend/.env) has
# one configured. AI tests inject a scripted fake. MT5 stays enabled by
# default so the existing ``fake_mt5`` adapter fixture is used; tests that
# require an unavailable terminal set MT5_ENABLED=false explicitly, and the
# live-terminal test opts in with RUN_MT5_LIVE=1 + MT5_ENABLED=true.
os.environ.setdefault("AI_ENABLED", "false")
# The research/backtest creation rate limits default to production-safe
# values; the integration suite creates many runs per minute, so tests run
# with a much higher cap and exercise the limiters explicitly (429 tests).
os.environ.setdefault("RESEARCH_MAX_CREATES_PER_MINUTE", "1000")
os.environ.setdefault("BACKTEST_MAX_CREATES_PER_MINUTE", "1000")


def _apply_migrations(url: str) -> None:
    """Run Alembic to head against the given database URL."""

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    os.environ["ALEMBIC_DATABASE_URL"] = url
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def database_url() -> str:
    """Session-scoped real PostgreSQL database (schema migrated once)."""

    override = os.environ.get("TA_TEST_DATABASE_URL")
    if override:
        from sqlalchemy.engine import make_url

        parsed = make_url(override)
        dbname = parsed.database or ""
        if "test" not in dbname.lower():
            raise RuntimeError(
                "TA_TEST_DATABASE_URL must point to a disposable database whose "
                f"name contains 'test' (got {dbname!r}); the suite truncates tables."
            )
        url = override
        if url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://") :]
        _apply_migrations(url)
        os.environ["DATABASE_URL"] = url
        yield url
        return

    try:
        import pgserver
    except ImportError:
        pytest.skip(
            "pgserver is required for integration tests on this platform: "
            "pip install -r requirements-dev.txt (or set TA_TEST_DATABASE_URL "
            "to a disposable PostgreSQL database whose name contains 'test')"
        )

    pgdata = Path(tempfile.mkdtemp(prefix="ta-pytest-pg-"))
    server = pgserver.get_server(str(pgdata), cleanup_mode=None)
    dbname = f"ta_test_{uuid.uuid4().hex[:8]}"
    server.psql(f"CREATE DATABASE {dbname};")

    url = f"postgresql+asyncpg://postgres@/{dbname}?host={pgdata}"

    # Apply migrations once for the whole session (exercises Alembic too).
    _apply_migrations(url)

    os.environ["DATABASE_URL"] = url
    yield url

    # best-effort cleanup of the temp cluster
    try:
        import shutil

        shutil.rmtree(pgdata, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture
async def app_client(request, database_url: str):
    """Async httpx client bound to the ASGI app with engine initialized.

    All tables are truncated BEFORE each test (deterministic isolation).

    Indirect parametrization: a test may pass a dict of extra environment
    variables via ``pytest.mark.parametrize("app_client", [{...}],
    indirect=True)`` — applied before settings are built (e.g. API auth
    tokens) and removed after the test so they never leak into other tests.
    """

    extra_env: dict[str, str] = dict(getattr(request, "param", None) or {})
    for key, value in extra_env.items():
        os.environ[key] = value

    from fastapi import FastAPI  # noqa: F401
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.core.config import get_settings
    from app.db.base import Base
    from app.db.session import dispose_engine, get_engine, init_engine

    get_settings.cache_clear()
    settings = get_settings()
    init_engine(settings)

    # The MT5 adapter is a process-wide singleton that captures the Settings
    # it was built with. Reset it so a test that changes MT5 config cannot
    # observe a stale adapter from an earlier test.
    from app.domains.market_data.mt5_adapter import reset_mt5_adapter

    reset_mt5_adapter()

    # In-process rate-limit windows must not leak across tests.
    from app.core.rate_limit import reset_rate_limits

    reset_rate_limits()

    # Truncate before the test runs (order: children first via CASCADE).
    async with get_engine().begin() as conn:
        for table in Base.metadata.sorted_tables:
            await conn.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))

    from app.main import create_app

    application = create_app(settings)

    transport = ASGITransport(app=application)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client
    finally:
        for key in extra_env:
            os.environ.pop(key, None)
        from app.domains.market_data.mt5_adapter import reset_mt5_adapter

        reset_mt5_adapter()
        await dispose_engine()
        get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# Phase 2: MetaTrader 5 test fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def env_settings(monkeypatch):
    """Minimal valid Settings for unit tests (no database server needed).

    If a DATABASE_URL is already configured (integration runs via the
    ``database_url`` fixture) it is respected; only unit-only runs inject a
    never-connected placeholder URL (SQLAlchemy engines are lazy).
    """

    import os

    from app.core.config import Settings, get_settings

    current_url = os.environ.get("DATABASE_URL", "")
    if not current_url.startswith(("postgresql+asyncpg://", "postgresql://")):
        monkeypatch.setenv(
            "DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/unit"
        )
    monkeypatch.setenv("CORS_ORIGINS", "")
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    settings = Settings()
    get_settings.cache_clear()
    yield settings
    get_settings.cache_clear()


@pytest.fixture
def fake_mt5(monkeypatch, env_settings):
    """Install an adapter backed by the FakeMT5 module as the singleton.

    Reconnection sleeps are stubbed so bounded-retry paths stay fast.
    """

    from app.domains.market_data import mt5_adapter as adapter_module
    from tests.fakes.fake_mt5 import FakeMT5

    fake = FakeMT5()
    adapter = adapter_module.MT5Adapter(
        env_settings, mt5_module=fake, sleep=lambda _s: None
    )
    monkeypatch.setattr(adapter_module, "_adapter", adapter)
    yield fake
    adapter_module.reset_mt5_adapter()
