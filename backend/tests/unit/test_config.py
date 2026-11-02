"""Unit tests: configuration validation (fail-fast behaviour)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core.config import ConfigError, Settings

PG_URL = "postgresql+asyncpg://ta_user:pw@localhost:5432/ta_platform"


def _settings(**overrides: object) -> Settings:
    env: dict[str, object] = {
        "database_url": PG_URL,
        "app_env": "local",
    }
    env.update(overrides)
    return Settings(**env)  # type: ignore[arg-type]


class TestDatabaseUrl:
    def test_valid_url_accepted(self) -> None:
        s = _settings()
        assert s.database_url == PG_URL

    def test_plain_postgresql_scheme_normalized(self) -> None:
        s = _settings(database_url="postgresql://ta_user:pw@db:5432/ta_platform")
        assert s.database_url.startswith("postgresql+asyncpg://")

    def test_non_postgres_scheme_rejected(self) -> None:
        with pytest.raises(ConfigError, match="PostgreSQL"):
            _settings(database_url="file:/home/z/db/custom.db")

    def test_empty_url_rejected(self) -> None:
        with pytest.raises(ConfigError, match="empty"):
            _settings(database_url="  ")

    def test_placeholder_rejected_outside_local(self) -> None:
        with pytest.raises(ConfigError, match="placeholder"):
            _settings(
                database_url="postgresql+asyncpg://ta_user:CHANGE_ME@db/ta",
                app_env="production",
            )

    def test_url_masking_hides_password(self) -> None:
        s = _settings(database_url="postgresql+asyncpg://ta_user:supersecret@db:5432/x")
        masked = s.database_url_safe()
        assert "supersecret" not in masked
        assert "ta_user:***" in masked


class TestEnvironmentConsistency:
    def test_debug_forbidden_in_production(self) -> None:
        with pytest.raises(ConfigError, match="DEBUG"):
            _settings(app_env="production", debug=True)

    def test_docs_disabled_in_production_by_default(self) -> None:
        s = _settings(app_env="production")
        assert s.enable_docs is False

    def test_docs_enabled_locally_by_default(self) -> None:
        s = _settings()
        assert s.enable_docs is True

    def test_docs_can_be_forced(self) -> None:
        s = _settings(app_env="production", docs_enabled=True)
        assert s.enable_docs is True

    def test_cors_origins_parsed_from_csv(self) -> None:
        s = _settings(cors_origins="http://a.dev, http://b.dev")
        assert s.cors_origins == ["http://a.dev", "http://b.dev"]

    def test_cors_origin_without_scheme_rejected(self) -> None:
        with pytest.raises(ConfigError, match="absolute origins"):
            _settings(cors_origins="localhost:3000")

    def test_port_out_of_range_rejected(self) -> None:
        with pytest.raises(Exception):
            _settings(api_port=99999)

    def test_unknown_market_data_provider_rejected(self) -> None:
        with pytest.raises(Exception):
            _settings(market_data_provider="binance")


class TestGetSettings:
    def test_polluted_non_postgres_env_url_is_ignored(self, monkeypatch) -> None:
        """Frontend tooling (e.g. Prisma) may export a file: DATABASE_URL —
        the backend must ignore it in favour of its own .env file.

        The .env file is provisioned BY THE TEST (temporarily) so the case
        never depends on ambient sandbox state; CWD is the backend directory
        (pydantic-settings resolves ``env_file=".env"`` against it).
        """

        from app.core import config as config_module

        polluted = "file:/home/z/other-tool/db.sqlite"
        env_path = Path(config_module.__file__).resolve().parents[2] / ".env"
        env_path.write_text(
            "DATABASE_URL=postgresql+asyncpg://ta_user:pw@localhost:5432/ta_platform\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("DATABASE_URL", polluted)
        monkeypatch.setenv("APP_ENV", "local")
        config_module.get_settings.cache_clear()
        try:
            settings = config_module.get_settings()
            # backend/.env supplies a postgres URL; the polluted file: value
            # must never win.
            assert settings.database_url.startswith(
                ("postgresql://", "postgresql+asyncpg://")
            )
            assert polluted not in settings.database_url
        finally:
            env_path.unlink(missing_ok=True)
            config_module.get_settings.cache_clear()
