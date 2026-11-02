"""Strategy & category management service.

Phase 3 responsibilities:

* Ownership: every read/write is scoped to the requesting owner; foreign
  records raise NotFoundError (never an authorization oracle).
* Non-destructive organisation: categories are archived, never silently
  deleted while dependents exist.
* Draft/publish workflow: saving a rule set creates an immutable DRAFT
  version; publishing marks it as the strategy's published definition,
  superseding the previous one. Published definitions are never mutated.
* Duplication: deep-copy a strategy (metadata + latest rule set) as a new
  draft.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ConflictError,
    NotFoundError,
    RuleValidationFailedError,
    ValidationAppError,
)
from app.db.models import (
    DIRECTIONS as DB_DIRECTIONS,
    MODES as DB_MODES,
    STRATEGY_STATUSES,
    VERSION_STATUSES,
    Strategy,
    StrategyCategory,
    StrategyValidationResult,
    StrategyVersion,
)
from app.domains.strategies.rules import (
    RuleValidationError,
    RuleSet,
    validate_rule_set,
)
from app.domains.strategies.schemas import (
    CategoryCreate,
    CategoryUpdate,
    DuplicateStrategyRequest,
    RuleSetUpload,
    StrategyCreate,
    StrategyUpdate,
)

_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 100


# --------------------------------------------------------------------------- #
# Categories (root categories and subcategories)
# --------------------------------------------------------------------------- #


def _category_dict(
    category: StrategyCategory,
    strategy_count: int,
    children: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": str(category.id),
        "name": category.name,
        "mode": category.mode,
        "description": category.description,
        "parent_id": str(category.parent_id) if category.parent_id else None,
        "is_archived": bool(category.is_archived),
        "created_at": category.created_at,
        "updated_at": category.updated_at,
        "strategy_count": strategy_count,
        "children": children or [],
    }


async def get_category_detail(
    session: AsyncSession, owner_id: str, category_id: uuid.UUID
) -> dict[str, Any]:
    """Fetch one category (root or subcategory) with its strategy count."""

    category = await _get_category(session, owner_id, category_id)
    counts = await _strategy_counts(session, owner_id)
    return _category_dict(category, counts.get(category.id, 0))


async def list_categories(
    session: AsyncSession,
    owner_id: str,
    mode: str | None = None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """Root categories with nested subcategories (owner-scoped)."""

    stmt = select(StrategyCategory).where(StrategyCategory.owner_id == owner_id)
    if mode:
        stmt = stmt.where(StrategyCategory.mode == mode)
    if not include_archived:
        stmt = stmt.where(StrategyCategory.is_archived.is_(False))
    categories = list((await session.scalars(stmt.order_by(StrategyCategory.created_at))).all())
    counts = await _strategy_counts(session, owner_id)

    by_parent: dict[uuid.UUID | None, list[StrategyCategory]] = {}
    for c in categories:
        by_parent.setdefault(c.parent_id, []).append(c)

    def build(category: StrategyCategory) -> dict[str, Any]:
        children = [
            build(child) for child in sorted(by_parent.get(category.id, []), key=lambda x: x.created_at)
        ]
        return _category_dict(category, counts.get(category.id, 0), children)

    return [build(root) for root in by_parent.get(None, [])]


async def create_category(
    session: AsyncSession, owner_id: str, payload: CategoryCreate
) -> dict[str, Any]:
    parent: StrategyCategory | None = None
    if payload.parent_id is not None:
        parent = await _get_category(session, owner_id, payload.parent_id)
        if parent.is_archived:
            raise ValidationAppError(
                "Cannot create subcategories under an archived category.",
                code="CATEGORY_ARCHIVED",
            )
        if parent.mode != payload.mode:
            raise ValidationAppError(
                "Subcategory mode must match its parent category "
                f"('{parent.mode}' != '{payload.mode}').",
            )
        if parent.parent_id is not None:
            raise ValidationAppError(
                "Category nesting is limited to one subcategory level.",
                code="MAX_CATEGORY_DEPTH",
            )
    await _ensure_category_name_free(
        session, owner_id, payload.name, payload.mode, payload.parent_id
    )
    category = StrategyCategory(
        owner_id=owner_id,
        name=payload.name,
        mode=payload.mode,
        description=payload.description,
        parent_id=payload.parent_id,
    )
    session.add(category)
    await session.flush()
    await session.refresh(category)
    return _category_dict(category, 0)


async def update_category(
    session: AsyncSession, owner_id: str, category_id: uuid.UUID, payload: CategoryUpdate
) -> dict[str, Any]:
    category = await _get_category(session, owner_id, category_id)
    if payload.name is not None and payload.name != category.name:
        await _ensure_category_name_free(
            session, owner_id, payload.name, category.mode, category.parent_id
        )
        category.name = payload.name
    if payload.description is not None:
        category.description = payload.description
    await session.flush()
    await session.refresh(category)
    counts = await _strategy_counts(session, owner_id)
    return _category_dict(category, counts.get(category.id, 0))


async def archive_category(
    session: AsyncSession, owner_id: str, category_id: uuid.UUID
) -> dict[str, Any]:
    """Archive a category AND its subcategories (non-destructive)."""

    category = await _get_category(session, owner_id, category_id)
    category.is_archived = True
    children = await session.scalars(
        select(StrategyCategory).where(StrategyCategory.parent_id == category_id)
    )
    archived_children = 0
    for child in children:
        child.is_archived = True
        archived_children += 1
    await session.flush()
    await session.refresh(category)
    result = _category_dict(category, (await _strategy_counts(session, owner_id)).get(category.id, 0))
    if archived_children:
        result["archived_subcategories"] = archived_children
    return result


async def unarchive_category(
    session: AsyncSession, owner_id: str, category_id: uuid.UUID
) -> dict[str, Any]:
    category = await _get_category(session, owner_id, category_id)
    category.is_archived = False
    await session.flush()
    await session.refresh(category)
    return _category_dict(
        category, (await _strategy_counts(session, owner_id)).get(category.id, 0)
    )


async def delete_category(
    session: AsyncSession, owner_id: str, category_id: uuid.UUID
) -> None:
    """Hard delete — refused while dependents exist (never silent)."""

    category = await _get_category(session, owner_id, category_id)
    children = await session.scalars(
        select(StrategyCategory).where(StrategyCategory.parent_id == category_id)
    )
    if children.first() is not None:
        raise ConflictError(
            "Delete the subcategories of this category first.",
            code="CATEGORY_HAS_CHILDREN",
        )
    strategies = await session.scalars(
        select(Strategy).where(Strategy.category_id == category_id).limit(1)
    )
    if strategies.first() is not None:
        raise ConflictError(
            "This category still contains strategies. Move or delete them first.",
            code="CATEGORY_NOT_EMPTY",
        )
    await session.delete(category)
    await session.flush()


async def _get_category(
    session: AsyncSession, owner_id: str, category_id: uuid.UUID
) -> StrategyCategory:
    category = await session.get(StrategyCategory, category_id)
    if category is None or category.owner_id != owner_id:
        raise NotFoundError(f"Category '{category_id}' was not found.")
    return category


async def _ensure_category_name_free(
    session: AsyncSession,
    owner_id: str,
    name: str,
    mode: str,
    parent_id: uuid.UUID | None,
) -> None:
    stmt = select(StrategyCategory).where(
        StrategyCategory.owner_id == owner_id,
        StrategyCategory.name == name,
        StrategyCategory.mode == mode,
        StrategyCategory.parent_id.is_(None) if parent_id is None
        else StrategyCategory.parent_id == parent_id,
    )
    existing = await session.scalars(stmt)
    if existing.first() is not None:
        raise ConflictError(
            f"A category named '{name}' already exists at this level for mode '{mode}'.",
            code="CATEGORY_NAME_TAKEN",
        )


async def _strategy_counts(
    session: AsyncSession, owner_id: str
) -> dict[uuid.UUID, int]:
    rows = await session.execute(
        select(Strategy.category_id, func.count(Strategy.id))
        .where(
            Strategy.category_id.is_not(None),
            Strategy.owner_id == owner_id,
        )
        .group_by(Strategy.category_id)
    )
    return {row[0]: int(row[1]) for row in rows.all()}


# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #


def _strategy_dict(strategy: Strategy) -> dict[str, Any]:
    return {
        "id": str(strategy.id),
        "name": strategy.name,
        "mode": strategy.mode,
        "direction": strategy.direction,
        "status": strategy.status,
        "description": strategy.description,
        "category_id": str(strategy.category_id) if strategy.category_id else None,
        "current_version": strategy.current_version,
        "published_version_number": strategy.published_version_number,
        "created_at": strategy.created_at,
        "updated_at": strategy.updated_at,
    }


async def list_strategies(
    session: AsyncSession,
    owner_id: str,
    *,
    mode: str | None = None,
    category_id: uuid.UUID | None = None,
    status: str | None = None,
    include_archived: bool = False,
    page: int = 1,
    page_size: int = _PAGE_SIZE_DEFAULT,
) -> tuple[list[Strategy], dict[str, int]]:
    """Owner-scoped, paginated strategy listing.

    Archived strategies are excluded unless ``include_archived`` or an
    explicit ``status`` filter (including ``status=archived``) is given.
    """

    stmt = select(Strategy).where(Strategy.owner_id == owner_id)
    count_stmt = select(func.count(Strategy.id)).where(Strategy.owner_id == owner_id)
    if mode:
        stmt = stmt.where(Strategy.mode == mode)
        count_stmt = count_stmt.where(Strategy.mode == mode)
    if category_id:
        stmt = stmt.where(Strategy.category_id == category_id)
        count_stmt = count_stmt.where(Strategy.category_id == category_id)
    if status:
        stmt = stmt.where(Strategy.status == status)
        count_stmt = count_stmt.where(Strategy.status == status)
    elif not include_archived:
        stmt = stmt.where(Strategy.status != "archived")
        count_stmt = count_stmt.where(Strategy.status != "archived")

    total = int((await session.execute(count_stmt)).scalar_one())
    page = max(1, page)
    page_size = min(max(1, page_size), _PAGE_SIZE_MAX)
    strategies = list(
        (
            await session.scalars(
                stmt.order_by(Strategy.updated_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    meta = {
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size if total else 1,
    }
    return strategies, meta


async def create_strategy(
    session: AsyncSession, owner_id: str, payload: StrategyCreate
) -> Strategy:
    if payload.mode not in DB_MODES:
        raise ValidationAppError(
            f"Mode '{payload.mode}' is not supported. Supported: {', '.join(DB_MODES)}."
        )
    if payload.direction not in DB_DIRECTIONS:
        raise ValidationAppError(
            f"Direction '{payload.direction}' is not supported. "
            f"Supported: {', '.join(DB_DIRECTIONS)}."
        )
    if payload.category_id is not None:
        category = await _get_category(session, owner_id, payload.category_id)
        if category.mode != payload.mode:
            raise ValidationAppError(
                f"Strategy mode '{payload.mode}' does not match category mode "
                f"'{category.mode}'. Forex and Binary Options strategies are "
                "kept strictly separate.",
                code="MODE_MISMATCH",
            )
        if category.is_archived:
            raise ValidationAppError(
                "Cannot create strategies inside an archived category.",
                code="CATEGORY_ARCHIVED",
            )
    duplicate = await session.scalars(
        select(Strategy).where(
            Strategy.owner_id == owner_id,
            Strategy.name == payload.name,
            Strategy.mode == payload.mode,
            Strategy.category_id == payload.category_id,
        )
    )
    if duplicate.first() is not None:
        raise ConflictError(
            f"A strategy named '{payload.name}' already exists in this category.",
            code="STRATEGY_NAME_TAKEN",
        )
    strategy = Strategy(
        owner_id=owner_id,
        name=payload.name,
        mode=payload.mode,
        direction=payload.direction,
        description=payload.description,
        category_id=payload.category_id,
    )
    session.add(strategy)
    await session.flush()
    await session.refresh(strategy)
    return strategy


async def get_strategy(
    session: AsyncSession, owner_id: str, strategy_id: uuid.UUID
) -> Strategy:
    strategy = await session.get(Strategy, strategy_id)
    if strategy is None or strategy.owner_id != owner_id:
        raise NotFoundError(f"Strategy '{strategy_id}' was not found.")
    return strategy


async def get_strategy_detail(
    session: AsyncSession, owner_id: str, strategy_id: uuid.UUID
) -> dict[str, Any]:
    strategy = await get_strategy(session, owner_id, strategy_id)
    rule_set: dict[str, Any] | None = None
    summary: list[str] = []
    if strategy.current_version > 0:
        version = await session.scalars(
            select(StrategyVersion)
            .where(
                StrategyVersion.strategy_id == strategy.id,
                StrategyVersion.version == strategy.current_version,
            )
            .limit(1)
        )
        current = version.first()
        if current is not None:
            rule_set = current.rule_set
            try:
                summary = validate_rule_set(rule_set).summary_lines()
            except RuleValidationError:  # pragma: no cover - stored data is pre-validated
                summary = []
    return {
        "strategy": strategy,
        "rule_set": rule_set,
        "summary": summary,
    }


async def update_strategy(
    session: AsyncSession,
    owner_id: str,
    strategy_id: uuid.UUID,
    payload: StrategyUpdate,
) -> Strategy:
    strategy = await get_strategy(session, owner_id, strategy_id)
    if payload.category_id is not None and payload.category_id != strategy.category_id:
        category = await _get_category(session, owner_id, payload.category_id)
        if category.mode != strategy.mode:
            raise ValidationAppError(
                "Cannot move a strategy into a category of another mode.",
                code="MODE_MISMATCH",
            )
        if category.is_archived:
            raise ValidationAppError(
                "Cannot move a strategy into an archived category.",
                code="CATEGORY_ARCHIVED",
            )
        strategy.category_id = payload.category_id
    elif payload.category_id is None and "category_id" in payload.model_fields_set:
        strategy.category_id = None
    if payload.name is not None and payload.name != strategy.name:
        duplicate = await session.scalars(
            select(Strategy).where(
                Strategy.owner_id == owner_id,
                Strategy.name == payload.name,
                Strategy.mode == strategy.mode,
                Strategy.category_id == strategy.category_id,
                Strategy.id != strategy.id,
            )
        )
        if duplicate.first() is not None:
            raise ConflictError(
                f"A strategy named '{payload.name}' already exists in this category.",
                code="STRATEGY_NAME_TAKEN",
            )
        strategy.name = payload.name
    if payload.direction is not None:
        strategy.direction = payload.direction
    if payload.status is not None:
        strategy.status = payload.status
    if payload.description is not None:
        strategy.description = payload.description
    await session.flush()
    await session.refresh(strategy)
    return strategy


async def archive_strategy(
    session: AsyncSession, owner_id: str, strategy_id: uuid.UUID
) -> Strategy:
    strategy = await get_strategy(session, owner_id, strategy_id)
    strategy.status = "archived"
    await session.flush()
    await session.refresh(strategy)
    return strategy


async def unarchive_strategy(
    session: AsyncSession, owner_id: str, strategy_id: uuid.UUID
) -> Strategy:
    strategy = await get_strategy(session, owner_id, strategy_id)
    if strategy.published_version_number is not None:
        strategy.status = "active"
    else:
        strategy.status = "draft"
    await session.flush()
    await session.refresh(strategy)
    return strategy


async def delete_strategy(
    session: AsyncSession, owner_id: str, strategy_id: uuid.UUID
) -> None:
    strategy = await get_strategy(session, owner_id, strategy_id)
    await session.delete(strategy)  # versions cascade via FK
    await session.flush()


# --------------------------------------------------------------------------- #
# Duplication
# --------------------------------------------------------------------------- #


async def duplicate_strategy(
    session: AsyncSession,
    owner_id: str,
    strategy_id: uuid.UUID,
    payload: DuplicateStrategyRequest,
) -> tuple[Strategy, StrategyVersion | None]:
    source = await get_strategy(session, owner_id, strategy_id)
    name = (payload.name or f"Copy of {source.name}").strip()
    if len(name) > 160:
        raise ValidationAppError(
            "Duplicate name must not exceed 160 characters.",
            code="NAME_TOO_LONG",
        )
    duplicate_name = await session.scalars(
        select(Strategy).where(
            Strategy.owner_id == owner_id,
            Strategy.name == name,
            Strategy.mode == source.mode,
            Strategy.category_id == source.category_id,
        )
    )
    if duplicate_name.first() is not None:
        raise ConflictError(
            f"A strategy named '{name}' already exists in this category.",
            code="STRATEGY_NAME_TAKEN",
        )

    duplicate = Strategy(
        owner_id=owner_id,
        name=name,
        mode=source.mode,
        direction=source.direction,
        description=source.description,
        category_id=source.category_id,
    )
    session.add(duplicate)
    await session.flush()
    await session.refresh(duplicate)

    copied_version: StrategyVersion | None = None
    if payload.include_rule_set and source.current_version > 0:
        source_version = await session.scalars(
            select(StrategyVersion)
            .where(
                StrategyVersion.strategy_id == source.id,
                StrategyVersion.version == source.current_version,
            )
            .limit(1)
        )
        latest = source_version.first()
        if latest is not None:
            copied_version = StrategyVersion(
                strategy_id=duplicate.id,
                version=1,
                status="draft",
                rule_set=dict(latest.rule_set),
                changelog=(
                    f"Duplicated from '{source.name}' v{latest.version}."
                ),
            )
            session.add(copied_version)
            duplicate.current_version = 1
            await session.flush()
            await session.refresh(copied_version)
            await session.refresh(duplicate)
    return duplicate, copied_version


# --------------------------------------------------------------------------- #
# Versioning (draft -> publish -> supersede; definitions are immutable)
# --------------------------------------------------------------------------- #


def _check_rule_set_matches_strategy(
    strategy: Strategy, validated: RuleSet
) -> None:
    if validated.mode != strategy.mode:
        raise ValidationAppError(
            f"Rule set mode '{validated.mode}' does not match strategy mode "
            f"'{strategy.mode}'.",
            code="MODE_MISMATCH",
        )
    # A "both" strategy accepts directional rule sets; a directional
    # strategy refuses rule sets that claim the other direction (or "both").
    if (
        strategy.direction != "both"
        and validated.direction != strategy.direction
    ):
        raise ValidationAppError(
            f"Rule set direction '{validated.direction}' does not match the "
            f"strategy direction '{strategy.direction}'. Update the strategy "
            "or the rule set so they agree.",
            code="DIRECTION_MISMATCH",
        )


async def save_rule_set_version(
    session: AsyncSession,
    owner_id: str,
    strategy_id: uuid.UUID,
    payload: RuleSetUpload,
) -> StrategyVersion:
    """Save a new DRAFT version (previous versions are never touched)."""

    strategy = await get_strategy(session, owner_id, strategy_id)
    try:
        validated = validate_rule_set(payload.rule_set)
    except RuleValidationError as exc:
        raise RuleValidationFailedError(
            "Rule set validation failed. See 'details' for the precise paths.",
            details=exc.errors,
        ) from exc

    _check_rule_set_matches_strategy(strategy, validated)

    next_version = strategy.current_version + 1
    version = StrategyVersion(
        strategy_id=strategy.id,
        version=next_version,
        status="draft",
        rule_set=validated.to_dict(),
        changelog=payload.changelog,
    )
    session.add(version)
    strategy.current_version = next_version
    try:
        await session.flush()
        await session.refresh(version)
        await session.refresh(strategy)
    except IntegrityError as exc:  # pragma: no cover - defensive
        raise ConflictError(
            f"Version {next_version} already exists for this strategy.",
            code="VERSION_CONFLICT",
        ) from exc
    return version


async def publish_version(
    session: AsyncSession, owner_id: str, strategy_id: uuid.UUID, version_number: int
) -> tuple[StrategyVersion, Strategy, list[str]]:
    """Publish a draft version as the strategy's definition.

    * The published DEFINITION is immutable (stored once, never edited).
    * The previously published version (if any) becomes ``superseded`` —
      it stays available for historical analyses.
    * Publishing requires the latest validation to pass; the definition is
      revalidated defensively before the status transition.
    """

    strategy = await get_strategy(session, owner_id, strategy_id)
    if strategy.status == "archived":
        raise ConflictError(
            "Archived strategies cannot be published. Unarchive first.",
            code="STRATEGY_ARCHIVED",
        )
    version = await get_version(session, owner_id, strategy_id, version_number)
    if version.status == "published":
        raise ConflictError(
            f"Version {version_number} is already the published version.",
            code="ALREADY_PUBLISHED",
        )
    if version.status == "superseded":
        raise ConflictError(
            f"Version {version_number} is a superseded historical definition "
            "and cannot be re-published. Save a new draft version instead.",
            code="VERSION_SUPERSEDED",
        )

    # Defensive revalidation (stored definitions must stay valid).
    try:
        validated = validate_rule_set(version.rule_set)
    except RuleValidationError as exc:  # pragma: no cover - defensive
        raise RuleValidationFailedError(
            "Stored rule set failed revalidation. See 'details'.",
            details=exc.errors,
        ) from exc
    _check_rule_set_matches_strategy(strategy, validated)

    # Supersede the current published version, if any.
    if strategy.published_version_number is not None:
        previous = await session.scalars(
            select(StrategyVersion)
            .where(
                StrategyVersion.strategy_id == strategy.id,
                StrategyVersion.version == strategy.published_version_number,
            )
            .limit(1)
        )
        prev_row = previous.first()
        if prev_row is not None and prev_row.status == "published":
            prev_row.status = "superseded"

    version.status = "published"
    strategy.published_version_number = version_number
    strategy.status = "active"
    await session.flush()
    await session.refresh(version)
    await session.refresh(strategy)
    return version, strategy, validated.summary_lines()


async def list_versions(
    session: AsyncSession, owner_id: str, strategy_id: uuid.UUID
) -> list[StrategyVersion]:
    await get_strategy(session, owner_id, strategy_id)
    stmt = (
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy_id)
        .order_by(StrategyVersion.version.desc())
    )
    return list((await session.scalars(stmt)).all())


async def get_version(
    session: AsyncSession, owner_id: str, strategy_id: uuid.UUID, version_number: int
) -> StrategyVersion:
    await get_strategy(session, owner_id, strategy_id)
    stmt = (
        select(StrategyVersion)
        .where(
            StrategyVersion.strategy_id == strategy_id,
            StrategyVersion.version == version_number,
        )
        .limit(1)
    )
    version = (await session.scalars(stmt)).first()
    if version is None:
        raise NotFoundError(
            f"Version {version_number} of strategy '{strategy_id}' was not found.",
            code="VERSION_NOT_FOUND",
        )
    return version


# --------------------------------------------------------------------------- #
# Validation results (audit trail)
# --------------------------------------------------------------------------- #


async def validate_and_record(
    session: AsyncSession,
    owner_id: str,
    strategy_id: uuid.UUID,
    rule_set_payload: dict[str, Any],
) -> dict[str, Any]:
    """Validate a rule set against the whitelisted schema, persist the
    result for the audit trail and return the response payload."""

    strategy = await get_strategy(session, owner_id, strategy_id)
    normalized: RuleSet | None = None
    errors: list[dict[str, str]] = []
    schema_version = "unknown"
    try:
        validated = validate_rule_set(rule_set_payload)
        normalized = validated
        schema_version = validated.schema_version
    except RuleValidationError as exc:
        errors = exc.errors
        for entry in errors:
            if entry["path"] == ".schema_version":
                raw = rule_set_payload.get("schema_version")
                schema_version = str(raw) if raw is not None else "unknown"

    valid = not errors
    condition_count = 0
    if normalized is not None:
        if (
            strategy.mode != normalized.mode
            or (
                strategy.direction != "both"
                and normalized.direction != strategy.direction
            )
        ):
            errors = list(errors)
            errors.append(
                {
                    "path": "$",
                    "message": (
                        f"Rule set (mode={normalized.mode}, "
                        f"direction={normalized.direction}) does not match "
                        f"the strategy (mode={strategy.mode}, "
                        f"direction={strategy.direction})."
                    ),
                    "code": "strategy_mismatch",
                }
            )
            valid = False

        def count(g: Any) -> int:
            n = len(g.conditions)
            for nested in g.groups:
                n += count(nested)
            return n

        condition_count = count(normalized.conditions)
        if normalized.confirmation is not None:
            condition_count += count(normalized.confirmation)
        if normalized.invalidation is not None:
            condition_count += count(normalized.invalidation)

    result = StrategyValidationResult(
        strategy_id=strategy.id,
        owner_id=owner_id,
        schema_version=schema_version[:8],
        valid=valid,
        errors=errors,
        condition_count=condition_count,
    )
    session.add(result)
    await session.flush()
    await session.refresh(result)

    return {
        "valid": valid,
        "errors": errors,
        "normalized": normalized.to_dict() if normalized is not None else None,
        "summary": normalized.summary_lines() if normalized is not None else [],
        "validation_id": result.id,
    }


_ = (STRATEGY_STATUSES, VERSION_STATUSES)  # referenced by DB CHECK constraints
