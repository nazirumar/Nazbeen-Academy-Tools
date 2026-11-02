"""AI-domain application errors (mapped to the shared error envelope)."""

from __future__ import annotations

from app.core.errors import AppError


class ProviderNotConfiguredError(AppError):
    """AI explanations are unavailable because no provider is configured.

    Deliberately a 503 with an actionable message: the deterministic
    platform remains fully usable — only the AI layer is unavailable.
    """

    status_code = 503
    code = "AI_PROVIDER_NOT_CONFIGURED"


class ProviderError(AppError):
    """The LLM provider failed (outage, quota, malformed response)."""

    status_code = 502
    code = "AI_PROVIDER_ERROR"


class ProviderTimeoutError(ProviderError):
    """The LLM provider did not answer within the configured timeout."""

    code = "AI_PROVIDER_TIMEOUT"


class AIValidationError(AppError):
    """The model output failed the validation layer (schema, evidence
    traceability or safety guards). Never surfaced as a "successful"
    explanation — the client gets an explicit, honest error."""

    status_code = 502
    code = "AI_OUTPUT_INVALID"


class RateLimitExceededError(AppError):
    """The per-owner AI request rate limit was exceeded."""

    status_code = 429
    code = "RATE_LIMITED"


__all__ = [
    "AIValidationError",
    "ProviderError",
    "ProviderNotConfiguredError",
    "ProviderTimeoutError",
    "RateLimitExceededError",
]
