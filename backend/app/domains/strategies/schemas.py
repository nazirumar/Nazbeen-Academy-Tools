"""Pydantic DTOs for the strategy management API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Mode = Literal["forex", "binary_options"]
StrategyStatus = Literal["draft", "active", "archived"]
Direction = Literal["bullish", "bearish", "both"]
VersionStatus = Literal["draft", "published", "superseded"]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Categories (root categories AND subcategories — one nesting level)
# --------------------------------------------------------------------------- #


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    mode: Mode
    description: str | None = Field(default=None, max_length=2000)
    parent_id: uuid.UUID | None = None


class CategoryUpdate(BaseModel):
    # mode is immutable once created (keeps strategies/categories consistent).
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class CategoryOut(_Out):
    id: uuid.UUID
    name: str
    mode: Mode
    description: str | None
    parent_id: uuid.UUID | None
    is_archived: bool = False
    created_at: datetime
    updated_at: datetime
    strategy_count: int | None = None
    children: list["CategoryOut"] = Field(default_factory=list)
    # Populated only by the archive endpoint (how many subcategories were
    # archived together with the parent).
    archived_subcategories: int | None = None


# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #


class StrategyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    mode: Mode
    direction: Direction
    description: str | None = Field(default=None, max_length=4000)
    # category_id may reference a root category OR a subcategory
    # (StrategyCategory rows with parent_id) — one source of truth.
    category_id: uuid.UUID | None = None


class StrategyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    direction: Direction | None = None
    description: str | None = Field(default=None, max_length=4000)
    category_id: uuid.UUID | None = None
    status: StrategyStatus | None = None


class RuleSetUpload(BaseModel):
    """Payload for saving a new immutable strategy version (draft)."""

    rule_set: dict[str, Any]
    changelog: str | None = Field(default=None, max_length=2000)


class StrategyOut(_Out):
    id: uuid.UUID
    name: str
    mode: Mode
    direction: Direction
    status: StrategyStatus
    description: str | None
    category_id: uuid.UUID | None
    current_version: int
    published_version_number: int | None = None
    created_at: datetime
    updated_at: datetime


class StrategyDetailOut(StrategyOut):
    rule_set: dict[str, Any] | None = None
    summary: list[str] = Field(default_factory=list)


class StrategyVersionOut(_Out):
    id: uuid.UUID
    strategy_id: uuid.UUID
    version: int
    status: VersionStatus
    changelog: str | None
    rule_set: dict[str, Any]
    created_at: datetime


class ValidationOnlyRequest(BaseModel):
    rule_set: dict[str, Any]


class ValidationOnlyResponse(BaseModel):
    valid: bool
    errors: list[dict[str, str]] = Field(default_factory=list)
    normalized: dict[str, Any] | None = None


class StrategyValidateRequest(BaseModel):
    """Validate a rule set against a strategy (result is persisted)."""

    rule_set: dict[str, Any]


class StrategyValidateResponse(BaseModel):
    valid: bool
    errors: list[dict[str, str]] = Field(default_factory=list)
    normalized: dict[str, Any] | None = None
    summary: list[str] = Field(default_factory=list)
    validation_id: uuid.UUID | None = None


class DuplicateStrategyRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    include_rule_set: bool = True


class DuplicateStrategyResponse(BaseModel):
    strategy: StrategyOut
    version: StrategyVersionOut | None = None


class PublishVersionResponse(BaseModel):
    version: StrategyVersionOut
    strategy: StrategyOut
    summary: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Pagination
# --------------------------------------------------------------------------- #


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    pages: int


class PaginatedStrategies(BaseModel):
    items: list[StrategyOut]
    page: PageMeta


# --------------------------------------------------------------------------- #
# Rule types (registry)
# --------------------------------------------------------------------------- #


class RuleTypeOut(BaseModel):
    type: str
    title: str
    description: str
    semantics: str
    available: bool
    status: str
    supported_modes: list[str]
    default_lookback: int
    data_note: str
    required_params: list[str]
    params_schema: dict[str, Any]
