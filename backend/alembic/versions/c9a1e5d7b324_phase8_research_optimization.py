"""phase 8: research optimization, walk-forward validation & robustness

Revision ID: c9a1e5d7b324
Revises: f4b8e6c2a917
Create Date: 2026-11-02 00:00:00.000000

Phase 8 changes:

* ``research_runs`` — one reproducible research configuration (bounded
  parameter search space, objective function, in-sample/out-of-sample
  period definitions, walk-forward settings, execution assumptions, seed,
  software versions, immutable rule-set snapshot) + lifecycle + aggregate
  results. A later strategy edit never alters an earlier run's meaning.
* ``research_candidates`` — explicitly selected parameter combinations
  (patches against the immutable snapshot) with selection-window scores,
  deterministic ranks, constraint evaluations and in/out-of-sample
  metric digests. Candidate 0 is always the original parameterization.
* ``research_windows`` — chronological walk-forward windows with exact
  training/testing boundaries (``train_end == test_start``, non-overlapping
  test windows), per-candidate training evaluations, the frozen selected
  parameters and the out-of-sample test metrics.
* ``research_scenarios`` — robustness scenarios (execution-cost variants,
  parameter neighbours, optional seeded bootstrap, selection stability)
  with explicit assumptions and documented limits.

No automatic trading, no fabricated data, no automatic publishing: the
tables store simulations of explicitly selected configurations only.
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9a1e5d7b324"
down_revision: str | None = "f4b8e6c2a917"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # AI conversations may reference one research run (loose reference,
    # validated against the owner at access time — same semantics as the
    # analysis/backtest attachments).
    op.add_column(
        "ai_conversations",
        sa.Column("research_id", sa.UUID(), nullable=True),
    )

    op.create_table(
        "research_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("validation_mode", sa.String(length=20), nullable=False),
        sa.Column(
            "strategy_id",
            sa.UUID(),
            sa.ForeignKey("strategies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("strategy_name", sa.String(length=160), nullable=True),
        sa.Column("strategy_version", sa.Integer(), nullable=False),
        sa.Column(
            "strategy_version_id",
            sa.UUID(),
            sa.ForeignKey("strategy_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("data_quality", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("aggregate", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("research_schema_version", sa.String(length=8), nullable=False),
        sa.Column("research_version", sa.String(length=32), nullable=False),
        sa.Column("engine_version", sa.String(length=32), nullable=False),
        sa.Column("walks_executed", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "mode IN ('forex', 'binary_options')", name="valid_mode"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="valid_status",
        ),
        sa.CheckConstraint(
            "validation_mode IN ('in_sample_only', 'holdout', 'walk_forward')",
            name="valid_validation_mode",
        ),
    )
    op.create_index(
        "ix_research_runs_owner_created_at", "research_runs", ["owner_id", "created_at"]
    )
    op.create_index(
        "ix_research_runs_owner_status", "research_runs", ["owner_id", "status"]
    )
    op.create_index(
        "ix_research_runs_strategy", "research_runs", ["owner_id", "strategy_id"]
    )
    op.create_index(
        "ix_research_runs_mode", "research_runs", ["mode"]
    )
    op.create_index(
        "ix_research_runs_symbol", "research_runs", ["symbol"]
    )
    op.create_index(
        "ix_research_runs_status", "research_runs", ["status"]
    )
    op.create_index(
        "ix_research_runs_strategy_id", "research_runs", ["strategy_id"]
    )
    op.create_index(
        "ix_research_runs_strategy_version_id",
        "research_runs",
        ["strategy_version_id"],
    )
    op.create_index("ix_research_runs_owner_id", "research_runs", ["owner_id"])

    op.create_table(
        "research_candidates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "run_id",
            sa.UUID(),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("candidate_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rule_set", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("score", sa.Double(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("selectable", sa.Boolean(), nullable=False),
        sa.Column(
            "constraint_violations", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("rank_note", sa.Text(), nullable=True),
        sa.Column("in_sample", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("out_of_sample", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("times_selected", sa.Integer(), nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending', 'evaluated', 'invalid', 'failed')",
            name="valid_status",
        ),
    )
    op.create_index(
        "ix_research_candidates_run_index",
        "research_candidates",
        ["run_id", "candidate_index"],
        unique=True,
    )
    op.create_index(
        "ix_research_candidates_run_rank", "research_candidates", ["run_id", "rank"]
    )
    op.create_index("ix_research_candidates_owner_id", "research_candidates", ["owner_id"])
    op.create_index("ix_research_candidates_run_id", "research_candidates", ["run_id"])

    op.create_table(
        "research_windows",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "run_id",
            sa.UUID(),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("window_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("train_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("train_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("test_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("test_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "train_evaluations", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("selected_candidate_index", sa.Integer(), nullable=True),
        sa.Column("selected_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("selection_score", sa.Double(), nullable=True),
        sa.Column("test_metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('completed', 'skipped', 'failed')", name="valid_status"
        ),
    )
    op.create_index(
        "ix_research_windows_run_index",
        "research_windows",
        ["run_id", "window_index"],
        unique=True,
    )
    op.create_index("ix_research_windows_owner_id", "research_windows", ["owner_id"])
    op.create_index("ix_research_windows_run_id", "research_windows", ["run_id"])

    op.create_table(
        "research_scenarios",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "run_id",
            sa.UUID(),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("scenario_index", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=24), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("delta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "category IN ('cost_variant', 'param_neighbor', 'bootstrap', "
            "'selection_stability')",
            name="valid_category",
        ),
    )
    op.create_index(
        "ix_research_scenarios_run_index",
        "research_scenarios",
        ["run_id", "scenario_index"],
        unique=True,
    )
    op.create_index("ix_research_scenarios_owner_id", "research_scenarios", ["owner_id"])
    op.create_index("ix_research_scenarios_run_id", "research_scenarios", ["run_id"])


def downgrade() -> None:
    op.drop_column("ai_conversations", "research_id")
    op.drop_table("research_scenarios")
    op.drop_table("research_windows")
    op.drop_table("research_candidates")
    op.drop_index("ix_research_runs_owner_id", table_name="research_runs")
    op.drop_index("ix_research_runs_strategy_version_id", table_name="research_runs")
    op.drop_index("ix_research_runs_strategy_id", table_name="research_runs")
    op.drop_index("ix_research_runs_status", table_name="research_runs")
    op.drop_index("ix_research_runs_symbol", table_name="research_runs")
    op.drop_index("ix_research_runs_mode", table_name="research_runs")
    op.drop_index("ix_research_runs_strategy", table_name="research_runs")
    op.drop_index("ix_research_runs_owner_status", table_name="research_runs")
    op.drop_index("ix_research_runs_owner_created_at", table_name="research_runs")
    op.drop_table("research_runs")
