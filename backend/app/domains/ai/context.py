"""Minimized, validated AI context builder (Phase 7 — layer C input).

The AI layer NEVER sees raw candles, credentials, other users' data or
anything beyond what the deterministic engines recorded. ``context.py``
transforms a persisted analysis report / backtest run into a compact,
validated structure in which:

* every condition keeps its four-state status and a stable evidence ref
  (``core:<id>``, ``confirm:<id>``, ``invalid:<id>``);
* every factual value the model may cite is an explicit evidence item;
* unavailable information is represented explicitly (``unavailable``),
  NEVER inferred or defaulted.

The context is pure data — validated by pydantic so a malformed report
fails loudly here instead of silently corrupting a prompt.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.errors import ValidationAppError

CONTEXT_SCHEMA_VERSION = "1.0"
MAX_EVIDENCE_VALUES = 24
MAX_VALUE_CHARS = 600
MAX_TEXT_CHARS = 1_200

EvidenceSource = Literal[
    "engine_condition_evaluation",
    "engine_regime",
    "data_quality",
    "strategy_definition",
    "backtest_metrics",
    "backtest_configuration",
]


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds")


def _clip(text: Any, limit: int = MAX_VALUE_CHARS) -> str:
    s = str(text)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _scalar(value: Any) -> Any:
    """Coerce one evidence value to a JSON-safe, bounded scalar."""
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str):
            return _clip(value)
        if isinstance(value, float):
            return value if value == value and abs(value) != float("inf") else None
        return value
    if isinstance(value, (list, tuple)):
        return _clip(", ".join(str(v) for v in value[:8]))
    if isinstance(value, dict):
        return _clip(json.dumps(value, default=str, separators=(",", ":")))
    return _clip(str(value))


class EvidenceItem(BaseModel):
    """One citable piece of recorded evidence."""

    ref: str = Field(min_length=1, max_length=120)
    source: EvidenceSource
    description: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    values: dict[str, Any] = Field(default_factory=dict)

    @field_validator("values")
    @classmethod
    def _bound_values(cls, v: dict[str, Any]) -> dict[str, Any]:
        return {str(k)[:60]: _scalar(val) for k, val in list(v.items())[:MAX_EVIDENCE_VALUES]}


class ConditionItem(BaseModel):
    """One condition/group with its recorded four-state status."""

    ref: str = Field(min_length=1, max_length=120)
    node_id: str = Field(min_length=1, max_length=120)
    kind: Literal["condition", "group"]
    status: Literal["met", "not_met", "inconclusive", "unsupported"]
    description: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    evidence_ref: str
    optional: bool = False


class StrategyRef(BaseModel):
    id: str | None = None
    name: str
    direction: str
    version: int
    version_status: str | None = None
    immutable_definition: bool | None = None
    mode: str | None = None


class DataTimeframeQuality(BaseModel):
    timeframe: str
    provenance: str | None = None
    provider_data_status: str | None = None
    returned_candles: int | None = None
    used_candle_count: int | None = None
    first_time: int | None = None
    last_time: int | None = None
    newest_closed_time: int | None = None
    stale: bool | None = None
    staleness_seconds: float | None = None
    note: str | None = None


class RegimeSummary(BaseModel):
    primary_timeframe: str | None = None
    regime: str | None = None
    volatility_state: str | None = None
    directional_bias: str | None = None
    agreement_status: str | None = None
    agreement_detail: str | None = None
    applicability_required: str | None = None
    applicability_classified: str | None = None
    applicability: bool | None = None
    evidence_ref: str | None = None


class AIContextBase(BaseModel):
    """Common fields for every AI context."""

    context_schema_version: Literal["1.0"] = "1.0"
    context_kind: str
    built_at: str
    limitations: list[str] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)


class AnalysisContext(AIContextBase):
    """Minimized, validated view of one persisted analysis report."""

    context_kind: Literal["analysis"] = "analysis"
    analysis_id: str
    mode: str
    symbol: str
    evaluated_at: str | None = None
    forming_candle_used: bool = False
    closed_candles_only: bool = True
    data_quality_status: str | None = None
    data_warnings: list[str] = Field(default_factory=list)
    required_timeframes: list[str] = Field(default_factory=list)
    evaluated_timeframes: list[str] = Field(default_factory=list)
    timeframe_quality: list[DataTimeframeQuality] = Field(default_factory=list)
    strategy: StrategyRef
    setup_status: str
    regime: RegimeSummary | None = None
    conditions: list[ConditionItem] = Field(default_factory=list)
    conditions_summary: dict[str, int] = Field(default_factory=dict)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class BacktestContext(AIContextBase):
    """Minimized, validated view of one persisted backtest run."""

    context_kind: Literal["backtest"] = "backtest"
    run_id: str
    mode: str
    symbol: str
    timeframe: str
    status: str
    strategy: StrategyRef | None = None
    start_time: str | None = None
    end_time: str | None = None
    completed_at: str | None = None
    simulation_mode: str | None = None
    candles_evaluated: int | None = None
    records_total: int | None = None
    # VERBATIM stored metrics — None stays None ("not available"), never 0.
    metrics: dict[str, Any] | None = None
    data_quality: dict[str, Any] | None = None
    assumptions: dict[str, Any] = Field(default_factory=dict)


class ResearchContext(AIContextBase):
    """Minimized, validated view of one persisted research/optimization run.

    The context carries ONLY stored evidence (configuration summary,
    progress, best candidate, out-of-sample aggregate, warnings,
    disclaimers). The AI layer never recomputes metrics — every number
    here is copied verbatim from the run's persisted payload.
    """

    context_kind: Literal["research"] = "research"
    run_id: str
    mode: str
    symbol: str
    timeframe: str
    validation_mode: str
    status: str
    strategy: StrategyRef | None = None
    start_time: str | None = None
    end_time: str | None = None
    completed_at: str | None = None
    simulation_mode: str | None = None
    objective: dict[str, Any] | None = None
    combination_count: int | None = None
    walk_budget: int | None = None
    walks_executed: int | None = None
    progress: dict[str, Any] | None = None
    best_candidate: dict[str, Any] | None = None
    out_of_sample: dict[str, Any] | None = None
    stability_views: dict[str, Any] | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    disclaimers: list[str] = Field(default_factory=list)


AIContext = AnalysisContext | BacktestContext | ResearchContext


# --------------------------------------------------------------------------- #
# Analysis report -> context
# --------------------------------------------------------------------------- #

_TREE_PREFIX = {"core": "core", "confirmation": "confirm", "invalidation": "invalid"}


def _flatten_node(
    node: dict[str, Any],
    prefix: str,
    conditions: list[ConditionItem],
    evidence: list[EvidenceItem],
) -> None:
    """Depth-first flattening of an evaluation tree with stable refs."""
    node_id = str(node.get("id", "node"))[:120]
    ref = f"{prefix}:{node_id}"
    status = node.get("status")
    if status not in ("met", "not_met", "inconclusive", "unsupported"):
        # Legacy 1.0 payloads only have the boolean view — represent it
        # honestly instead of guessing a four-state value.
        status = "met" if node.get("satisfied") else "not_met"
    conditions.append(
        ConditionItem(
            ref=ref,
            node_id=node_id,
            kind="group" if node.get("kind") == "group" else "condition",
            status=status,  # type: ignore[arg-type]
            description=_clip(node.get("description") or node_id, MAX_TEXT_CHARS),
            evidence_ref=ref,
            optional=bool(node.get("optional", False)),
        )
    )
    evidence.append(
        EvidenceItem(
            ref=ref,
            source="engine_condition_evaluation",
            description=f"Recorded evaluation of {node.get('kind', 'condition')} '{node_id}': status {status}.",
            values={"evidence": node.get("evidence") or {}},
        )
    )
    for child in node.get("children") or []:
        _flatten_node(child, prefix, conditions, evidence)


def build_analysis_context(report: dict[str, Any]) -> AnalysisContext:
    """Build the validated AI context for one analysis report payload."""
    evaluation = report.get("evaluation") or {}
    data = report.get("data") or {}
    strategy = report.get("strategy") or {}

    conditions: list[ConditionItem] = []
    evidence: list[EvidenceItem] = []

    for section, prefix in _TREE_PREFIX.items():
        tree = evaluation.get(section)
        if isinstance(tree, dict):
            _flatten_node(tree, prefix, conditions, evidence)

    # --- regime evidence -------------------------------------------------------
    regime_block = report.get("regime") or {}
    regime: RegimeSummary | None = None
    primary = regime_block.get("primary") or {}
    if primary:
        applicability = regime_block.get("applicability") or {}
        agreement = regime_block.get("agreement") or {}
        regime = RegimeSummary(
            primary_timeframe=regime_block.get("primary_timeframe"),
            regime=primary.get("regime"),
            volatility_state=primary.get("volatility_state"),
            directional_bias=primary.get("directional_bias"),
            agreement_status=agreement.get("status"),
            agreement_detail=agreement.get("detail"),
            applicability_required=applicability.get("required"),
            applicability_classified=applicability.get("classified"),
            applicability=applicability.get("applicable"),
            evidence_ref="regime:primary",
        )
        evidence.append(
            EvidenceItem(
                ref="regime:primary",
                source="engine_regime",
                description=(
                    f"Engine regime classification on "
                    f"{regime_block.get('primary_timeframe') or 'primary timeframe'}: "
                    f"{primary.get('regime')}."
                ),
                values={
                    "regime": primary.get("regime"),
                    "volatility_state": primary.get("volatility_state"),
                    "directional_bias": primary.get("directional_bias"),
                    "criterion": primary.get("evidence", {}).get("criterion")
                    if isinstance(primary.get("evidence"), dict)
                    else None,
                    "reason": primary.get("evidence", {}).get("reason")
                    if isinstance(primary.get("evidence"), dict)
                    else None,
                    "agreement_status": agreement.get("status"),
                    "agreement_detail": agreement.get("detail"),
                    "applicable": applicability.get("applicable"),
                },
            )
        )

    # --- per-timeframe data quality ---------------------------------------------
    tf_quality: list[DataTimeframeQuality] = []
    for tf in data.get("timeframes") or []:
        tf_quality.append(
            DataTimeframeQuality(
                timeframe=str(tf.get("timeframe", "")),
                provenance=tf.get("provenance"),
                provider_data_status=tf.get("provider_data_status"),
                returned_candles=tf.get("returned_candles"),
                used_candle_count=tf.get("used_candle_count"),
                first_time=tf.get("first_time"),
                last_time=tf.get("last_time"),
                newest_closed_time=tf.get("newest_closed_time"),
                stale=tf.get("stale"),
                staleness_seconds=tf.get("staleness_seconds"),
                note=tf.get("note"),
            )
        )
        evidence.append(
            EvidenceItem(
                ref=f"data:{tf.get('timeframe', 'tf')}",
                source="data_quality",
                description=f"Data quality for timeframe {tf.get('timeframe')}.",
                values={
                    "provenance": tf.get("provenance"),
                    "provider_data_status": tf.get("provider_data_status"),
                    "used_candle_count": tf.get("used_candle_count"),
                    "newest_closed_time": tf.get("newest_closed_time"),
                    "stale": tf.get("stale"),
                    "note": tf.get("note"),
                },
            )
        )

    warnings = [
        _clip(f"[{w.get('timeframe', '*')}] {w.get('message', '')}", MAX_TEXT_CHARS)
        for w in (data.get("warnings") or []) + list(evaluation.get("warnings") or [])
    ]

    unavailable: list[str] = []
    if not evaluation.get("forming_candle_used", False):
        unavailable.append(
            "The forming (in-progress) candle was excluded: the report describes "
            "closed candles only."
        )
    if not tf_quality:
        unavailable.append("No per-timeframe data-quality records in this report.")
    if regime is None:
        unavailable.append("No market-regime classification recorded in this report.")
    unavailable.append(
        "No tick data, order-book, volume-profile or broker execution feed is "
        "part of this report."
    )

    conditions_summary = evaluation.get("conditions_summary") or {}
    try:
        analysis_id = str(report.get("analysis_id") or uuid.uuid4())
        uuid.UUID(analysis_id)
    except ValueError as exc:
        raise ValidationAppError(
            "Report payload has an invalid analysis_id.",
            code="INVALID_REPORT_PAYLOAD",
        ) from exc

    return AnalysisContext(
        built_at=_now_iso(),
        analysis_id=analysis_id,
        mode=str(report.get("mode", "")),
        symbol=str(report.get("symbol", "")),
        evaluated_at=report.get("evaluated_at"),
        forming_candle_used=bool(evaluation.get("forming_candle_used", False)),
        closed_candles_only=bool(
            evaluation.get("closed_candles_only", data.get("closed_candles_only", True))
        ),
        data_quality_status=data.get("quality_status"),
        data_warnings=warnings,
        required_timeframes=list(report.get("required_timeframes") or []),
        evaluated_timeframes=list(report.get("evaluated_timeframes") or []),
        timeframe_quality=tf_quality,
        strategy=StrategyRef(
            id=strategy.get("id"),
            name=str(strategy.get("name", "unknown")),
            direction=str(strategy.get("direction", "unknown")),
            version=int(strategy.get("version", 0)),
            version_status=strategy.get("version_status"),
            immutable_definition=strategy.get("immutable_definition"),
            mode=strategy.get("mode"),
        ),
        setup_status=str(evaluation.get("setup_status", "unknown")),
        regime=regime,
        conditions=conditions,
        conditions_summary={
            k: int(v) for k, v in conditions_summary.items() if isinstance(v, int)
        },
        evidence=evidence,
        limitations=[_clip(line, MAX_TEXT_CHARS) for line in report.get("limitations") or []],
        unavailable=unavailable,
    )


# --------------------------------------------------------------------------- #
# Backtest run summary -> context
# --------------------------------------------------------------------------- #

_BACKTEST_LIMITATIONS = (
    "Backtest results are HISTORICAL SIMULATIONS on recorded candles; they are "
    "not proof of broker execution and not a prediction of future performance.",
    "MT5 data is not a binary-options broker's execution feed, and OTC prices "
    "are never presented as verified market data.",
    "Metrics that are null/'not available' were not computable from the "
    "recorded results — they must not be read as zeros.",
    "Small samples, missing candles and differing test periods limit "
    "comparability between runs; historical win rates never guarantee future "
    "results.",
)


def build_backtest_context(summary: dict[str, Any]) -> BacktestContext:
    """Build the validated AI context for one backtest run summary."""
    config = summary.get("config") or {}
    metrics = summary.get("metrics")
    if metrics is not None and not isinstance(metrics, dict):
        metrics = None

    strategy = None
    if summary.get("strategy_name"):
        strategy = StrategyRef(
            id=summary.get("strategy_id"),
            name=str(summary["strategy_name"]),
            direction=str((config.get("rule_set") or {}).get("direction", "unknown")),
            version=int(summary.get("strategy_version", 0)),
            version_status=None,
            mode=summary.get("mode"),
        )

    assumptions: dict[str, Any] = {}
    if config.get("forex_execution") is not None:
        assumptions["forex_execution"] = config["forex_execution"]
    if config.get("binary") is not None:
        assumptions["binary"] = config["binary"]
    if config.get("simulation_mode") is not None:
        assumptions["simulation_mode"] = config["simulation_mode"]

    return BacktestContext(
        built_at=_now_iso(),
        run_id=str(summary.get("id", "")),
        mode=str(summary.get("mode", "")),
        symbol=str(summary.get("symbol", "")),
        timeframe=str(summary.get("timeframe", "")),
        status=str(summary.get("status", "")),
        strategy=strategy,
        start_time=summary.get("start_time"),
        end_time=summary.get("end_time"),
        completed_at=summary.get("completed_at"),
        simulation_mode=(
            summary.get("simulation_mode") or config.get("simulation_mode")
        ),
        candles_evaluated=summary.get("candles_evaluated"),
        records_total=summary.get("records_total"),
        metrics=metrics,
        data_quality=summary.get("data_quality"),
        assumptions=assumptions,
        limitations=list(_BACKTEST_LIMITATIONS),
        unavailable=[
            "No unrecorded trades, slippage beyond the configured assumptions, "
            "or broker-side fills are part of this run.",
        ],
    )


# --------------------------------------------------------------------------- #
# Prompt serialization (bounded)
# --------------------------------------------------------------------------- #


def context_to_prompt(context: AIContext, max_chars: int) -> str:
    """Serialize the context to compact JSON for the provider prompt.

    Deterministically drops evidence VALUE payloads (never statuses or
    refs) when the serialization exceeds ``max_chars`` and fails with a
    clear error if even the skeleton cannot fit — the platform never
    silently truncates a claim mid-way.
    """

    def _dump(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)

    data = context.model_dump(mode="json")
    text = _dump(data)
    if len(text) <= max_chars:
        return text

    # Drop evidence values first (keep refs/statuses/descriptions).
    evidence = data.get("evidence") or []
    for item in evidence:
        if item.get("values"):
            item["values"] = {"_note": "omitted for size"}
    text = _dump(data)
    if len(text) <= max_chars:
        return text

    # Then drop regime/data notes and assumptions.
    for key in ("assumptions", "data_quality"):
        if key in data:
            data[key] = {"_note": "omitted for size"}
    data.setdefault("unavailable", []).append(
        "Part of the structured context was omitted for size; only cited "
        "evidence refs are authoritative."
    )
    text = _dump(data)
    if len(text) <= max_chars:
        return text

    # Finally trim evidence/conditions lists to the head (refs preserved).
    for key in ("evidence", "conditions"):
        items = data.get(key) or []
        keep = max(1, len(items) // 2)
        data[key] = items[:keep]
        data.setdefault("unavailable", []).append(
            f"Only the first {keep} of {len(items)} {key} entries were "
            "included; the rest were omitted for size."
        )
    text = _dump(data)
    if len(text) <= max_chars:
        return text
    raise ValidationAppError(
        "The analysis context exceeds the configured AI input budget even "
        "after deterministic trimming. Reduce the strategy size or raise "
        "AI_MAX_INPUT_CHARS.",
        code="AI_CONTEXT_TOO_LARGE",
    )


def build_research_context(summary: dict[str, Any]) -> ResearchContext:
    """Build the validated AI context for one research run summary.

    Copies stored evidence verbatim (never recomputed, never invented).
    Failed runs keep their error; incomplete aggregates stay incomplete —
    the AI layer explains what was persisted, nothing more.
    """

    config = summary.get("config") or {}
    aggregate = summary.get("aggregate") or {}
    progress = summary.get("progress")
    strategy = None
    if summary.get("strategy_name"):
        strategy = StrategyRef(
            id=summary.get("strategy_id"),
            name=str(summary["strategy_name"]),
            direction=str(config.get("direction", "unknown")),
            version=int(summary.get("strategy_version", 0)),
            version_status=None,
            mode=summary.get("mode"),
        )
    limitations = [
        "This is a historical simulation over stored candles; it is not a "
        "prediction and does not guarantee future performance.",
        "The assistant explains the stored results; it never recomputes "
        "metrics or overrides the deterministic engine.",
    ]
    unavailable: list[str] = []
    if summary.get("status") != "completed":
        unavailable.append(
            "The run is not completed; aggregates may be partial or absent."
        )
    if not aggregate:
        unavailable.append("No aggregate results were persisted for this run.")
    if summary.get("error"):
        unavailable.append(f"Run error: {summary['error'].get('code')}")
    return ResearchContext(
        built_at=_now_iso(),
        run_id=str(summary.get("id", "")),
        mode=str(summary.get("mode", "")),
        symbol=str(summary.get("symbol", "")),
        timeframe=str(summary.get("timeframe", "")),
        validation_mode=str(summary.get("validation_mode", "")),
        status=str(summary.get("status", "")),
        strategy=strategy,
        start_time=summary.get("start_time"),
        end_time=summary.get("end_time"),
        completed_at=summary.get("completed_at"),
        simulation_mode=summary.get("simulation_mode"),
        objective=summary.get("objective"),
        combination_count=summary.get("combination_count"),
        walk_budget=summary.get("walk_budget"),
        walks_executed=summary.get("walks_executed"),
        progress=progress,
        best_candidate=aggregate.get("best_candidate"),
        out_of_sample=aggregate.get("out_of_sample"),
        stability_views=aggregate.get("stability_views"),
        warnings=list(aggregate.get("warnings") or []),
        disclaimers=list(aggregate.get("disclaimers") or []),
        limitations=limitations,
        unavailable=unavailable,
    )


__all__ = [
    "AnalysisContext",
    "BacktestContext",
    "CONTEXT_SCHEMA_VERSION",
    "ResearchContext",
    "build_analysis_context",
    "build_backtest_context",
    "build_research_context",
    "context_to_prompt",
]
