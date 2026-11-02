"""Deterministic, explainable rule evaluation.

Semantics (documented in README, /api/v1/rule-types and /api/v1/meta):

- ``price_level`` — ``above``/``below``: the **latest candle** in the
  lookback window satisfies the comparison; ``crosses_above``/
  ``crosses_below``: the latest candle completes the cross.
- ``candle_property`` — derived property of the latest candle vs a
  threshold.
- ``candle_pattern`` — classic pattern on the latest 1-2 candles.
- ``swing_point`` — a swing of the given strength CONFIRMED inside the
  window (confirmed = ``strength`` subsequent candles exist inside the
  supplied data; nothing from the future is assumed).
- ``structure_pattern`` — the last N confirmed swings are strictly
  rising/falling (HH / HL / LH / LL).
- ``market_structure_break`` — latest close beyond the most recent
  confirmed opposite swing (generic, precisely-defined BOS).
- ``moving_average`` — SMA/EMA vs a threshold or a price field, with
  fresh-cross semantics for ``crosses_*``.
- ``price_zone`` — interaction with a user-defined [low, high] zone.
- Group logic (four-state aware):
    * ``AND`` — met only when EVERY required child is ``met``. A single
      ``not_met`` child fails the group; any ``inconclusive`` or
      ``unsupported`` child prevents a ``met`` outcome (unknown is
      never converted into success).
    * ``OR``  — met when at least one required child is ``met``. If none
      is met, unavailable children (inconclusive/unsupported) surface as
      the group status — they are never concealed.
- Condition/group status: ``met`` | ``not_met`` | ``inconclusive`` |
  ``unsupported``. ``inconclusive`` means "cannot be evaluated from the
  supplied data" (insufficient history, missing reference) — distinct
  from ``not_met`` (evaluated and the criterion failed).
- Overall status:
    * ``invalidated``            — the invalidation group is satisfied
    * ``regime_mismatch``        — the strategy requires a market regime
      the classifier did not confirm (unknown regimes never match)
    * ``conditions_met``         — core met AND confirmation (if any) met
    * ``conditions_missing``     — core or confirmation evaluated and not met
    * ``inconclusive``           — core/confirmation could not be fully
      evaluated (insufficient data, unsupported rule type)
    * ``insufficient_data``      — at least one referenced timeframe has no usable data

**Closed candles by default.** When candles carry ``forming: true`` and
the rule set does not disable ``evaluation.on_close_only`` (default
True), forming candles are EXCLUDED from evaluation with an explicit
warning. If forming-candle analysis is explicitly enabled, results are
labelled provisional.

**No look-ahead bias**: every evaluator is a pure function of the candles
supplied at evaluation time. A condition never uses information that was
unavailable when the last supplied candle closed (swing confirmation
explicitly requires ``strength`` candles AFTER the swing inside the data;
confirmation times come from the shared feature library).

The evaluator never fetches or invents market data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.domains.analysis.features import confirmed_swings as _feature_swings
from app.domains.analysis.quality import assess_candle_series
from app.domains.strategies.rules import Condition, Group, RuleSet

# Condition/group result states. ``met``/``not_met`` are definitive;
# ``inconclusive`` means the condition could not be evaluated from the
# supplied data; ``unsupported`` means no evaluator exists for the type.
STATUS_MET = "met"
STATUS_NOT_MET = "not_met"
STATUS_INCONCLUSIVE = "inconclusive"
STATUS_UNSUPPORTED = "unsupported"
CONDITION_STATUSES = (STATUS_MET, STATUS_NOT_MET, STATUS_INCONCLUSIVE, STATUS_UNSUPPORTED)


@dataclass
class NodeResult:
    """Evaluation result for a condition OR a group (explainability tree)."""

    kind: str  # "condition" | "group"
    id: str
    logic: str | None  # groups only
    status: str  # met | not_met | inconclusive | unsupported
    description: str
    evidence: dict[str, Any] = field(default_factory=dict)
    children: list["NodeResult"] = field(default_factory=list)
    optional: bool = False

    @property
    def satisfied(self) -> bool:
        """Backwards-compatible view: only ``met`` counts as satisfied."""

        return self.status == STATUS_MET

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": self.kind,
            "id": self.id,
            "status": self.status,
            "satisfied": self.satisfied,
            "description": self.description,
            "evidence": self.evidence,
        }
        if self.optional:
            data["optional"] = True
        if self.kind == "group":
            data["logic"] = self.logic
            data["children"] = [child.to_dict() for child in self.children]
        return data


@dataclass
class EvaluationResult:
    direction: str
    setup_status: str
    core: NodeResult
    confirmation: NodeResult | None
    invalidation: NodeResult | None
    warnings: list[dict[str, Any]]
    summary: list[str]
    evaluated_at: str
    core_status: str = STATUS_NOT_MET
    confirmation_status: str | None = None
    invalidation_status: str | None = None
    conditions_summary: dict[str, int] = field(default_factory=dict)
    closed_candles_only: bool = True
    forming_candle_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "setup_status": self.setup_status,
            "core": self.core.to_dict(),
            "confirmation": self.confirmation.to_dict() if self.confirmation else None,
            "invalidation": self.invalidation.to_dict() if self.invalidation else None,
            "core_status": self.core_status,
            "confirmation_status": self.confirmation_status,
            "invalidation_status": self.invalidation_status,
            "conditions_summary": self.conditions_summary,
            "closed_candles_only": self.closed_candles_only,
            "forming_candle_used": self.forming_candle_used,
            "warnings": self.warnings,
            "summary": self.summary,
            "evaluated_at": self.evaluated_at,
        }


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _series_from(candles: list[dict[str, Any]], price_field: str) -> list[float]:
    return [float(c[price_field]) for c in candles]


def _window(candles: list[dict[str, Any]], lookback: int) -> list[dict[str, Any]]:
    return candles[-lookback:] if lookback and len(candles) >= lookback else candles


def _unsatisfied(
    condition: Condition, description: str, evidence: dict[str, Any], reason: str
) -> NodeResult:
    """A condition that could NOT be evaluated -> ``inconclusive``.

    Used exclusively for data-insufficiency paths (no candles, not enough
    history, missing reference). This is deliberately distinct from a
    condition that was evaluated and failed.
    """

    evidence = dict(evidence)
    evidence["reason"] = reason
    return NodeResult(
        kind="condition",
        id=condition.id,
        logic=None,
        status=STATUS_INCONCLUSIVE,
        description=description,
        evidence=evidence,
        optional=condition.optional,
    )


def _node(condition: Condition, description: str, evidence: dict[str, Any], satisfied: bool) -> NodeResult:
    """An EVALUATED condition outcome (definitive met / not_met)."""

    return NodeResult(
        kind="condition",
        id=condition.id,
        logic=None,
        status=STATUS_MET if satisfied else STATUS_NOT_MET,
        description=description,
        evidence=evidence,
        optional=condition.optional,
    )


def _crossover_satisfied(
    operator: str,
    series_a: list[float],
    series_b: list[float],
) -> tuple[bool, list[int]]:
    """Shared fresh-cross semantics. Returns (satisfied, cross_indices)."""

    crosses: list[int] = []
    for i in range(1, len(series_a)):
        prev, cur = series_a[i - 1], series_a[i]
        if operator == "crosses_above":
            hit = prev <= series_b[i - 1] and cur > series_b[i]
        else:
            hit = prev >= series_b[i - 1] and cur < series_b[i]
        if hit:
            crosses.append(i)
    satisfied = bool(crosses) and crosses[-1] == len(series_a) - 1
    return satisfied, crosses


# --------------------------------------------------------------------------- #
# price_level
# --------------------------------------------------------------------------- #


def evaluate_price_level_condition(
    condition: Condition, candles: list[dict[str, Any]]
) -> NodeResult:
    """Evaluate a ``price_level`` condition with full evidence."""

    tf = condition.timeframe
    lookback = condition.lookback
    window = _window(candles, lookback)
    field = str(condition.params["field"])
    operator = str(condition.params["operator"])
    threshold = float(condition.params["value"])
    needs_two = operator in ("crosses_above", "crosses_below")
    series = _series_from(window, field)

    description = (
        f"{field} {operator.replace('_', ' ')} "
        f"{threshold:g} on {tf} (last {len(window)} candle(s))"
    )
    evidence: dict[str, Any] = {
        "condition_type": condition.type,
        "timeframe": tf,
        "field": field,
        "operator": operator,
        "threshold": threshold,
        "lookback": lookback,
        "candles_evaluated": len(window),
        "candles_available": len(candles),
    }

    if not window or (needs_two and len(window) < 2):
        return _unsatisfied(
            condition,
            description,
            evidence,
            reason=(
                "Cross conditions need at least 2 candles in the window."
                if needs_two
                else "No usable candles for this timeframe."
            ),
        )

    if operator in ("above", "below"):
        comparisons = [
            (v > threshold) if operator == "above" else (v < threshold) for v in series
        ]
        satisfied = comparisons[-1]
        matched = sum(comparisons)
        first_hit = next(
            (window[i]["time"] for i, hit in enumerate(comparisons) if hit), None
        )
        last_hit = next(
            (window[i]["time"] for i in range(len(comparisons) - 1, -1, -1) if comparisons[i]),
            None,
        )
        evidence.update(
            {
                "current_value": series[-1],
                "matched_count": matched,
                "first_satisfied_time": first_hit,
                "last_satisfied_time": last_hit,
                "criterion": "latest candle must satisfy the comparison",
            }
        )
    else:  # crosses_above / crosses_below
        crosses: list[dict[str, Any]] = []
        for i in range(1, len(series)):
            prev, cur = series[i - 1], series[i]
            if operator == "crosses_above":
                hit = prev <= threshold < cur
            else:
                hit = prev >= threshold > cur
            if hit:
                crosses.append({"time": window[i]["time"], "value": cur})
        # Documented semantics: the FRESH cross decides — the latest candle
        # must be the one completing the cross.
        satisfied = bool(crosses) and crosses[-1]["time"] == window[-1]["time"]
        evidence.update(
            {
                "current_value": series[-1],
                "previous_value": series[-2],
                "crosses_in_window": crosses[-10:],  # cap for payload size
                "total_crosses_in_window": len(crosses),
                "last_cross_time": crosses[-1]["time"] if crosses else None,
                "criterion": "latest candle must complete the cross",
            }
        )

    return _node(condition, description, evidence, satisfied)


# --------------------------------------------------------------------------- #
# candle_property
# --------------------------------------------------------------------------- #


def _candle_property_value(candle: dict[str, Any], prop: str) -> float:
    o, h, l, c = (float(candle[k]) for k in ("open", "high", "low", "close"))
    if prop in ("open", "high", "low", "close"):
        return {"open": o, "high": h, "low": l, "close": c}[prop]
    if prop == "body":
        return abs(c - o)
    if prop == "range":
        return h - l
    if prop == "upper_wick":
        return h - max(o, c)
    if prop == "lower_wick":
        return min(o, c) - l
    raise ValueError(f"Unknown candle property '{prop}'")  # pragma: no cover


def evaluate_candle_property(
    condition: Condition, candles: list[dict[str, Any]]
) -> NodeResult:
    tf = condition.timeframe
    prop = str(condition.params["property"])
    operator = str(condition.params["operator"])
    threshold = float(condition.params["value"])
    window = _window(candles, condition.lookback)

    description = (
        f"{prop.replace('_', ' ')} {operator} {threshold:g} on {tf} "
        "(latest candle)"
    )
    evidence: dict[str, Any] = {
        "condition_type": condition.type,
        "timeframe": tf,
        "property": prop,
        "operator": operator,
        "threshold": threshold,
        "candles_evaluated": len(window),
        "candles_available": len(candles),
    }
    if not window:
        return _unsatisfied(
            condition, description, evidence, "No usable candles for this timeframe."
        )
    value = _candle_property_value(window[-1], prop)
    satisfied = value > threshold if operator == "above" else value < threshold
    evidence.update(
        {
            "current_value": value,
            "criterion": "property of the latest candle must satisfy the comparison",
        }
    )
    return _node(condition, description, evidence, satisfied)


# --------------------------------------------------------------------------- #
# candle_pattern
# --------------------------------------------------------------------------- #


def evaluate_candle_pattern(
    condition: Condition, candles: list[dict[str, Any]]
) -> NodeResult:
    tf = condition.timeframe
    pattern = str(condition.params["pattern"])
    max_body_ratio = float(condition.params.get("max_body_ratio", 0.1))
    window = _window(candles, condition.lookback)

    description = f"{pattern.replace('_', ' ')} on {tf} (latest candle)"
    evidence: dict[str, Any] = {
        "condition_type": condition.type,
        "timeframe": tf,
        "pattern": pattern,
        "candles_evaluated": len(window),
        "candles_available": len(candles),
    }
    if not window:
        return _unsatisfied(
            condition, description, evidence, "No usable candles for this timeframe."
        )
    needs_two = pattern in ("bullish_engulfing", "bearish_engulfing", "inside_bar")
    if needs_two and len(window) < 2:
        return _unsatisfied(
            condition,
            description,
            evidence,
            reason=f"Pattern '{pattern}' needs at least 2 candles.",
        )

    cur, prev = window[-1], window[-2] if len(window) > 1 else None

    def body(c: dict[str, Any]) -> float:
        return abs(float(c["close"]) - float(c["open"]))

    def is_bull(c: dict[str, Any]) -> bool:
        return float(c["close"]) > float(c["open"])

    def is_bear(c: dict[str, Any]) -> bool:
        return float(c["close"]) < float(c["open"])

    rng = float(cur["high"]) - float(cur["low"])
    body_cur = body(cur)
    if rng <= 0:
        matched = pattern == "doji"  # a zero-range candle is a perfect doji
        evidence.update({"pattern_range": 0.0, "criterion": pattern})
        return _node(condition, description, evidence, bool(matched))

    matched = False
    detail: dict[str, Any] = {"criterion": pattern}
    if pattern == "bullish_engulfing" and prev is not None:
        matched = (
            is_bear(prev)
            and is_bull(cur)
            and float(cur["open"]) <= float(prev["close"])
            and float(cur["close"]) >= float(prev["open"])
        )
    elif pattern == "bearish_engulfing" and prev is not None:
        matched = (
            is_bull(prev)
            and is_bear(cur)
            and float(cur["open"]) >= float(prev["close"])
            and float(cur["close"]) <= float(prev["open"])
        )
    elif pattern == "hammer":
        lower_wick = min(float(cur["open"]), float(cur["close"])) - float(cur["low"])
        upper_wick = float(cur["high"]) - max(float(cur["open"]), float(cur["close"]))
        mid = (float(cur["high"]) + float(cur["low"])) / 2
        matched = (
            lower_wick >= 2 * body_cur
            and upper_wick <= body_cur
            and float(cur["close"]) >= mid
        )
        detail.update({"lower_wick": lower_wick, "upper_wick": upper_wick})
    elif pattern == "shooting_star":
        lower_wick = min(float(cur["open"]), float(cur["close"])) - float(cur["low"])
        upper_wick = float(cur["high"]) - max(float(cur["open"]), float(cur["close"]))
        mid = (float(cur["high"]) + float(cur["low"])) / 2
        matched = (
            upper_wick >= 2 * body_cur
            and lower_wick <= body_cur
            and float(cur["close"]) <= mid
        )
        detail.update({"lower_wick": lower_wick, "upper_wick": upper_wick})
    elif pattern == "doji":
        matched = body_cur <= max_body_ratio * rng
        detail.update({"body": body_cur, "range": rng, "max_body_ratio": max_body_ratio})
    elif pattern == "inside_bar" and prev is not None:
        matched = float(cur["high"]) <= float(prev["high"]) and float(cur["low"]) >= float(prev["low"])
        if prev is not None:
            detail.update(
                {
                    "previous_high": float(prev["high"]),
                    "previous_low": float(prev["low"]),
                }
            )

    evidence.update(detail)
    evidence["matched_pattern"] = matched
    return _node(condition, description, evidence, matched)


# --------------------------------------------------------------------------- #
# swing points (shared by three rule types)
# --------------------------------------------------------------------------- #


def _confirmed_swings(
    window: list[dict[str, Any]], kind: str, strength: int
) -> list[dict[str, Any]]:
    """Swings confirmed strictly inside the supplied window (no look-ahead).

    Thin delegate to the canonical feature library implementation
    (:func:`app.domains.analysis.features.confirmed_swings`) so rule
    evaluation and feature observations can never diverge. Entries carry
    ``index``/``time``/``price`` plus the explicit confirmation timing.
    """

    return _feature_swings(window, kind, strength)


def evaluate_swing_point(
    condition: Condition, candles: list[dict[str, Any]]
) -> NodeResult:
    tf = condition.timeframe
    kind = str(condition.params["kind"])
    strength = int(condition.params["strength"])
    window = _window(candles, condition.lookback)

    description = (
        f"confirmed swing {kind} (strength {strength}) within last "
        f"{len(window)} candle(s) on {tf}"
    )
    evidence: dict[str, Any] = {
        "condition_type": condition.type,
        "timeframe": tf,
        "kind": kind,
        "strength": strength,
        "lookback": condition.lookback,
        "candles_evaluated": len(window),
        "candles_available": len(candles),
        "criterion": (
            "a swing high/low confirmed by `strength` subsequent candles "
            "inside the window (no look-ahead)"
        ),
    }
    minimum = 2 * strength + 1
    if len(window) < minimum:
        return _unsatisfied(
            condition,
            description,
            evidence,
            reason=(
                f"Detecting strength-{strength} swings needs at least "
                f"{minimum} candles in the window."
            ),
        )
    swings = _confirmed_swings(window, kind, strength)
    evidence["swings_found"] = len(swings)
    evidence["swings"] = [
        {"time": s["time"], "price": s["price"]} for s in swings[-5:]
    ]
    return _node(condition, description, evidence, bool(swings))


# --------------------------------------------------------------------------- #
# structure_pattern (HH / HL / LH / LL)
# --------------------------------------------------------------------------- #


def evaluate_structure_pattern(
    condition: Condition, candles: list[dict[str, Any]]
) -> NodeResult:
    tf = condition.timeframe
    pattern = str(condition.params["pattern"])
    count = int(condition.params["count"])
    strength = int(condition.params.get("strength", 2))
    window = _window(candles, condition.lookback)

    kind = "high" if pattern in ("higher_highs", "lower_highs") else "low"
    rising = pattern in ("higher_highs", "higher_lows")
    description = (
        f"{pattern.replace('_', ' ')}: last {count} confirmed swing {kind}s "
        f"{'strictly rising' if rising else 'strictly falling'} on {tf}"
    )
    evidence: dict[str, Any] = {
        "condition_type": condition.type,
        "timeframe": tf,
        "pattern": pattern,
        "count": count,
        "strength": strength,
        "candles_evaluated": len(window),
        "candles_available": len(candles),
        "criterion": (
            f"the most recent {count} confirmed swing {kind}s must be "
            f"{'strictly rising' if rising else 'strictly falling'}"
        ),
    }
    minimum = 2 * strength + count
    if len(window) < minimum:
        return _unsatisfied(
            condition,
            description,
            evidence,
            reason=(
                f"Confirming {count} strength-{strength} swings needs at "
                f"least {minimum} candles in the window."
            ),
        )
    swings = _confirmed_swings(window, kind, strength)[-count:]
    evidence["swings_found"] = len(swings)
    evidence["swing_prices"] = [s["price"] for s in swings]
    if len(swings) < count:
        evidence["reason"] = (
            f"Only {len(swings)} confirmed swing {kind}(s) in the window; "
            f"{count} are required."
        )
        return _node(condition, description, evidence, False)
    if rising:
        satisfied = all(
            swings[i]["price"] > swings[i - 1]["price"] for i in range(1, len(swings))
        )
    else:
        satisfied = all(
            swings[i]["price"] < swings[i - 1]["price"] for i in range(1, len(swings))
        )
    return _node(condition, description, evidence, satisfied)


# --------------------------------------------------------------------------- #
# market_structure_break (generic BOS)
# --------------------------------------------------------------------------- #


def evaluate_market_structure_break(
    condition: Condition, candles: list[dict[str, Any]]
) -> NodeResult:
    tf = condition.timeframe
    side = str(condition.params["side"])
    strength = int(condition.params.get("strength", 2))
    window = _window(candles, condition.lookback)

    swing_kind = "high" if side == "bullish" else "low"
    description = (
        f"{side} structure break: close beyond the most recent confirmed "
        f"swing {swing_kind} (strength {strength}) on {tf}"
    )
    evidence: dict[str, Any] = {
        "condition_type": condition.type,
        "timeframe": tf,
        "side": side,
        "strength": strength,
        "candles_evaluated": len(window),
        "candles_available": len(candles),
        "criterion": (
            f"latest close must be {'above' if side == 'bullish' else 'below'} "
            f"the most recent confirmed swing {swing_kind} in the window"
        ),
    }
    if len(window) < 2 * strength + 2:
        return _unsatisfied(
            condition,
            description,
            evidence,
            reason=(
                f"A break evaluation needs at least {2 * strength + 2} "
                "candles (swing + the breaking candle)."
            ),
        )

    # Swings confirmed strictly BEFORE the latest candle: the last
    # `strength` candles cannot be confirmed swings, and the breaking
    # candle itself is the latest one.
    swing_zone = window[:-1]
    swings = _confirmed_swings(swing_zone, swing_kind, strength)
    evidence["reference_swings"] = [
        {"time": s["time"], "price": s["price"]} for s in swings[-3:]
    ]
    if not swings:
        return _unsatisfied(
            condition,
            description,
            evidence,
            reason=f"No confirmed swing {swing_kind} available as the break reference.",
        )
    reference = swings[-1]
    close = float(window[-1]["close"])
    satisfied = close > reference["price"] if side == "bullish" else close < reference["price"]
    evidence.update(
        {
            "reference_swing_price": reference["price"],
            "reference_swing_time": reference["time"],
            "current_close": close,
        }
    )
    return _node(condition, description, evidence, satisfied)


# --------------------------------------------------------------------------- #
# moving_average
# --------------------------------------------------------------------------- #


def _sma(series: list[float], period: int) -> list[float] | None:
    if len(series) < period:
        return None
    out = [math.nan] * len(series)
    running = sum(series[:period])
    out[period - 1] = running / period
    for i in range(period, len(series)):
        running += series[i] - series[i - period]
        out[i] = running / period
    return out


def _ema(series: list[float], period: int) -> list[float] | None:
    if len(series) < period:
        return None
    out = [math.nan] * len(series)
    alpha = 2.0 / (period + 1)
    prev = sum(series[:period]) / period  # SMA seed (documented)
    out[period - 1] = prev
    for i in range(period, len(series)):
        prev = alpha * series[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def evaluate_moving_average(
    condition: Condition, candles: list[dict[str, Any]]
) -> NodeResult:
    tf = condition.timeframe
    ma_type = str(condition.params["ma_type"])
    period = int(condition.params["period"])
    source = str(condition.params.get("source", "close"))
    compare_to = str(condition.params["compare_to"])
    operator = str(condition.params["operator"])
    window = _window(candles, condition.lookback)

    needs_two = operator in ("crosses_above", "crosses_below")
    target_desc = (
        f"{float(condition.params['value']):g}"
        if compare_to == "value" and "value" in condition.params
        else str(condition.params.get("price_field", "price field"))
    )
    description = (
        f"{ma_type.upper()}({period}) of {source} {operator.replace('_', ' ')} "
        f"{target_desc} on {tf} (last {len(window)} candle(s))"
    )
    evidence: dict[str, Any] = {
        "condition_type": condition.type,
        "timeframe": tf,
        "ma_type": ma_type,
        "period": period,
        "source": source,
        "compare_to": compare_to,
        "operator": operator,
        "lookback": condition.lookback,
        "candles_evaluated": len(window),
        "candles_available": len(candles),
    }

    if not window or (needs_two and len(window) < 2):
        return _unsatisfied(
            condition,
            description,
            evidence,
            reason="Cross conditions need at least 2 candles in the window.",
        )
    series = _series_from(window, source)
    ma_series = (_ema if ma_type == "ema" else _sma)(series, period)
    if ma_series is None or math.isnan(ma_series[-1]) or (
        needs_two and math.isnan(ma_series[-2])
    ):
        return _unsatisfied(
            condition,
            description,
            evidence,
            reason=(
                f"{ma_type.upper()}({period}) needs at least {period} "
                "candles in the window."
            ),
        )

    if compare_to == "value":
        threshold = float(condition.params["value"])
        target_series = [threshold] * len(series)
        evidence["threshold"] = threshold
        target_label = f"{threshold:g}"
    else:
        target_field = str(condition.params["price_field"])
        target_series = _series_from(window, target_field)
        evidence["price_field"] = target_field
        target_label = target_field

    description = (
        f"{ma_type.upper()}({period}) of {source} "
        f"{operator.replace('_', ' ')} {target_label} on {tf} "
        f"(last {len(window)} candle(s))"
    )

    if operator in ("above", "below"):
        last_ma = ma_series[-1]
        satisfied = last_ma > target_series[-1] if operator == "above" else last_ma < target_series[-1]
        evidence.update(
            {
                "ma_value": last_ma,
                "target_value": target_series[-1],
                "criterion": "MA on the latest candle must satisfy the comparison",
            }
        )
    else:
        # Compare the MA against the target series at aligned indices; skip
        # the MA warm-up (NaN) region — the first valid MA value sits at
        # index period-1 (the SMA/EMA seed).
        start = period - 1
        ma_valid = ma_series[start:]
        target_valid = target_series[start:]
        satisfied, crosses = _crossover_satisfied(operator, ma_valid, target_valid)
        evidence.update(
            {
                "ma_value": ma_series[-1],
                "previous_ma_value": ma_series[-2],
                "target_value": target_series[-1],
                "previous_target_value": target_series[-2],
                "crosses_in_window": [
                    {"time": window[start + i]["time"], "value": ma_valid[i]}
                    for i in crosses[-10:]
                ],
                "total_crosses_in_window": len(crosses),
                "criterion": "latest candle must complete the cross",
            }
        )
    return _node(condition, description, evidence, satisfied)


# --------------------------------------------------------------------------- #
# price_zone
# --------------------------------------------------------------------------- #


def evaluate_price_zone(
    condition: Condition, candles: list[dict[str, Any]]
) -> NodeResult:
    tf = condition.timeframe
    zone_low = float(condition.params["zone_low"])
    zone_high = float(condition.params["zone_high"])
    operator = str(condition.params["operator"])
    window = _window(candles, condition.lookback)

    description = (
        f"zone [{zone_low:g}, {zone_high:g}] {operator.replace('_', ' ')} "
        f"on {tf} (last {len(window)} candle(s))"
    )
    evidence: dict[str, Any] = {
        "condition_type": condition.type,
        "timeframe": tf,
        "zone_low": zone_low,
        "zone_high": zone_high,
        "operator": operator,
        "lookback": condition.lookback,
        "candles_evaluated": len(window),
        "candles_available": len(candles),
    }
    if not window:
        return _unsatisfied(
            condition, description, evidence, "No usable candles for this timeframe."
        )

    def touches(c: dict[str, Any]) -> bool:
        return float(c["low"]) <= zone_high and float(c["high"]) >= zone_low

    if operator == "touched":
        hits = [c for c in window if touches(c)]
        evidence.update(
            {
                "touches": len(hits),
                "first_touch_time": hits[0]["time"] if hits else None,
                "last_touch_time": hits[-1]["time"] if hits else None,
                "criterion": "any candle in the window overlaps the zone",
            }
        )
        return _node(condition, description, evidence, bool(hits))

    latest = window[-1]
    close = float(latest["close"])
    if operator == "closed_inside":
        satisfied = zone_low <= close <= zone_high
        evidence.update(
            {
                "current_close": close,
                "criterion": "latest candle's close lies inside the zone (inclusive)",
            }
        )
        return _node(condition, description, evidence, satisfied)

    # rejected: latest candle touched the zone intrabar but closed outside.
    touched = touches(latest)
    closed_inside = zone_low <= close <= zone_high
    satisfied = touched and not closed_inside
    evidence.update(
        {
            "current_close": close,
            "touched_zone": touched,
            "criterion": "latest candle touched the zone but closed outside it",
        }
    )
    return _node(condition, description, evidence, satisfied)


# --------------------------------------------------------------------------- #
# Dispatch registry: condition type -> evaluator. The registry (rule_registry)
# and this mapping must stay in sync; unit tests enforce that every
# "available" type has exactly one evaluator.
# --------------------------------------------------------------------------- #

CONDITION_EVALUATORS = {
    "price_level": evaluate_price_level_condition,
    "candle_property": evaluate_candle_property,
    "candle_pattern": evaluate_candle_pattern,
    "swing_point": evaluate_swing_point,
    "structure_pattern": evaluate_structure_pattern,
    "market_structure_break": evaluate_market_structure_break,
    "moving_average": evaluate_moving_average,
    "price_zone": evaluate_price_zone,
}

# Phase 4 advanced rule implementations (rules_advanced.py). Registered
# AFTER this module's own evaluators; rules_advanced imports this module,
# so the wiring happens here at the end of the module (no import cycle).
from app.domains.analysis import rules_advanced as _advanced  # noqa: E402

CONDITION_EVALUATORS.update(
    {
        "mss_choch": _advanced.evaluate_mss_choch,
        "bos": _advanced.evaluate_bos,
        "liquidity_sweep": _advanced.evaluate_liquidity_sweep,
        "fvg": _advanced.evaluate_fvg,
    }
)


def _minimum_candles(condition: Condition) -> int:
    """Worst-case minimum window size implied by the condition's type."""

    p = condition.params
    t = condition.type
    if t == "moving_average":
        return int(p.get("period", 2)) + (2 if str(p.get("operator", "")).startswith("crosses") else 1)
    if t in ("swing_point",):
        return 2 * int(p.get("strength", 1)) + 1
    if t in ("structure_pattern",):
        return 2 * int(p.get("strength", 2)) + int(p.get("count", 2))
    if t in ("market_structure_break", "mss_choch", "bos"):
        # Two swing contexts per kind + the breaking candle.
        return 2 * int(p.get("strength", 2)) + 3
    if t == "liquidity_sweep":
        return 2 * int(p.get("strength", 2)) + 2
    if t == "fvg":
        return 3
    if t == "candle_pattern":
        return 2
    return 1


# --------------------------------------------------------------------------- #
# Group + rule set evaluation
# --------------------------------------------------------------------------- #


def _candle_status(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Candle-basis evidence for one condition's timeframe."""

    if not candles:
        return {"last_candle_time": None, "last_candle_status": "no_data"}
    last = candles[-1]
    forming = last.get("forming") is True
    return {
        "last_candle_time": last.get("time"),
        "last_candle_status": "forming" if forming else "closed",
        "forming_candle_used": forming,
    }


def _group_status(logic: str, children: list[NodeResult]) -> tuple[str, str | None]:
    """Four-state group semantics (documented in the module docstring).

    Returns ``(status, reason)``. Unknown/unsupported children never
    become success; OR never conceals unavailable children.
    """

    required = [c for c in children if not c.optional]
    statuses = [c.status for c in required]
    optional_unmet = sum(1 for c in children if c.optional and c.status != STATUS_MET)

    if logic == "AND":
        if any(s == STATUS_UNSUPPORTED for s in statuses):
            return STATUS_UNSUPPORTED, (
                "AND group cannot be fully evaluated: a required rule type "
                "has no evaluator. Unknown rules are never treated as met."
            )
        if any(s == STATUS_NOT_MET for s in statuses):
            return STATUS_NOT_MET, (
                "At least one required rule was evaluated and is not met."
            )
        if any(s == STATUS_INCONCLUSIVE for s in statuses):
            return STATUS_INCONCLUSIVE, (
                "At least one required rule could not be evaluated from the "
                "supplied data — AND groups never assume unknown rules hold."
            )
        if not required:
            return STATUS_INCONCLUSIVE, (
                "The group contains no required rules (misconfigured?)."
            )
        return STATUS_MET, "Every required rule is met."

    # OR semantics (Phase 3 compatibility preserved: a met OPTIONAL child
    # may satisfy the group; unavailable REQUIRED children still surface).
    if any(c.status == STATUS_MET for c in children):
        return STATUS_MET, "At least one rule (required or optional) is met."
    statuses = [c.status for c in required]
    if not required:
        # Only optional children: report unavailability honestly.
        if any(c.status == STATUS_UNSUPPORTED for c in children):
            return STATUS_UNSUPPORTED, (
                "No rule is met and the group only contains optional rules, "
                "one of which cannot be evaluated (unsupported type)."
            )
        if any(c.status == STATUS_INCONCLUSIVE for c in children):
            return STATUS_INCONCLUSIVE, (
                "No rule is met and the group only contains optional rules, "
                "one of which could not be evaluated from the supplied data."
            )
        return STATUS_NOT_MET, "No rule (required or optional) is met."
    if any(s == STATUS_UNSUPPORTED for s in statuses):
        return STATUS_UNSUPPORTED, (
            "No required rule is met and at least one cannot be evaluated "
            "(unsupported type). Unavailable rules are reported, never "
            "concealed."
        )
    if any(s == STATUS_INCONCLUSIVE for s in statuses):
        return STATUS_INCONCLUSIVE, (
            "No required rule is met and at least one could not be "
            "evaluated from the supplied data."
        )
    return STATUS_NOT_MET, "Every required rule was evaluated and none is met."


def evaluate_group(
    group: Group, candles_by_timeframe: dict[str, list[dict[str, Any]]]
) -> NodeResult:
    children: list[NodeResult] = []
    for cond in group.conditions:
        candles = candles_by_timeframe.get(cond.timeframe, [])
        evaluator = CONDITION_EVALUATORS.get(cond.type)
        if evaluator is None:
            # Defensive: validation already rejects unknown/unavailable
            # types. If one ever reaches the evaluator, fail CLOSED with an
            # explicit reason instead of pretending it was checked.
            children.append(
                NodeResult(
                    kind="condition",
                    id=cond.id,
                    logic=None,
                    status=STATUS_UNSUPPORTED,
                    description=f"{cond.type} on {cond.timeframe}",
                    evidence={
                        "condition_type": cond.type,
                        "reason": (
                            "No evaluator registered for this rule type. "
                            "The platform does not pretend unsupported rules "
                            "are satisfied."
                        ),
                    },
                    optional=cond.optional,
                )
            )
            continue
        node = evaluator(cond, candles)
        node.evidence.update(_candle_status(candles))
        children.append(node)
    for nested in group.groups:
        children.append(evaluate_group(nested, candles_by_timeframe))

    status, reason = _group_status(group.logic, children)
    suffix = (
        f" ({sum(1 for c in children if c.optional and c.status != STATUS_MET)} "
        "optional rule(s) currently unmet)"
        if any(c.optional and c.status != STATUS_MET for c in children)
        else ""
    )
    return NodeResult(
        kind="group",
        id=f"group_{group.logic.lower()}_{id(group) % 100000:05d}",
        logic=group.logic,
        status=status,
        description=(
            f"ALL of {len(children)} condition(s)/group(s) must hold{suffix}"
            if group.logic == "AND"
            else f"AT LEAST ONE of {len(children)} condition(s)/group(s) must hold"
        ),
        evidence={"status_reason": reason} if reason else {},
        children=children,
    )


def collect_timeframes(group: Group | None, into: set[str]) -> None:
    if group is None:
        return
    for cond in group.conditions:
        into.add(cond.timeframe)
    for nested in group.groups:
        collect_timeframes(nested, into)


def evaluate_rule_set(
    rule_set: RuleSet,
    candles_by_timeframe: dict[str, list[dict[str, Any]]],
    *,
    regime_check: dict[str, Any] | None = None,
) -> EvaluationResult:
    """Evaluate a validated rule set against caller-supplied candles.

    ``regime_check`` (optional) is the output of
    :func:`app.domains.analysis.regime.regime_applicability`. When it is
    supplied and ``applicable`` is False, the setup is NEVER reported as
    fully satisfied — ``setup_status`` becomes ``regime_mismatch`` (an
    unknown regime can never satisfy a strategy regime requirement).
    """

    from datetime import datetime, timezone

    evaluated_at = datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds")
    warnings: list[dict[str, Any]] = []
    used_timeframes: set[str] = set()
    collect_timeframes(rule_set.conditions, used_timeframes)
    collect_timeframes(rule_set.confirmation, used_timeframes)
    collect_timeframes(rule_set.invalidation, used_timeframes)

    # Closed-candle policy (Phase 4): forming candles are excluded by
    # default for confirmed candle-based conditions; explicit opt-in is
    # labelled provisional.
    on_close_only = bool(rule_set.evaluation.get("on_close_only", True))
    forming_used = False

    # Normalise + assess every referenced timeframe; keep usable series only.
    usable: dict[str, list[dict[str, Any]]] = {}
    missing_timeframes: list[str] = []
    for tf in sorted(used_timeframes):
        raw = candles_by_timeframe.get(tf)
        if not raw:
            missing_timeframes.append(tf)
            continue
        series_warnings, rows = assess_candle_series(
            raw, tf, _required_count_for_timeframe(rule_set, tf)
        )
        warnings.extend(series_warnings)
        # Drop candles with invalid OHLC from evaluation (warned above).
        clean = [row for row in rows if _row_valid(row)]
        if clean and clean[-1].get("forming") is True:
            if on_close_only:
                warnings.append(
                    {
                        "code": "FORMING_CANDLE_EXCLUDED",
                        "timeframe": tf,
                        "message": (
                            "The forming (not yet closed) candle was excluded "
                            "from evaluation (closed-candles-only policy)."
                        ),
                    }
                )
                clean = [row for row in clean if row.get("forming") is not True]
            else:
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
        if clean:
            usable[tf] = clean
        elif rows:
            missing_timeframes.append(tf)

    if missing_timeframes:
        core = NodeResult(
            kind="group",
            id="group_core",
            logic="AND",
            status=STATUS_INCONCLUSIVE,
            description="Not evaluated: required candle data is missing.",
            evidence={
                "missing_timeframes": missing_timeframes,
                "reason": "The request did not include usable candles for "
                "every timeframe referenced by the rule set. The platform "
                "does not fabricate market data.",
            },
        )
        return EvaluationResult(
            direction=rule_set.direction,
            setup_status="insufficient_data",
            core=core,
            confirmation=None,
            invalidation=None,
            warnings=warnings,
            summary=[
                f"Missing candle data for timeframe(s): {', '.join(missing_timeframes)}.",
                "Provide candles for all referenced timeframes to obtain a "
                "full evaluation.",
            ],
            evaluated_at=evaluated_at,
            core_status=core.status,
            conditions_summary=_status_counts(None),
            closed_candles_only=on_close_only,
            forming_candle_used=forming_used,
        )

    core_result = evaluate_group(rule_set.conditions, usable)
    confirmation_result = (
        evaluate_group(rule_set.confirmation, usable)
        if rule_set.confirmation is not None
        else None
    )
    invalidation_result = (
        evaluate_group(rule_set.invalidation, usable)
        if rule_set.invalidation is not None
        else None
    )

    if invalidation_result is not None and invalidation_result.status == STATUS_MET:
        setup_status = "invalidated"
    elif regime_check is not None and not regime_check.get("applicable", True):
        setup_status = "regime_mismatch"
    elif (
        core_result.status == STATUS_MET
        and (confirmation_result is None or confirmation_result.status == STATUS_MET)
    ):
        setup_status = "conditions_met"
    elif STATUS_NOT_MET in (core_result.status, confirmation_result.status if confirmation_result else None):
        setup_status = "conditions_missing"
    else:
        # Core/confirmation inconclusive or unsupported: never pretend a
        # result. This is distinct from conditions_missing.
        setup_status = "inconclusive"

    summary = _build_summary(
        rule_set,
        setup_status,
        core_result,
        confirmation_result,
        invalidation_result,
    )
    if forming_used:
        summary.append(
            "PROVISIONAL: a forming candle was included in the evaluation; "
            "results may change before it closes."
        )
    if regime_check is not None:
        summary.append(
            f"Market regime: classified '{regime_check.get('classified')}' "
            f"(strategy requires '{regime_check.get('required')}') — "
            + (
                "applicable."
                if regime_check.get("applicable")
                else "NOT applicable."
            )
        )

    counts = _status_counts(core_result)
    if confirmation_result is not None:
        conf_counts = _status_counts(confirmation_result)
        counts = {k: counts[k] + conf_counts[k] for k in counts}

    return EvaluationResult(
        direction=rule_set.direction,
        setup_status=setup_status,
        core=core_result,
        confirmation=confirmation_result,
        invalidation=invalidation_result,
        warnings=warnings,
        summary=summary,
        evaluated_at=datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds"),
        core_status=core_result.status,
        confirmation_status=(
            confirmation_result.status if confirmation_result is not None else None
        ),
        invalidation_status=(
            invalidation_result.status if invalidation_result is not None else None
        ),
        conditions_summary=counts,
        closed_candles_only=on_close_only,
        forming_candle_used=forming_used,
    )


def _status_counts(node: NodeResult | None) -> dict[str, int]:
    """Count CONDITIONS (not groups) by status across a subtree."""

    counts = {STATUS_MET: 0, STATUS_NOT_MET: 0, STATUS_INCONCLUSIVE: 0, STATUS_UNSUPPORTED: 0}
    if node is None:
        return counts

    def walk(n: NodeResult) -> None:
        if n.kind == "condition":
            counts[n.status] = counts.get(n.status, 0) + 1
            return
        for child in n.children:
            walk(child)

    walk(node)
    return counts


def _required_count(group: Group) -> int:
    """Worst-case candles needed by any condition in the tree."""

    needed = 1
    for cond in group.conditions:
        needed = max(needed, cond.lookback, _minimum_candles(cond))
    for nested in group.groups:
        needed = max(needed, _required_count(nested))
    return needed


def _required_count_for_timeframe(rule_set: RuleSet, timeframe: str) -> int:
    required = 1

    def scan(group: Group | None) -> None:
        nonlocal required
        if group is None:
            return
        for cond in group.conditions:
            if cond.timeframe != timeframe:
                continue
            required = max(required, cond.lookback, _minimum_candles(cond))
        for nested in group.groups:
            scan(nested)

    scan(rule_set.conditions)
    scan(rule_set.confirmation)
    scan(rule_set.invalidation)
    return required


def _row_valid(row: dict[str, Any]) -> bool:
    values = [row.get(k) for k in ("open", "high", "low", "close")]
    if any(
        v is None or isinstance(v, bool) or not isinstance(v, (int, float))
        or not math.isfinite(float(v)) or float(v) <= 0
        for v in values
    ):
        return False
    o, h, l, c = (float(v) for v in values)
    return h >= l and h >= o and h >= c and l <= o and l <= c


def _build_summary(
    rule_set: RuleSet,
    status: str,
    core: NodeResult,
    confirmation: NodeResult | None,
    invalidation: NodeResult | None,
) -> list[str]:
    lines: list[str] = []
    direction_label = {
        "bullish": "BULLISH (look for long setups)",
        "bearish": "BEARISH (look for short setups)",
        "both": "BOTH DIRECTIONS (long and short setups)",
    }[rule_set.direction]
    lines.append(f"Strategy direction: {direction_label}.")
    if status == "invalidated":
        lines.append(
            "The invalidation rules are currently satisfied — the setup is "
            "considered invalid regardless of the core conditions."
        )
    elif status == "regime_mismatch":
        lines.append(
            "The strategy's market-regime requirement is not satisfied by "
            "the classified regime — the setup is not evaluated as fully "
            "satisfied."
        )
    elif status == "conditions_met":
        extra = " and all confirmation rules" if confirmation is not None else ""
        lines.append(f"Core conditions{extra} are met on the provided data.")
    elif status == "conditions_missing":
        failed = _count_failed(core) + (_count_failed(confirmation) if confirmation else 0)
        optional_unmet = _count_optional_unmet(core) + (
            _count_optional_unmet(confirmation) if confirmation else 0
        )
        if failed:
            lines.append(
                f"{failed} required condition(s)/group(s) are not satisfied "
                "yet — the setup is not confirmed by the current data."
            )
        if optional_unmet:
            lines.append(
                f"{optional_unmet} optional rule(s) are unmet (they do not "
                "block the setup)."
            )
    elif status == "inconclusive":
        lines.append(
            "One or more required conditions could not be evaluated from "
            "the supplied data (insufficient history or unsupported rule "
            "type) — no setup conclusion is possible. Unknown conditions "
            "are never treated as satisfied."
        )
    lines.append(
        f"Evaluated {rule_set.mode.replace('_', ' ')} rule set v"
        f"{rule_set.schema_version} on timeframes: "
        f"{', '.join(rule_set.timeframes)}."
    )
    if invalidation is not None and not invalidation.satisfied:
        lines.append("Invalidation rules are NOT triggered (good for this setup).")
    return lines


def _count_failed(node: NodeResult | None) -> int:
    """Count unsatisfied REQUIRED conditions/groups (optional excluded)."""

    if node is None:
        return 0
    if node.optional and not node.satisfied:
        return 0
    if node.kind == "condition":
        return 0 if node.satisfied else 1
    return sum(_count_failed(child) for child in node.children)


def _count_optional_unmet(node: NodeResult | None) -> int:
    if node is None:
        return 0
    if node.kind == "condition":
        return 1 if (node.optional and not node.satisfied) else 0
    return sum(_count_optional_unmet(child) for child in node.children)
