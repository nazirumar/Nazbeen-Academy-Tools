"""AI research domain (Phase 7) — explainable, evidence-first AI support.

Architecture (strictly separated layers):

A. Data & feature layer  -> the existing market-data + feature services.
B. Deterministic layer   -> the existing analysis engine / backtest engine
                            (the ONLY source of conditions, statuses,
                            evidence and metrics).
C. AI explanation layer  -> ``context.py`` builds a minimized, validated
                            structured context; ``provider.py`` talks to
                            an OpenAI-compatible LLM API; ``prompts.py``
                            constrains the model to explain supplied
                            evidence.
D. Validation layer      -> ``validation.py`` checks the model output
                            against strict schemas, evidence references
                            and numeric traceability BEFORE anything is
                            returned or persisted.

Hard rules (never violated by this domain):

* The LLM never calculates OHLC values, never evaluates conditions,
  never decides whether an unsupported rule "passed".
* The LLM has NO database write capability and no tool/execution access;
  generated Python/JS/SQL/shell is never executed.
* Provider API keys stay in server-side environment variables, are never
  logged and never returned by any endpoint.
* When the provider is not configured the deterministic platform remains
  fully usable and the API reports AI explanations as unavailable.
"""

from app.domains.ai.errors import (
    AIValidationError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderTimeoutError,
    RateLimitExceededError,
)

AI_LAYER_VERSION = "1.0.0"

__all__ = [
    "AI_LAYER_VERSION",
    "AIValidationError",
    "ProviderError",
    "ProviderNotConfiguredError",
    "ProviderTimeoutError",
    "RateLimitExceededError",
]
