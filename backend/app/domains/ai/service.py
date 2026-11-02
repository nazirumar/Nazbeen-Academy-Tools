"""AI orchestration service (Phase 7 — layers C+D wired together).

* ``explain_analysis`` — load ONE authorized analysis report, build the
  minimized context, ask the provider, validate the answer (fail-closed)
  and return it. Stateless: nothing is persisted.
* ``research_chat`` — owner-scoped conversation flow. Artefact
  references are validated against the requesting owner BEFORE any
  fetch; the user question is persisted before the provider call; a
  failure is persisted as an honest ``error`` role message and then
  re-raised (nothing is silently swallowed).
* Provider errors are mapped to the shared error envelope; secrets are
  never logged and user text is redacted before leaving the server.

The provider is injectable (``provider=``) so automated tests can supply
a scripted fake and never call a real API.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.domains.ai import conversations as conversation_service
from app.domains.ai import prompts
from app.domains.ai.context import (
    AnalysisContext,
    BacktestContext,
    build_analysis_context,
    build_backtest_context,
)
from app.domains.ai.errors import (
    AIValidationError,
    ProviderError,
    ProviderNotConfiguredError,
)
from app.domains.ai.provider import (
    LLMBadResponse,
    LLMNotConfigured,
    LLMProvider,
    LLMQuotaExceeded,
    LLMRequestFailed,
    LLMTimeout,
    get_provider,
)
from app.domains.ai.rate_limit import check_rate_limit
from app.domains.ai.secrets import redact_text
from app.domains.ai.validation import (
    validate_chat_answer,
    validate_explanation,
)
from app.core.errors import ValidationAppError
from app.domains.analysis import reports as report_service
from app.domains.backtest import service as backtest_service

logger = get_logger(__name__)

_HYGIENE_NOTE = (
    "AI-generated interpretation of the recorded evidence. Not verified "
    "market data, not a prediction, no probability of profit. Every claim "
    "must be checkable against the cited evidence refs and the "
    "deterministic report."
)


def status_payload(settings: Settings) -> dict[str, Any]:
    """Honest AI-layer status (no secrets, no availability guarantees)."""
    summary = settings.ai_config_summary()
    summary["explanations_endpoint"] = "/api/v1/ai/explanations"
    summary["chat_endpoint"] = "/api/v1/ai/research/chat"
    return summary


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds")


def _map_provider_exception(exc: Exception) -> Exception:
    if isinstance(exc, LLMNotConfigured):
        return ProviderNotConfiguredError(str(exc))
    if isinstance(exc, LLMTimeout):
        return ProviderError(str(exc), code="AI_PROVIDER_TIMEOUT")
    if isinstance(exc, LLMQuotaExceeded):
        return ProviderError(str(exc), code="AI_PROVIDER_QUOTA")
    if isinstance(exc, (LLMBadResponse, LLMRequestFailed)):
        return ProviderError(str(exc))
    return exc


async def _complete(
    provider: LLMProvider, *, system: str, user: str, settings: Settings
) -> Any:
    try:
        return await provider.complete_json(
            system=system, user=user, max_output_tokens=settings.ai_max_output_tokens
        )
    except Exception as exc:  # noqa: BLE001 - mapped to the shared envelope
        raise _map_provider_exception(exc) from exc


def _resolve_provider(
    settings: Settings, provider: LLMProvider | None
) -> LLMProvider:
    """Return the injected provider or resolve the configured one (mapped)."""
    if provider is not None:
        return provider
    try:
        return get_provider(settings)
    except LLMNotConfigured as exc:
        raise ProviderNotConfiguredError(str(exc)) from exc


# --------------------------------------------------------------------------- #
# Explanations (stateless)
# --------------------------------------------------------------------------- #


async def explain_analysis(
    session: AsyncSession,
    owner: str,
    settings: Settings,
    analysis_id: uuid.UUID,
    *,
    provider: LLMProvider | None = None,
) -> dict[str, Any]:
    """Explain one authorized analysis report (fail-closed validation)."""

    check_rate_limit(f"explain:{owner}", settings.ai_rate_limit_per_minute)

    row = await report_service.get_report(session, owner, analysis_id)  # 404 scoped
    context = build_analysis_context(row.report)

    chosen = _resolve_provider(settings, provider)
    system, user = prompts.build_explanation_prompt(
        context, max_input_chars=settings.ai_max_input_chars
    )
    result = await _complete(chosen, system=system, user=user, settings=settings)
    output, validation = validate_explanation(result.content, context)

    logger.info(
        "ai_explanation_generated",
        extra={
            "analysis_id": str(analysis_id),
            "owner": owner,
            "provider": result.provider,
            "model": result.model,
            "attempts": result.attempts,
            "elapsed_seconds": result.elapsed_seconds,
            "checked_refs": validation.checked_refs,
        },
    )
    return {
        "analysis_id": str(analysis_id),
        "strategy": {
            "id": context.strategy.id,
            "name": context.strategy.name,
            "version": context.strategy.version,
            "version_status": context.strategy.version_status,
            "direction": context.strategy.direction,
        },
        "mode": context.mode,
        "symbol": context.symbol,
        "evaluated_at": context.evaluated_at,
        "explanation": output.model_dump(),
        "validation": validation.model_dump(),
        "provider": {
            "name": result.provider,
            "model": result.model,
            "elapsed_seconds": result.elapsed_seconds,
            "attempts": result.attempts,
        },
        "generated_at": _now_iso(),
        "ai_layer_version": "1.0.0",
        "note": _HYGIENE_NOTE,
    }


# --------------------------------------------------------------------------- #
# Research chat (owner-scoped persistence)
# --------------------------------------------------------------------------- #


async def resolve_contexts(
    session: AsyncSession,
    owner: str,
    *,
    conversation_analysis_id: uuid.UUID | None,
    conversation_backtest_id: uuid.UUID | None,
    request_analysis_id: uuid.UUID | None,
    request_backtest_id: uuid.UUID | None,
) -> tuple[list[AnalysisContext | BacktestContext], list[dict[str, Any]]]:
    """Validate every referenced artefact against the owner, then build.

    Returns (contexts, refs_used). Foreign-owned references are 404s —
    indistinguishable from non-existent ones.
    """
    analysis_ids: list[uuid.UUID] = []
    backtest_ids: list[uuid.UUID] = []
    if conversation_analysis_id:
        analysis_ids.append(conversation_analysis_id)
    if request_analysis_id and request_analysis_id not in analysis_ids:
        analysis_ids.append(request_analysis_id)
    if conversation_backtest_id:
        backtest_ids.append(conversation_backtest_id)
    if request_backtest_id and request_backtest_id not in backtest_ids:
        backtest_ids.append(request_backtest_id)

    if not analysis_ids and not backtest_ids:
        raise ValidationAppError(
            "No analysis or backtest attached. Attach one via the request "
            "(analysis_id / backtest_id) or start the conversation with a "
            "reference — the assistant only explains saved results.",
            code="NO_CONTEXT_ATTACHED",
        )

    contexts: list[AnalysisContext | BacktestContext] = []
    refs: list[dict[str, Any]] = []
    for aid in analysis_ids:
        row = await report_service.get_report(session, owner, aid)  # 404 scoped
        contexts.append(build_analysis_context(row.report))
        refs.append({"kind": "analysis", "id": str(aid)})
    for bid in backtest_ids:
        run = await backtest_service.get_run(session, owner, bid)  # 404 scoped
        contexts.append(build_backtest_context(backtest_service.run_summary(run)))
        refs.append({"kind": "backtest", "id": str(bid)})
    return contexts, refs


async def research_chat(
    session: AsyncSession,
    owner: str,
    settings: Settings,
    *,
    message: str,
    conversation_id: uuid.UUID | None = None,
    analysis_id: uuid.UUID | None = None,
    backtest_id: uuid.UUID | None = None,
    title: str | None = None,
    provider: LLMProvider | None = None,
) -> dict[str, Any]:
    """Handle one research-chat turn (owner-scoped, fail-closed)."""

    check_rate_limit(f"chat:{owner}", settings.ai_rate_limit_per_minute)

    # 1) Resolve or create the conversation (owner-scoped).
    if conversation_id is not None:
        conversation = await conversation_service.get_conversation(
            session, owner, conversation_id
        )
    else:
        default_title = title or message.strip()[:60] or "Research chat"
        conversation = await conversation_service.create_conversation(
            session,
            owner,
            title=default_title,
            analysis_id=analysis_id,
            backtest_id=backtest_id,
        )

    # 2) Validate every referenced artefact BEFORE fetching anything.
    contexts, refs_used = await resolve_contexts(
        session,
        owner,
        conversation_analysis_id=conversation.analysis_id,
        conversation_backtest_id=conversation.backtest_id,
        request_analysis_id=analysis_id,
        request_backtest_id=backtest_id,
    )

    # 3) Persist the user question FIRST (the question survives failures).
    user_message = await conversation_service.add_message(
        session,
        conversation,
        role="user",
        content=message.strip(),
        refs={"artefacts": refs_used},
    )

    # 4) Provider call with bounded, redacted prompts.
    history = [
        {"role": "user" if m["role"] == "user" else "assistant", "content": m["content"]}
        for m in await conversation_service.list_messages(
            session, owner, conversation, limit=8
        )
        if m["id"] != str(user_message.id) and m["role"] in ("user", "assistant")
    ]
    chosen = _resolve_provider(settings, provider)
    system, user_prompt = prompts.build_chat_prompt(
        contexts,
        question=redact_text(message),
        history=history,
        max_input_chars=settings.ai_max_input_chars,
    )

    try:
        result = await _complete(chosen, system=system, user=user_prompt, settings=settings)
        output, validation = validate_chat_answer(result.content, contexts)
    except AIValidationError:
        await _persist_failure(session, conversation, refs_used, "validation_failed")
        raise
    except Exception as exc:  # noqa: BLE001 - persist honest failure, re-raise
        mapped = _map_provider_exception(exc)
        await _persist_failure(
            session, conversation, refs_used, getattr(mapped, "code", "provider_error")
        )
        raise mapped from exc

    # 5) Persist the validated answer with its audit trail.
    assistant_message = await conversation_service.add_message(
        session,
        conversation,
        role="assistant",
        content=output.answer,
        context={"artefacts": [ctx.model_dump(mode="json") for ctx in contexts]},
        validation=validation.model_dump(),
        provider=result.provider,
        model=result.model,
        refs={"artefacts": refs_used},
    )

    logger.info(
        "ai_chat_answer_generated",
        extra={
            "conversation_id": str(conversation.id),
            "owner": owner,
            "provider": result.provider,
            "model": result.model,
            "attempts": result.attempts,
            "checked_refs": validation.checked_refs,
        },
    )

    return {
        "conversation": conversation_service.conversation_dict(conversation),
        "messages": [
            conversation_service.message_dict(user_message),
            conversation_service.message_dict(assistant_message),
        ],
        "refs": refs_used,
        "ai_layer_version": "1.0.0",
        "note": _HYGIENE_NOTE,
    }


async def _persist_failure(
    session: AsyncSession,
    conversation: Any,
    refs_used: list[dict[str, Any]],
    code: str,
) -> None:
    """Persist an honest failure marker (no secrets, no stack traces)."""
    human = {
        "validation_failed": "The AI answer failed output validation and "
        "was discarded (fail-closed). No answer is shown for this question.",
        "AI_PROVIDER_NOT_CONFIGURED": "AI explanations are unavailable: no "
        "provider is configured.",
        "AI_PROVIDER_TIMEOUT": "The AI provider did not answer in time.",
        "AI_PROVIDER_QUOTA": "The AI provider reported an authentication or "
        "quota problem.",
    }.get(code, "The AI provider request failed.")
    try:
        await conversation_service.add_message(
            session,
            conversation,
            role="error",
            content=human,
            refs={"artefacts": refs_used},
        )
        await session.commit()
    except Exception:  # noqa: BLE001 - never mask the original failure
        logger.warning("ai_failure_marker_not_persisted", extra={"code": code})


__all__ = [
    "explain_analysis",
    "resolve_contexts",
    "research_chat",
    "status_payload",
]
