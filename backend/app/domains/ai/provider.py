"""LLM provider abstraction (Phase 7).

Design goals:

* Vendor-neutral: any OpenAI-compatible ``POST {base_url}/chat/completions``
  endpoint works (base URL + model are configuration, never hardcoded).
* Server-side secrets only: the API key is read from settings (environment)
  and used exclusively in the Authorization header of the outbound request.
  It is never logged, never echoed and never returned by any endpoint.
* Bounded behaviour: per-request timeout, bounded retries with linear
  backoff on transient failures (429 / 5xx / timeouts), and quota errors
  mapped to a distinct, honest error.
* Strict output contract: ``complete_json`` returns a parsed JSON object
  or raises ``LLMBadResponse`` — the validation layer then checks it
  against the evidence schema.
* No tool use, no code execution, no database access — the provider can
  only produce text.

The provider is deliberately tiny: this layer exists to EXPLAIN results
that the deterministic engines already produced, not to reason freely.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.domains.ai.secrets import redact_text

logger = get_logger(__name__)


class LLMNotConfigured(Exception):
    """The AI layer is disabled or its provider configuration is incomplete."""


class LLMRequestFailed(Exception):
    """The provider request failed after bounded retries (transient/error)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMQuotaExceeded(LLMRequestFailed):
    """The provider reported quota/auth exhaustion (401/402/403/429 after retries)."""


class LLMTimeout(LLMRequestFailed):
    """The provider did not answer within the configured timeout."""


class LLMBadResponse(Exception):
    """The provider answered but the payload is unusable (non-JSON, empty)."""


@dataclass(frozen=True)
class CompletionResult:
    """One provider completion plus safe metadata (never contains secrets)."""

    content: dict[str, Any]
    provider: str
    model: str
    prompt_chars: int
    output_chars: int
    elapsed_seconds: float
    attempts: int


class LLMProvider(Protocol):
    """Minimal provider contract used by the service layer."""

    name: str
    model: str

    async def complete_json(
        self, *, system: str, user: str, max_output_tokens: int
    ) -> CompletionResult:
        """Return a parsed JSON object produced from the given prompts."""
        ...  # pragma: no cover


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse the model's answer into a JSON object (tolerating code fences)."""
    candidate = text.strip()
    # Some models wrap JSON in ```json fences despite instructions.
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(candidate)
        if match is None:
            raise LLMBadResponse(
                "Provider response did not contain a JSON object."
            ) from None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMBadResponse(
                f"Provider response contained invalid JSON ({exc.msg})."
            ) from None
    if not isinstance(parsed, dict):
        raise LLMBadResponse("Provider response JSON is not an object.")
    return parsed


class OpenAICompatibleProvider:
    """OpenAI-compatible chat-completions client (the only supported type).

    Configuration-only vendor choice: point ``AI_BASE_URL`` at any endpoint
    implementing the chat-completions contract and select ``AI_MODEL``.
    Nothing about a specific model's availability is assumed — failures are
    surfaced honestly.
    """

    name = "openai_compatible"

    def __init__(self, settings: Settings) -> None:
        if not settings.ai_provider_configured:
            raise LLMNotConfigured(
                "AI provider is not configured (AI_ENABLED / AI_BASE_URL / "
                "AI_MODEL / AI_API_KEY)."
            )
        self._settings = settings
        self.model = settings.ai_model
        self._base_url = settings.ai_base_url.rstrip("/")
        self._api_key = settings.ai_api_key
        self._timeout = settings.ai_timeout_seconds
        self._max_retries = settings.ai_max_retries
        self._backoff = settings.ai_retry_backoff_seconds
        self._temperature = settings.ai_temperature

    async def complete_json(
        self, *, system: str, user: str, max_output_tokens: int
    ) -> CompletionResult:
        """POST the prompts and parse a JSON-object answer.

        Retries are bounded and apply ONLY to transient failures
        (429/5xx/timeouts). Auth and quota errors (401/402/403) and 4xx
        responses fail immediately. Secrets are never included in errors
        or logs — messages are redacted before logging.
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self._temperature,
            "max_tokens": max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/chat/completions"

        attempts = 0
        last_error: LLMRequestFailed | None = None
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            while attempts <= self._max_retries:
                attempts += 1
                try:
                    response = await client.post(url, json=payload, headers=headers)
                except httpx.TimeoutException:
                    last_error = LLMTimeout(
                        f"Provider timed out after {self._timeout:g}s."
                    )
                    if attempts > self._max_retries:
                        break
                    await asyncio.sleep(self._backoff * attempts)
                    continue
                except httpx.HTTPError as exc:
                    # Network-level failure — redact before logging.
                    logger.warning(
                        "ai_provider_network_error",
                        extra={"detail": redact_text(str(exc))[:300]},
                    )
                    last_error = LLMRequestFailed(
                        "Provider request failed at the network level."
                    )
                    if attempts > self._max_retries:
                        break
                    await asyncio.sleep(self._backoff * attempts)
                    continue

                if response.status_code in (401, 402, 403):
                    raise LLMQuotaExceeded(
                        "Provider rejected the credentials or the quota is "
                        f"exhausted (HTTP {response.status_code}).",
                        status_code=response.status_code,
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = LLMRequestFailed(
                        f"Provider returned HTTP {response.status_code}.",
                        status_code=response.status_code,
                    )
                    if attempts > self._max_retries:
                        break
                    await asyncio.sleep(self._backoff * attempts)
                    continue
                if response.status_code >= 400:
                    raise LLMRequestFailed(
                        f"Provider rejected the request (HTTP {response.status_code}).",
                        status_code=response.status_code,
                    )

                try:
                    body = response.json()
                except ValueError:
                    raise LLMBadResponse(
                        "Provider returned a non-JSON HTTP body."
                    ) from None
                try:
                    content = body["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError):
                    raise LLMBadResponse(
                        "Provider response is missing choices[0].message.content."
                    ) from None
                if not isinstance(content, str) or not content.strip():
                    raise LLMBadResponse("Provider returned an empty completion.")
                return CompletionResult(
                    content=_extract_json_object(content),
                    provider=self.name,
                    model=self.model,
                    prompt_chars=len(system) + len(user),
                    output_chars=len(content),
                    elapsed_seconds=round(time.perf_counter() - started, 3),
                    attempts=attempts,
                )

        assert last_error is not None
        if isinstance(last_error, LLMQuotaExceeded):
            raise last_error
        logger.warning(
            "ai_provider_request_failed",
            extra={
                "attempts": attempts,
                "status_code": last_error.status_code,
                "detail": redact_text(str(last_error))[:300],
            },
        )
        raise last_error


_PROVIDER_CACHE: dict[int, LLMProvider] = {}


def get_provider(settings: Settings) -> LLMProvider:
    """Return the configured provider (cached per settings identity)."""
    key = id(settings)
    cached = _PROVIDER_CACHE.get(key)
    if cached is not None:
        return cached
    if not settings.ai_enabled or not settings.ai_provider_configured:
        raise LLMNotConfigured(
            "AI explanations are unavailable: no provider is configured "
            "(set AI_ENABLED, AI_BASE_URL, AI_MODEL, AI_API_KEY). The "
            "deterministic analysis platform remains fully usable."
        )
    provider: LLMProvider = OpenAICompatibleProvider(settings)
    _PROVIDER_CACHE[key] = provider
    return provider


def reset_provider_cache() -> None:
    """Test helper: drop cached providers."""
    _PROVIDER_CACHE.clear()


__all__ = [
    "CompletionResult",
    "LLMBadResponse",
    "LLMNotConfigured",
    "LLMProvider",
    "LLMQuotaExceeded",
    "LLMRequestFailed",
    "LLMTimeout",
    "OpenAICompatibleProvider",
    "get_provider",
    "reset_provider_cache",
]
