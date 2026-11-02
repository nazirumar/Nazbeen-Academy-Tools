"""Research-conversation persistence (owner-scoped, Phase 7).

Ordinary authenticated application services do ALL writes — the AI
layer itself has no database capability. Every lookup is scoped by
``owner_id``; foreign-owned rows are indistinguishable from missing
ones (404, no existence leak).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.models import AIConversation, AIMessage

_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 100


def conversation_dict(row: AIConversation) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "title": row.title,
        "analysis_id": str(row.analysis_id) if row.analysis_id else None,
        "backtest_id": str(row.backtest_id) if row.backtest_id else None,
        "message_count": row.message_count,
        "last_message_at": row.last_message_at.isoformat(timespec="milliseconds")
        if row.last_message_at
        else None,
        "created_at": row.created_at.isoformat(timespec="milliseconds"),
        "updated_at": row.updated_at.isoformat(timespec="milliseconds"),
    }


def message_dict(row: AIMessage) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "conversation_id": str(row.conversation_id),
        "seq": row.seq,
        "role": row.role,
        "content": row.content,
        "validation": row.validation,
        "provider": row.provider,
        "model": row.model,
        "refs": row.refs,
        "created_at": row.created_at.isoformat(timespec="milliseconds"),
    }


async def create_conversation(
    session: AsyncSession,
    owner_id: str,
    *,
    title: str,
    analysis_id: uuid.UUID | None = None,
    backtest_id: uuid.UUID | None = None,
) -> AIConversation:
    row = AIConversation(
        owner_id=owner_id,
        title=title[:200],
        analysis_id=analysis_id,
        backtest_id=backtest_id,
        ai_schema_version="1.0",
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)  # load server defaults in async context
    return row


async def get_conversation(
    session: AsyncSession, owner_id: str, conversation_id: uuid.UUID
) -> AIConversation:
    row = await session.get(AIConversation, conversation_id)
    if row is None or row.owner_id != owner_id:
        raise NotFoundError(
            f"Research conversation '{conversation_id}' was not found."
        )
    return row


async def list_conversations(
    session: AsyncSession,
    owner_id: str,
    *,
    page: int = 1,
    page_size: int = _PAGE_SIZE_DEFAULT,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    page = max(1, page)
    page_size = min(max(1, page_size), _PAGE_SIZE_MAX)
    stmt = select(AIConversation).where(AIConversation.owner_id == owner_id)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(
        (
            await session.scalars(
                stmt.order_by(AIConversation.updated_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    total_int = int(total or 0)
    pages = (total_int + page_size - 1) // page_size if page_size else 1
    return [conversation_dict(row) for row in rows], {
        "page": page,
        "page_size": page_size,
        "total": total_int,
        "pages": max(1, pages),
    }


async def delete_conversation(
    session: AsyncSession, owner_id: str, conversation_id: uuid.UUID
) -> None:
    row = await get_conversation(session, owner_id, conversation_id)
    await session.delete(row)
    await session.flush()


async def add_message(
    session: AsyncSession,
    conversation: AIConversation,
    *,
    role: str,
    content: str,
    context: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
    provider: str | None = None,
    model: str | None = None,
    refs: dict[str, Any] | None = None,
) -> AIMessage:
    row = AIMessage(
        conversation_id=conversation.id,
        owner_id=conversation.owner_id,
        seq=conversation.message_count + 1,
        role=role,
        content=content,
        context=context,
        validation=validation,
        provider=provider,
        model=model,
        refs=refs,
    )
    session.add(row)
    conversation.message_count += 1
    conversation.last_message_at = datetime.now(tz=timezone.utc)
    await session.flush()
    await session.refresh(row)  # load server defaults in async context
    await session.refresh(conversation)  # refresh server-onupdate columns
    return row


async def list_messages(
    session: AsyncSession,
    owner_id: str,
    conversation: AIConversation,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    limit = min(max(1, limit), 200)
    rows = list(
        (
            await session.scalars(
                select(AIMessage)
                .where(
                    AIMessage.conversation_id == conversation.id,
                    AIMessage.owner_id == owner_id,
                )
                .order_by(AIMessage.seq.desc())
                .limit(limit)
            )
        ).all()
    )
    return [message_dict(row) for row in reversed(rows)]


__all__ = [
    "add_message",
    "conversation_dict",
    "create_conversation",
    "delete_conversation",
    "get_conversation",
    "list_conversations",
    "list_messages",
    "message_dict",
]
