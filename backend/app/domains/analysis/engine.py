"""Analysis engine — orchestrates the explainable analysis pipeline.

Pipeline (each stage lives in its own module; this module only wires them):

1. Strategy resolution   (strategies service — immutable versioned rule set)
2. Market data access    (market_data provider — validated, provenance-tagged;
                          the engine REFUSES to run without a connected
                          provider and never fabricates candles)
3. Closed-candle policy  (forming candles excluded by default, provisional
                          when explicitly enabled)
4. Feature calculation   (analysis.features — deterministic, no look-ahead)
5. Regime classification (analysis.regime — configurable heuristics)
6. Rule evaluation       (analysis.evaluator — four-state results)
7. Report assembly       (versioned schema 1.0, persisted by reports.py)

Mode rules:

* Forex    — evaluates ONLY the timeframes the strategy requires; reports
             higher/lower-timeframe agreement or conflict (never forced).
* Binary   — M1 by default (the strategy declares anything beyond that);
             the report always states the data source and warns that MT5
             is not the broker's execution feed and that OTC feeds are
             never simulated.
"""

from __future__ import annotations

import dataclasses
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import MarketDataUnavailableError, ValidationAppError
from app.db.models.analysis import REPORT_SCHEMA_VERSION
from app.domains.analysis import features as fx
from app.domains.analysis import regime as rg
from app.domains.analysis.evaluator import evaluate_rule_set
from app.domains.market_data.base import get_provider
from app.domains.market_data.timeframes import timeframe_seconds
from app.domains.strategies import service as strategy_service
from app.domains.strategies.rules import RuleSet, validate_rule_set

ENGINE_VERSION = "4.0.0"

SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9._#&+-]{1,40}$")

# Regime classification needs enough history for EMA(50) + ATR baselines.
_REGIME_MIN_CANDLES = 60

_LIMITATIONS_COMMON = (
    "This report describes technical conditions on historical market data. "
    "It is not a prediction, carries no probability of profit, and is not "
    "trading advice.",
    "Regime classification is an explicit, configurable heuristic about the "
    "recent past on each timeframe — never a claim about future movement.",
    "Only candle data supplied by the connected market-data provider is "
    "used; missing candles are reported, never fabricated or filled.",
)

_BINARY_DISCLAIMER = (
    "Binary options mode: market data comes from a MetaTrader 5 terminal. "
    "It is NOT your binary options broker's execution feed; prices can "
    "differ between feeds. OTC broker feeds are unavailable and are never "
    "simulated."
)


class EngineError(Exception):
    """Internal engine failure (wrapped into a 500 by the router)."""


# --------------------------------------------------------------------------- #
# Strategy + version resolution
# --------------------------------------------------------------------------- #


async def _resolve_strategy_version(
    session: AsyncSession, owner_id: str, strategy_id: uuid.UUID, version: int | None
) -> tuple[dict[str, Any], int, str, dict[str, Any]]:
    """Return (strategy, version_number, version_status, rule_set_raw).

    Resolution order: explicit ``version`` -> published version -> latest
    saved version. Published versions are immutable; the report records
    exactly which version number produced it.
    """

    strategy = await strategy_service.get_strategy(session, owner_id, strategy_id)
    strategy_dict: dict[str, Any] = {
        "id": str(strategy.id),
        "name": strategy.name,
        "mode": strategy.mode,
        "direction": strategy.direction,
        "status": strategy.status,
        "current_version": strategy.current_version,
        "published_version_number": strategy.published_version_number,
    }
    if version is not None:
        version_number = version
    elif strategy.published_version_number is not None:
        version_number = strategy.published_version_number
    elif strategy.current_version and strategy.current_version > 0:
        version_number = strategy.current_version
    else:
        raise ValidationAppError(
            f"Strategy '{strategy.name}' has no saved rule set to analyse. "
            "Save a rule set version first.",
            code="NO_RULE_SET_SAVED",
        )
    version_row = await strategy_service.get_version(
        session, owner_id, strategy_id, version_number
    )
    return strategy_dict, version_row.version, version_row.status, version_row.rule_set


# --------------------------------------------------------------------------- #
# Candle collection
# --------------------------------------------------------------------------- #


def _zones_by_timeframe(rule_set: RuleSet) -> dict[str, list[dict[str, Any]]]:
    """User-defined price zones declared by ``price_zone`` conditions."""

    zones: dict[str, list[dict[str, Any]]] = {}

    def scan(group) -> None:  # type: ignore[no-untyped-def]
        for cond in group.conditions:
            if cond.type == "price_zone":
                low = cond.params.get("zone_low")
                high = cond.params.get("zone_high")
                if isinstance(low, (int, float)) and isinstance(high, (int, float)):
                    entry = {"zone_low": float(low), "zone_high": float(high)}
                    bucket = zones.setdefault(cond.timeframe, [])
                    if entry not in bucket:
                        bucket.append(entry)
        for nested in group.groups:
            scan(nested)

    for group in (rule_set.conditions, rule_set.confirmation, rule_set.invalidation):
        if group is not None:
            scan(group)
    return zones


def _candles_needed(rule_set: RuleSet, timeframe: str, cap: int) -> int:
    """History needed on one timeframe: rules + regime baseline, capped."""

    from app.domains.analysis.evaluator import _required_count_for_timeframe

    needed = max(_required_count_for_timeframe(rule_set, timeframe), _REGIME_MIN_CANDLES)
    return max(1, min(needed, cap))


def _series_rows(series: Any) -> list[dict[str, Any]]:
    """Convert a provider CandleSeries into plain evaluator candle dicts."""

    rows: list[dict[str, Any]] = []
    for p in series.candles:
        point: dict[str, Any] = p if isinstance(p, dict) else p.model_dump()
        rows.append(
            {
                "time": int(point["time"]),
                "open": float(point["open"]),
                "high": float(point["high"]),
                "low": float(point["low"]),
                "close": float(point["close"]),
                "volume": point.get("volume"),
                "forming": bool(point.get("forming", False)),
            }
        )
    return rows


async def _fetch_timeframes(
    settings: Settings,
    symbol: str,
    timeframes: list[str],
    rule_set: RuleSet,
    candle_limit: int | None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Fetch every required timeframe (sequentially — MT5 access is
    serialized by design). Returns (rows_by_tf, tf_meta, fetch_warnings).

    Raises MarketDataUnavailableError when the provider cannot supply real
    data — the engine never falls back to cached-or-synthetic guesses.
    """

    provider = get_provider(settings)
    rows_by_tf: dict[str, list[dict[str, Any]]] = {}
    tf_meta: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []

    for tf in timeframes:
        needed = candle_limit or _candles_needed(rule_set, tf, settings.mt5_max_candles)
        series = await provider.get_candles(symbol, tf, needed)
        rows = _series_rows(series)
        rows_by_tf[tf] = rows
        tf_meta[tf] = {
            "timeframe": tf,
            "requested_candles": needed,
            "returned_candles": len(rows),
            "provider_data_status": series.data_status,
            "provenance": series.data_provenance,
            "provider_warnings": list(series.warnings or []),
            "first_time": series.first_time,
            "last_time": series.last_time,
            "newest_closed_time": series.newest_closed_time,
        }
        for w in series.warnings or []:
            warnings.append(
                {
                    "code": w.get("code", "PROVIDER_WARNING"),
                    "timeframe": tf,
                    "message": w.get("message", str(w)),
                }
            )
    return rows_by_tf, tf_meta, warnings


def _closed_policy(
    rule_set: RuleSet,
    rows_by_tf: dict[str, list[dict[str, Any]]],
    include_forming_override: bool | None,
) -> tuple[dict[str, list[dict[str, Any]]], bool, list[dict[str, Any]]]:
    """Apply the closed-candle policy. Returns (evaluation_rows, forming_used,
    warnings)."""

    if include_forming_override is None:
        on_close_only = bool(rule_set.evaluation.get("on_close_only", True))
    else:
        on_close_only = not include_forming_override

    warnings: list[dict[str, Any]] = []
    forming_used = False
    evaluation_rows: dict[str, list[dict[str, Any]]] = {}
    for tf, rows in rows_by_tf.items():
        if rows and rows[-1].get("forming") is True:
            if on_close_only:
                warnings.append(
                    {
                        "code": "FORMING_CANDLE_EXCLUDED",
                        "timeframe": tf,
                        "message": (
                            "The forming (not yet closed) candle was excluded "
                            "from analysis (closed-candles-only policy)."
                        ),
                    }
                )
                evaluation_rows[tf] = [c for c in rows if c.get("forming") is not True]
                continue
            forming_used = True
            warnings.append(
                {
                    "code": "PROVISIONAL_FORMING_CANDLE",
                    "timeframe": tf,
                    "message": (
                        "The forming candle was INCLUDED at the user's "
                        "request. Results are provisional and may change "
                        "before the candle closes."
                    ),
                }
            )
        evaluation_rows[tf] = list(rows)
    return evaluation_rows, forming_used, warnings


def _staleness_meta(
    tf: str, rows: list[dict[str, Any]], settings: Settings
) -> dict[str, Any]:
    """Honest staleness assessment for one timeframe's CLOSED candles."""

    interval = timeframe_seconds(tf)
    closed = [c for c in rows if c.get("forming") is not True]
    now = int(datetime.now(tz=timezone.utc).timestamp())
    tolerance = settings.mt5_stale_after_intervals
    if not closed:
        return {
            "stale": True,
            "staleness_seconds": None,
            "note": "No closed candle available — the timeframe is unusable.",
        }
    newest = closed[-1]
    newest_closed_age = max(0, now - (int(newest["time"]) + interval))
    stale = newest_closed_age > tolerance * interval
    return {
        "stale": stale,
        "staleness_seconds": newest_closed_age,
        "stale_after_intervals": tolerance,
        "note": (
            f"Newest closed candle is {newest_closed_age}s old "
            f"(tolerance {tolerance:g} × {tf} interval = "
            f"{tolerance * interval:.0f}s)."
        ),
    }


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #


def _overall_quality(tf_meta: dict[str, dict[str, Any]], staleness: dict[str, dict[str, Any]]) -> str:
    provider_statuses = {m["provider_data_status"] for m in tf_meta.values()}
    if any(s.get("stale") for s in staleness.values()):
        return "stale"
    if "insufficient" in provider_statuses or "empty" in provider_statuses:
        return "insufficient"
    if "partial" in provider_statuses:
        return "partial"
    return "ok"


def build_report(
    *,
    analysis_id: uuid.UUID,
    symbol: str,
    strategy_dict: dict[str, Any],
    version_number: int,
    version_status: str,
    rule_set: RuleSet,
    rule_set_dict: dict[str, Any],
    tf_meta: dict[str, dict[str, Any]],
    staleness: dict[str, dict[str, Any]],
    evaluation_rows: dict[str, list[dict[str, Any]]],
    forming_used: bool,
    data_warnings: list[dict[str, Any]],
    evaluated_at: str,
    regime_results: list[rg.RegimeResult],
    primary_timeframe: str,
    regime_check: dict[str, Any] | None,
    observations: dict[str, Any],
    evaluation: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    """Assemble the versioned analysis report payload (schema 1.0)."""

    timeframes = sorted(rule_set.timeframes, key=timeframe_seconds)
    data_quality = _overall_quality(tf_meta, staleness)
    primary_result = next(r for r in regime_results if r.timeframe == primary_timeframe)
    agreement = rg.timeframe_agreement(regime_results)

    limitations = list(_LIMITATIONS_COMMON)
    if forming_used:
        limitations.append(
            "A forming candle was included at the user's request: condition "
            "results that use it are provisional and may change when the "
            "candle closes."
        )
    else:
        limitations.append(
            "All candle-based conditions were evaluated on CLOSED candles "
            "only (forming candle excluded)."
        )
    if mode == "binary_options":
        limitations.append(_BINARY_DISCLAIMER)
    if rule_set.market_regime and rule_set.market_regime != "any":
        limitations.append(
            "A strategy regime requirement was checked against the "
            f"'{primary_timeframe}' classification; an unknown regime never "
            "satisfies a requirement."
        )

    report: dict[str, Any] = {
        "analysis_id": str(analysis_id),
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "symbol": symbol,
        "mode": mode,
        "strategy": {
            "id": strategy_dict["id"],
            "name": strategy_dict["name"],
            "mode": strategy_dict["mode"],
            "direction": rule_set.direction,
            "strategy_status": strategy_dict["status"],
            "version": version_number,
            "version_status": version_status,
            "immutable_definition": True,
            "rule_set": rule_set_dict,
        },
        "required_timeframes": list(rule_set.timeframes),
        "evaluated_timeframes": timeframes,
        "data": {
            "provider": "mt5",
            "quality_status": data_quality,
            "closed_candles_only": not forming_used,
            "forming_candle_used": forming_used,
            "timeframes": [
                {
                    **tf_meta[tf],
                    **staleness[tf],
                    "used_candle_count": len(evaluation_rows.get(tf, [])),
                    "used_first_time": (
                        evaluation_rows[tf][0]["time"] if evaluation_rows.get(tf) else None
                    ),
                    "used_last_time": (
                        evaluation_rows[tf][-1]["time"] if evaluation_rows.get(tf) else None
                    ),
                }
                for tf in timeframes
            ],
            "warnings": data_warnings,
            "source_note": (
                "Market data: MetaTrader 5 terminal via the platform's "
                "isolated MT5 integration. Per-timeframe provenance "
                "(mt5_live / postgres_cache) is reported above."
            ),
        },
        "evaluated_at": evaluated_at,
        "regime": {
            "primary_timeframe": primary_timeframe,
            "primary": primary_result.to_dict(),
            "by_timeframe": [r.to_dict() for r in regime_results],
            "agreement": agreement,
            "applicability": regime_check,
            "note": (
                "Heuristic classification of recent price action; it is "
                "not a forecast and carries no confidence percentage."
            ),
        },
        "observations": observations,
        "evaluation": evaluation,
        "limitations": limitations,
    }
    return report


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


async def run_analysis(
    session: AsyncSession,
    owner_id: str,
    settings: Settings,
    *,
    strategy_id: uuid.UUID,
    version: int | None,
    symbol: str,
    include_forming: bool | None = None,
    candle_limit: int | None = None,
) -> dict[str, Any]:
    """Run one full analysis and return the assembled report (persisted by
    the caller)."""

    symbol = symbol.strip().upper()
    if not SYMBOL_PATTERN.match(symbol):
        raise ValidationAppError(
            "symbol must be 1-40 characters from [A-Za-z0-9._#&+-].",
            code="INVALID_SYMBOL",
        )

    # ---- 1. strategy + immutable version ----------------------------------
    strategy_dict, version_number, version_status, rule_set_raw = await _resolve_strategy_version(
        session, owner_id, strategy_id, version
    )
    try:
        rule_set = validate_rule_set(rule_set_raw)
    except Exception as exc:  # RuleValidationError — defensive for stored data
        raise ValidationAppError(
            "The stored rule set failed validation and cannot be evaluated.",
            code="RULE_SET_INVALID",
        ) from exc

    if rule_set.symbols and symbol not in rule_set.symbols:
        raise ValidationAppError(
            f"Symbol '{symbol}' is outside the strategy's symbol scope "
            f"({', '.join(rule_set.symbols)}).",
            code="SYMBOL_NOT_IN_SCOPE",
        )
    mode = rule_set.mode
    if strategy_dict["mode"] != mode:
        raise ValidationAppError(
            "Strategy mode mismatch between strategy record and rule set.",
            code="MODE_MISMATCH",
        )

    # Forming-candle override -> applied to the evaluation policy.
    if include_forming is not None:
        evaluation_settings = dict(rule_set.evaluation)
        evaluation_settings["on_close_only"] = not include_forming
        rule_set = dataclasses.replace(rule_set, evaluation=evaluation_settings)

    # ---- 2. required timeframes (only what the strategy needs) -------------
    timeframes = sorted(set(rule_set.timeframes), key=timeframe_seconds)
    if mode == "binary_options" and set(timeframes) - {"M1"}:
        # Allowed only because the user explicitly declared them.
        pass

    # ---- 3. fetch real market data (refuses when provider unavailable) ----
    try:
        rows_by_tf, tf_meta, fetch_warnings = await _fetch_timeframes(
            settings, symbol, timeframes, rule_set, candle_limit
        )
    except MarketDataUnavailableError:
        # Re-raise unchanged: the API must honestly report that no analysis
        # is possible without a connected provider. Never fabricate data.
        raise

    # ---- 4. closed-candle policy -------------------------------------------
    evaluation_rows, forming_used, policy_warnings = _closed_policy(
        rule_set, rows_by_tf, include_forming
    )

    # ---- 5. staleness --------------------------------------------------------
    staleness = {
        tf: _staleness_meta(tf, rows_by_tf.get(tf, []), settings) for tf in timeframes
    }
    for tf, s in staleness.items():
        if s["stale"]:
            policy_warnings.append(
                {
                    "code": "STALE_DATA",
                    "timeframe": tf,
                    "message": (
                        f"Data for {tf} may be stale: {s['note']} The analysis "
                        "still ran on the available candles."
                    ),
                }
            )

    # ---- 6. features + observations (closed candles only) -------------------
    zones = _zones_by_timeframe(rule_set)
    observations = {
        "by_timeframe": [
            fx.summarize_timeframe(tf, evaluation_rows.get(tf, []), zones=zones.get(tf))
            for tf in timeframes
        ],
        "note": (
            "Descriptive structure/zone observations computed by the shared "
            "feature library; swing confirmation times are recorded "
            "explicitly (no look-ahead)."
        ),
    }

    # ---- 7. regime classification -------------------------------------------
    classifier = rg.DefaultRegimeClassifier()
    regime_results = [
        classifier.classify(evaluation_rows.get(tf, []), tf) for tf in timeframes
    ]
    primary_timeframe = timeframes[-1]  # highest timeframe (sorted ascending by interval)
    primary_result = next(r for r in regime_results if r.timeframe == primary_timeframe)
    # Always report applicability (required "any" -> no gate).
    regime_check: dict[str, Any] = rg.regime_applicability(
        rule_set.market_regime, primary_result
    )

    # ---- 8. evaluation ---------------------------------------------------------
    from datetime import datetime as _dt

    evaluated_at = _dt.now(tz=timezone.utc).isoformat(timespec="milliseconds")
    evaluation_result = evaluate_rule_set(rule_set, evaluation_rows, regime_check=regime_check)

    # ---- 9. assemble ------------------------------------------------------------
    analysis_id = uuid.uuid4()
    report = build_report(
        analysis_id=analysis_id,
        symbol=symbol,
        strategy_dict=strategy_dict,
        version_number=version_number,
        version_status=version_status,
        rule_set=rule_set,
        rule_set_dict=rule_set.to_dict(),
        tf_meta=tf_meta,
        staleness=staleness,
        evaluation_rows=evaluation_rows,
        forming_used=forming_used,
        data_warnings=fetch_warnings + policy_warnings,
        evaluated_at=evaluated_at,
        regime_results=regime_results,
        primary_timeframe=primary_timeframe,
        regime_check=regime_check,
        observations=observations,
        evaluation=evaluation_result.to_dict(),
        mode=mode,
    )
    return report


# Kept for API docs: the readiness check imports these lazily.
def readiness_snapshot(settings: Settings) -> dict[str, Any]:
    """Synchronous readiness information (no DB / provider I/O)."""

    from app.domains.strategies.rule_registry import (
        AVAILABLE_RULE_TYPES,
        PLANNED_RULE_TYPES,
    )
    from app.domains.analysis.evaluator import CONDITION_EVALUATORS
    from app.domains.analysis.features import FEATURE_CATALOG

    return {
        "engine_version": ENGINE_VERSION,
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "rule_registry": {
            "available": len(AVAILABLE_RULE_TYPES),
            "planned": len(PLANNED_RULE_TYPES),
            "evaluators": len(CONDITION_EVALUATORS),
            "coherent": set(AVAILABLE_RULE_TYPES) == set(CONDITION_EVALUATORS),
        },
        "feature_count": len(FEATURE_CATALOG),
        "regimes": list(rg.REGIME_NAMES),
    }
