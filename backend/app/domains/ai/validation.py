"""AI output validation layer (Phase 7 — layer D).

Every provider answer passes through here BEFORE it is returned or
persisted. The checks are deterministic and evidence-based:

1. Schema validation — strict pydantic models (unknown keys rejected,
   bounded string lengths).
2. Evidence-reference validation — every ``refs`` entry must exist in
   the supplied context's evidence refs; items asserting facts must
   carry at least one ref. Dangling references fail validation.
3. Numeric traceability — every number appearing in the output text
   must appear in the supplied context JSON. Numbers that cannot be
   traced to the supplied evidence fail validation. This is a
   token-presence check, not a semantic proof: it deterministically
   blocks invented statistics, but it cannot guarantee that a number
   used with correct *meaning* (limits documented in tests and docs).
4. Safety guards — guarantee/profitability phrasing and code blocks are
   rejected (fail-closed), because such statements violate platform
   policy regardless of the evidence.

If any check fails, the caller gets :class:`AIValidationError` — the
output is never silently repaired, filtered into plausibility or shown
as a "successful" explanation. A validation failure is an honest,
visible outcome.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.domains.ai.context import AIContext
from app.domains.ai.errors import AIValidationError

# --- safety patterns (fail-closed) ------------------------------------------ #
_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "profitability_guarantee",
        re.compile(
            r"(?i)\b(guaranteed?|risk[\s-]?free|sure[\s-]?thing|"
            r"cannot lose|no risk of loss|100%\s*(win|profit|accurate))\b"
        ),
    ),
    (
        "future_profit_claim",
        re.compile(
            r"(?i)\b(will (?:definitely|certainly)? ?(?:profit|win|succeed)|"
            r"is expected to profit|future win rate of)\b"
        ),
    ),
    (
        "order_advice",
        re.compile(
            r"(?i)\b(place (?:a )?(?:buy|sell) order|open a (?:long|short|trade) "
            r"position now|you should (?:buy|sell)|enter (?:a )?trade now)\b"
        ),
    ),
    (
        "code_block",
        re.compile(r"```|<script\b|def\s+\w+\s*\(|SELECT\s+.+\s+FROM\s+", re.IGNORECASE),
    ),
    (
        "confidence_percentage",
        re.compile(r"(?i)\b(confidence|probability|certainty)\b[^.]{0,40}\b\d{1,3}(\.\d+)?\s*%"),
    ),
)

_NUMBER_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\w])")

# Numbers that are always acceptable (counts, years, sizes).
_ALLOWED_NUMBERS = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"}


class CitedItem(BaseModel):
    """One output assertion with its evidence references."""

    text: str = Field(min_length=1, max_length=1_500)
    refs: list[str] = Field(min_length=1, max_length=12)

    @field_validator("refs")
    @classmethod
    def _nonempty_refs(cls, v: list[str]) -> list[str]:
        cleaned = [r.strip() for r in v if isinstance(r, str) and r.strip()]
        if not cleaned:
            raise ValueError("Every cited item needs at least one evidence ref.")
        return cleaned


class ExplanationOutput(BaseModel):
    """Validated output schema for POST /ai/explanations."""

    summary: str = Field(min_length=1, max_length=1_000)
    market_context: str = Field(min_length=1, max_length=1_500)
    conditions_met: list[CitedItem] = Field(default_factory=list, max_length=60)
    conditions_not_met: list[CitedItem] = Field(default_factory=list, max_length=60)
    conditions_inconclusive_or_unsupported: list[CitedItem] = Field(
        default_factory=list, max_length=60
    )
    evidence_support: list[CitedItem] = Field(default_factory=list, max_length=60)
    data_quality_notes: list[str] = Field(default_factory=list, max_length=30)
    invalidation_notes: list[str] = Field(default_factory=list, max_length=30)
    uncertainty: list[str] = Field(default_factory=list, max_length=30)
    suggested_observations: list[str] = Field(default_factory=list, max_length=30)
    unavailable_information: list[str] = Field(default_factory=list, max_length=30)

    @field_validator(
        "summary",
        "market_context",
        *{f for f in ("data_quality_notes", "invalidation_notes", "uncertainty",
                      "suggested_observations", "unavailable_information")},
    )
    @classmethod
    def _clip_plain_strings(cls, v: str | list[str]) -> str | list[str]:
        if isinstance(v, str):
            return v.strip()
        return [s.strip() for s in v if isinstance(s, str) and s.strip()]

    @field_validator("summary", "market_context")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v:
            raise ValueError("Must not be empty.")
        return v


class ChatOutput(BaseModel):
    """Validated output schema for the research chat."""

    answer: str = Field(min_length=1, max_length=4_000)
    evidence_cited: list[CitedItem] = Field(default_factory=list, max_length=40)
    uncertainty: list[str] = Field(default_factory=list, max_length=20)
    unavailable_information: list[str] = Field(default_factory=list, max_length=20)
    suggested_follow_ups: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("answer")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Answer must not be empty.")
        return v.strip()


class ValidationSummary(BaseModel):
    """Deterministic validation result stored with the answer."""

    schema_ok: bool
    evidence_refs_ok: bool
    numeric_trace_ok: bool
    safety_ok: bool
    checks: list[str] = Field(default_factory=list)
    checked_refs: int = 0


def _contexts(context: AIContext | list[AIContext]) -> list[AIContext]:
    return context if isinstance(context, list) else [context]


def _context_refs(context: AIContext | list[AIContext]) -> set[str]:
    refs: set[str] = set()
    for ctx in _contexts(context):
        if hasattr(ctx, "evidence"):  # AnalysisContext
            refs.update(item.ref for item in ctx.evidence)  # type: ignore[attr-defined]
        else:  # BacktestContext pseudo-refs (documented in the prompt)
            refs.add("run:" + ctx.run_id)  # type: ignore[union-attr]
            for key in ("metrics", "data_quality", "assumptions", "configuration"):
                refs.add(f"backtest:{key}")  # type: ignore[union-attr]
    return refs


def _context_json(context: AIContext | list[AIContext]) -> str:
    return "[" + ",".join(ctx.model_dump_json() for ctx in _contexts(context)) + "]"


def _context_numbers(context: AIContext | list[AIContext]) -> set[str]:
    """All numeric tokens present in the supplied context (as strings)."""
    blob = _context_json(context)
    numbers: set[str] = set(_ALLOWED_NUMBERS)
    for match in _NUMBER_RE.finditer(blob):
        token = match.group(1).replace(",", "")
        numbers.add(token)
        # Tolerate formatting variants (trailing zeros are different
        # tokens; keep both raw and normalized).
        if "." in token:
            numbers.add(token.rstrip("0").rstrip("."))
            numbers.add(f"{float(token):g}")
        else:
            numbers.add(str(int(token)))
    return numbers


def check_safety(text: str) -> list[str]:
    """Return the list of safety violations found in ``text`` (empty = ok)."""
    violations: list[str] = []
    for name, pattern in _FORBIDDEN_PATTERNS:
        if pattern.search(text):
            violations.append(name)
    return violations


def check_numeric_traceability(text: str, context: AIContext | list[AIContext]) -> list[str]:
    """Return numbers in ``text`` that are NOT traceable to the context."""
    numbers = _context_numbers(context)
    untraceable: list[str] = []
    for match in _NUMBER_RE.finditer(text):
        token = match.group(1).replace(",", "")
        if token in numbers:
            continue
        variants = {token}
        if "." in token:
            variants.add(token.rstrip("0").rstrip("."))
            variants.add(f"{float(token):g}")
        else:
            variants.add(str(int(token)))
        if not variants & numbers:
            untraceable.append(match.group(1))
    return untraceable


def _validate_cited_items(
    items: list[CitedItem], refs: set[str]
) -> tuple[list[str], int]:
    problems: list[str] = []
    checked = 0
    for item in items:
        checked += 1
        for ref in item.refs:
            if ref not in refs:
                problems.append(f"Unknown evidence ref '{ref}'")
    return problems, checked


def validate_explanation(
    raw: dict[str, Any], context: AIContext | list[AIContext]
) -> tuple[ExplanationOutput, ValidationSummary]:
    """Validate one explanation answer against its context. Fail-closed."""
    failures: list[str] = []

    try:
        output = ExplanationOutput.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - mapped to a clean app error
        raise AIValidationError(
            "The AI explanation failed schema validation and was discarded.",
            details=[{"path": "output", "message": str(exc)[:500]}],
        ) from None

    refs = _context_refs(context)
    ref_problems: list[str] = []
    checked = 0
    for items in (
        output.conditions_met,
        output.conditions_not_met,
        output.conditions_inconclusive_or_unsupported,
        output.evidence_support,
    ):
        problems, n = _validate_cited_items(items, refs)
        ref_problems.extend(problems)
        checked += n

    flat_text = "\n".join(
        [output.summary, output.market_context]
        + [item.text for items in (
            output.conditions_met,
            output.conditions_not_met,
            output.conditions_inconclusive_or_unsupported,
            output.evidence_support,
        ) for item in items]
        + output.data_quality_notes
        + output.invalidation_notes
        + output.uncertainty
        + output.suggested_observations
        + output.unavailable_information
    )

    safety = check_safety(flat_text)
    if safety:
        failures.append(f"safety: {', '.join(safety)}")

    untraceable = check_numeric_traceability(flat_text, context)
    if untraceable:
        failures.append(
            "numeric_traceability: numbers not present in the supplied "
            f"evidence: {', '.join(untraceable[:8])}"
        )

    summary = ValidationSummary(
        schema_ok=True,
        evidence_refs_ok=not ref_problems,
        numeric_trace_ok=not untraceable,
        safety_ok=not safety,
        checks=ref_problems[:12] + failures,
        checked_refs=checked,
    )

    if ref_problems:
        raise AIValidationError(
            "The AI explanation referenced evidence that was not supplied; "
            "it was discarded (fail-closed).",
            details=[{"path": "refs", "message": p} for p in ref_problems[:12]],
        )
    if failures:
        raise AIValidationError(
            "The AI explanation violated output policy and was discarded "
            f"({'; '.join(f.split(':')[0] for f in failures)}).",
            details=[{"path": "validation", "message": f} for f in failures],
        )
    return output, summary


def validate_chat_answer(
    raw: dict[str, Any], context: AIContext | list[AIContext]
) -> tuple[ChatOutput, ValidationSummary]:
    """Validate one research-chat answer against its context. Fail-closed."""
    try:
        output = ChatOutput.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        raise AIValidationError(
            "The AI chat answer failed schema validation and was discarded.",
            details=[{"path": "output", "message": str(exc)[:500]}],
        ) from None

    refs = _context_refs(context)
    ref_problems, checked = _validate_cited_items(output.evidence_cited, refs)

    flat_text = "\n".join(
        [output.answer]
        + [item.text for item in output.evidence_cited]
        + output.uncertainty
        + output.unavailable_information
        + output.suggested_follow_ups
    )

    safety = check_safety(flat_text)
    untraceable = check_numeric_traceability(flat_text, context)

    failures: list[str] = []
    if safety:
        failures.append(f"safety: {', '.join(safety)}")
    if untraceable:
        failures.append(
            "numeric_traceability: numbers not present in the supplied "
            f"evidence: {', '.join(untraceable[:8])}"
        )

    summary = ValidationSummary(
        schema_ok=True,
        evidence_refs_ok=not ref_problems,
        numeric_trace_ok=not untraceable,
        safety_ok=not safety,
        checks=ref_problems[:12] + failures,
        checked_refs=checked,
    )

    if ref_problems:
        raise AIValidationError(
            "The AI answer referenced evidence that was not supplied; it "
            "was discarded (fail-closed).",
            details=[{"path": "refs", "message": p} for p in ref_problems[:12]],
        )
    if failures:
        raise AIValidationError(
            "The AI answer violated output policy and was discarded "
            f"({'; '.join(f.split(':')[0] for f in failures)}).",
            details=[{"path": "validation", "message": f} for f in failures],
        )
    return output, summary


__all__ = [
    "ChatOutput",
    "CitedItem",
    "ExplanationOutput",
    "ValidationSummary",
    "check_numeric_traceability",
    "check_safety",
    "validate_chat_answer",
    "validate_explanation",
]
