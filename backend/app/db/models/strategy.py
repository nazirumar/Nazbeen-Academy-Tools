"""Strategy domain persistence models.

- ``StrategyCategory``: user-owned hierarchical categories (one level of
  nesting is enforced at the API layer via ``parent_id``; the schema supports
  arbitrary depth for future needs). Categories can be archived instead of
  deleted — destructive operations never silently remove dependent records.
- ``Strategy``: a user-created strategy belonging to a category; never
  contains logic itself. Points at the currently published version (if any).
- ``StrategyVersion``: immutable, versioned JSON rule sets with a lifecycle
  status (draft -> published -> superseded). Every accepted edit produces a
  NEW row — previous versions are never mutated, giving a full audit trail
  for later backtesting.
- ``StrategyValidationResult``: audit trail of schema validation runs
  performed against a strategy (useful to prove what was validated and when).

Ownership: every category/strategy row carries an ``owner_id`` resolved from
the ``X-User-Id`` request header (default ``local`` for single-user local
deployments). All queries are owner-scoped; foreign owners get 404s.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

MODES = ("forex", "binary_options")
STRATEGY_STATUSES = ("draft", "active", "archived")
DIRECTIONS = ("bullish", "bearish", "both")
VERSION_STATUSES = ("draft", "published", "superseded")

# Default owner for deployments that do not send the X-User-Id header.
DEFAULT_OWNER_ID = "local"
OWNER_ID_MAX_LEN = 64


class StrategyCategory(Base, TimestampMixin):
    __tablename__ = "strategy_categories"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('forex', 'binary_options')", name="valid_mode"
        ),
        UniqueConstraint(
            "owner_id", "parent_id", "name", "mode", name="uq_category_sibling_name"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[str] = mapped_column(
        String(OWNER_ID_MAX_LEN), nullable=False, default=DEFAULT_OWNER_ID, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategy_categories.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )

    parent: Mapped["StrategyCategory | None"] = relationship(
        back_populates="children", remote_side="StrategyCategory.id"
    )
    children: Mapped[list["StrategyCategory"]] = relationship(back_populates="parent")
    strategies: Mapped[list["Strategy"]] = relationship(back_populates="category")


class Strategy(Base, TimestampMixin):
    __tablename__ = "strategies"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('forex', 'binary_options')", name="valid_mode"
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'archived')", name="valid_status"
        ),
        CheckConstraint(
            "direction IN ('bullish', 'bearish', 'both')", name="valid_direction"
        ),
        UniqueConstraint(
            "owner_id", "category_id", "name", name="uq_strategy_name_in_category"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[str] = mapped_column(
        String(OWNER_ID_MAX_LEN), nullable=False, default=DEFAULT_OWNER_ID, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategy_categories.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # 0 means "created but no rule set saved yet".
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Version number currently published (immutable definition); NULL while
    # the strategy has never been published.
    published_version_number: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    category: Mapped["StrategyCategory | None"] = relationship(
        back_populates="strategies"
    )
    versions: Mapped[list["StrategyVersion"]] = relationship(
        back_populates="strategy",
        cascade="all, delete-orphan",
        order_by="StrategyVersion.version.desc()",
    )
    validation_results: Mapped[list["StrategyValidationResult"]] = relationship(
        back_populates="strategy",
        cascade="all, delete-orphan",
        order_by="StrategyValidationResult.created_at.desc()",
    )


class StrategyVersion(Base, TimestampMixin):
    __tablename__ = "strategy_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'superseded')",
            name="valid_version_status",
        ),
        UniqueConstraint("strategy_id", "version", name="uq_version_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Lifecycle: draft (editable replacement via new draft versions),
    # published (immutable definition currently in force), superseded
    # (historical published definition — kept forever, never mutated).
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    # Validated against the whitelisted rule format before it ever reaches
    # this column (see app.domains.strategies.rules).
    rule_set: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)

    strategy: Mapped["Strategy"] = relationship(back_populates="versions")


class StrategyValidationResult(Base, TimestampMixin):
    """Persisted outcome of a strategy schema validation run."""

    __tablename__ = "strategy_validation_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    owner_id: Mapped[str] = mapped_column(
        String(OWNER_ID_MAX_LEN), nullable=False, default=DEFAULT_OWNER_ID, index=True
    )
    schema_version: Mapped[str] = mapped_column(String(8), nullable=False)
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # List of {path, message, code} entries (empty when valid).
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    condition_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    strategy: Mapped["Strategy | None"] = relationship(
        back_populates="validation_results"
    )
