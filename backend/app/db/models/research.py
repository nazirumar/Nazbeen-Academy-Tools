"""Research persistence models (Phase 8) — optimization & walk-forward.

Design rules (mirroring Phase 6 backtesting):

* ``ResearchRun`` — one fully reproducible research configuration (search
  space, objective, period definitions, walk-forward settings, execution
  assumptions, seed, software versions) + lifecycle + aggregate results.
  The configuration is stored as validated at creation time; a later
  strategy edit can never silently alter an earlier run's meaning.
* ``ResearchCandidate`` — one explicitly selected parameter combination
  (the patch applied to the immutable rule-set snapshot) and its
  evaluation results. Candidate 0 is ALWAYS the original, unmodified
  parameterization, so the unchanged strategy is always part of the
  comparison. ``rank`` is assigned deterministically; unrankable
  candidates keep ``rank = NULL`` with an explicit reason (undefined
  metric, insufficient samples) — never a fabricated zero.
* ``ResearchWindow`` — one chronological walk-forward window: its exact
  training and testing periods, the per-candidate training evaluations,
  the frozen selected parameters and the out-of-sample test metrics.
  Candidates are selected ONLY from training data; test windows are held
  out by construction (``train_end == test_start``, non-overlapping).
* ``ResearchScenario`` — one robustness scenario (execution-cost variant,
  parameter-neighbour sensitivity, seeded bootstrap, selection stability)
  with its assumptions, results and an explicit note about what the
  scenario can and cannot establish.

A candidate configuration is NOT a strategy version: it becomes a new
draft strategy version only through the explicit Strategy Builder flow.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
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

RESEARCH_SCHEMA_VERSION = "1.0"
RESEARCH_RUN_STATUSES = ("queued", "running", "completed", "failed", "cancelled")
VALIDATION_MODES = ("in_sample_only", "holdout", "walk_forward")
WINDOW_MODES = ("rolling", "expanding")
CANDIDATE_STATUSES = ("evaluated", "invalid", "failed", "pending")
WINDOW_STATUSES = ("completed", "skipped", "failed")
SCENARIO_CATEGORIES = (
    "cost_variant",        # B: execution-cost / payout sensitivity
    "param_neighbor",      # A: nearby-parameter sensitivity
    "bootstrap",           # F: optional seeded resampling stress test
    "selection_stability", # WF: dispersion of per-window selected params
)


class ResearchRun(Base, TimestampMixin):
    """One optimization/validation run: config, lifecycle, aggregate."""

    __tablename__ = "research_runs"
    __table_args__ = (
        CheckConstraint("mode IN ('forex', 'binary_options')", name="valid_mode"),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="valid_status",
        ),
        CheckConstraint(
            "validation_mode IN ('in_sample_only', 'holdout', 'walk_forward')",
            name="valid_validation_mode",
        ),
        Index("ix_research_runs_owner_created_at", "owner_id", "created_at"),
        Index("ix_research_runs_owner_status", "owner_id", "status"),
        Index("ix_research_runs_strategy", "owner_id", "strategy_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    mode: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    validation_mode: Mapped[str] = mapped_column(String(20), nullable=False)

    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    strategy_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    strategy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategy_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", index=True
    )
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Full reproducible configuration (see docs/research-optimization-schema.md):
    # search space, objective, period split, walk-forward settings, execution
    # assumptions, seed, software versions and the immutable rule-set snapshot.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Progress {phase, candidates_total, candidates_evaluated, candidates_failed,
    # windows_total, windows_completed, scenarios_total, scenarios_completed}.
    progress: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Union of per-walk data-quality summaries + pre-run coverage.
    data_quality: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Aggregate results: best candidate, OOS aggregation, time-period /
    # regime / data-quality stability views, disclaimers and warnings.
    aggregate: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Failure details {code, message, details} for failed runs.
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    research_schema_version: Mapped[str] = mapped_column(String(8), nullable=False)
    research_version: Mapped[str] = mapped_column(String(32), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)

    walks_executed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchCandidate(Base, TimestampMixin):
    """One explicitly selected parameter combination + its results."""

    __tablename__ = "research_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'evaluated', 'invalid', 'failed')",
            name="valid_status",
        ),
        Index("ix_research_candidates_run_index", "run_id", "candidate_index", unique=True),
        Index("ix_research_candidates_run_rank", "run_id", "rank"),
        Index("ix_research_candidates_owner_id", "owner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # 0-based, deterministic enumeration order (candidate 0 = original params).
    candidate_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")

    # The explicit patch: [{condition_id, field, value}, ...] — the ONLY
    # difference from the immutable rule-set snapshot.
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # The FULL patched rule set (validated before evaluation) — what was
    # actually simulated, independent of any later strategy edits.
    rule_set: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # Objective score on the SELECTION window (in-sample / train / whole).
    # NULL = not rankable (undefined metric, constraints, failure) — reason below.
    score: Mapped[float | None] = mapped_column(Double, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selectable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    constraint_violations: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    rank_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Selection-window metrics digest (in-sample for holdout / whole-window
    # for in_sample_only; for walk-forward runs: NULL here — per-window
    # training evaluations live on the windows).
    in_sample: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Out-of-sample metrics digest — ONLY for the candidate selected on
    # in-sample data (holdout mode). Never populated by OOS peeking.
    out_of_sample: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Walk-forward convenience aggregates (selection window only).
    times_selected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class ResearchWindow(Base, TimestampMixin):
    """One chronological walk-forward window (train -> test)."""

    __tablename__ = "research_windows"
    __table_args__ = (
        CheckConstraint(
            "status IN ('completed', 'skipped', 'failed')",
            name="valid_status",
        ),
        Index("ix_research_windows_run_index", "run_id", "window_index", unique=True),
        Index("ix_research_windows_owner_id", "owner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)

    window_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="completed")

    # Exact chronological periods (training strictly precedes testing;
    # train_end == test_start — no gap, no overlap).
    train_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    train_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    test_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    test_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Per-candidate TRAINING evaluations (compact digests; the ONLY input to
    # selection). Bounded by the run's walk limit.
    train_evaluations: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    # The frozen winner (candidate_index + params) — selected on train data.
    selected_candidate_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    selection_score: Mapped[float | None] = mapped_column(Double, nullable=True)

    # Out-of-sample evaluation of the FROZEN winner on the test window.
    test_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ResearchScenario(Base, TimestampMixin):
    """One robustness scenario with documented assumptions."""

    __tablename__ = "research_scenarios"
    __table_args__ = (
        CheckConstraint(
            "category IN ('cost_variant', 'param_neighbor', 'bootstrap', "
            "'selection_stability')",
            name="valid_category",
        ),
        Index("ix_research_scenarios_run_index", "run_id", "scenario_index", unique=True),
        Index("ix_research_scenarios_owner_id", "owner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)

    scenario_index: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)

    # What changed vs the baseline assumptions (explicit, never implicit).
    assumptions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Result metrics digest + baseline delta; NULL when the scenario failed.
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    delta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="completed")
    # What this scenario establishes and what it does NOT.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


__all__ = [
    "CANDIDATE_STATUSES",
    "RESEARCH_RUN_STATUSES",
    "RESEARCH_SCHEMA_VERSION",
    "ResearchCandidate",
    "ResearchRun",
    "ResearchScenario",
    "ResearchWindow",
    "SCENARIO_CATEGORIES",
    "VALIDATION_MODES",
    "WINDOW_MODES",
    "WINDOW_STATUSES",
]
