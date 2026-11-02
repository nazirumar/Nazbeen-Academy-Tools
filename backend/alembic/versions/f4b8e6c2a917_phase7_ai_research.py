"""phase 7: AI research layer — conversations and auditable messages

Revision ID: f4b8e6c2a917
Revises: d8e5f3a1b620
Create Date: 2026-11-02 00:00:00.000000

Phase 7 changes:

* ``ai_conversations`` — owner-scoped research conversations. Loose
  references to one analysis report and/or one backtest run (plain UUID
  columns, no FK: deleting the referenced artefact must never cascade or
  orphan conversations — access is re-validated against the requesting
  owner on every message instead).
* ``ai_messages`` — user questions and AI answers with the minimized,
  validated context that was supplied to the provider stored alongside
  (``context`` JSONB) plus the validation summary, provider/model name and
  artefact references. Every AI statement stays auditable against the
  evidence it was given. The AI layer itself has NO database write
  capability: rows are created exclusively by authenticated services.
* Unique ``(conversation_id, seq)`` preserves message ordering; owner
  indexes keep listings scoped; role is whitelist-constrained.
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f4b8e6c2a917"
down_revision: str | None = "d8e5f3a1b620"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_conversations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "owner_id",
            sa.String(length=64),
            server_default="local",
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("analysis_id", sa.UUID(), nullable=True),
        sa.Column("backtest_id", sa.UUID(), nullable=True),
        sa.Column("ai_schema_version", sa.String(length=8), nullable=False),
        sa.Column("message_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_conversations")),
    )
    op.create_index(
        op.f("ix_ai_conversations_owner_id"),
        "ai_conversations",
        ["owner_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_conversations_owner_updated",
        "ai_conversations",
        ["owner_id", "updated_at"],
        unique=False,
    )

    op.create_table(
        "ai_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column(
            "owner_id",
            sa.String(length=64),
            server_default="local",
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=12), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("validation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("refs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ai_conversations.id"],
            name=op.f("fk_ai_messages_conversation_id_ai_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_messages")),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'error')",
            name=op.f("ck_ai_messages_valid_role"),
        ),
    )
    op.create_index(
        op.f("ix_ai_messages_conversation_id"),
        "ai_messages",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_messages_conversation_seq",
        "ai_messages",
        ["conversation_id", "seq"],
        unique=True,
    )
    op.create_index(
        op.f("ix_ai_messages_owner_id"),
        "ai_messages",
        ["owner_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("ai_messages")
    op.drop_table("ai_conversations")
