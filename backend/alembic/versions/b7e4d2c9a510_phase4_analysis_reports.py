"""phase 4: analysis engine — persisted explainable analysis reports

Revision ID: b7e4d2c9a510
Revises: a3f8c1d92e47
Create Date: 2026-11-02 00:00:00.000000

Phase 4 changes:

* ``analysis_reports`` table: one immutable, versioned report payload per
  engine run. The full report (rule-set snapshot, condition tree, regime
  evidence, data-quality warnings) lives in the ``report`` JSONB column;
  candle data is never duplicated — reports carry references only.
* ``strategy_id`` is ``ON DELETE SET NULL`` so analysis history survives
  strategy deletion; ``strategy_name`` is denormalised for listings.
* Owner-scoped indexes for history listing (owner + created_at, owner +
  symbol, owner + strategy).
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7e4d2c9a510"
down_revision: str | None = "a3f8c1d92e47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SETUP_STATUS_CHECK = (
    "setup_status IN ('conditions_met', 'conditions_missing', "
    "'invalidated', 'insufficient_data', 'inconclusive', "
    "'regime_mismatch')"
)


def upgrade() -> None:
    op.create_table(
        "analysis_reports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "owner_id",
            sa.String(length=64),
            server_default="local",
            nullable=False,
        ),
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("strategy_id", sa.UUID(), nullable=True),
        sa.Column("strategy_name", sa.String(length=160), nullable=True),
        sa.Column("strategy_version", sa.Integer(), nullable=False),
        sa.Column("setup_status", sa.String(length=24), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("regime", sa.String(length=16), nullable=True),
        sa.Column("report_schema_version", sa.String(length=8), nullable=False),
        sa.Column("engine_version", sa.String(length=32), nullable=False),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_reports")),
        sa.CheckConstraint(
            "mode IN ('forex', 'binary_options')", name="valid_mode"
        ),
        sa.CheckConstraint(
            _SETUP_STATUS_CHECK, name="valid_setup_status"
        ),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.id"],
            name=op.f("fk_analysis_reports_strategy_id_strategies"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        op.f("ix_analysis_reports_owner_id"),
        "analysis_reports",
        ["owner_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_analysis_reports_symbol"),
        "analysis_reports",
        ["symbol"],
        unique=False,
    )
    op.create_index(
        op.f("ix_analysis_reports_mode"),
        "analysis_reports",
        ["mode"],
        unique=False,
    )
    op.create_index(
        op.f("ix_analysis_reports_strategy_id"),
        "analysis_reports",
        ["strategy_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_analysis_reports_setup_status"),
        "analysis_reports",
        ["setup_status"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_reports_owner_created_at",
        "analysis_reports",
        ["owner_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_reports_owner_created_at", table_name="analysis_reports")
    op.drop_index(op.f("ix_analysis_reports_setup_status"), table_name="analysis_reports")
    op.drop_index(op.f("ix_analysis_reports_strategy_id"), table_name="analysis_reports")
    op.drop_index(op.f("ix_analysis_reports_mode"), table_name="analysis_reports")
    op.drop_index(op.f("ix_analysis_reports_symbol"), table_name="analysis_reports")
    op.drop_index(op.f("ix_analysis_reports_owner_id"), table_name="analysis_reports")
    op.drop_table("analysis_reports")
