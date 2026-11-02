"""Advanced technical rule implementations (Phase 4).

Four rule types are implemented here, each with a PRECISE, documented
definition. These are THIS PLATFORM's definitions — deliberately explicit
and configurable. They are **not** claims of universally accepted
ICT/SMC standards; where the community disagrees, the chosen semantics are
stated and the parameters left configurable.

* ``liquidity_sweep`` — wick through a confirmed swing with a close back
  on the origin side.
* ``fvg`` — three-candle fair value gap with explicit
  untouched/unfilled/any mitigation states.
* ``mss_choch`` — market structure shift (change of character): a break
  of the opposing swing REQUIRING prior opposite structure context.
* ``bos`` — break of structure as CONTINUATION: a break in the direction
  of established structure. (The generic, context-free break remains the
  Phase 3 ``market_structure_break`` rule type.)

Common invariants (same as every other rule type):

* pure functions of the supplied candles — no look-ahead;
* insufficient inputs => ``inconclusive`` with an explicit reason, never
  a silent ``not_met``;
* only CLOSED candles decide conditions (the evaluation layer strips
  forming candles by default and labels provisional results otherwise).
"""

from __future__ import annotations

from typing import Any

from app.domains.analysis.evaluator import (
    NodeResult,
    _node,
    _unsatisfied,
    _window,
)
from app.domains.analysis.features import atr as feature_atr
from app.domains.analysis.features import confirmed_swings
from app.domains.strategies.rules import Condition


def _structure_context(
    candles: list[dict[str, Any]], strength: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Confirmed swing highs/lows available strictly before the latest candle.

    The latest candle is reserved as the potential *breaking* candle, so
    swings are computed on ``candles[:-1]`` (same convention as the Phase 3
    ``market_structure_break`` rule).
    """

    zone = candles[:-1]
    return (
        confirmed_swings(zone, "high", strength),
        confirmed_swings(zone, "low", strength),
    )


# --------------------------------------------------------------------------- #
# liquidity_sweep
# --------------------------------------------------------------------------- #


def evaluate_liquidity_sweep(
    condition: Condition, candles: list[dict[str, Any]]
) -> NodeResult:
    """Sweep of prior liquidity: wick beyond a confirmed swing, close back.

    Definition (ours): let H be the most recent swing high of the given
    ``strength`` confirmed strictly before the latest candle. The latest
    candle SWEEPS buy-side liquidity when its HIGH is strictly above
    H.price and its CLOSE is below H.price (wick-through, close-back).
    Mirror for swing lows (sell-side). Equal touches are not sweeps
    (strict inequality). Snapshot semantics: only the LATEST candle can
    sweep.
    """

    tf = condition.timeframe
    kind = str(condition.params["kind"])
    strength = int(condition.params.get("strength", 2))
    window = _window(candles, condition.lookback)

    side_label = "buy-side (swing highs)" if kind == "high" else "sell-side (swing lows)"
    description = (
        f"liquidity sweep of {side_label} (strength {strength}) on {tf} "
        f"(latest candle, last {len(window)})"
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
            "latest candle wicks strictly beyond the most recent confirmed "
            "swing (strength) but closes back on the origin side"
        ),
    }
    if len(window) < 2 * strength + 2:
        return _unsatisfied(
            condition,
            description,
            evidence,
            reason=(
                f"A sweep evaluation needs at least {2 * strength + 2} "
                "candles (swing confirmation + the sweeping candle)."
            ),
        )

    swing_kind = kind
    swings = confirmed_swings(window[:-1], swing_kind, strength)
    evidence["reference_swings"] = [
        {"time": s["time"], "price": s["price"], "confirmed_at_time": s["confirmed_at_time"]}
        for s in swings[-3:]
    ]
    if not swings:
        return _unsatisfied(
            condition,
            description,
            evidence,
            reason=(
                f"No confirmed swing {swing_kind} available as the liquidity "
                "reference — cannot evaluate a sweep."
            ),
        )
    reference = swings[-1]
    latest = window[-1]
    high, low, close = (
        float(latest["high"]),
        float(latest["low"]),
        float(latest["close"]),
    )
    if kind == "high":
        swept = high > reference["price"]
        closed_back = close < reference["price"]
    else:
        swept = low < reference["price"]
        closed_back = close > reference["price"]
    satisfied = bool(swept and closed_back)
    evidence.update(
        {
            "reference_swing_price": reference["price"],
            "reference_swing_time": reference["time"],
            "reference_confirmed_at_time": reference["confirmed_at_time"],
            "sweep_candle_time": latest["time"],
            "sweep_candle_high": high,
            "sweep_candle_low": low,
            "sweep_candle_close": close,
            "wick_beyond_swing": bool(swept),
            "closed_back_inside": bool(closed_back),
            "note": (
                "Wick-through with close-back (our definition). A CLOSE "
                "beyond the swing would be a structure break, not a sweep."
            ),
        }
    )
    return _node(condition, description, evidence, satisfied)


# --------------------------------------------------------------------------- #
# fvg (fair value gap)
# --------------------------------------------------------------------------- #


def _detect_fvgs(
    window: list[dict[str, Any]], direction: str
) -> list[dict[str, Any]]:
    """All FVGs of one direction inside the window (chronological).

    Bullish FVG at index i (i >= 1, i+1 <= n-1): gap between
    ``high[i-1]`` (bottom) and ``low[i+1]`` (top) when low[i+1] > high[i-1].
    Created at candle i+1's close. Mitigation is measured on candles AFTER
    i+1: full fill = a later low <= gap bottom; partial = a later candle
    traded into the gap without filling it; untouched otherwise.
    Bearish mirror: gap between ``low[i-1]`` (top) and ``high[i+1]``
    (bottom) when high[i+1] < low[i-1].
    """

    gaps: list[dict[str, Any]] = []
    n = len(window)
    for i in range(1, n - 1):
        left, right = window[i - 1], window[i + 1]
        if direction == "bullish":
            gap_low, gap_high = float(left["high"]), float(right["low"])
        else:
            gap_low, gap_high = float(right["high"]), float(left["low"])
        if gap_high <= gap_low:
            continue
        created_at = right["time"]
        later = window[i + 2 :]
        filled = any(
            (float(c["low"]) <= gap_low) if direction == "bullish"
            else (float(c["high"]) >= gap_high)
            for c in later
        )
        touched = any(
            (float(c["low"]) <= gap_high) if direction == "bullish"
            else (float(c["high"]) >= gap_low)
            for c in later
        )
        if filled:
            mitigation = "full"
        elif touched:
            mitigation = "partial"
        else:
            mitigation = "none"
        gaps.append(
            {
                "gap_low": gap_low,
                "gap_high": gap_high,
                "direction": direction,
                "created_at_time": created_at,
                "left_time": left["time"],
                "right_time": right["time"],
                "mitigation": mitigation,
                "filled": filled,
            }
        )
    return gaps


def evaluate_fvg(
    condition: Condition, candles: list[dict[str, Any]]
) -> NodeResult:
    """Fair Value Gap presence with an explicit mitigation requirement.

    Requirement semantics (ours):
    * ``exists``           — at least one FVG of the direction in the window.
    * ``exists_unfilled``  — at least one FVG never fully filled by a later
      candle (partial mitigation allowed).
    * ``exists_untouched`` — at least one FVG that no later candle has
      traded into at all.
    """

    tf = condition.timeframe
    direction = str(condition.params["direction"])
    requirement = str(condition.params["requirement"])
    window = _window(candles, condition.lookback)

    description = (
        f"{direction} fair value gap ({requirement.replace('_', ' ')}) on {tf} "
        f"(last {len(window)} candle(s))"
    )
    evidence: dict[str, Any] = {
        "condition_type": condition.type,
        "timeframe": tf,
        "direction": direction,
        "requirement": requirement,
        "lookback": condition.lookback,
        "candles_evaluated": len(window),
        "candles_available": len(candles),
        "criterion": (
            "three-candle imbalance: bullish when low[i+1] > high[i-1], "
            "bearish when high[i+1] < low[i-1]; mitigation measured on "
            "candles after the gap completes"
        ),
    }
    if len(window) < 3:
        return _unsatisfied(
            condition,
            description,
            evidence,
            reason="An FVG needs at least 3 candles in the window.",
        )

    gaps = _detect_fvgs(window, direction)
    evidence["gaps_found"] = len(gaps)
    evidence["recent_gaps"] = gaps[-5:]
    if requirement == "exists":
        matching = gaps
    elif requirement == "exists_unfilled":
        matching = [g for g in gaps if not g["filled"]]
    else:  # exists_untouched
        matching = [g for g in gaps if g["mitigation"] == "none"]
    evidence["matching_gaps"] = len(matching)
    if matching:
        latest = matching[-1]
        evidence["latest_matching_gap"] = {
            "gap_low": latest["gap_low"],
            "gap_high": latest["gap_high"],
            "created_at_time": latest["created_at_time"],
            "mitigation": latest["mitigation"],
        }
    return _node(condition, description, evidence, bool(matching))


# --------------------------------------------------------------------------- #
# mss_choch (market structure shift / change of character)
# --------------------------------------------------------------------------- #


def evaluate_mss_choch(
    condition: Condition, candles: list[dict[str, Any]]
) -> NodeResult:
    """Market structure shift (CHoCH) — break WITH prior opposite character.

    Definition (ours), bullish case:
    1. Prior character is bearish: the last TWO confirmed swing highs are
       strictly falling AND the last TWO confirmed swing lows are strictly
       falling (LH + LL) among swings confirmed strictly before the latest
       candle.
    2. Shift: the latest candle's CLOSE is above the most recent confirmed
       swing high.
    3. Optional displacement filter: when ``displacement`` is true, the
       breaking candle's body must be >= ``displacement_min_atr`` × ATR(14).

    Bearish case mirrors. If the prior character cannot be established
    (fewer than two confirmed swings of a kind), the condition is
    INCONCLUSIVE — a change of character requires a character to change
    FROM; without it we refuse to guess.
    """

    tf = condition.timeframe
    direction = str(condition.params["direction"])
    strength = int(condition.params.get("strength", 2))
    displacement = bool(condition.params.get("displacement", False))
    displacement_min_atr = float(condition.params.get("displacement_min_atr", 1.0))
    window = _window(candles, condition.lookback)

    description = (
        f"{direction} MSS/CHoCH (strength {strength}"
        + (f", displacement >= {displacement_min_atr:g} ATR" if displacement else "")
        + f") on {tf} (last {len(window)})"
    )
    evidence: dict[str, Any] = {
        "condition_type": condition.type,
        "timeframe": tf,
        "direction": direction,
        "strength": strength,
        "displacement_filter": displacement,
        "displacement_min_atr": displacement_min_atr if displacement else None,
        "lookback": condition.lookback,
        "candles_evaluated": len(window),
        "candles_available": len(candles),
        "criterion": (
            "break of the opposing confirmed swing REQUIRING two falling "
            "(bullish) or two rising (bearish) swings of BOTH kinds as "
            "prior character — our precise definition, not a universal "
            "ICT standard"
        ),
    }
    if len(window) < 2 * strength + 3:
        return _unsatisfied(
            condition,
            description,
            evidence,
            reason=(
                f"A CHoCH evaluation needs at least {2 * strength + 3} "
                "candles (two confirmed swings of each kind + the breaking "
                "candle)."
            ),
        )

    swing_highs, swing_lows = _structure_context(window, strength)
    evidence["recent_swing_highs"] = [
        {"time": s["time"], "price": s["price"]} for s in swing_highs[-3:]
    ]
    evidence["recent_swing_lows"] = [
        {"time": s["time"], "price": s["price"]} for s in swing_lows[-3:]
    ]
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return _unsatisfied(
            condition,
            description,
            evidence,
            reason=(
                "Prior structure character could not be established (need "
                "at least two confirmed swing highs and two swing lows) — "
                "a change of character is only defined relative to an "
                "existing character."
            ),
        )

    if direction == "bullish":
        # Prior character bearish: LH + LL.
        character_ok = (
            swing_highs[-1]["price"] < swing_highs[-2]["price"]
            and swing_lows[-1]["price"] < swing_lows[-2]["price"]
        )
        character_label = "bearish (LH+LL)" if character_ok else "not bearish"
        reference = swing_highs[-1]
        close = float(window[-1]["close"])
        shifted = close > reference["price"]
    else:
        # Prior character bullish: HH + HL.
        character_ok = (
            swing_highs[-1]["price"] > swing_highs[-2]["price"]
            and swing_lows[-1]["price"] > swing_lows[-2]["price"]
        )
        character_label = "bullish (HH+HL)" if character_ok else "not bullish"
        reference = swing_lows[-1]
        close = float(window[-1]["close"])
        shifted = close < reference["price"]

    evidence.update(
        {
            "prior_character": character_label,
            "character_present": bool(character_ok),
            "reference_swing_price": reference["price"],
            "reference_swing_time": reference["time"],
            "breaking_close": close,
            "structure_shifted": bool(shifted),
        }
    )

    displacement_ok = True
    if displacement:
        atr_value = feature_atr(window, 14)
        if atr_value is None or atr_value <= 0:
            return _unsatisfied(
                condition,
                description,
                evidence,
                reason=(
                    "Displacement filter requires ATR(14): at least 15 "
                    "candles in the window."
                ),
            )
        body = abs(float(window[-1]["close"]) - float(window[-1]["open"]))
        displacement_ratio = body / atr_value
        displacement_ok = displacement_ratio >= displacement_min_atr
        evidence["displacement_body"] = body
        evidence["displacement_atr"] = atr_value
        evidence["displacement_ratio"] = round(displacement_ratio, 6)

    satisfied = bool(character_ok and shifted and displacement_ok)
    return _node(condition, description, evidence, satisfied)


# --------------------------------------------------------------------------- #
# bos (break of structure — continuation)
# --------------------------------------------------------------------------- #


def evaluate_bos(
    condition: Condition, candles: list[dict[str, Any]]
) -> NodeResult:
    """Break of structure as CONTINUATION (requires aligned character).

    Definition (ours), bullish case:
    1. Prior character is bullish: the last TWO confirmed swing highs are
       strictly rising AND the last TWO confirmed swing lows are strictly
       rising (HH + HL) among swings confirmed strictly before the latest
       candle.
    2. Continuation break: the latest candle's CLOSE is above the most
       recent confirmed swing high.

    Bearish mirrors. Without the two-swing aligned character the result is
    INCONCLUSIVE (a continuation break needs a trend to continue).
    The context-free snapshot break remains ``market_structure_break``.
    """

    tf = condition.timeframe
    direction = str(condition.params["direction"])
    strength = int(condition.params.get("strength", 2))
    window = _window(candles, condition.lookback)

    description = (
        f"{direction} BOS — continuation break with aligned structure "
        f"(strength {strength}) on {tf} (last {len(window)})"
    )
    evidence: dict[str, Any] = {
        "condition_type": condition.type,
        "timeframe": tf,
        "direction": direction,
        "strength": strength,
        "lookback": condition.lookback,
        "candles_evaluated": len(window),
        "candles_available": len(candles),
        "criterion": (
            "close beyond the most recent confirmed swing WHILE the last "
            "two swings of both kinds already rise (bullish) / fall "
            "(bearish) — continuation semantics; context-free breaks use "
            "the market_structure_break type"
        ),
    }
    if len(window) < 2 * strength + 3:
        return _unsatisfied(
            condition,
            description,
            evidence,
            reason=(
                f"A BOS evaluation needs at least {2 * strength + 3} "
                "candles (two confirmed swings of each kind + the breaking "
                "candle)."
            ),
        )

    swing_highs, swing_lows = _structure_context(window, strength)
    evidence["recent_swing_highs"] = [
        {"time": s["time"], "price": s["price"]} for s in swing_highs[-3:]
    ]
    evidence["recent_swing_lows"] = [
        {"time": s["time"], "price": s["price"]} for s in swing_lows[-3:]
    ]
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return _unsatisfied(
            condition,
            description,
            evidence,
            reason=(
                "Structure character could not be established (need at "
                "least two confirmed swing highs and two swing lows) — a "
                "continuation break requires existing structure."
            ),
        )

    if direction == "bullish":
        aligned = (
            swing_highs[-1]["price"] > swing_highs[-2]["price"]
            and swing_lows[-1]["price"] > swing_lows[-2]["price"]
        )
        reference = swing_highs[-1]
        close = float(window[-1]["close"])
        broke = close > reference["price"]
    else:
        aligned = (
            swing_highs[-1]["price"] < swing_highs[-2]["price"]
            and swing_lows[-1]["price"] < swing_lows[-2]["price"]
        )
        reference = swing_lows[-1]
        close = float(window[-1]["close"])
        broke = close < reference["price"]

    evidence.update(
        {
            "structure_aligned": bool(aligned),
            "reference_swing_price": reference["price"],
            "reference_swing_time": reference["time"],
            "breaking_close": close,
            "break_confirmed": bool(broke),
        }
    )
    satisfied = bool(aligned and broke)
    return _node(condition, description, evidence, satisfied)


__all__ = [
    "evaluate_bos",
    "evaluate_fvg",
    "evaluate_liquidity_sweep",
    "evaluate_mss_choch",
]
