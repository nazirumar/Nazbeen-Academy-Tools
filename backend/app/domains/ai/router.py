"""AI research HTTP endpoints (Phase 7).

* ``GET    /ai/status``                              — honest AI-layer status (no secrets).
* ``POST   /ai/explanations``                        — explain one authorized analysis report.
* ``POST   /ai/research/chat``                       — one research-chat turn (owner-scoped).
* ``GET    /ai/research/conversations``              — list the owner's conversations.
* ``POST   /ai/research/conversations``              — create a conversation.
* ``GET    /ai/research/conversations/{id}``         — retrieve one conversation + messages.
* ``DELETE /ai/research/conversations/{id}``         — delete an authorized conversation.

All endpoints are owner-scoped (``X-User-Id``); foreign artefacts are 404s
(no existence leak). The AI layer never writes strategies, analyses,
backtests or candles: its ONLY persistence is its own conversation
records. Provider credentials stay server-side; ``/ai/status`` exposes
booleans and the model name only.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.ownership import current_owner
from app.core.responses import created, ok
from app.db.session import get_db_session
from app.domains.ai import conversations as conversation_service
from app.domains.ai import service as ai_service
from app.domains.ai.schemas import (
    ChatRequest,
    ConversationCreateRequest,
    ExplanationRequest,
)

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/status")
async def ai_status(
    settings: Settings = Depends(get_settings),
) -> dict:
    """AI-layer status: whether explanations are available and why not.

    Reports configuration booleans only — never keys, never endpoints of
    the provider, never an availability guarantee.
    """
    return ok(ai_service.status_payload(settings))


@router.post("/explanations")
async def create_explanation(
    payload: ExplanationRequest,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Explain one authorized, persisted analysis report.

    * The report is fetched owner-scoped (foreign reports are 404s).
    * The provider receives a minimized, validated context — never raw
      candles, credentials or other users' data.
    * The answer passes the fail-closed validation layer (schema,
      evidence refs, numeric traceability, safety); invalid answers are
      discarded with an explicit error, never shown as success.
    """
    return created(
        await ai_service.explain_analysis(
            session, owner, settings, payload.analysis_id
        )
    )


@router.post("/research/chat")
async def research_chat(
    payload: ChatRequest,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Submit one research question with validated context.

    Conversation and every referenced analysis/backtest are checked
    against the requesting owner before anything is fetched or
    answered. The user question is persisted first; failures are
    persisted as honest ``error`` messages and surfaced with their
    proper status codes.
    """
    return created(
        await ai_service.research_chat(
            session,
            owner,
            settings,
            message=payload.message,
            conversation_id=payload.conversation_id,
            analysis_id=payload.analysis_id,
            backtest_id=payload.backtest_id,
            title=payload.title,
        )
    )


@router.get("/research/conversations")
async def list_conversations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Paginated research conversations of the requesting owner."""
    items, page_meta = await conversation_service.list_conversations(
        session, owner, page=page, page_size=page_size
    )
    return ok({"items": items, "page": page_meta})


@router.post("/research/conversations")
async def create_conversation(
    payload: ConversationCreateRequest,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Create an empty research conversation (optionally pre-attached).

    Referenced artefacts are validated against the requesting owner at
    creation time — the assistant only ever explains saved results.
    """
    # Validate optional references eagerly (owner-scoped 404s).
    if payload.analysis_id or payload.backtest_id:
        await ai_service.resolve_contexts(
            session,
            owner,
            conversation_analysis_id=None,
            conversation_backtest_id=None,
            request_analysis_id=payload.analysis_id,
            request_backtest_id=payload.backtest_id,
        )
    row = await conversation_service.create_conversation(
        session,
        owner,
        title=payload.title or "Research chat",
        analysis_id=payload.analysis_id,
        backtest_id=payload.backtest_id,
    )
    return created(conversation_service.conversation_dict(row))


@router.get("/research/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: uuid.UUID,
    limit: int = Query(default=200, ge=1, le=200),
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve one authorized conversation with its messages."""
    conversation = await conversation_service.get_conversation(
        session, owner, conversation_id
    )
    messages = await conversation_service.list_messages(
        session, owner, conversation, limit=limit
    )
    return ok(
        {
            "conversation": conversation_service.conversation_dict(conversation),
            "messages": messages,
        }
    )


@router.delete("/research/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete an authorized conversation (messages cascade)."""
    await conversation_service.delete_conversation(session, owner, conversation_id)
    return ok({"deleted": str(conversation_id)})


__all__ = ["router"]
