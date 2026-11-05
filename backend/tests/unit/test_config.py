"""Unit tests: configuration validation (fail-fast behaviour)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core.config import ConfigError, Settings

PG_URL = "postgresql+asyncpg://ta_user:pw@localhost:5432/ta_platform"

# Phase 9: any production-mode Settings construction must carry an API token
# (production refuses to start without one — see TestApiAuthConfig).
TEST_TOKEN = "phase9-test-token-0123456789"


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
                api_auth_token=TEST_TOKEN,
            )

    def test_url_masking_hides_password(self) -> None:
        s = _settings(database_url="postgresql+asyncpg://ta_user:supersecret@db:5432/x")
        masked = s.database_url_safe()
        assert "supersecret" not in masked
        assert "ta_user:***" in masked


class TestEnvironmentConsistency:
    def test_debug_forbidden_in_production(self) -> None:
        with pytest.raises(ConfigError, match="DEBUG"):
            _settings(app_env="production", debug=True, api_auth_token=TEST_TOKEN)

    def test_docs_disabled_in_production_by_default(self) -> None:
        s = _settings(app_env="production", api_auth_token=TEST_TOKEN)
        assert s.enable_docs is False

    def test_docs_enabled_locally_by_default(self) -> None:
        s = _settings()
        assert s.enable_docs is True

    def test_docs_can_be_forced(self) -> None:
        s = _settings(
            app_env="production", docs_enabled=True, api_auth_token=TEST_TOKEN
        )
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


class TestMarketDataStartupConfig:
    """Market-data startup behaviour and replay limits have safe defaults."""

    def test_mt5_warmup_disabled_by_default(self) -> None:
        # A wedged terminal must never block the HTTP server at startup.
        s = _settings()
        assert s.mt5_warmup_on_startup is False

    def test_mt5_warmup_can_be_enabled(self) -> None:
        s = _settings(mt5_warmup_on_startup=True)
        assert s.mt5_warmup_on_startup is True

    def test_replay_limits_default(self) -> None:
        s = _settings()
        assert s.replay_max_candles == 20_000
        assert s.replay_max_candles_returned == 5_000
        assert s.replay_max_creates_per_minute == 30


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


class TestApiAuthConfig:
    """Phase 9: bearer-token configuration (validation + fail-fast)."""

    def test_disabled_by_default(self) -> None:
        s = _settings()
        assert s.api_auth_enabled is False
        assert s.resolved_api_tokens == []
        assert s.auth_config_summary()["enabled"] is False

    def test_single_deployment_token(self) -> None:
        s = _settings(api_auth_token=TEST_TOKEN)
        assert s.api_auth_enabled is True
        assert s.resolved_api_tokens == [(TEST_TOKEN, None)]
        summary = s.auth_config_summary()
        assert summary == {
            "enabled": True,
            "token_count": 1,
            "bound_owner_count": 0,
        }
        # The token itself must never appear in the summary.
        assert TEST_TOKEN not in str(summary)

    def test_csv_with_owner_bindings(self) -> None:
        s = _settings(
            api_auth_tokens=f"{TEST_TOKEN}:alice, tok-unbound-9876543210"
        )
        assert s.resolved_api_tokens == [
            (TEST_TOKEN, "alice"),
            ("tok-unbound-9876543210", None),
        ]
        assert s.auth_config_summary()["bound_owner_count"] == 1

    def test_duplicate_token_deduplicated(self) -> None:
        s = _settings(
            api_auth_token=TEST_TOKEN, api_auth_tokens=f"{TEST_TOKEN}"
        )
        assert len(s.resolved_api_tokens) == 1

    def test_same_token_conflicting_bindings_rejected(self) -> None:
        with pytest.raises(ConfigError, match="different"):
            _settings(
                api_auth_tokens=f"{TEST_TOKEN}:alice, {TEST_TOKEN}:bob"
            )

    def test_token_too_short_rejected(self) -> None:
        with pytest.raises(ConfigError, match="at least 12"):
            _settings(api_auth_token="short")

    def test_token_with_whitespace_rejected(self) -> None:
        with pytest.raises(ConfigError, match="printable ASCII"):
            _settings(api_auth_token="has space inside-123")

    def test_binding_with_empty_owner_rejected(self) -> None:
        with pytest.raises(ConfigError, match="token:owner"):
            _settings(api_auth_tokens=f"{TEST_TOKEN}:")

    def test_binding_with_invalid_owner_rejected(self) -> None:
        with pytest.raises(ConfigError, match="owner bindings"):
            _settings(api_auth_tokens=f"{TEST_TOKEN}:bad owner!")

    def test_production_requires_a_token(self) -> None:
        with pytest.raises(ConfigError, match="requires API authentication"):
            _settings(app_env="production")

    def test_production_rejects_placeholder_token(self) -> None:
        with pytest.raises(ConfigError, match="placeholder"):
            _settings(app_env="production", api_auth_token="CHANGE_ME-token-123")

    def test_local_mode_accepts_no_token(self) -> None:
        # Backwards compatibility: local single-user mode is unchanged.
        s = _settings(app_env="local")
        assert s.api_auth_enabled is False
