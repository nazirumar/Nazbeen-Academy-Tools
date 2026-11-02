"""Scripted fake LLM provider for AI-layer tests (Phase 7).

Never performs network calls. Returns preconfigured content, raises a
preconfigured exception, or validates that it received the expected
prompt pieces (evidence JSON, injection-safe delimiters).
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.domains.ai.provider import CompletionResult


class ScriptedProvider:
    """Deterministic provider double for automated tests."""

    name = "scripted_fake"
    model = "fake-model-1"

    def __init__(
        self,
        *,
        content: dict[str, Any] | None = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self._content = content or {}
        self._error = error
        self._delay = delay
        self.calls: list[dict[str, Any]] = []

    async def complete_json(
        self, *, system: str, user: str, max_output_tokens: int
    ) -> CompletionResult:
        self.calls.append(
            {"system": system, "user": user, "max_output_tokens": max_output_tokens}
        )
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return CompletionResult(
            content=self._content,
            provider=self.name,
            model=self.model,
            prompt_chars=len(system) + len(user),
            output_chars=200,
            elapsed_seconds=0.01,
            attempts=1,
        )


def valid_explanation_content() -> dict[str, Any]:
    """A schema-valid explanation that cites plausible engine refs.

    Note: refs/numbers only validate when they exist in the supplied
    context — tests supply matching contexts (see ``minimal_report``).
    """
    return {
        "summary": "The engine recorded conditions_missing on EURUSD M1 with 59 closed candles evaluated.",
        "market_context": "Regime evidence on M1 recorded a range classification with neutral bias.",
        "conditions_met": [
            {"text": "The close-above condition was evaluated as met on 59 candles.", "refs": ["core:px"]}
        ],
        "conditions_not_met": [],
        "conditions_inconclusive_or_unsupported": [],
        "evidence_support": [
            {"text": "Regime classification is supported by the primary timeframe evidence.", "refs": ["regime:primary"]}
        ],
        "data_quality_notes": ["59 closed candles were used; the forming candle was excluded."],
        "invalidation_notes": [],
        "uncertainty": ["Regime classification is an explicit heuristic, not a forecast."],
        "suggested_observations": ["Observe whether price stays above the recorded level on further closed candles."],
        "unavailable_information": ["No tick data or broker feed is part of this report."],
    }


def valid_chat_content() -> dict[str, Any]:
    return {
        "answer": "Based on the recorded evaluation, the core condition was met on the supplied candles.",
        "evidence_cited": [
            {"text": "The evaluation evidence shows the condition result.", "refs": ["core:px"]}
        ],
        "uncertainty": ["The answer relies only on the supplied report."],
        "unavailable_information": ["No live data was fetched."],
        "suggested_follow_ups": ["Ask about the recorded regime evidence."],
    }


__all__ = ["ScriptedProvider", "valid_chat_content", "valid_explanation_content"]
