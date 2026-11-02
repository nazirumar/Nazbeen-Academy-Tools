"""Analysis HTTP endpoints.

* ``POST /analysis/evaluate``  (Phase 1) — explainable evaluation of a rule
  set against caller-supplied candles; no persistence.
* ``POST /analysis/run``       (Phase 4) — full engine run: fetches REAL
  candles from the market-data provider for the strategy's required
  timeframes, classifies the market regime, evaluates every condition and
  persists a versioned report.
* ``GET  /analysis/reports`` / ``GET /analysis/reports/{id}`` — history and
  retrieval (owner-scoped).
* ``GET  /analysis/features``  — documented feature/regime catalogs.
* ``GET  /analysis/readiness`` — engine readiness (DB, registry, provider).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import (
    RuleValidationFailedError,
    ValidationAppError,
)
from app.core.ownership import current_owner
from app.core.responses import created, ok
from app.db.session import check_database, get_db_session
from app.domains.analysis import reports as report_service
from app.domains.analysis.engine import run_analysis
from app.domains.analysis.evaluator import evaluate_rule_set
from app.domains.analysis.features import feature_catalog
from app.domains.analysis.regime import (
    REGIME_BUCKETS,
    regime_definitions,
    volatility_definitions,
)
from app.domains.analysis.schemas import EvaluateRequest, RunAnalysisRequest
from app.domains.market_data.base import get_provider
from app.domains.strategies.rules import (
    RuleValidationError,
    RuleSet,
    validate_rule_set,
)
from app.domains.strategies import service as strategy_service

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post("/evaluate")
async def evaluate(
    payload: EvaluateRequest,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Evaluate a strategy rule set and return the explainable result tree.

    - Rule sets are loaded from an immutable stored version
      (``strategy_id`` [+ ``version``]) or supplied inline.
    - Candles come from the request body (manual paste / CSV import in
      Phase 1). If a referenced timeframe has no candles, the evaluation
      returns ``setup_status: "insufficient_data"`` with an explicit warning
      listing the missing timeframes — the platform never invents data.
    - The response always includes the conditions met/missing tree, evidence
      per condition and data-quality warnings.
    """

    # ---- resolve the rule set ------------------------------------------------
    if payload.strategy_id is not None:
        strategy_id: uuid.UUID = payload.strategy_id
        if payload.version is None:
            detail = await strategy_service.get_strategy_detail(
                session, owner, strategy_id
            )
            strategy = detail["strategy"]
            if detail["rule_set"] is None:
                raise ValidationAppError(
                    f"Strategy '{strategy.name}' has no saved rule set yet. "
                    "Save a rule set version first, or evaluate an inline "
                    "rule set.",
                    code="NO_RULE_SET_SAVED",
                )
            raw_rule_set = detail["rule_set"]
            version_number = strategy.current_version
        else:
            version_row = await strategy_service.get_version(
                session, owner, strategy_id, payload.version
            )
            raw_rule_set = version_row.rule_set
            version_number = version_row.version
        source: dict = {"strategy_id": str(strategy_id), "version": version_number}
    else:
        raw_rule_set = payload.rule_set or {}
        source = {"inline": True}

    try:
        rule_set: RuleSet = validate_rule_set(raw_rule_set)
    except RuleValidationError as exc:
        raise RuleValidationFailedError(
            "The rule set failed validation. See 'details' for precise paths.",
            details=exc.errors,
        ) from exc

    # ---- market data provenance (never fabricate) ------------------------------
    provider = get_provider(settings)
    provider_status = provider.status()

    candles_by_tf: dict[str, list[dict]] = {
        tf: [row.model_dump() for row in rows]
        for tf, rows in payload.candles.items()
    }

    result = evaluate_rule_set(rule_set, candles_by_tf)

    return ok(
        {
            "source": source,
            "data_provenance": "user_supplied_inline",
            "market_data_provider": provider_status.to_dict(),
            "rule_set": rule_set.to_dict(),
            "evaluation": result.to_dict(),
            "note": (
                "Inline evaluation runs on the candles you supplied — it is "
                "not a live market analysis. Use POST /analysis/run for a "
                "provider-backed engine run."
            ),
        }
    )


# --------------------------------------------------------------------------- #
# Phase 4: analysis engine (provider-backed, persisted, explainable)
# --------------------------------------------------------------------------- #


@router.post("/run")
async def run_engine_analysis(
    payload: RunAnalysisRequest,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Run the analysis engine against a stored strategy version.

    * Candles are fetched from the configured market-data provider for
      EVERY timeframe the strategy requires — never more, never fewer.
      A disconnected provider yields 503 and NO report (the platform
      does not fabricate market data or serve fake live analysis).
    * The report records the immutable strategy version, per-timeframe
      data provenance/staleness, market regime evidence, condition-by-
      condition four-state results and limitations. No probability or
      confidence figures are produced.
    """

    report = await run_analysis(
        session,
        owner,
        settings,
        strategy_id=payload.strategy_id,
        version=payload.version,
        symbol=payload.symbol,
        include_forming=payload.include_forming,
        candle_limit=payload.candle_limit,
    )
    await report_service.save_report(session, owner, report)
    return created(report)


@router.get("/reports")
async def list_analysis_reports(
    symbol: str | None = Query(default=None, max_length=40),
    strategy_id: uuid.UUID | None = Query(default=None),
    mode: str | None = Query(default=None, pattern="^(forex|binary_options)$"),
    setup_status: str | None = Query(default=None, max_length=24),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Paginated analysis history for the requesting owner (newest first)."""

    items, page_meta = await report_service.list_reports(
        session,
        owner,
        symbol=symbol,
        strategy_id=strategy_id,
        mode=mode,
        setup_status=setup_status,
        page=page,
        page_size=page_size,
    )
    return ok({"items": items, "page": page_meta})


@router.get("/reports/{report_id}")
async def get_analysis_report(
    report_id: uuid.UUID,
    owner: str = Depends(current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retrieve one persisted report (full payload, owner-scoped)."""

    row = await report_service.get_report(session, owner, report_id)
    return ok(row.report)


@router.get("/features")
async def get_analysis_features() -> dict:
    """Documented catalogs: market features, regimes, volatility states."""

    return ok(
        {
            "features": feature_catalog(),
            "regimes": regime_definitions(),
            "volatility_states": volatility_definitions(),
            "strategy_regime_buckets": {k: sorted(v) for k, v in REGIME_BUCKETS.items()},
            "note": (
                "Every feature/regime documents its definition, required "
                "history and limitations. Features describe the supplied "
                "candles only — never future data."
            ),
        }
    )


@router.get("/readiness")
async def analysis_readiness(
    settings: Settings = Depends(get_settings),
) -> dict:
    """Analysis-engine readiness: DB, rule registry coherence, provider.

    ``ready`` means the engine can produce reports for stored strategies;
    ``live_analysis_ready`` additionally requires a connected market-data
    provider. Readiness never fabricates data availability.
    """

    db_check = await check_database(settings)
    snapshot_engine = readiness_snapshot(settings)
    try:
        provider = get_provider(settings)
        # Prefer the LIVE bounded status check so readiness reflects the
        # real terminal state, not a stale cache snapshot.
        if hasattr(provider, "get_status"):
            provider_status = dict(await provider.get_status())  # type: ignore[attr-defined]
        else:
            provider_status = provider.status().to_dict()
    except Exception as exc:  # noqa: BLE001 - readiness must not throw
        provider_status = {
            "provider": settings.market_data_provider,
            "available": False,
            "connected": False,
            "detail": f"Provider unavailable: {type(exc).__name__}",
            "checked_at": None,
        }

    registry = snapshot_engine["rule_registry"]
    ready = (
        db_check.get("status") == "up"
        and registry["coherent"]
        and registry["available"] > 0
    )
    live_ready = bool(provider_status.get("connected"))
    return ok(
        {
            "ready": ready,
            "live_analysis_ready": live_ready,
            "engine_version": snapshot_engine["engine_version"],
            "report_schema_version": snapshot_engine["report_schema_version"],
            "checks": {
                "database": db_check,
                "rule_registry": registry,
                "feature_count": snapshot_engine["feature_count"],
                "regimes": snapshot_engine["regimes"],
                "market_data_provider": provider_status,
            },
            "note": (
                "Live analysis requires a connected MetaTrader 5 terminal; "
                "when it is disconnected the engine reports 503 instead of "
                "inventing market data."
                if not live_ready
                else "Provider connected: live analysis is available."
            ),
        }
    )


def readiness_snapshot(settings: Settings) -> dict:
    """Static engine facts for the readiness endpoint (see engine.py)."""

    from app.domains.analysis.engine import readiness_snapshot as _snapshot

    return _snapshot(settings)
