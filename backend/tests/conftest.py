"""Pytest fixtures: real PostgreSQL (pgserver) + ASGI test client.

Integration tests run against a REAL PostgreSQL server provided by the
``pgserver`` package (bundled binaries, no root required). If pgserver is
not installed, integration tests skip with a clear message instead of
silently faking the database.
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
# The research creation rate limit defaults to a production-safe value;
# the integration suite creates many runs per minute, so tests run with a
# much higher cap and exercise the limiter explicitly (429 test).
os.environ.setdefault("RESEARCH_MAX_CREATES_PER_MINUTE", "1000")


@pytest.fixture(scope="session")
def database_url() -> str:
    """Session-scoped real PostgreSQL database (schema migrated once)."""

    try:
        import pgserver
    except ImportError:
        pytest.skip(
            "pgserver is required for integration tests: "
            "pip install -r requirements-dev.txt"
        )

    pgdata = Path(tempfile.mkdtemp(prefix="ta-pytest-pg-"))
    server = pgserver.get_server(str(pgdata), cleanup_mode=None)
    dbname = f"ta_test_{uuid.uuid4().hex[:8]}"
    server.psql(f"CREATE DATABASE {dbname};")

    url = f"postgresql+asyncpg://postgres@/{dbname}?host={pgdata}"

    # Apply migrations once for the whole session (exercises Alembic too).
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    os.environ["ALEMBIC_DATABASE_URL"] = url
    command.upgrade(cfg, "head")

    os.environ["DATABASE_URL"] = url
    yield url

    # best-effort cleanup of the temp cluster
    try:
        import shutil

        shutil.rmtree(pgdata, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture
async def app_client(database_url: str):
    """Async httpx client bound to the ASGI app with engine initialized.

    All tables are truncated BEFORE each test (deterministic isolation).
    """

    from fastapi import FastAPI  # noqa: F401
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.core.config import get_settings
    from app.db.base import Base
    from app.db.session import dispose_engine, get_engine, init_engine

    get_settings.cache_clear()
    settings = get_settings()
    init_engine(settings)

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
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

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
