"""Unit tests: advanced rule types (Phase 4) — mss_choch, bos,
liquidity_sweep, fvg.

Every fixture is a hand-built deterministic candle series whose swing
structure is verified inline. The tests pin OUR documented definitions
(see app/domains/analysis/rules_advanced.py and /api/v1/rule-types) and
the no-look-ahead guarantees.
"""

from __future__ import annotations

import pytest

from app.domains.analysis.evaluator import (
    STATUS_INCONCLUSIVE,
    STATUS_MET,
    STATUS_NOT_MET,
    evaluate_group,
)
from app.domains.analysis.rules_advanced import (
    evaluate_bos,
    evaluate_fvg,
    evaluate_liquidity_sweep,
    evaluate_mss_choch,
)
from app.domains.strategies.rules import Condition, Group

START = 1_700_000_000
STEP = 60
TF = "M1"


def build(entries: list[tuple], start: float = 1.1000) -> list[dict]:
    """Build candles from (close, wick_up, wick_down) entries.

    open = previous close (continuous series); high = max(o, c) + wu;
    low = min(o, c) - wd. Deterministic and OHLC-consistent by construction.
    """

    out: list[dict] = []
    prev = start
    for i, (c, wu, wd) in enumerate(entries):
        o = prev
        out.append(
            {
                "time": START + i * STEP,
                "open": o,
                "high": round(max(o, c) + wu, 6),
                "low": round(min(o, c) - wd, 6),
                "close": c,
                "volume": None,
            }
        )
        prev = c
    return out


def invert(candles: list[dict]) -> list[dict]:
    """Mirror a series around 1.6500 (highs <-> lows) for bearish cases."""

    return [
        {
            "time": c["time"],
            "open": round(3.3 - c["open"], 6),
            "high": round(3.3 - c["low"], 6),
            "low": round(3.3 - c["high"], 6),
            "close": round(3.3 - c["close"], 6),
            "volume": None,
        }
        for c in candles
    ]


def cond(type_: str, params: dict, lookback: int = 100, cid: str = "c1") -> Condition:
    return Condition(
        id=cid, type=type_, timeframe=TF, params=params, lookback=lookback, legacy=False
    )


# Bearish character -> bullish shift (values verified in comments).
CHOCH_DOWN_ENTRIES = [
    (1.1000, 0.0002, 0.0002),  # 0
    (1.1000, 0.0002, 0.0002),  # 1
    (1.1000, 0.0002, 0.0002),  # 2
    (1.1002, 0.0002, 0.0002),  # 3
    (1.1004, 0.0004, 0.0002),  # 4
    (1.1020, 0.0030, 0.0002),  # 5  swing high #1 = 1.1050
    (1.1008, 0.0002, 0.0002),  # 6
    (1.1005, 0.0002, 0.0002),  # 7  swing high #1 CONFIRMED here
    (1.0988, 0.0002, 0.0004),  # 8
    (1.0985, 0.0002, 0.0003),  # 9
    (1.0960, 0.0002, 0.0030),  # 10 swing low #1 = 1.0930
    (1.0980, 0.0002, 0.0004),  # 11
    (1.0985, 0.0002, 0.0003),  # 12 swing low #1 confirmed
    (1.0995, 0.0004, 0.0002),  # 13
    (1.0998, 0.0004, 0.0002),  # 14
    (1.1010, 0.0020, 0.0002),  # 15 swing high #2 = 1.1030 (LH)
    (1.0995, 0.0004, 0.0002),  # 16
    (1.0998, 0.0004, 0.0002),  # 17 swing high #2 confirmed
    (1.0975, 0.0002, 0.0004),  # 18
    (1.0968, 0.0002, 0.0004),  # 19
    (1.0950, 0.0002, 0.0026),  # 20 swing low #2 = 1.0924 (LL)
    (1.0965, 0.0002, 0.0004),  # 21
    (1.0970, 0.0002, 0.0004),  # 22 swing low #2 confirmed
    (1.0972, 0.0002, 0.0002),  # 23
    (1.0974, 0.0002, 0.0002),  # 24
    (1.1040, 0.0005, 0.0002),  # 25 BREAK: close 1.1040 > 1.1030
]

# Bullish character -> continuation break.
BOS_UP_ENTRIES = [
    (1.1000, 0.0002, 0.0002),  # 0
    (1.1000, 0.0002, 0.0002),  # 1
    (1.1000, 0.0002, 0.0002),  # 2
    (1.1002, 0.0002, 0.0002),  # 3
    (1.1004, 0.0004, 0.0002),  # 4
    (1.1020, 0.0030, 0.0002),  # 5  swing high #1 = 1.1050
    (1.1008, 0.0002, 0.0002),  # 6
    (1.1005, 0.0002, 0.0002),  # 7  confirmed
    (1.0988, 0.0002, 0.0004),  # 8
    (1.0985, 0.0002, 0.0003),  # 9
    (1.0960, 0.0002, 0.0030),  # 10 swing low #1 = 1.0930
    (1.0980, 0.0002, 0.0004),  # 11
    (1.0985, 0.0002, 0.0003),  # 12 confirmed
    (1.1000, 0.0004, 0.0002),  # 13
    (1.1020, 0.0004, 0.0002),  # 14
    (1.1050, 0.0030, 0.0002),  # 15 swing high #2 = 1.1080 (HH)
    (1.1000, 0.0004, 0.0002),  # 16
    (1.1005, 0.0004, 0.0002),  # 17 confirmed
    (1.0990, 0.0002, 0.0004),  # 18
    (1.0985, 0.0002, 0.0004),  # 19
    (1.0995, 0.0002, 0.0015),  # 20 swing low #2 = 1.0953 (HL)
    (1.0998, 0.0002, 0.0004),  # 21
    (1.1002, 0.0002, 0.0004),  # 22 confirmed
    (1.1005, 0.0002, 0.0002),  # 23
    (1.1008, 0.0002, 0.0002),  # 24
    (1.1090, 0.0005, 0.0002),  # 25 BREAK: close 1.1090 > 1.1080
]

SWEEP_ENTRIES = [
    (1.1000, 0.0002, 0.0002),  # 0
    (1.1000, 0.0002, 0.0002),  # 1
    (1.1000, 0.0002, 0.0002),  # 2
    (1.1002, 0.0002, 0.0002),  # 3
    (1.1004, 0.0004, 0.0002),  # 4
    (1.1020, 0.0030, 0.0002),  # 5  swing high = 1.1050 (confirmed at 7)
    (1.1008, 0.0002, 0.0002),  # 6
    (1.1005, 0.0002, 0.0002),  # 7  confirmation completes
    (1.1000, 0.0002, 0.0002),  # 8
    (1.0985, 0.0002, 0.0002),  # 9
    (1.0985, 0.0065, 0.0002),  # 10 SWEEP: high 1.1050+? -> exactly 1.1050 with next test
]

SWEEP_ENTRIES_STRICT = [
    *SWEEP_ENTRIES[:9],
    (1.0990, 0.0070, 0.0002),  # 10 SWEEP: high 1.1065 > 1.1050, close 1.0990
]

FVG_BULL_ENTRIES = [
    (1.0985, 0.0015, 0.0005),  # 0  high = 1.1000 (gap bottom), start=1.0980
    (1.1015, 0.0005, 0.0005),  # 1
    (1.1030, 0.0005, 0.0008),  # 2  low = 1.1007 -> FVG [1.1000, 1.1007]
    (1.1035, 0.0004, 0.0004),  # 3  low 1.1031 -> untouched
    (1.1040, 0.0004, 0.0004),  # 4
]


class TestMssChoch:
    def test_bullish_choch_met(self) -> None:
        node = evaluate_mss_choch(cond("mss_choch", {"direction": "bullish"}), build(CHOCH_DOWN_ENTRIES))
        assert node.status == STATUS_MET
        assert node.evidence["prior_character"] == "bearish (LH+LL)"
        assert node.evidence["reference_swing_price"] == 1.1030
        assert node.evidence["breaking_close"] == 1.1040

    def test_bearish_choch_met_on_inverted_series(self) -> None:
        node = evaluate_mss_choch(
            cond("mss_choch", {"direction": "bearish"}), invert(build(CHOCH_DOWN_ENTRIES))
        )
        assert node.status == STATUS_MET
        assert node.evidence["prior_character"] == "bullish (HH+HL)"

    def test_character_missing_is_inconclusive(self) -> None:
        flat = build([(1.1000, 0.0002, 0.0002)] * 40)
        node = evaluate_mss_choch(cond("mss_choch", {"direction": "bullish"}), flat)
        assert node.status == STATUS_INCONCLUSIVE
        assert "character" in node.evidence["reason"].lower()

    def test_break_without_opposite_character_is_not_met(self) -> None:
        # Breaks above a swing high but the prior character is BULLISH
        # (BOS_UP_ENTRIES) -> bullish CHoCH must NOT fire (that is a BOS,
        # not a change of character).
        node = evaluate_mss_choch(cond("mss_choch", {"direction": "bullish"}), build(BOS_UP_ENTRIES))
        assert node.status == STATUS_NOT_MET
        assert node.evidence["character_present"] is False

    def test_insufficient_candles_inconclusive(self) -> None:
        node = evaluate_mss_choch(
            cond("mss_choch", {"direction": "bullish"}, lookback=5), build(CHOCH_DOWN_ENTRIES)
        )
        assert node.status == STATUS_INCONCLUSIVE

    def test_displacement_filter(self) -> None:
        candles = build(CHOCH_DOWN_ENTRIES)
        met = evaluate_mss_choch(
            cond("mss_choch", {"direction": "bullish", "displacement": True, "displacement_min_atr": 1.0}),
            candles,
        )
        assert met.status == STATUS_MET
        blocked = evaluate_mss_choch(
            cond("mss_choch", {"direction": "bullish", "displacement": True, "displacement_min_atr": 50.0}),
            candles,
        )
        assert blocked.status == STATUS_NOT_MET
        assert blocked.evidence["displacement_ratio"] < 50.0


class TestBos:
    def test_bullish_bos_met(self) -> None:
        node = evaluate_bos(cond("bos", {"direction": "bullish"}), build(BOS_UP_ENTRIES))
        assert node.status == STATUS_MET
        assert node.evidence["structure_aligned"] is True
        assert node.evidence["reference_swing_price"] == 1.1080

    def test_bearish_bos_met_on_inverted(self) -> None:
        node = evaluate_bos(cond("bos", {"direction": "bearish"}), invert(build(BOS_UP_ENTRIES)))
        assert node.status == STATUS_MET

    def test_break_without_aligned_character_is_not_met(self) -> None:
        # The CHOCH_DOWN series breaks above a swing high but structure
        # falls — a continuation BOS must NOT fire (that is a CHoCH).
        node = evaluate_bos(cond("bos", {"direction": "bullish"}), build(CHOCH_DOWN_ENTRIES))
        assert node.status == STATUS_NOT_MET
        assert node.evidence["structure_aligned"] is False

    def test_no_structure_is_inconclusive(self) -> None:
        flat = build([(1.1000, 0.0002, 0.0002)] * 40)
        node = evaluate_bos(cond("bos", {"direction": "bullish"}), flat)
        assert node.status == STATUS_INCONCLUSIVE


class TestLiquiditySweep:
    def test_sweep_high_met(self) -> None:
        node = evaluate_liquidity_sweep(
            cond("liquidity_sweep", {"kind": "high"}), build(SWEEP_ENTRIES_STRICT)
        )
        assert node.status == STATUS_MET
        assert node.evidence["reference_swing_price"] == 1.1050
        assert node.evidence["wick_beyond_swing"] is True
        assert node.evidence["closed_back_inside"] is True

    def test_sweep_low_met_on_inverted(self) -> None:
        node = evaluate_liquidity_sweep(
            cond("liquidity_sweep", {"kind": "low"}), invert(build(SWEEP_ENTRIES_STRICT))
        )
        assert node.status == STATUS_MET

    def test_close_beyond_is_not_a_sweep(self) -> None:
        # Close ABOVE the swing high: that is a structure break, not a sweep.
        entries = SWEEP_ENTRIES_STRICT[:-1] + [(1.1060, 0.0010, 0.0002)]
        node = evaluate_liquidity_sweep(cond("liquidity_sweep", {"kind": "high"}), build(entries))
        assert node.status == STATUS_NOT_MET
        assert node.evidence["wick_beyond_swing"] is True
        assert node.evidence["closed_back_inside"] is False

    def test_equal_high_is_not_a_sweep(self) -> None:
        # high == swing price exactly: strictly-greater rule says NO sweep.
        entries = SWEEP_ENTRIES_STRICT[:9] + [
            (1.0985, 0.0002, 0.0002),
            (1.0985, 0.0065, 0.0002),
        ]
        candles = build(entries)
        assert candles[-1]["high"] == 1.1050
        node = evaluate_liquidity_sweep(cond("liquidity_sweep", {"kind": "high"}), candles)
        assert node.status == STATUS_NOT_MET

    def test_no_reference_is_inconclusive(self) -> None:
        flat = build([(1.1000, 0.0002, 0.0002)] * 20)
        node = evaluate_liquidity_sweep(cond("liquidity_sweep", {"kind": "high"}), flat)
        assert node.status == STATUS_INCONCLUSIVE

    def test_reference_must_confirm_before_sweep(self) -> None:
        # Sweep candle placed at the swing's CONFIRMING candle (i7): the
        # swing is not yet an usable reference inside candles[:-1] -> the
        # rule refuses to evaluate (inconclusive), never fabricates.
        entries = SWEEP_ENTRIES_STRICT[:7] + [(1.0990, 0.0070, 0.0002)]
        node = evaluate_liquidity_sweep(cond("liquidity_sweep", {"kind": "high"}), build(entries))
        assert node.status == STATUS_INCONCLUSIVE


class TestFvg:
    def test_bullish_gap_exists(self) -> None:
        # Exactly three candles -> the single designed gap.
        node = evaluate_fvg(
            cond("fvg", {"direction": "bullish", "requirement": "exists"}),
            build(FVG_BULL_ENTRIES[:3], start=1.0980),
        )
        assert node.status == STATUS_MET
        latest = node.evidence["latest_matching_gap"]
        assert latest["gap_low"] == 1.1000
        assert latest["gap_high"] == 1.1007
        assert latest["created_at_time"] == START + 2 * STEP

    def test_untouched(self) -> None:
        # Five candles -> two gaps, neither mitigated by a later candle.
        node = evaluate_fvg(
            cond("fvg", {"direction": "bullish", "requirement": "exists_untouched"}),
            build(FVG_BULL_ENTRIES, start=1.0980),
        )
        assert node.status == STATUS_MET
        assert node.evidence["gaps_found"] == 2
        assert node.evidence["matching_gaps"] == 2
        assert node.evidence["latest_matching_gap"]["mitigation"] == "none"

    def test_partial_fill_counts_as_unfilled_not_untouched(self) -> None:
        entries = FVG_BULL_ENTRIES[:3] + [
            (1.1005, 0.0004, 0.0002),  # low 1.1003: inside gap, not through
            (1.1010, 0.0004, 0.0004),  # low 1.1001: still above gap bottom
        ]
        candles = build(entries, start=1.0980)
        unfilled = evaluate_fvg(
            cond("fvg", {"direction": "bullish", "requirement": "exists_unfilled"}), candles
        )
        assert unfilled.status == STATUS_MET
        untouched = evaluate_fvg(
            cond("fvg", {"direction": "bullish", "requirement": "exists_untouched"}), candles
        )
        assert untouched.status == STATUS_NOT_MET

    def test_full_fill_rejects_unfilled(self) -> None:
        entries = FVG_BULL_ENTRIES[:3] + [
            (1.1005, 0.0004, 0.0008),  # low 1.0997 <= gap bottom 1.1000
            (1.1030, 0.0004, 0.0002),
        ]
        candles = build(entries, start=1.0980)
        assert (
            evaluate_fvg(cond("fvg", {"direction": "bullish", "requirement": "exists"}), candles).status
            == STATUS_MET
        )
        assert (
            evaluate_fvg(
                cond("fvg", {"direction": "bullish", "requirement": "exists_unfilled"}), candles
            ).status
            == STATUS_NOT_MET
        )

    def test_bearish_gap_on_inverted(self) -> None:
        node = evaluate_fvg(
            cond("fvg", {"direction": "bearish", "requirement": "exists"}),
            invert(build(FVG_BULL_ENTRIES, start=1.0980)),
        )
        assert node.status == STATUS_MET

    def test_needs_three_candles(self) -> None:
        node = evaluate_fvg(
            cond("fvg", {"direction": "bullish", "requirement": "exists"}, lookback=2),
            build(FVG_BULL_ENTRIES, start=1.0980),
        )
        assert node.status == STATUS_INCONCLUSIVE

    def test_mitigation_uses_only_later_candles(self) -> None:
        # A candle BEFORE the gap that traded at the gap price must not
        # count as mitigation (no look-ahead).
        entries = [
            (1.1035, 0.0003, 0.0005),  # 0 high 1.1038 (inside future gap)
            (1.1025, 0.0000, 0.0005),  # 1 high 1.1035 (gap bottom)
            (1.1045, 0.0004, 0.0005),  # 2 low 1.1040
            (1.1055, 0.0004, 0.0005),  # 3 low 1.1040 -> gap [1.1035, 1.1040]
        ]
        node = evaluate_fvg(
            cond("fvg", {"direction": "bullish", "requirement": "exists_untouched"}),
            build(entries, start=1.1035),
        )
        assert node.status == STATUS_MET


class TestGroupIntegration:
    def test_advanced_rules_work_inside_groups(self) -> None:
        candles = build(CHOCH_DOWN_ENTRIES)
        group = Group(
            logic="AND",
            conditions=(
                cond("mss_choch", {"direction": "bullish"}, cid="a"),
                cond("fvg", {"direction": "bullish", "requirement": "exists"}, cid="b"),
            ),
            groups=(),
        )
        node = evaluate_group(group, {TF: candles})
        assert node.status == STATUS_MET
        statuses = {child.id: child.status for child in node.children}
        assert statuses == {"a": STATUS_MET, "b": STATUS_MET}

    def test_registry_and_evaluators_stay_in_sync(self) -> None:
        from app.domains.analysis.evaluator import CONDITION_EVALUATORS
        from app.domains.strategies.rule_registry import AVAILABLE_RULE_TYPES

        for advanced in ("mss_choch", "bos", "liquidity_sweep", "fvg"):
            assert advanced in CONDITION_EVALUATORS
            assert advanced in AVAILABLE_RULE_TYPES

    def test_validation_accepts_advanced_types(self) -> None:
        from app.domains.strategies.rules import validate_rule_set

        payload = {
            "schema_version": "2.0",
            "mode": "forex",
            "timeframes": [TF],
            "direction": "bullish",
            "conditions": {
                "logic": "OR",
                "conditions": [
                    {"id": "s1", "type": "liquidity_sweep", "timeframe": TF,
                     "params": {"kind": "high"}},
                    {"id": "c1", "type": "mss_choch", "timeframe": TF,
                     "params": {"direction": "bullish"}},
                    {"id": "b1", "type": "bos", "timeframe": TF,
                     "params": {"direction": "bullish"}},
                    {"id": "f1", "type": "fvg", "timeframe": TF,
                     "params": {"direction": "bullish", "requirement": "exists_unfilled"}},
                ],
                "groups": [],
            },
        }
        rule_set = validate_rule_set(payload)
        assert rule_set.to_dict()["conditions"]["conditions"][0]["params"]["strength"] == 2

    def test_mss_choch_boolean_param_validated(self) -> None:
        from app.domains.strategies.rules import RuleValidationError, validate_rule_set

        payload = {
            "schema_version": "2.0",
            "mode": "forex",
            "timeframes": [TF],
            "direction": "bullish",
            "conditions": {
                "logic": "AND",
                "conditions": [
                    {"id": "c1", "type": "mss_choch", "timeframe": TF,
                     "params": {"direction": "bullish", "displacement": "yes"}},
                ],
                "groups": [],
            },
        }
        with pytest.raises(RuleValidationError) as exc:
            validate_rule_set(payload)
        assert any(e["path"].endswith(".params.displacement") for e in exc.value.errors)
