"""Strategy management HTTP API.

Endpoints (all owner-scoped via the optional ``X-User-Id`` header):

* Categories & subcategories: CRUD + archive/unarchive (non-destructive).
* Strategies: paginated CRUD, duplicate, archive/unarchive.
* Versions: save draft, list, get, publish (immutable published
  definitions; previous published versions become superseded).
* Validation: validate-only (anonymous) and per-strategy validation with a
  persisted audit result.
* Rule types: the supported/planned rule-type registry with parameter
  schemas.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.core.ownership import current_owner
from app.core.responses import created, ok
from app.db.session import get_db_session
from app.domains.strategies import service
from app.domains.strategies.rule_registry import (
    PLANNED_RULE_TYPES,
    REGISTRY,
    UnknownRuleTypeError,
    get_spec,
    registry_summary,
)
from app.domains.strategies.rules import RuleValidationError, validate_rule_set
from app.domains.strategies.schemas import (
    CategoryCreate,
    CategoryOut,
    CategoryUpdate,
    DuplicateStrategyRequest,
    DuplicateStrategyResponse,
    PaginatedStrategies,
    PublishVersionResponse,
    RuleSetUpload,
    RuleTypeOut,
    StrategyCreate,
    StrategyDetailOut,
    StrategyOut,
    StrategyUpdate,
    StrategyValidateRequest,
    StrategyValidateResponse,
    StrategyVersionOut,
    ValidationOnlyRequest,
    ValidationOnlyResponse,
)

router = APIRouter(tags=["strategies"])


# --------------------------------------------------------------------------- #
# Rule types (registry)
# --------------------------------------------------------------------------- #


@router.get("/rule-types")
async def get_rule_types() -> dict:
    """Every registered rule type with its parameter schema and status.

    ``available=false`` entries are PLANNED types: the UI may render them,
    but they cannot be saved into strategies and are never evaluated.
    """

    return ok({"rule_types": registry_summary()})


@router.get("/rule-types/{rule_type}")
async def get_rule_type_detail(rule_type: str) -> dict:
    try:
        spec = get_spec(rule_type)
    except UnknownRuleTypeError:
        raise NotFoundError(
            f"Rule type '{rule_type}' is not registered. Registered types: "
            f"{', '.join(sorted(REGISTRY))}."
        )
    return ok(RuleTypeOut.model_validate({
        "type": spec.type,
        "title": spec.title,
        "description": spec.description,
        "semantics": spec.semantics,
        "available": spec.available,
        "status": spec.status,
        "supported_modes": list(spec.supported_modes),
        "default_lookback": spec.default_lookback,
        "data_note": spec.data_note,
        "required_params": list(spec.required_params),
        "params_schema": spec.params_schema,
    }).model_dump())


# --------------------------------------------------------------------------- #
# Categories (root categories) & subcategories
# --------------------------------------------------------------------------- #


@router.get("/strategy-categories")
async def get_categories(
    mode: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    data = await service.list_categories(
        session, owner, mode=mode, include_archived=include_archived
    )
    return ok([CategoryOut.model_validate(c).model_dump(mode="json") for c in data])


@router.get("/strategy-categories/{category_id}")
async def get_category(
    category_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    data = await service.get_category_detail(session, owner, category_id)
    return ok(CategoryOut.model_validate(data).model_dump(mode="json"))


@router.post("/strategy-categories", status_code=201)
async def post_category(
    payload: CategoryCreate,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    data = await service.create_category(session, owner, payload)
    return created(CategoryOut.model_validate(data).model_dump(mode="json"))


@router.patch("/strategy-categories/{category_id}")
async def patch_category(
    category_id: uuid.UUID,
    payload: CategoryUpdate,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    data = await service.update_category(session, owner, category_id, payload)
    return ok(CategoryOut.model_validate(data).model_dump(mode="json"))


@router.post("/strategy-categories/{category_id}/archive", status_code=200)
async def archive_category(
    category_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    data = await service.archive_category(session, owner, category_id)
    return ok(CategoryOut.model_validate(data).model_dump(mode="json"))


@router.post("/strategy-categories/{category_id}/unarchive", status_code=200)
async def unarchive_category(
    category_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    data = await service.unarchive_category(session, owner, category_id)
    return ok(CategoryOut.model_validate(data).model_dump(mode="json"))


@router.delete("/strategy-categories/{category_id}", status_code=204)
async def delete_category(
    category_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    await service.delete_category(session, owner, category_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #


@router.get("/strategies")
async def get_strategies(
    mode: str | None = Query(default=None),
    category_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    if status is not None and status not in ("draft", "active", "archived"):
        raise ValidationAppError(
            "status must be one of: draft, active, archived.",
            code="INVALID_STATUS_FILTER",
        )
    strategies, meta = await service.list_strategies(
        session,
        owner,
        mode=mode,
        category_id=category_id,
        status=status,
        include_archived=include_archived,
        page=page,
        page_size=page_size,
    )
    payload = PaginatedStrategies(
        items=[StrategyOut.model_validate(s).model_dump(mode="json") for s in strategies],
        page=meta,  # type: ignore[arg-type]
    )
    return ok(payload.model_dump(mode="json"))


@router.post("/strategies", status_code=201)
async def post_strategy(
    payload: StrategyCreate,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    strategy = await service.create_strategy(session, owner, payload)
    return created(StrategyOut.model_validate(strategy).model_dump(mode="json"))


@router.get("/strategies/{strategy_id}")
async def get_strategy(
    strategy_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    detail = await service.get_strategy_detail(session, owner, strategy_id)
    out = StrategyDetailOut.model_validate(detail["strategy"]).model_dump(mode="json")
    out["rule_set"] = detail["rule_set"]
    out["summary"] = detail["summary"]
    return ok(out)


@router.patch("/strategies/{strategy_id}")
async def patch_strategy(
    strategy_id: uuid.UUID,
    payload: StrategyUpdate,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    strategy = await service.update_strategy(session, owner, strategy_id, payload)
    return ok(StrategyOut.model_validate(strategy).model_dump(mode="json"))


@router.post("/strategies/{strategy_id}/duplicate", status_code=201)
async def duplicate_strategy(
    strategy_id: uuid.UUID,
    payload: DuplicateStrategyRequest | None = None,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    duplicate, version = await service.duplicate_strategy(
        session, owner, strategy_id, payload or DuplicateStrategyRequest()
    )
    return created(
        DuplicateStrategyResponse(
            strategy=StrategyOut.model_validate(duplicate),
            version=(
                StrategyVersionOut.model_validate(version) if version else None
            ),
        ).model_dump(mode="json")
    )


@router.post("/strategies/{strategy_id}/archive", status_code=200)
async def archive_strategy(
    strategy_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    strategy = await service.archive_strategy(session, owner, strategy_id)
    return ok(StrategyOut.model_validate(strategy).model_dump(mode="json"))


@router.post("/strategies/{strategy_id}/unarchive", status_code=200)
async def unarchive_strategy(
    strategy_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    strategy = await service.unarchive_strategy(session, owner, strategy_id)
    return ok(StrategyOut.model_validate(strategy).model_dump(mode="json"))


@router.delete("/strategies/{strategy_id}", status_code=204)
async def delete_strategy(
    strategy_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    await service.delete_strategy(session, owner, strategy_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


@router.post("/rule-sets/validate")
async def validate_rule_set_only(payload: ValidationOnlyRequest) -> dict:
    """Validate a rule set WITHOUT saving it (used by the frontend editor)."""
    try:
        validated = validate_rule_set(payload.rule_set)
    except RuleValidationError as exc:
        return ok(
            ValidationOnlyResponse(valid=False, errors=list(exc.errors)).model_dump()
        )
    return ok(
        ValidationOnlyResponse(valid=True, normalized=validated.to_dict()).model_dump()
    )


@router.post("/strategies/{strategy_id}/validate")
async def validate_strategy_rule_set(
    strategy_id: uuid.UUID,
    payload: StrategyValidateRequest,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Validate a rule set against a strategy and persist the result.

    The result (valid/errors) is stored in ``strategy_validation_results``
    for the audit trail; the rule set itself is NOT saved as a version.
    """

    data = await service.validate_and_record(
        session, owner, strategy_id, payload.rule_set
    )
    return ok(StrategyValidateResponse.model_validate(data).model_dump(mode="json"))


# --------------------------------------------------------------------------- #
# Rule set versions (draft -> publish workflow)
# --------------------------------------------------------------------------- #


@router.post("/strategies/{strategy_id}/versions", status_code=201)
async def post_rule_set_version(
    strategy_id: uuid.UUID,
    payload: RuleSetUpload,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    version = await service.save_rule_set_version(session, owner, strategy_id, payload)
    return created(StrategyVersionOut.model_validate(version).model_dump(mode="json"))


@router.get("/strategies/{strategy_id}/versions")
async def get_versions(
    strategy_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    versions = await service.list_versions(session, owner, strategy_id)
    items = [
        StrategyVersionOut.model_validate(v).model_dump(mode="json") for v in versions
    ]
    return ok(items)


@router.get("/strategies/{strategy_id}/versions/{version_number}")
async def get_version(
    strategy_id: uuid.UUID,
    version_number: int,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    version = await service.get_version(session, owner, strategy_id, version_number)
    return ok(StrategyVersionOut.model_validate(version).model_dump(mode="json"))


@router.post("/strategies/{strategy_id}/versions/{version_number}/publish")
async def publish_version(
    strategy_id: uuid.UUID,
    version_number: int,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    version, strategy, summary = await service.publish_version(
        session, owner, strategy_id, version_number
    )
    return ok(
        PublishVersionResponse(
            version=StrategyVersionOut.model_validate(version),
            strategy=StrategyOut.model_validate(strategy),
            summary=summary,
        ).model_dump(mode="json")
    )
