"""Market regime classification — configurable, explicit, honest heuristics.

The classifier assigns each evaluated timeframe one of SIX regimes:

* ``uptrend``    — directional momentum up AND price on the upper side of
                   the mean.
* ``downtrend``  — directional momentum down AND price on the lower side.
* ``range``      — no directional momentum and price contained in a
                   bounded box.
* ``breakout``   — a recent close left a previously established box.
* ``transition`` — conflicting or wide directionless behaviour (the
                   market is *changing*, not trending or ranging).
* ``uncertain``  — insufficient data or unusable inputs. Never treated as
                   a match for any strategy regime requirement.

**These are explicit, deterministic HEURISTICS.** A classified regime
describes the recent past on one timeframe; it is never a claim about
future price movement and never a probability. Definitions, parameters
and evidence are published verbatim (``GET /api/v1/analysis/features``).

The classifier also reports a volatility state (``quiet``/``normal``/
``volatile``/``unknown``) from ATR compression/expansion, and maps the
classified regime to the strategy applicability buckets used by the
Phase 3 rule-set vocabulary (``trending``/``ranging``/``volatile``/
``quiet`` plus the fine-grained regime names).

A strategy that requires a regime must not be evaluated as fully
satisfied when the regime is ``uncertain`` — enforced via empty
applicability buckets and the engine's regime gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.domains.analysis.features import atr, candle_direction, ema

REGIME_NAMES: tuple[str, ...] = (
    "uptrend",
    "downtrend",
    "range",
    "breakout",
    "transition",
    "uncertain",
)

VOLATILITY_STATES: tuple[str, ...] = ("quiet", "normal", "volatile", "unknown")

# Coarse Phase 3 strategy vocabulary each fine-grained regime maps to.
REGIME_BUCKETS: dict[str, frozenset[str]] = {
    "uptrend": frozenset({"trending"}),
    "downtrend": frozenset({"trending"}),
    "range": frozenset({"ranging"}),
    "breakout": frozenset({"trending"}),
    "transition": frozenset(),
    "uncertain": frozenset(),
}

DIRECTIONAL_BIAS: dict[str, str] = {
    "uptrend": "bullish",
    "downtrend": "bearish",
    "range": "neutral",
    "breakout": "directional",  # direction recorded in evidence.breakout_direction
    "transition": "unknown",
    "uncertain": "unknown",
}


@dataclass(frozen=True)
class RegimeParams:
    """Every threshold is configurable; defaults are documented heuristics.

    These defaults are engineering choices, NOT universally accepted
    industry constants — users may override them per analysis.
    """

    ema_period: int = 50
    atr_period: int = 14
    slope_lookback: int = 5
    # |EMA slope over slope_lookback| (in ATR units) treated as directional.
    trend_threshold_atr: float = 0.8
    # Box window for range/breakout detection.
    range_window: int = 20
    # The most recent candles that may break out of the box.
    range_recent: int = 3
    # Box width (in ATR) still considered a contained range.
    range_max_width_atr: float = 5.0
    # Minimum closed candles before classifying (below => uncertain).
    min_candles: int = 60
    # Volatility state thresholds on atr / atr_long ratio.
    volatile_atr_ratio: float = 1.5
    quiet_atr_ratio: float = 0.6
    atr_long_period: int = 100

    def validate(self) -> None:
        if self.ema_period < 2:
            raise ValueError("ema_period must be >= 2")
        if self.atr_period < 1:
            raise ValueError("atr_period must be >= 1")
        if self.slope_lookback < 1:
            raise ValueError("slope_lookback must be >= 1")
        if self.trend_threshold_atr <= 0:
            raise ValueError("trend_threshold_atr must be > 0")
        if self.range_window < 2:
            raise ValueError("range_window must be >= 2")
        if self.range_recent < 1:
            raise ValueError("range_recent must be >= 1")
        if self.range_max_width_atr <= 0:
            raise ValueError("range_max_width_atr must be > 0")
        if self.min_candles < 2:
            raise ValueError("min_candles must be >= 2")
        if self.volatile_atr_ratio <= self.quiet_atr_ratio:
            raise ValueError("volatile_atr_ratio must be > quiet_atr_ratio")


@dataclass
class RegimeResult:
    """Outcome of classifying one timeframe, with full evidence."""

    timeframe: str
    regime: str
    volatility_state: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def buckets(self) -> frozenset[str]:
        base = set(REGIME_BUCKETS[self.regime])
        if self.volatility_state == "volatile":
            base.add("volatile")
        elif self.volatility_state == "quiet":
            base.add("quiet")
        return frozenset(base)

    @property
    def directional_bias(self) -> str:
        bias = DIRECTIONAL_BIAS[self.regime]
        if bias == "directional":
            return str(self.evidence.get("breakout_direction", "unknown"))
        return bias

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "regime": self.regime,
            "volatility_state": self.volatility_state,
            "directional_bias": self.directional_bias,
            "applicable_buckets": sorted(self.buckets),
            "evidence": self.evidence,
        }


class RegimeClassifier(Protocol):
    """Pluggable classification interface (users may supply their own)."""

    def classify(
        self, candles: list[dict[str, Any]], timeframe: str, params: RegimeParams
    ) -> RegimeResult: ...


class DefaultRegimeClassifier:
    """Documented default implementation (deterministic, closed candles).

    Classification order (first match wins):

    1. Fewer than ``min_candles`` usable candles        -> ``uncertain``.
    2. BREAKOUT: the base box was a genuine contained, directionless
       range (|slope| < threshold AND box width <= ``range_max_width_atr``)
       AND a recent close left the box                  -> ``breakout``.
    3. Slope >= +thr AND close > EMA                    -> ``uptrend``.
       Slope <= -thr AND close < EMA                    -> ``downtrend``.
    4. |Slope| >= thr but close on the WRONG side of the EMA
       (momentum/price conflict)                        -> ``transition``.
    5. Box width <= ``range_max_width_atr``             -> ``range``.
    6. Otherwise (wide, directionless)                  -> ``transition``.

    The breakout qualification matters: in a trending series the recent
    closes ALWAYS sit outside the lookback box — that is trend
    continuation, not a breakout. Only a previously directionless,
    contained market can break out.

    Volatility: ratio = ATR(atr_period) / ATR(atr_long_period);
    ratio >= volatile_atr_ratio -> ``volatile``; <= quiet_atr_ratio ->
    ``quiet``; otherwise ``normal`` (``unknown`` without enough data).
    """

    name = "default"

    def classify(
        self,
        candles: list[dict[str, Any]],
        timeframe: str,
        params: RegimeParams | None = None,
    ) -> RegimeResult:
        params = params or RegimeParams()
        params.validate()
        usable = [c for c in candles if c.get("forming") is not True]

        if len(usable) < params.min_candles:
            return RegimeResult(
                timeframe=timeframe,
                regime="uncertain",
                volatility_state="unknown",
                evidence={
                    "reason": (
                        f"Insufficient data: {len(usable)} closed candle(s) "
                        f"available, {params.min_candles} required."
                    ),
                    "candles_available": len(usable),
                    "candles_required": params.min_candles,
                    "criterion": "regime classification needs minimum history",
                },
            )

        closes = [float(c["close"]) for c in usable]
        ema_series = ema(closes, params.ema_period)
        ema_now = ema_series[-1]
        ema_prev = ema_series[-1 - params.slope_lookback]
        atr_now = atr(usable, params.atr_period)

        if ema_now is None or ema_prev is None or atr_now is None or atr_now <= 0:
            return RegimeResult(
                timeframe=timeframe,
                regime="uncertain",
                volatility_state="unknown",
                evidence={
                    "reason": (
                        "Could not compute EMA/ATR from the supplied candles "
                        "(insufficient history for the configured periods)."
                    ),
                    "candles_available": len(usable),
                    "ema_period": params.ema_period,
                    "atr_period": params.atr_period,
                },
            )

        slope = ema_now - ema_prev
        slope_atr = slope / atr_now
        close = closes[-1]
        thr = params.trend_threshold_atr

        # --- box (range/breakout detection) --------------------------------
        recent = usable[-params.range_recent:]
        base = (
            usable[-(params.range_window + params.range_recent) : -params.range_recent]
            if len(usable) >= params.range_window + params.range_recent
            else usable[:-params.range_recent]
        )
        box_high = max(float(c["high"]) for c in base)
        box_low = min(float(c["low"]) for c in base)
        box_width_atr = (box_high - box_low) / atr_now

        evidence: dict[str, Any] = {
            "classifier": self.name,
            "candles_used": len(usable),
            "ema_period": params.ema_period,
            "ema_value": ema_now,
            "ema_slope": slope,
            "slope_atr": round(slope_atr, 6),
            "trend_threshold_atr": thr,
            "atr": atr_now,
            "atr_period": params.atr_period,
            "close": close,
            "latest_candle_direction": candle_direction(usable[-1]),
            "box_high": box_high,
            "box_low": box_low,
            "box_width_atr": round(box_width_atr, 6),
            "range_window": params.range_window,
            "range_recent": params.range_recent,
            "criterion": (
                "breakout (from a contained, directionless box) > trend "
                "(slope+position) > conflict > contained range > wide "
                "directionless; all thresholds configurable"
            ),
        }

        # --- 2. breakout (only from a contained, directionless range) -------
        breakout_direction: str | None = None
        box_qualifies_as_range = (
            abs(slope_atr) < thr and box_width_atr <= params.range_max_width_atr
        )
        if box_qualifies_as_range:
            for c in recent:
                rc = float(c["close"])
                if rc > box_high:
                    breakout_direction = "bullish"
                    break
                if rc < box_low:
                    breakout_direction = "bearish"
                    break
        if breakout_direction is not None:
            evidence["breakout_direction"] = breakout_direction
            evidence["reason"] = (
                f"A directionless, contained box [{box_low:g}, {box_high:g}] "
                f"({box_width_atr:.2f} ATR) was left by a recent "
                f"{'close above box_high' if breakout_direction == 'bullish' else 'close below box_low'} "
                f"({close:g})."
            )
            return self._with_volatility(
                RegimeResult(
                    timeframe=timeframe,
                    regime="breakout",
                    volatility_state="unknown",
                    evidence=evidence,
                ),
                usable,
                params,
            )

        # --- 3. trend --------------------------------------------------------
        if slope_atr >= thr and close > ema_now:
            evidence["reason"] = (
                f"EMA slope {slope_atr:+.2f} ATR over {params.slope_lookback} "
                f"candles >= +{thr:g} and close {close:g} > EMA {ema_now:g}."
            )
            regime = "uptrend"
        elif slope_atr <= -thr and close < ema_now:
            evidence["reason"] = (
                f"EMA slope {slope_atr:+.2f} ATR over {params.slope_lookback} "
                f"candles <= -{thr:g} and close {close:g} < EMA {ema_now:g}."
            )
            regime = "downtrend"
        # --- 4. conflict -> transition ---------------------------------------
        elif abs(slope_atr) >= thr:
            evidence["reason"] = (
                f"Directional EMA slope ({slope_atr:+.2f} ATR) conflicts with "
                f"price position vs EMA (close {close:g} vs EMA {ema_now:g}) — "
                "conflicting evidence, market may be transitioning."
            )
            regime = "transition"
        # --- 5. contained range ----------------------------------------------
        elif box_width_atr <= params.range_max_width_atr:
            evidence["reason"] = (
                f"No directional momentum (slope {slope_atr:+.2f} ATR) and "
                f"box width {box_width_atr:.2f} ATR <= "
                f"{params.range_max_width_atr:g} — contained range."
            )
            regime = "range"
        # --- 6. wide directionless -------------------------------------------
        else:
            evidence["reason"] = (
                f"No directional momentum (slope {slope_atr:+.2f} ATR) but "
                f"box width {box_width_atr:.2f} ATR > "
                f"{params.range_max_width_atr:g} — wide directionless "
                "oscillation."
            )
            regime = "transition"

        return self._with_volatility(
            RegimeResult(
                timeframe=timeframe,
                regime=regime,
                volatility_state="unknown",
                evidence=evidence,
            ),
            usable,
            params,
        )

    def _with_volatility(
        self,
        result: RegimeResult,
        usable: list[dict[str, Any]],
        params: RegimeParams,
    ) -> RegimeResult:
        """Attach the ATR-ratio volatility state to a classified result."""

        atr_short = atr(usable, params.atr_period)
        atr_long = atr(usable, params.atr_long_period)
        if atr_short is None or atr_long is None or atr_long <= 0:
            result.volatility_state = "unknown"
            result.evidence["volatility_state"] = "unknown"
            result.evidence["volatility_reason"] = (
                "Not enough history for the long ATR baseline."
            )
            return result
        ratio = atr_short / atr_long
        result.evidence["atr_ratio"] = round(ratio, 6)
        result.evidence["atr_long_period"] = params.atr_long_period
        if ratio >= params.volatile_atr_ratio:
            state = "volatile"
            reason = f"ATR ratio {ratio:.2f} >= {params.volatile_atr_ratio:g}."
        elif ratio <= params.quiet_atr_ratio:
            state = "quiet"
            reason = f"ATR ratio {ratio:.2f} <= {params.quiet_atr_ratio:g}."
        else:
            state = "normal"
            reason = (
                f"ATR ratio {ratio:.2f} between {params.quiet_atr_ratio:g} "
                f"and {params.volatile_atr_ratio:g}."
            )
        result.volatility_state = state
        result.evidence["volatility_state"] = state
        result.evidence["volatility_reason"] = reason
        return result


# --------------------------------------------------------------------------- #
# Strategy applicability (Phase 3 vocabulary + fine-grained regime names)
# --------------------------------------------------------------------------- #


def regime_applicability(
    required: str | None, result: RegimeResult
) -> dict[str, Any]:
    """Does the classified regime satisfy the strategy's regime requirement?

    * ``None``/``any`` requirement           -> applicable (no gate).
    * Fine-grained requirement (uptrend,
      downtrend, range, breakout, transition) -> exact regime match.
    * Coarse requirement (trending, ranging,
      volatile, quiet)                        -> bucket membership.
    * ``uncertain`` regime NEVER matches a concrete requirement — a
      strategy that requires a regime is not satisfied by unknown data.
    """

    if required is None or required == "any":
        return {
            "required": required or "any",
            "classified": result.regime,
            "applicable": True,
            "matched_buckets": sorted(result.buckets),
            "note": "No regime requirement.",
        }
    if result.regime == "uncertain":
        return {
            "required": required,
            "classified": result.regime,
            "applicable": False,
            "matched_buckets": [],
            "note": (
                "The regime could not be classified (insufficient or "
                "unusable data). A strategy that requires a regime is not "
                "satisfied by unknown conditions."
            ),
        }
    fine = {"uptrend", "downtrend", "range", "breakout", "transition"}
    if required in fine:
        applicable = result.regime == required
    else:
        applicable = required in result.buckets
    return {
        "required": required,
        "classified": result.regime,
        "applicable": applicable,
        "matched_buckets": sorted(result.buckets),
        "note": (
            "Regime requirement satisfied by the classified regime."
            if applicable
            else (
                f"Classified regime '{result.regime}' does not satisfy the "
                f"required regime '{required}'. The strategy is not "
                "evaluated as fully satisfied."
            )
        ),
    }


def timeframe_agreement(results: list[RegimeResult]) -> dict[str, Any]:
    """Higher/lower timeframe agreement report. NEVER forces agreement.

    ``agreement``  — every classified timeframe carries the same
                     non-unknown directional bias.
    ``conflict``   — at least one bullish and one bearish bias.
    ``partial``    — some timeframes are classifiable, others unknown.
    ``insufficient`` — no timeframe could be classified.
    """

    if not results:
        return {
            "status": "insufficient",
            "detail": "No timeframes were classified.",
            "biases": {},
        }
    biases = {r.timeframe: r.directional_bias for r in results}
    directional = {tf: b for tf, b in biases.items() if b in ("bullish", "bearish")}
    if not directional:
        status, detail = "insufficient", (
            "No timeframe carried a directional bias (all neutral or unknown)."
        )
    elif len(set(directional.values())) == 1:
        if len(directional) == len(biases):
            status, detail = "agreement", (
                f"All classified timeframes agree: {next(iter(directional.values()))}."
            )
        else:
            status, detail = "partial", (
                f"Classified timeframes agree ({next(iter(directional.values()))}) "
                "but some timeframes are neutral/unknown."
            )
    else:
        status, detail = "conflict", (
            "Timeframes disagree on directional bias: "
            + ", ".join(f"{tf}={b}" for tf, b in sorted(biases.items()))
            + ". Disagreement is reported, never forced into agreement."
        )
    return {"status": status, "detail": detail, "biases": biases}


def regime_definitions() -> list[dict[str, Any]]:
    """Catalog entries for the API (``GET /api/v1/analysis/features``)."""

    return [
        {
            "name": "uptrend",
            "definition": (
                "EMA(ema_period) slope over the last slope_lookback candles "
                ">= +trend_threshold_atr ATR AND latest close above the EMA."
            ),
            "strategy_buckets": sorted(REGIME_BUCKETS["uptrend"]),
            "limitations": (
                "Deterministic heuristic describing the recent past on ONE "
                "timeframe; not a prediction and not a probability."
            ),
        },
        {
            "name": "downtrend",
            "definition": (
                "EMA slope <= -trend_threshold_atr ATR AND latest close "
                "below the EMA."
            ),
            "strategy_buckets": sorted(REGIME_BUCKETS["downtrend"]),
            "limitations": "Same heuristic limitations as uptrend.",
        },
        {
            "name": "range",
            "definition": (
                "No directional momentum (|slope| < threshold) and the "
                "base-window box width <= range_max_width_atr ATR."
            ),
            "strategy_buckets": sorted(REGIME_BUCKETS["range"]),
            "limitations": (
                "A 'range' is containment over the configured lookback; "
                "it says nothing about where price goes next."
            ),
        },
        {
            "name": "breakout",
            "definition": (
                "A recent close beyond the base-window box (max high / min "
                "low of the range_window candles before the recent ones)."
            ),
            "strategy_buckets": sorted(REGIME_BUCKETS["breakout"]),
            "limitations": (
                "Breakouts can fail; classification is a snapshot, not a "
                "continuation claim."
            ),
        },
        {
            "name": "transition",
            "definition": (
                "Conflicting evidence (directional slope with price on the "
                "wrong side of the EMA) or wide directionless oscillation "
                "(box > range_max_width_atr without momentum)."
            ),
            "strategy_buckets": sorted(REGIME_BUCKETS["transition"]),
            "limitations": (
                "Transition matches NO strategy regime bucket — strategies "
                "requiring a regime are not satisfied during transition."
            ),
        },
        {
            "name": "uncertain",
            "definition": (
                "Fewer than min_candles usable closed candles, or EMA/ATR "
                "uncomputable for the configured periods."
            ),
            "strategy_buckets": [],
            "limitations": (
                "Never matches any strategy regime requirement: unknown "
                "conditions are never treated as satisfied."
            ),
        },
    ]


def volatility_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "volatile",
            "definition": (
                "ATR(atr_period) / ATR(atr_long_period) >= volatile_atr_ratio."
            ),
        },
        {
            "name": "quiet",
            "definition": (
                "ATR(atr_period) / ATR(atr_long_period) <= quiet_atr_ratio."
            ),
        },
        {
            "name": "normal",
            "definition": "ATR ratio between the quiet and volatile thresholds.",
        },
        {
            "name": "unknown",
            "definition": "Not enough history for the long ATR baseline.",
        },
    ]


__all__ = [
    "DIRECTIONAL_BIAS",
    "DefaultRegimeClassifier",
    "REGIME_BUCKETS",
    "REGIME_NAMES",
    "RegimeClassifier",
    "RegimeParams",
    "RegimeResult",
    "VOLATILITY_STATES",
    "regime_applicability",
    "regime_definitions",
    "timeframe_agreement",
    "volatility_definitions",
]
