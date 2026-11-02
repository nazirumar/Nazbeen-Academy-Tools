"""Application settings with fail-fast validation.

All configuration comes from environment variables (optionally a `.env` file
next to the `backend/` directory). The application refuses to start when
configuration is invalid or when placeholder credentials are used outside
local/test environments. Dependency outages (e.g. PostgreSQL unreachable)
do NOT crash the app: they are reported honestly by `/health/ready`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AppEnv = Literal["local", "development", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormat = Literal["json", "console"]

PLACEHOLDER_MARKERS = ("CHANGE_ME", "CHANGEME", "PLACEHOLDER", "<", ">")

POSTGRES_SCHEMES = ("postgresql+asyncpg://", "postgresql://")


class ConfigError(Exception):
    """Raised when the provided configuration cannot be used to start the app."""


class Settings(BaseSettings):
    """Validated application settings (loaded from environment / .env file)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application identity -------------------------------------------------
    app_name: str = "TA Platform API"
    app_env: AppEnv = "local"
    app_version: str = "0.1.0"
    debug: bool = False

    # --- HTTP server -----------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_v1_prefix: str = "/api/v1"
    # Feature docs are disabled in production unless explicitly enabled.
    docs_enabled: bool | None = None

    # --- Database (PostgreSQL) -------------------------------------------------
    # Required: no silent fallback. Use e.g.
    #   postgresql+asyncpg://ta_user:secret@localhost:5432/ta_platform
    database_url: str
    db_echo: bool = False
    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    db_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    # Readiness probe query timeout.
    healthcheck_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    # --- Security ---------------------------------------------------------------
    # Comma-separated list of allowed CORS origins (empty = same-origin only).
    # NoDecode: env values arrive raw (CSV), parsed by the validator below —
    # pydantic-settings would otherwise demand JSON for list fields.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Observability ----------------------------------------------------------
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"

    # --- Market data --------------------------------------------------------------
    # The MetaTrader 5 provider is the only provider. When MT5 is not installed
    # or the terminal is offline the app stays up and reports honestly.
    market_data_provider: Literal["mt5"] = "mt5"

    # --- MetaTrader 5 integration (Phase 2) ----------------------------------------
    # Security model:
    # - Credentials live ONLY here (environment / backend/.env). They are never
    #   accepted by any API endpoint, never logged and never returned.
    # - MT5_TERMINAL_PATH is configuration-only; API requests cannot pass an
    #   arbitrary executable path or trigger command execution.
    mt5_enabled: bool = True
    # Optional explicit login. When empty the adapter attaches to the already
    # logged-in terminal (recommended — no credentials stored anywhere).
    mt5_login: int | None = None
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_terminal_path: str = ""
    # Bounded, serialized terminal access.
    mt5_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    mt5_retry_attempts: int = Field(default=3, ge=1, le=10)
    mt5_retry_backoff_seconds: float = Field(default=1.5, gt=0, le=60)
    # Candle request limits (API clamps against these).
    mt5_max_candles: int = Field(default=5000, ge=1, le=100_000)
    mt5_default_candles: int = Field(default=200, ge=1)
    # PostgreSQL cache of historical candles.
    mt5_cache_enabled: bool = True
    # A cached series may be served when its newest candle is younger than
    # max(cache_max_age_seconds, timeframe_interval + grace) seconds.
    mt5_cache_max_age_seconds: int = Field(default=90, ge=5, le=86400)
    # Closed candles older than N intervals (plus a market-closure grace for
    # D1/weekends) are reported as STALE_DATA.
    mt5_stale_after_intervals: float = Field(default=3.0, gt=0, le=1000)

    # --- Backtesting (Phase 6) ------------------------------------------------------
    # Hard cap on primary-timeframe candles per backtest run (walk length).
    backtest_max_candles: int = Field(default=20_000, ge=100, le=200_000)
    # Warmup candles loaded before the configured start so the first
    # evaluations have the lookback history they require.
    backtest_warmup_candles: int = Field(default=150, ge=10, le=5_000)
    # Binary options expiry upper bound (candles).
    backtest_max_expiry_candles: int = Field(default=120, ge=1, le=10_000)
    # Concurrent in-process backtest executions (single-process runner; the
    # architecture is documented — no external task queue is introduced).
    backtest_max_concurrent: int = Field(default=1, ge=1, le=8)

    # --- Research / optimization (Phase 8) -------------------------------------
    # Bounded computation for parameter optimization and walk-forward
    # validation (in-process runner, same architecture as backtests).
    # Hard cap on distinct parameter combinations per research run.
    research_max_combinations: int = Field(default=200, ge=1, le=5_000)
    # Hard cap on parameter definitions per run (search-space dimensions).
    research_max_parameters: int = Field(default=8, ge=1, le=32)
    # Hard cap on allowed values per parameter dimension.
    research_max_values_per_parameter: int = Field(default=12, ge=1, le=64)
    # Hard cap on total engine walks per run (candidates x phases + robustness
    # scenarios). Keeps worst-case computation predictable.
    research_max_walks: int = Field(default=1_500, ge=10, le=50_000)
    # Hard cap on walk-forward windows per run.
    research_max_windows: int = Field(default=40, ge=1, le=500)
    # Hard cap on execution-cost sensitivity scenarios per run.
    research_max_cost_scenarios: int = Field(default=6, ge=0, le=24)
    # Bootstrap resamples for the OPTIONAL randomized stress test
    # (0 disables the scenario entirely). Bounded computation, seeded.
    research_bootstrap_samples: int = Field(default=200, ge=0, le=2_000)
    # Concurrent in-process research executions (single-process runner).
    research_max_concurrent: int = Field(default=1, ge=1, le=4)
    # Per-owner cap on NEW research runs per minute (creation rate limit).
    # Each accepted creation starts a bounded but potentially long-running
    # in-process run; the cap stops accidental floods. Read/cancel
    # endpoints are not throttled (cheap, owner-scoped queries).
    research_max_creates_per_minute: int = Field(default=10, ge=1, le=10_000)

    # --- AI research layer (Phase 7) -------------------------------------------------
    # The AI layer EXPLAINS existing deterministic analysis results; it never
    # replaces the engine, never evaluates rules, never writes to the database
    # and never executes generated code. Disabled by default: the deterministic
    # platform is fully usable without any provider.
    ai_enabled: bool = False
    # Only an OpenAI-compatible chat-completions API is supported (configurable
    # base URL + model — no vendor lock-in, no hardcoded assumption that a
    # specific model exists).
    ai_provider: Literal["openai_compatible"] = "openai_compatible"
    # e.g. "https://api.openai.com/v1" or a self-hosted gateway.
    ai_base_url: str = ""
    # Model name as expected by the provider (e.g. "gpt-4o-mini").
    ai_model: str = ""
    # SERVER-SIDE ONLY. Never accepted by any endpoint, never logged, never
    # returned to the browser (the /ai/status endpoint reports only booleans).
    ai_api_key: str = ""
    ai_timeout_seconds: float = Field(default=45.0, gt=0, le=300)
    # Bounded retries for transient failures (429 / 5xx / timeouts).
    ai_max_retries: int = Field(default=2, ge=0, le=10)
    ai_retry_backoff_seconds: float = Field(default=1.5, gt=0, le=60)
    # Simple in-process sliding-window limit per owner (documented limitation:
    # single process — not a distributed limiter).
    ai_rate_limit_per_minute: int = Field(default=10, ge=1, le=1000)
    # Bounded prompt/output sizes.
    ai_max_input_chars: int = Field(default=24_000, ge=1_000, le=200_000)
    ai_max_output_tokens: int = Field(default=1_400, ge=100, le=8_000)
    ai_temperature: float = Field(default=0.1, ge=0.0, le=2.0)

    # --- Validation ----------------------------------------------------------------
    request_max_body_bytes: int = Field(default=2 * 1024 * 1024, ge=1024)

    # ------------------------------------------------------------------ validators
    @field_validator("database_url", mode="after")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        url = value.strip()
        if not url:
            raise ConfigError(
                "DATABASE_URL is empty. Provide a PostgreSQL URL, e.g. "
                "postgresql+asyncpg://ta_user:password@localhost:5432/ta_platform"
            )
        for marker in PLACEHOLDER_MARKERS:
            if marker in url and cls._current_env() not in ("local", "test"):
                raise ConfigError(
                    "DATABASE_URL still contains a placeholder "
                    f"('{marker}'). Copy backend/.env.example to backend/.env "
                    "and set real credentials."
                )
        if not url.startswith(POSTGRES_SCHEMES):
            raise ConfigError(
                "DATABASE_URL must be a PostgreSQL URL starting with "
                "postgresql+asyncpg:// or postgresql:// "
                f"(got: {url.split('@')[-1][:60]})."
            )
        if url.startswith("postgresql://"):
            # Normalize to the async driver used by SQLAlchemy.
            url = "postgresql+asyncpg://" + url[len("postgresql://") :]
        return url

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("cors_origins", mode="after")
    @classmethod
    def _validate_cors_origins(cls, value: list[str]) -> list[str]:
        for origin in value:
            if not origin.startswith(("http://", "https://")):
                raise ConfigError(
                    f"CORS_ORIGINS entries must be absolute origins (got '{origin}')."
                )
        return value

    @model_validator(mode="after")
    def _mt5_credentials_consistency(self) -> "Settings":
        """Explicit login requires password AND server (MT5 API contract)."""

        using_login = self.mt5_login is not None
        has_password = bool(self.mt5_password)
        has_server = bool(self.mt5_server)
        if using_login and not (has_password and has_server):
            raise ConfigError(
                "MT5_LOGIN was provided but MT5_PASSWORD and/or MT5_SERVER is "
                "empty. MetaTrader5.initialize needs login, password and "
                "server together — or leave all three empty to attach to the "
                "already logged-in terminal."
            )
        if not using_login and (has_password or has_server):
            raise ConfigError(
                "MT5_PASSWORD/MT5_SERVER were set without MT5_LOGIN. Provide "
                "all three, or clear them to use the terminal's own login."
            )
        return self

    @property
    def mt5_credentials_configured(self) -> bool:
        """True when an explicit terminal login is configured."""
        return self.mt5_login is not None

    def mt5_config_summary(self) -> dict[str, object]:
        """Safe-to-publish MT5 configuration (no secrets, ever)."""

        return {
            "enabled": self.mt5_enabled,
            "explicit_login": self.mt5_credentials_configured,
            "terminal_path_configured": bool(self.mt5_terminal_path),
            "timeout_seconds": self.mt5_timeout_seconds,
            "retry_attempts": self.mt5_retry_attempts,
            "retry_backoff_seconds": self.mt5_retry_backoff_seconds,
            "max_candles": self.mt5_max_candles,
            "default_candles": self.mt5_default_candles,
            "cache_enabled": self.mt5_cache_enabled,
            "cache_max_age_seconds": self.mt5_cache_max_age_seconds,
            "stale_after_intervals": self.mt5_stale_after_intervals,
        }

    @model_validator(mode="after")
    def _ai_configuration_consistency(self) -> "Settings":
        """Enabling the AI layer requires a complete provider configuration."""
        if self.ai_enabled:
            missing = [
                name
                for name, value in (
                    ("AI_BASE_URL", self.ai_base_url),
                    ("AI_MODEL", self.ai_model),
                    ("AI_API_KEY", self.ai_api_key),
                )
                if not value.strip()
            ]
            if missing:
                raise ConfigError(
                    "AI_ENABLED=true requires "
                    + ", ".join(missing)
                    + ". The deterministic analysis platform remains fully "
                    "usable with AI_ENABLED=false (default)."
                )
        return self

    @property
    def ai_provider_configured(self) -> bool:
        """True when the AI provider could be constructed (without secrets)."""
        return bool(
            self.ai_enabled
            and self.ai_base_url.strip()
            and self.ai_model.strip()
            and self.ai_api_key.strip()
        )

    def ai_config_summary(self) -> dict[str, object]:
        """Safe-to-publish AI configuration (booleans only — no secrets)."""
        return {
            "enabled": self.ai_enabled,
            "provider_configured": self.ai_provider_configured,
            "provider": self.ai_provider,
            "model": self.ai_model if self.ai_enabled else None,
            "timeout_seconds": self.ai_timeout_seconds,
            "max_retries": self.ai_max_retries,
            "rate_limit_per_minute": self.ai_rate_limit_per_minute,
            "max_input_chars": self.ai_max_input_chars,
            "max_output_tokens": self.ai_max_output_tokens,
            "note": "The AI layer only explains existing deterministic "
            "analysis results; it never replaces the engine, never evaluates "
            "rules and never writes to the database.",
        }

    @model_validator(mode="after")
    def _environ_consistency(self) -> "Settings":
        if self.app_env == "production":
            if self.debug:
                raise ConfigError("DEBUG=true is not allowed when APP_ENV=production.")
            if any(marker in self.database_url for marker in PLACEHOLDER_MARKERS):
                raise ConfigError(
                    "Refusing to start in production with placeholder DATABASE_URL."
                )
        return self

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _current_env() -> str:
        import os

        return os.getenv("APP_ENV", "local")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def enable_docs(self) -> bool:
        if self.docs_enabled is not None:
            return self.docs_enabled
        return not self.is_production

    def database_url_safe(self) -> str:
        """Return the DB URL with the password masked (for logging)."""

        url = self.database_url
        if "@" not in url:
            return url
        scheme, rest = url.split("://", 1)
        credentials, host = rest.rsplit("@", 1)
        if ":" in credentials:
            user = credentials.split(":", 1)[0]
            credentials = f"{user}:***"
        return f"{scheme}://{credentials}@{host}"


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor used by the application runtime.

    Environment variables take precedence over the ``.env`` file (standard
    behaviour). One exception is made for robustness: some monorepo tooling
    (e.g. Prisma in a frontend workspace) exports a ``DATABASE_URL`` of a
    non-PostgreSQL scheme (``file:...``, ``mysql:...``). Such a value can
    never be valid for this backend, so it is ignored in favour of
    ``backend/.env`` instead of producing a confusing startup failure.
    """

    import os

    polluted = "DATABASE_URL" in os.environ and not os.environ[
        "DATABASE_URL"
    ].startswith(POSTGRES_SCHEMES)
    saved = os.environ.pop("DATABASE_URL") if polluted else None
    try:
        try:
            return Settings()  # type: ignore[call-arg]
        except ConfigError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface a clean startup error
            raise ConfigError(f"Invalid configuration: {exc}") from exc
    finally:
        if saved is not None:
            os.environ["DATABASE_URL"] = saved
