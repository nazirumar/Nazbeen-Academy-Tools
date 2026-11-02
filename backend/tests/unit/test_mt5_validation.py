"""Unit tests: candle validation & data-quality checks (no MT5, no DB)."""

from __future__ import annotations

import math

from app.domains.market_data.validation import validate_candles
from tests.fakes.fake_mt5 import make_candle

NOW = 1_700_000_000  # fixed "now" for determinism (M1 grid aligned)


def m1_series(n: int, *, end_open: int | None = None) -> list[dict]:
    """n consecutive M1 candles whose LAST open is `end_open` (default aligned to NOW)."""

    end = end_open if end_open is not None else (NOW // 60 * 60)
    return [
        make_candle(end - (n - 1 - i) * 60, 1.1000 + i * 0.0001, 1.1000 + (i + 1) * 0.0001)
        for i in range(n)
    ]


class TestOHLCValidation:
    def test_valid_series_passes(self) -> None:
        result = validate_candles(m1_series(5), "M1", now_epoch=NOW)
        assert result.data_status == "ok"
        assert len(result.candles) == 5
        assert result.malformed_count == 0
        assert result.invalid_ohlc_count == 0
        assert all(c["time_utc"].endswith("+00:00") for c in result.candles)

    def test_high_below_close_excluded(self) -> None:
        rows = m1_series(3)
        rows[1] = {**rows[1], "high": rows[1]["close"] - 0.001}  # high < close
        result = validate_candles(rows, "M1", now_epoch=NOW)
        assert result.invalid_ohlc_count == 1
        assert len(result.candles) == 2
        codes = {w.code for w in result.warnings}
        assert "INVALID_OHLC" in codes
        assert result.data_status == "partial"

    def test_low_above_open_excluded(self) -> None:
        rows = m1_series(3)
        rows[2] = {**rows[2], "low": rows[2]["open"] + 0.001}  # low > open
        result = validate_candles(rows, "M1", now_epoch=NOW)
        assert result.invalid_ohlc_count == 1
        assert len(result.candles) == 2

    def test_high_below_low_excluded(self) -> None:
        rows = m1_series(2)
        rows[0] = {**rows[0], "high": rows[0]["low"] - 0.01}
        result = validate_candles(rows, "M1", now_epoch=NOW)
        assert result.invalid_ohlc_count == 1

    def test_nan_price_is_malformed(self) -> None:
        rows = m1_series(3)
        rows[0] = {**rows[0], "close": float("nan")}
        result = validate_candles(rows, "M1", now_epoch=NOW)
        assert result.malformed_count == 1
        assert len(result.candles) == 2
        assert {w.code for w in result.warnings} >= {"MALFORMED_CANDLES"}

    def test_infinite_price_is_malformed(self) -> None:
        rows = m1_series(2)
        rows[1] = {**rows[1], "open": math.inf}
        result = validate_candles(rows, "M1", now_epoch=NOW)
        assert result.malformed_count == 1

    def test_non_numeric_price_is_malformed(self) -> None:
        rows = m1_series(2)
        rows[1] = {**rows[1], "open": "1.09"}
        result = validate_candles(rows, "M1", now_epoch=NOW)
        assert result.malformed_count == 1

    def test_non_positive_price_is_malformed(self) -> None:
        rows = m1_series(2)
        rows[0] = {**rows[0], "close": 0.0}
        result = validate_candles(rows, "M1", now_epoch=NOW)
        assert result.malformed_count == 1

    def test_missing_time_is_malformed(self) -> None:
        rows = m1_series(2)
        del rows[0]["time"]
        result = validate_candles(rows, "M1", now_epoch=NOW)
        assert result.malformed_count == 1

    def test_non_dict_row_is_malformed(self) -> None:
        result = validate_candles(["junk", *m1_series(2)], "M1", now_epoch=NOW)
        assert result.malformed_count == 1
        assert len(result.candles) == 2

    def test_all_malformed_rejected_as_empty(self) -> None:
        rows = [{**c, "high": -5} for c in m1_series(3)]
        result = validate_candles(rows, "M1", now_epoch=NOW)
        assert result.data_status == "empty"
        assert any(w.code == "EMPTY_AFTER_VALIDATION" for w in result.warnings)


class TestOrderingAndDuplicates:
    def test_unsorted_input_sorted_output(self) -> None:
        rows = m1_series(4)[::-1]
        result = validate_candles(rows, "M1", now_epoch=NOW)
        times = [c["time"] for c in result.candles]
        assert times == sorted(times)
        assert any(w.code == "TIMESTAMPS_UNSORTED" for w in result.warnings)

    def test_duplicates_keep_last(self) -> None:
        rows = m1_series(3)
        t0 = rows[0]["time"]
        # Two OHLC-consistent candles sharing a timestamp (differing closes).
        stale_first = make_candle(t0, 1.1000, 0.5, high=1.1002, low=0.4)
        fresher = make_candle(t0, 1.1000, 2.0, high=2.1, low=0.9)
        result = validate_candles([stale_first, *rows[1:], fresher], "M1", now_epoch=NOW)
        assert result.duplicate_count == 1
        target = [c for c in result.candles if c["time"] == t0]
        assert target and target[0]["close"] == 2.0
        assert any(w.code == "DUPLICATE_TIMESTAMPS" for w in result.warnings)


class TestGaps:
    def test_missing_bar_reported_not_filled(self) -> None:
        rows = m1_series(4)
        with_gap = [rows[0], rows[1], rows[3]]  # skip rows[2]
        result = validate_candles(with_gap, "M1", now_epoch=NOW)
        assert result.gap_count == 1
        assert len(result.candles) == 3  # NOTHING was back-filled
        gap_warning = next(w for w in result.warnings if w.code == "GAPS_DETECTED")
        assert "not" in gap_warning.message.lower()

    def test_weekend_gap_tolerated_for_d1(self) -> None:
        # Friday open, then Monday open (3 calendar days later) is normal.
        friday = 1700006400  # a Tuesday-ish epoch; what matters is the delta
        rows = [
            make_candle(friday, 1.1, 1.11),
            make_candle(friday + 3 * 86400, 1.11, 1.12),
        ]
        result = validate_candles(rows, "D1", now_epoch=friday + 3 * 86400 + 80000)
        # 3-day delta = 3 intervals > 4.5 * 1 interval? No: 3 < 4.5 -> tolerated.
        assert result.gap_count == 0

    def test_real_d1_gap_flagged(self) -> None:
        t0 = 1700006400
        rows = [make_candle(t0, 1.1, 1.11), make_candle(t0 + 6 * 86400, 1.11, 1.12)]
        result = validate_candles(rows, "D1", now_epoch=t0 + 6 * 86400 + 80000)
        assert result.gap_count == 1


class TestFormingVsClosed:
    def test_last_candle_on_current_interval_is_forming(self) -> None:
        rows = m1_series(3)  # last open == floor(NOW/60)*60
        result = validate_candles(rows, "M1", now_epoch=NOW)
        assert result.forming_count == 1
        assert result.candles[-1]["forming"] is True
        assert result.candles[0]["forming"] is False
        assert any(w.code == "FORMING_CANDLE_INCLUDED" for w in result.warnings)

    def test_closed_only_excludes_forming(self) -> None:
        rows = m1_series(3)
        result = validate_candles(rows, "M1", now_epoch=NOW, closed_only=True)
        assert result.excluded_forming == 1
        assert all(c["forming"] is False for c in result.candles)
        assert any(w.code == "FORMING_CANDLE_EXCLUDED" for w in result.warnings)

    def test_all_candles_closed_when_now_is_next_interval(self) -> None:
        end_open = (NOW // 60 * 60) - 60  # last candle closed exactly at NOW boundary
        rows = m1_series(3, end_open=end_open)
        result = validate_candles(rows, "M1", now_epoch=NOW)
        assert result.forming_count == 0
        assert all(c["forming"] is False for c in result.candles)

    def test_server_time_ahead_flagged(self) -> None:
        end_open = (NOW // 60 * 60) + 600  # opens 10 minutes in the future
        rows = m1_series(2, end_open=end_open)
        result = validate_candles(rows, "M1", now_epoch=NOW)
        assert result.server_time_skew_seconds is not None
        assert any(w.code == "SERVER_TIME_AHEAD" for w in result.warnings)


class TestStaleness:
    def test_fresh_history_not_stale(self) -> None:
        end_open = (NOW // 60 * 60) - 120  # closed 1 interval ago
        rows = m1_series(3, end_open=end_open)
        result = validate_candles(rows, "M1", now_epoch=NOW)
        assert result.is_stale is False
        assert result.data_status == "ok"

    def test_old_history_stale(self) -> None:
        end_open = (NOW // 60 * 60) - 60 * 10  # closed ~9 minutes ago
        rows = m1_series(3, end_open=end_open)
        result = validate_candles(rows, "M1", now_epoch=NOW)
        assert result.is_stale is True
        assert result.data_status == "stale"
        stale = next(w for w in result.warnings if w.code == "STALE_DATA")
        assert "tick" in stale.message.lower()

    def test_d1_weekend_not_stale(self) -> None:
        # Friday candle, "now" is Monday shortly after open.
        friday_open = 1700006400 - (1700006400 % 86400)
        # Choose a real Friday: 2023-11-17 00:00 UTC = 1700179200
        friday = 1700179200
        monday = friday + 3 * 86400
        rows = [make_candle(friday, 1.1, 1.11)]
        result = validate_candles(rows, "D1", now_epoch=monday + 3600)
        assert result.is_stale is False

    def test_d1_beyond_weekend_stale(self) -> None:
        friday = 1700179200
        # A week after the last D1 candle: beyond weekend + holiday allowance.
        next_friday = friday + 8 * 86400
        rows = [make_candle(friday, 1.1, 1.11)]
        result = validate_candles(rows, "D1", now_epoch=next_friday)
        assert result.is_stale is True


class TestInsufficientHistory:
    def test_short_history_reported(self) -> None:
        rows = m1_series(3)
        result = validate_candles(rows, "M1", now_epoch=NOW, requested_count=100)
        assert result.data_status == "insufficient"
        assert any(w.code == "INSUFFICIENT_HISTORY" for w in result.warnings)


class TestEmptyPayloads:
    def test_none_payload(self) -> None:
        result = validate_candles(None, "M1", now_epoch=NOW)
        assert result.data_status == "empty"
        assert result.candles == []

    def test_empty_list_payload(self) -> None:
        result = validate_candles([], "M1", now_epoch=NOW)
        assert result.data_status == "empty"
        assert any(w.code == "EMPTY_RESPONSE" for w in result.warnings)


class TestVolumePreference:
    def test_real_volume_preferred_when_positive(self) -> None:
        rows = [{**make_candle(NOW - 60, 1.1, 1.1001), "real_volume": 5000}]
        result = validate_candles(rows, "M1", now_epoch=NOW)
        assert result.candles[0]["volume"] == 5000

    def test_tick_volume_fallback(self) -> None:
        rows = [make_candle(NOW - 60, 1.1, 1.1001, volume=42)]
        result = validate_candles(rows, "M1", now_epoch=NOW)
        assert result.candles[0]["volume"] == 42

    def test_garbage_volume_is_none(self) -> None:
        rows = [{**make_candle(NOW - 60, 1.1, 1.1001), "tick_volume": None,
                 "real_volume": "bad"}]
        result = validate_candles(rows, "M1", now_epoch=NOW)
        assert result.candles[0]["volume"] is None


class TestWarningShape:
    def test_warning_dicts_have_required_keys(self) -> None:
        rows = m1_series(2)
        rows[0] = {**rows[0], "high": -1}
        result = validate_candles(rows, "M1", now_epoch=NOW)
        for warning in result.warning_dicts:
            assert set(warning) == {"code", "severity", "message"}
            assert warning["code"]
            assert warning["severity"] in {"info", "warning", "error"}
