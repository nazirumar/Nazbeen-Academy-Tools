"""phase 11: historical market replay sessions

Revision ID: e2c7a91b4d30
Revises: c9a1e5d7b324
Create Date: 2026-11-05 00:00:00.000000

Phase 11 adds one small persistence table:

* ``replay_sessions`` - a saved deterministic cursor over the platform's
  existing PostgreSQL candle cache. The requested window, resolved closed
  candle window, honest data-quality summary, cursor time and visibility
  counters are persisted so a session can be resumed later. Playback speed is
  presentation-only.

No candle data is duplicated, no future candle is exposed by the API, and no
order/execution table is added in this first vertical slice. The existing
Phase 6 backtest tables remain the single source of simulated trade records.
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e2c7a91b4d30"
down_revision: str | None = "c9a1e5d7b324"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "replay_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_candle_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_candle_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cursor_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_candles", sa.Integer(), nullable=False),
        sa.Column("visible_candles", sa.Integer(), nullable=False),
        sa.Column("playback_speed", sa.Double(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "data_quality", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("session_schema_version", sa.String(length=8), nullable=False),
        sa.Column("last_advanced_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "mode IN ('forex', 'binary_options')",
            name=op.f("ck_replay_sessions_valid_mode"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'completed')",
            name=op.f("ck_replay_sessions_valid_status"),
        ),
        sa.CheckConstraint(
            "visible_candles >= 1",
            name=op.f("ck_replay_sessions_valid_visible_candles"),
        ),
        sa.CheckConstraint(
            "total_candles >= 1",
            name=op.f("ck_replay_sessions_valid_total_candles"),
        ),
        sa.CheckConstraint(
            "visible_candles <= total_candles",
            name=op.f("ck_replay_sessions_visible_lte_total"),
        ),
        sa.CheckConstraint(
            "playback_speed > 0",
            name=op.f("ck_replay_sessions_valid_playback_speed"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_replay_sessions")),
    )
    op.create_index(
        op.f("ix_replay_sessions_owner_id"),
        "replay_sessions",
        ["owner_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_replay_sessions_mode"),
        "replay_sessions",
        ["mode"],
        unique=False,
    )
    op.create_index(
        op.f("ix_replay_sessions_symbol"),
        "replay_sessions",
        ["symbol"],
        unique=False,
    )
    op.create_index(
        op.f("ix_replay_sessions_status"),
        "replay_sessions",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_replay_sessions_owner_created_at",
        "replay_sessions",
        ["owner_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_replay_sessions_owner_status",
        "replay_sessions",
        ["owner_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_replay_sessions_owner_status", table_name="replay_sessions")
    op.drop_index("ix_replay_sessions_owner_created_at", table_name="replay_sessions")
    op.drop_index(op.f("ix_replay_sessions_status"), table_name="replay_sessions")
    op.drop_index(op.f("ix_replay_sessions_symbol"), table_name="replay_sessions")
    op.drop_index(op.f("ix_replay_sessions_mode"), table_name="replay_sessions")
    op.drop_index(op.f("ix_replay_sessions_owner_id"), table_name="replay_sessions")
    op.drop_table("replay_sessions")
