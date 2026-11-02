"""Unit tests: market regime classification (Phase 4).

Deterministic synthetic series with known shapes. The classifier is
configurable — some tests pass explicit RegimeParams to reach branches
that need extreme data (documented per test). Regimes describe the recent
past; the tests also pin the honest behaviors: uncertain never matches a
requirement, agreement is never forced, no confidence numbers exist.
"""

from __future__ import annotations

import pytest

from app.domains.analysis.regime import (
    DefaultRegimeClassifier,
    RegimeParams,
    regime_applicability,
    regime_definitions,
    timeframe_agreement,
)

START = 1_700_000_000
STEP = 60


def candle(i: int, o: float, h: float, low: float, c: float) -> dict:
    return {"time": START + i * STEP, "open": o, "high": h, "low": low, "close": c}


def trending(count: int = 80, step_price: float = 0.002, start: float = 1.10) -> list[dict]:
    """Rising series: each candle gaps up by step_price, small wicks."""

    out = []
    price = start
    for i in range(count):
        o = price
        c = round(price + step_price, 5)
        out.append(candle(i, o, round(c + 0.0002, 5), round(o - 0.0002, 5), c))
        price = c
    return out


def flat(count: int = 80, price: float = 1.1000, wick: float = 0.0001) -> list[dict]:
    out = []
    for i in range(count):
        out.append(candle(i, price, round(price + wick, 5), round(price - wick, 5), price))
    return out


def with_wicks(candles: list[dict], wick: float) -> list[dict]:
    """Rebuild the same closes with larger wicks (TR manipulation)."""

    out = []
    for i, c in enumerate(candles):
        mid = c["close"]
        out.append(candle(i, c["open"], round(mid + wick, 5), round(mid - wick, 5), mid))
    return out


class TestClassification:
    def setup_method(self) -> None:
        self.classifier = DefaultRegimeClassifier()
        self.params = RegimeParams(min_candles=60)

    def test_uptrend(self) -> None:
        # 120 candles so the ATR(100) volatility baseline has history.
        result = self.classifier.classify(trending(120), "M1", self.params)
        assert result.regime == "uptrend"
        assert result.volatility_state in ("normal", "quiet", "volatile")
        assert result.directional_bias == "bullish"
        assert "trending" in result.buckets
        assert result.evidence["slope_atr"] > 0
        assert result.evidence["criterion"]

    def test_downtrend(self) -> None:
        series = trending(step_price=-0.002)
        result = self.classifier.classify(series, "M1", self.params)
        assert result.regime == "downtrend"
        assert result.directional_bias == "bearish"
        assert "trending" in result.buckets

    def test_range_on_flat_series(self) -> None:
        result = self.classifier.classify(flat(), "M1", self.params)
        assert result.regime == "range"
        assert result.directional_bias == "neutral"
        assert result.buckets == frozenset({"ranging"})

    def test_breakout_above_box(self) -> None:
        series = flat(70)
        breakout_open = series[-1]["close"]
        for j, i in enumerate(range(70, 73)):
            series.append(
                candle(
                    i,
                    breakout_open + j * 0.001,
                    breakout_open + j * 0.001 + 0.0011,
                    breakout_open + j * 0.001 - 0.0001,
                    breakout_open + (j + 1) * 0.001,
                )
            )
        result = self.classifier.classify(series, "M1", self.params)
        assert result.regime == "breakout"
        assert result.evidence["breakout_direction"] == "bullish"

    def test_transition_on_conflicting_evidence(self) -> None:
        # Strong uptrend, then one huge crash candle closing far below the
        # EMA while the EMA slope is still positive. With a small trend
        # threshold the conflict branch must fire (momentum up, price
        # below mean) -> transition.
        series = trending(80, step_price=0.002)
        crash_open = series[-1]["close"]
        series.append(
            candle(80, crash_open, crash_open + 0.0002, crash_open - 0.12, crash_open - 0.10)
        )
        params = RegimeParams(min_candles=60, trend_threshold_atr=0.1)
        result = self.classifier.classify(series, "M1", params)
        assert result.regime == "transition"
        assert "conflict" in result.evidence["reason"].lower()

    def test_wide_directionless_is_transition(self) -> None:
        # Flat closes but a box much wider than range_max_width_atr after
        # the wick inflation (slope ~ 0, no breakout: closes stay inside).
        series = flat(80)
        series = with_wicks(series, wick=0.01)  # box ~0.02 wide, ATR ~0.01
        params = RegimeParams(min_candles=60, range_max_width_atr=0.5)
        result = self.classifier.classify(series, "M1", params)
        assert result.regime == "transition"

    def test_uncertain_on_insufficient_data(self) -> None:
        result = self.classifier.classify(flat(30), "M1", self.params)
        assert result.regime == "uncertain"
        assert result.volatility_state == "unknown"
        assert result.buckets == frozenset()
        assert "Insufficient data" in result.evidence["reason"]

    def test_forming_candles_are_excluded(self) -> None:
        series = trending(80)
        forming = dict(series[-1])
        forming["forming"] = True
        series_with_forming = series + [forming]
        result = self.classifier.classify(series_with_forming, "M1", self.params)
        assert result.regime == "uptrend"
        assert result.evidence["candles_used"] == 80

    def test_no_confidence_percentage_exists(self) -> None:
        result = self.classifier.classify(trending(), "M1", self.params)
        blob = str(result.to_dict()).lower()
        assert "confidence" not in blob
        assert "probability" not in blob

    def test_invalid_params_rejected(self) -> None:
        with pytest.raises(ValueError):
            RegimeParams(trend_threshold_atr=0).validate()
        with pytest.raises(ValueError):
            RegimeParams(volatile_atr_ratio=0.3, quiet_atr_ratio=0.5).validate()


class TestVolatilityState:
    def setup_method(self) -> None:
        self.classifier = DefaultRegimeClassifier()
        self.params = RegimeParams(min_candles=60)

    def test_volatile_expansion(self) -> None:
        # Quiet history (tiny TR), the last 14 candles much more volatile.
        # 120 candles give the ATR(100) baseline enough history.
        series = flat(120, wick=0.0001)
        for i in range(106, 120):
            c = series[i]["close"]
            series[i] = candle(i, c, round(c + 0.005, 5), round(c - 0.005, 5), c)
        result = self.classifier.classify(series, "M1", self.params)
        assert result.volatility_state == "volatile"
        assert "volatile" in result.buckets
        assert result.regime == "range"  # closes still flat/contained

    def test_quiet_compression(self) -> None:
        # Noisy history, recent candles compress to tiny TR.
        series = flat(120, wick=0.005)
        for i in range(106, 120):
            c = series[i]["close"]
            series[i] = candle(i, c, round(c + 0.0001, 5), round(c - 0.0001, 5), c)
        result = self.classifier.classify(series, "M1", self.params)
        assert result.volatility_state == "quiet"
        assert "quiet" in result.buckets


class TestRegimeApplicability:
    def _result(self, regime: str) -> object:
        from app.domains.analysis.regime import RegimeResult

        return RegimeResult(
            timeframe="M1",
            regime=regime,
            volatility_state="normal",
            evidence={},
        )

    def test_any_and_none_never_gate(self) -> None:
        assert regime_applicability(None, self._result("uptrend"))["applicable"] is True
        assert regime_applicability("any", self._result("range"))["applicable"] is True

    def test_coarse_bucket_mapping(self) -> None:
        assert regime_applicability("trending", self._result("uptrend"))["applicable"] is True
        assert regime_applicability("trending", self._result("downtrend"))["applicable"] is True
        assert regime_applicability("trending", self._result("range"))["applicable"] is False
        assert regime_applicability("ranging", self._result("range"))["applicable"] is True
        assert regime_applicability("ranging", self._result("uptrend"))["applicable"] is False

    def test_fine_grained_exact_match(self) -> None:
        assert regime_applicability("uptrend", self._result("uptrend"))["applicable"] is True
        assert regime_applicability("uptrend", self._result("downtrend"))["applicable"] is False
        assert regime_applicability("breakout", self._result("breakout"))["applicable"] is True

    def test_uncertain_never_matches(self) -> None:
        for required in ("trending", "ranging", "uptrend", "volatile", "quiet"):
            check = regime_applicability(required, self._result("uncertain"))
            assert check["applicable"] is False
            assert "not satisfied" in check["note"]

    def test_transition_matches_no_bucket(self) -> None:
        result = self._result("transition")
        assert result.buckets == frozenset()  # type: ignore[attr-defined]
        for required in ("trending", "ranging", "transition"):
            assert regime_applicability(required, result)["applicable"] == (
                required == "transition"
            )

    def test_volatile_bucket_from_volatility_state(self) -> None:
        from app.domains.analysis.regime import RegimeResult

        result = RegimeResult(
            timeframe="M1", regime="range", volatility_state="volatile", evidence={}
        )
        assert "volatile" in result.buckets
        assert regime_applicability("volatile", result)["applicable"] is True


class TestTimeframeAgreement:
    def _result(self, timeframe: str, regime: str) -> object:
        from app.domains.analysis.regime import RegimeResult

        return RegimeResult(
            timeframe=timeframe, regime=regime, volatility_state="normal", evidence={}
        )

    def test_agreement(self) -> None:
        report = timeframe_agreement(
            [self._result("H1", "uptrend"), self._result("M15", "uptrend")]
        )
        assert report["status"] == "agreement"

    def test_conflict_never_forced(self) -> None:
        report = timeframe_agreement(
            [self._result("H1", "uptrend"), self._result("M15", "downtrend")]
        )
        assert report["status"] == "conflict"
        assert report["biases"]["H1"] == "bullish"
        assert report["biases"]["M15"] == "bearish"

    def test_partial_with_unknown(self) -> None:
        report = timeframe_agreement(
            [self._result("H1", "uptrend"), self._result("M15", "uncertain")]
        )
        assert report["status"] == "partial"

    def test_insufficient(self) -> None:
        report = timeframe_agreement([self._result("M1", "uncertain")])
        assert report["status"] == "insufficient"
        assert timeframe_agreement([])["status"] == "insufficient"


class TestRegimeCatalog:
    def test_definitions_published(self) -> None:
        defs = regime_definitions()
        assert {d["name"] for d in defs} == {
            "uptrend",
            "downtrend",
            "range",
            "breakout",
            "transition",
            "uncertain",
        }
        for d in defs:
            assert d["definition"]
            assert d["limitations"]
        uncertain = next(d for d in defs if d["name"] == "uncertain")
        assert uncertain["strategy_buckets"] == []
