"""Unit tests: market feature engine (Phase 4).

Deterministic synthetic candles only. The recurring theme is the NO
LOOK-AHEAD guarantee: features computed on a prefix of a series must equal
the features of the full series restricted to information available at
that prefix's end (swing confirmation times are the explicit mechanism).
"""

from __future__ import annotations

import math

import pytest

from app.domains.analysis.features import (
    atr,
    candle_anatomy,
    candle_direction,
    confirmed_swings,
    data_completeness,
    ema,
    feature_catalog,
    sma,
    structure_breaks,
    summarize_timeframe,
    swing_structure,
    swings_as_of,
    true_ranges,
    zone_interaction,
)

START = 1_700_000_000
STEP = 60


def candle(i: int, o: float, h: float, low: float, c: float, forming: bool | None = None) -> dict:
    row = {
        "time": START + i * STEP,
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "volume": None,
    }
    if forming is not None:
        row["forming"] = forming
    return row


# --------------------------------------------------------------------------- #
# candle anatomy
# --------------------------------------------------------------------------- #


class TestCandleAnatomy:
    def test_bullish(self) -> None:
        a = candle_anatomy(candle(0, 100, 103, 99, 102))
        assert a["direction"] == "bullish"
        assert a["body"] == 2.0
        assert a["range"] == 4.0
        assert a["upper_wick"] == 1.0
        assert a["lower_wick"] == 1.0
        assert a["body_ratio"] == 0.5

    def test_bearish_and_doji(self) -> None:
        assert candle_direction(candle(0, 102, 103, 99, 100)) == "bearish"
        assert candle_direction(candle(0, 101, 103, 99, 101)) == "doji"

    def test_zero_range_ratios_are_none(self) -> None:
        a = candle_anatomy(candle(0, 100, 100, 100, 100))
        assert a["range"] == 0.0
        assert a["body_ratio"] is None
        assert a["upper_wick_ratio"] is None


# --------------------------------------------------------------------------- #
# indicators
# --------------------------------------------------------------------------- #


class TestIndicators:
    def test_sma_matches_hand_computation(self) -> None:
        out = sma([1.0, 2.0, 3.0, 4.0], 2)
        assert out == [None, 1.5, 2.5, 3.5]

    def test_ema_seed_is_sma(self) -> None:
        out = ema([2.0, 4.0, 6.0, 8.0], 2)
        assert out[1] == 3.0  # SMA seed
        # alpha = 2/3; EMA[2] = 2/3*6 + 1/3*3 = 5
        assert out[2] == pytest.approx(5.0)
        assert out[0] is None

    def test_true_ranges_first_candle_uses_high_low(self) -> None:
        candles = [
            candle(0, 10, 12, 9, 11),
            candle(1, 11, 15, 10.5, 14),  # TR = max(4.5, 4, 3.5) = 4.5
        ]
        trs = true_ranges(candles)
        assert trs[0] == 3.0
        assert trs[1] == 4.5

    def test_atr_needs_period_plus_one(self) -> None:
        candles = [candle(i, 10, 11, 9, 10) for i in range(14)]
        assert atr(candles, 14) is None
        candles.append(candle(14, 10, 11, 9, 10))
        assert atr(candles, 14) == pytest.approx(2.0)

    def test_atr_constant_series(self) -> None:
        candles = [candle(i, 10, 11, 9, 10) for i in range(20)]
        assert atr(candles, 14) == pytest.approx(2.0)


# --------------------------------------------------------------------------- #
# swing points + confirmation timing
# --------------------------------------------------------------------------- #


def zigzag_highs(count: int = 12) -> list[dict]:
    """Flat series with a single clear swing high at index 5 (price 105)."""

    out = [candle(i, 100, 100.5, 99.5, 100) for i in range(count)]
    out[5] = candle(5, 100.2, 105.0, 100.0, 101.0)
    return out


class TestConfirmedSwings:
    def test_swing_high_detected_with_confirmation(self) -> None:
        swings = confirmed_swings(zigzag_highs(), "high", 2)
        assert len(swings) == 1
        s = swings[0]
        assert s["index"] == 5
        assert s["price"] == 105.0
        assert s["confirmed_at_index"] == 7
        assert s["confirmed_at_time"] == START + 7 * STEP

    def test_unconfirmed_edge_swing_is_never_reported(self) -> None:
        # A peak at the last candle can never be confirmed: it must NOT
        # appear, while the earlier interior swing remains valid.
        candles = zigzag_highs(12)
        candles[11] = candle(11, 100.2, 106.0, 100.0, 101.0)
        swings = confirmed_swings(candles, "high", 2)
        assert all(s["index"] != 11 for s in swings)
        assert [s["index"] for s in swings] == [5]

    def test_strength_requires_full_window(self) -> None:
        assert confirmed_swings(zigzag_highs(6), "high", 2) == []
        # strength=3 needs 7 candles; peak at 5 confirmed by candles 6-8.
        assert confirmed_swings(zigzag_highs(9), "high", 3)[0]["index"] == 5

    def test_equal_highs_are_not_swings(self) -> None:
        candles = zigzag_highs(12)
        candles[5] = candle(5, 100.2, 105.0, 100.0, 101.0)
        candles[6] = candle(6, 100.2, 105.0, 100.0, 101.0)
        assert confirmed_swings(candles, "high", 2) == []

    def test_no_lookahead_as_of(self) -> None:
        candles = zigzag_highs(12)
        full = confirmed_swings(candles, "high", 2)
        # At the confirmation candle the swing is known...
        as_of_confirm = swings_as_of(candles, "high", 2, full[0]["confirmed_at_time"])
        assert len(as_of_confirm) == 1
        # ...but one candle BEFORE confirmation it is NOT known yet.
        before = full[0]["confirmed_at_time"] - STEP
        assert swings_as_of(candles, "high", 2, before) == []

    def test_invalid_kind_rejected(self) -> None:
        with pytest.raises(ValueError):
            confirmed_swings(zigzag_highs(), "middle", 2)


class TestSwingStructure:
    def test_labels_and_trend(self) -> None:
        candles = []
        # Two rising swing highs (105, 108) at 5 and 11 with strength 2.
        base = [candle(i, 100, 100.5, 99.5, 100) for i in range(14)]
        base[5] = candle(5, 100.2, 105.0, 100.0, 101.0)
        base[11] = candle(11, 100.4, 108.0, 100.2, 101.0)
        candles = base
        highs = confirmed_swings(candles, "high", 2)
        structure = swing_structure(highs, "high")
        assert structure["labels"] == ["HH"]
        assert structure["structure_trend"] == "mixed"  # only one label

        lows = confirmed_swings(candles, "low", 2)
        assert swing_structure(lows, "low")["labels"] == []

    def test_trend_bullish_with_two_labels(self) -> None:
        base = [candle(i, 100, 100.5, 99.5, 100) for i in range(20)]
        base[5] = candle(5, 100.2, 105.0, 100.0, 101.0)
        base[11] = candle(11, 100.4, 108.0, 100.2, 101.0)
        base[17] = candle(17, 100.6, 111.0, 100.4, 101.0)
        highs = confirmed_swings(base, "high", 2)
        structure = swing_structure(highs, "high")
        assert structure["labels"] == ["HH", "HH"]
        assert structure["structure_trend"] == "bullish"


# --------------------------------------------------------------------------- #
# structure breaks
# --------------------------------------------------------------------------- #


class TestStructureBreaks:
    def test_break_requires_confirmed_reference(self) -> None:
        # Swing high 105 at index 5 (confirmed at 7); candle 9 closes above.
        candles = zigzag_highs(10)
        candles[9] = candle(9, 101.0, 105.4, 100.8, 105.2)
        events = structure_breaks(candles, strength=2)
        assert events
        event = events[-1]
        assert event["side"] == "bullish"
        assert event["level"] == 105.0
        assert event["reference_time"] == START + 5 * STEP
        assert event["time"] == START + 9 * STEP

    def test_break_before_confirmation_is_not_reported(self) -> None:
        # Candle 7 closes above the swing but the swing only completes its
        # confirmation AT candle 7 (5 + strength): s + strength < i must
        # hold, i.e. the break candle must be strictly after candle 7.
        candles = zigzag_highs(8)
        candles[7] = candle(7, 101.0, 105.4, 100.8, 105.2)
        assert structure_breaks(candles, strength=2) == []

    def test_no_event_without_break(self) -> None:
        assert structure_breaks(zigzag_highs(10), strength=2) == []


# --------------------------------------------------------------------------- #
# zones + completeness
# --------------------------------------------------------------------------- #


class TestZoneInteraction:
    def test_position_distance_touches(self) -> None:
        candles = [
            candle(0, 100, 100.9, 99, 100),  # below the zone, no touch
            candle(1, 100, 102, 98.5, 101.5),  # overlaps zone [101, 102]
            candle(2, 101.5, 103, 101, 101.8),  # latest close inside zone
        ]
        z = zone_interaction(candles, 101.0, 102.0)
        assert z["position"] == "inside"
        assert z["distance"] == 0.0
        assert z["touch_count"] == 2
        assert z["first_touch_time"] == START + STEP

    def test_above_with_distance(self) -> None:
        candles = [candle(0, 110, 111, 109, 110)]
        z = zone_interaction(candles, 100.0, 102.0)
        assert z["position"] == "above"
        assert z["distance"] == 8.0
        assert z["touch_count"] == 0

    def test_inverted_zone_rejected(self) -> None:
        with pytest.raises(ValueError):
            zone_interaction([candle(0, 100, 101, 99, 100)], 105.0, 100.0)


class TestDataCompleteness:
    def test_counts_and_forming(self) -> None:
        candles = [candle(i, 100, 101, 99, 100) for i in range(5)]
        candles[-1]["forming"] = True
        c = data_completeness(candles, "M1")
        assert c["total"] == 5
        assert c["closed"] == 4
        assert c["forming"] == 1
        assert c["first_time"] == START
        assert c["last_time"] == START + 4 * STEP
        assert c["gap_count"] == 0

    def test_gap_detection(self) -> None:
        candles = [candle(i, 100, 101, 99, 100) for i in range(5)]
        candles[3]["time"] = candles[2]["time"] + 10 * STEP  # 10-minute delta
        c = data_completeness(candles, "M1")
        assert c["gap_count"] == 1
        assert c["largest_gap_intervals"] == 10.0

    def test_empty_series(self) -> None:
        c = data_completeness([], "M5")
        assert c["total"] == 0
        assert c["first_time"] is None


class TestSummarizeTimeframe:
    def test_summary_shape(self) -> None:
        candles = zigzag_highs(12)
        summary = summarize_timeframe("M1", candles, zones=[{"zone_low": 98.0, "zone_high": 99.0}])
        assert summary["timeframe"] == "M1"
        assert summary["completeness"]["total"] == 12
        assert summary["latest_candle"]["direction"] in ("bullish", "bearish", "doji")
        assert summary["swings"]["strength"] == 2
        assert summary["swings"]["highs"][0]["price"] == 105.0
        assert summary["swings"]["highs"][0]["confirmed_at_time"] is not None
        assert summary["zones"][0]["zone_low"] == 98.0

    def test_empty_timeframe_is_honest(self) -> None:
        summary = summarize_timeframe("M5", [])
        assert summary["completeness"]["total"] == 0
        assert summary["latest_candle"] is None
        assert summary["swings"] is None
        assert summary["structure_breaks"] == []


class TestFeatureCatalog:
    def test_every_feature_documented(self) -> None:
        catalog = feature_catalog()
        assert {f["name"] for f in catalog} >= {
            "candle_anatomy",
            "moving_averages",
            "atr",
            "swing_points",
            "swing_structure",
            "structure_breaks",
            "zone_interaction",
            "data_completeness",
        }
        for entry in catalog:
            assert entry["definition"]
            assert entry["required_history"]
            assert "limitations" in entry
            assert math.isfinite(1.0)  # silence linters on unused imports
