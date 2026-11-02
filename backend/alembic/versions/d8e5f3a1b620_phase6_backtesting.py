"""phase 6: backtesting — backtest runs and chronological result records

Revision ID: d8e5f3a1b620
Revises: b7e4d2c9a510
Create Date: 2026-11-02 00:00:00.000000

Phase 6 changes:

* ``backtest_runs`` — one validated backtest configuration + lifecycle
  (queued/running/completed/failed/cancelled), the resolved immutable
  strategy-version reference, data-quality summary and aggregate metrics.
* ``backtest_results`` — chronological evaluation/setup/trade records with
  ``seq`` (strict order), ``result_time`` (simulated evaluation instant),
  outcome, realized P&L, equity path points and the full explainable
  evaluation payload (JSONB). Candle data is referenced, never duplicated.
* ``strategy_id`` / ``strategy_version_id`` are ``ON DELETE SET NULL``:
  run history survives strategy deletion; the rule-set snapshot inside
  ``backtest_runs.config`` keeps every completed run auditable.
* Owner-scoped indexes; unique ``(run_id, seq)`` guarantees chronological
  order integrity.
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d8e5f3a1b620"
down_revision: str | None = "b7e4d2c9a510"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "owner_id",
            sa.String(length=64),
            server_default="local",
            nullable=False,
        ),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("strategy_id", sa.UUID(), nullable=True),
        sa.Column("strategy_name", sa.String(length=160), nullable=True),
        sa.Column("strategy_version", sa.Integer(), nullable=False),
        sa.Column("strategy_version_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("data_quality", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("backtest_schema_version", sa.String(length=8), nullable=False),
        sa.Column("engine_version", sa.String(length=32), nullable=False),
        sa.Column("candles_evaluated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_total", sa.Integer(), server_default="0", nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backtest_runs")),
        sa.CheckConstraint("mode IN ('forex', 'binary_options')", name="valid_mode"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="valid_status",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.id"],
            name=op.f("fk_backtest_runs_strategy_id_strategies"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"],
            ["strategy_versions.id"],
            name=op.f("fk_backtest_runs_strategy_version_id_strategy_versions"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(op.f("ix_backtest_runs_owner_id"), "backtest_runs", ["owner_id"], unique=False)
    op.create_index(op.f("ix_backtest_runs_mode"), "backtest_runs", ["mode"], unique=False)
    op.create_index(op.f("ix_backtest_runs_symbol"), "backtest_runs", ["symbol"], unique=False)
    op.create_index(
        op.f("ix_backtest_runs_status"), "backtest_runs", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_backtest_runs_strategy_id"), "backtest_runs", ["strategy_id"], unique=False
    )
    op.create_index(
        op.f("ix_backtest_runs_strategy_version_id"),
        "backtest_runs",
        ["strategy_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_backtest_runs_owner_created_at", "backtest_runs", ["owner_id", "created_at"]
    )
    op.create_index(
        "ix_backtest_runs_owner_status", "backtest_runs", ["owner_id", "status"]
    )

    op.create_table(
        "backtest_results",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("result_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("setup_status", sa.String(length=24), nullable=True),
        sa.Column("direction", sa.String(length=16), nullable=True),
        sa.Column("regime", sa.String(length=16), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("pnl", sa.Double(), nullable=True),
        sa.Column("return_pct", sa.Double(), nullable=True),
        sa.Column("equity_after", sa.Double(), nullable=True),
        sa.Column("evaluation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("execution", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backtest_results")),
        sa.CheckConstraint("kind IN ('setup', 'trade')", name="valid_kind"),
        sa.CheckConstraint(
            "outcome IN ('win', 'loss', 'tie', 'open', 'unresolved', "
            "'ambiguous', 'skipped') OR outcome IS NULL",
            name="valid_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["backtest_runs.id"],
            name=op.f("fk_backtest_results_run_id_backtest_runs"),
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_backtest_results_run_seq", "backtest_results", ["run_id", "seq"], unique=True)
    op.create_index("ix_backtest_results_run_time", "backtest_results", ["run_id", "result_time"])
    op.create_index("ix_backtest_results_owner_id", "backtest_results", ["owner_id"])
    op.create_index(op.f("ix_backtest_results_setup_status"), "backtest_results", ["setup_status"])
    op.create_index(op.f("ix_backtest_results_outcome"), "backtest_results", ["outcome"])


def downgrade() -> None:
    op.drop_index(op.f("ix_backtest_results_outcome"), table_name="backtest_results")
    op.drop_index(op.f("ix_backtest_results_setup_status"), table_name="backtest_results")
    op.drop_index("ix_backtest_results_owner_id", table_name="backtest_results")
    op.drop_index("ix_backtest_results_run_time", table_name="backtest_results")
    op.drop_index("ix_backtest_results_run_seq", table_name="backtest_results")
    op.drop_table("backtest_results")
    op.drop_index("ix_backtest_runs_owner_status", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_owner_created_at", table_name="backtest_runs")
    op.drop_index(op.f("ix_backtest_runs_strategy_version_id"), table_name="backtest_runs")
    op.drop_index(op.f("ix_backtest_runs_strategy_id"), table_name="backtest_runs")
    op.drop_index(op.f("ix_backtest_runs_status"), table_name="backtest_runs")
    op.drop_index(op.f("ix_backtest_runs_symbol"), table_name="backtest_runs")
    op.drop_index(op.f("ix_backtest_runs_mode"), table_name="backtest_runs")
    op.drop_index(op.f("ix_backtest_runs_owner_id"), table_name="backtest_runs")
    op.drop_table("backtest_runs")
