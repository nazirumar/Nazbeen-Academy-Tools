"""API v1 router aggregation + platform metadata endpoint."""

from __future__ import annotations

from fastapi import APIRouter

import app
from app.api.health import router as health_router
from app.core.responses import ok
from app.domains.ai.router import router as ai_router
from app.domains.analysis.router import router as analysis_router
from app.domains.backtest.router import router as backtest_router
from app.domains.market_data.router import market_router, mt5_router
from app.domains.market_data.router import router as market_data_router
from app.domains.research.router import router as research_router
from app.domains.market_data.timeframes import (
    MODE_TIMEFRAME_PROFILES,
    SUPPORTED_MODES,
)
from app.domains.strategies.router import router as strategies_router
from app.domains.strategies.rule_registry import registry_summary
from app.domains.strategies.rules import (
    LIMITS,
    MARKET_REGIMES,
    RULE_SET_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    DIRECTIONS,
    GROUP_LOGIC,
    PRICE_FIELDS,
    SUPPORTED_CONDITION_TYPES,
    SUPPORTED_OPERATORS,
)

api_v1_router = APIRouter()
api_v1_router.include_router(market_data_router)
api_v1_router.include_router(mt5_router)
api_v1_router.include_router(market_router)
api_v1_router.include_router(strategies_router)
api_v1_router.include_router(analysis_router)
api_v1_router.include_router(backtest_router)
api_v1_router.include_router(research_router)
api_v1_router.include_router(ai_router)


@api_v1_router.get("/meta")
async def platform_meta() -> dict:
    """Platform metadata: modes, timeframes and rule-format whitelists.

    The frontend consumes this instead of hard-coding constants, so UI and
    engine can never drift apart.
    """

    return ok(
        {
            "app": app.__app_name__,
            "version": app.__version__,
            "modes": [
                {"name": mode, "default_timeframes": MODE_TIMEFRAME_PROFILES[mode]}
                for mode in SUPPORTED_MODES
            ],
            "market_data": {
                "provider": "mt5",
                "order_execution": False,
                "note": "Live market data comes from a MetaTrader 5 terminal; "
                "the platform never fabricates prices and never places orders.",
            },
            "rule_format": {
                "schema_version": RULE_SET_SCHEMA_VERSION,
                "schema_versions": list(SUPPORTED_SCHEMA_VERSIONS),
                "condition_types": list(SUPPORTED_CONDITION_TYPES),
                "operators": list(SUPPORTED_OPERATORS),
                "price_fields": list(PRICE_FIELDS),
                "group_logic": list(GROUP_LOGIC),
                "directions": list(DIRECTIONS),
                "market_regimes": list(MARKET_REGIMES),
                "limits": LIMITS,
                "code_execution": False,
                "note": "Rule sets are pure data validated against these "
                "whitelists; arbitrary user code is never executed. Legacy "
                "1.0 rule sets (price_level only) remain valid and "
                "evaluatable.",
            },
            "rule_types": {
                "endpoint": "/api/v1/rule-types",
                "available": [
                    rt["type"]
                    for rt in registry_summary()
                    if rt["available"]
                ],
                "planned": [
                    rt["type"]
                    for rt in registry_summary()
                    if not rt["available"]
                ],
                "note": "Planned types are documented but NOT operational; "
                "validation rejects them until their semantics and tests "
                "are complete.",
            },
        }
    )


__all__ = ["api_v1_router", "health_router"]
