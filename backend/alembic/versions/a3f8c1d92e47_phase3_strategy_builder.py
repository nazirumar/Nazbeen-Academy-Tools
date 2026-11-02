"""phase 3: strategy builder — ownership, workflows, rule schema v2

Revision ID: a3f8c1d92e47
Revises: c5f21a0b93d2
Create Date: 2026-11-02 00:00:00.000000

Phase 3 changes:

* Ownership columns (``owner_id``) on categories, strategies and validation
  results. Existing rows belong to the default ``local`` owner.
* Category archiving (``is_archived``) — non-destructive organisation.
* Version lifecycle (``strategy_versions.status``): existing versions were
  created under the save-as-current model, so they are backfilled to
  ``published`` and pointed to by ``strategies.published_version_number``.
* Direction ``both`` is now legal (CHECK constraint rebuilt).
* ``strategy_validation_results`` audit table.
* Uniqueness now includes ``owner_id`` so different owners never collide.
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3f8c1d92e47"
down_revision: str | None = "c5f21a0b93d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_OWNER = "local"


def upgrade() -> None:
    # --- strategy_categories: owner + archive ---------------------------------
    op.add_column(
        "strategy_categories",
        sa.Column(
            "owner_id",
            sa.String(length=64),
            nullable=False,
            server_default=_DEFAULT_OWNER,
        ),
    )
    op.add_column(
        "strategy_categories",
        sa.Column(
            "is_archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        op.f("ix_strategy_categories_owner_id"),
        "strategy_categories",
        ["owner_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_categories_is_archived"),
        "strategy_categories",
        ["is_archived"],
        unique=False,
    )
    op.drop_constraint("uq_category_sibling_name", "strategy_categories", type_="unique")
    op.create_unique_constraint(
        "uq_category_sibling_name",
        "strategy_categories",
        ["owner_id", "parent_id", "name", "mode"],
    )

    # --- strategies: owner + published pointer + direction 'both' --------------
    op.add_column(
        "strategies",
        sa.Column(
            "owner_id",
            sa.String(length=64),
            nullable=False,
            server_default=_DEFAULT_OWNER,
        ),
    )
    op.add_column(
        "strategies",
        sa.Column("published_version_number", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_strategies_owner_id"), "strategies", ["owner_id"], unique=False
    )
    op.drop_constraint("uq_strategy_name_in_category", "strategies", type_="unique")
    op.create_unique_constraint(
        "uq_strategy_name_in_category", "strategies", ["owner_id", "category_id", "name"]
    )
    op.drop_constraint("valid_direction", "strategies", type_="check")
    op.create_check_constraint(
        "valid_direction",
        "strategies",
        "direction IN ('bullish', 'bearish', 'both')",
    )

    # --- strategy_versions: lifecycle status -----------------------------------
    op.add_column(
        "strategy_versions",
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="draft",
        ),
    )
    op.create_check_constraint(
        "valid_version_status",
        "strategy_versions",
        "status IN ('draft', 'published', 'superseded')",
    )
    # Backfill: every pre-Phase-3 version was created as the current
    # definition of its strategy -> published; strategies point at it.
    op.execute(
        "UPDATE strategy_versions SET status = 'published' WHERE status = 'draft'"
    )
    op.execute(
        "UPDATE strategies SET published_version_number = current_version "
        "WHERE current_version > 0"
    )

    # --- strategy_validation_results (audit of validation runs) -----------------
    op.create_table(
        "strategy_validation_results",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_id", sa.UUID(), nullable=True),
        sa.Column("owner_id", sa.String(length=64), nullable=False, server_default=_DEFAULT_OWNER),
        sa.Column("schema_version", sa.String(length=8), nullable=False),
        sa.Column("valid", sa.Boolean(), nullable=False),
        sa.Column("errors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("condition_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("valid IN (true, false)", name=op.f("ck_strategy_validation_results_valid")),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.id"],
            name=op.f("fk_strategy_validation_results_strategy_id_strategies"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_strategy_validation_results")),
    )
    op.create_index(
        op.f("ix_strategy_validation_results_strategy_id"),
        "strategy_validation_results",
        ["strategy_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_validation_results_owner_id"),
        "strategy_validation_results",
        ["owner_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("strategy_validation_results")

    op.drop_constraint(
        "valid_version_status", "strategy_versions", type_="check"
    )
    op.drop_column("strategy_versions", "status")

    op.drop_constraint(
        "valid_direction", "strategies", type_="check"
    )
    op.create_check_constraint(
        "valid_direction", "strategies", "direction IN ('bullish', 'bearish')"
    )
    op.drop_constraint(
        "uq_strategy_name_in_category", "strategies", type_="unique"
    )
    op.create_unique_constraint(
        "uq_strategy_name_in_category", "strategies", ["category_id", "name"]
    )
    op.drop_index(op.f("ix_strategies_owner_id"), table_name="strategies")
    op.drop_column("strategies", "published_version_number")
    op.drop_column("strategies", "owner_id")

    op.drop_constraint(
        "uq_category_sibling_name", "strategy_categories", type_="unique"
    )
    op.create_unique_constraint(
        "uq_category_sibling_name", "strategy_categories", ["parent_id", "name", "mode"]
    )
    op.drop_index(op.f("ix_strategy_categories_is_archived"), table_name="strategy_categories")
    op.drop_index(op.f("ix_strategy_categories_owner_id"), table_name="strategy_categories")
    op.drop_column("strategy_categories", "is_archived")
    op.drop_column("strategy_categories", "owner_id")
