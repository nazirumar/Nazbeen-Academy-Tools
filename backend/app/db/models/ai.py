"""AI research-layer persistence models (Phase 7).

* ``AIConversation`` — one research conversation, scoped by ``owner_id``.
  Conversations may reference (loosely — no FK, validated at access time)
  one analysis report and/or one backtest run so the chat context stays
  attached to the artefact being discussed. Every referenced record is
  re-validated against the requesting owner on EVERY message — a foreign
  reference is indistinguishable from a non-existent one (404).
* ``AIMessage`` — one message in a conversation. The minimized, validated
  AI context that was supplied to the provider is stored alongside the
  answer (``context`` JSONB) together with the validation summary, so
  every AI statement stays auditable against the evidence it was given.
  The AI layer has NO database write capability itself: rows are created
  exclusively by ordinary, authenticated application services.

Secrets policy: user text is redacted before it reaches a provider and is
never logged; the model/provider name is stored but API keys never are.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UUID,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

AI_SCHEMA_VERSION = "1.0"
MESSAGE_ROLES = ("user", "assistant", "system", "error")
CONTEXT_KINDS = ("analysis", "backtest", "chat")


class AIConversation(Base, TimestampMixin):
    """One owner-scoped research conversation."""

    __tablename__ = "ai_conversations"
    __table_args__ = (
        Index("ix_ai_conversations_owner_updated", "owner_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    # Loose references (validated against the owner at access time; no FK so
    # deleting an analysis/backtest never orphans or cascades conversations).
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    backtest_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    ai_schema_version: Mapped[str] = mapped_column(String(8), nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AIMessage(Base, TimestampMixin):
    """One message inside a conversation (user question or AI answer)."""

    __tablename__ = "ai_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'system', 'error')", name="valid_role"),
        Index("ix_ai_messages_conversation_seq", "conversation_id", "seq", unique=True),
        Index("ix_ai_messages_owner_id", "owner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)

    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(12), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # The minimized, validated context supplied to the provider (audit trail:
    # every AI statement can be checked against the evidence it was given).
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Validation summary {schema_ok, evidence_refs_ok, numeric_trace_ok, checks...}.
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    refs: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


__all__ = [
    "AI_SCHEMA_VERSION",
    "AIConversation",
    "AIMessage",
    "CONTEXT_KINDS",
    "MESSAGE_ROLES",
]
