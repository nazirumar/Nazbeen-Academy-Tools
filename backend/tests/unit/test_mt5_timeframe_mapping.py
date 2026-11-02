"""Unit tests: extensible MT5 timeframe mapping."""

from __future__ import annotations

import pytest

from app.domains.market_data import mt5_timeframes as mtf
from app.domains.market_data.mt5_timeframes import (
    MT5_TIMEFRAME_CONSTANTS,
    default_timeframes_for_mode,
    resolve_mt5_timeframe,
)
from app.domains.market_data.timeframes import (
    MODE_TIMEFRAME_PROFILES,
    SUPPORTED_TIMEFRAMES,
    UnsupportedTimeframeError,
)
from tests.fakes.fake_mt5 import FakeMT5


class TestMapping:
    def test_all_platform_timeframes_map_to_fake_constants(self) -> None:
        fake = FakeMT5()
        for tf in SUPPORTED_TIMEFRAMES:
            value = resolve_mt5_timeframe(fake, tf)
            assert isinstance(value, int)

    def test_resolved_values_match_mt5_constants(self) -> None:
        fake = FakeMT5()
        assert resolve_mt5_timeframe(fake, "M1") == fake.TIMEFRAME_M1
        assert resolve_mt5_timeframe(fake, "H1") == fake.TIMEFRAME_H1
        assert resolve_mt5_timeframe(fake, "D1") == fake.TIMEFRAME_D1

    def test_every_whitelisted_code_has_a_mapping_entry(self) -> None:
        for tf in SUPPORTED_TIMEFRAMES:
            assert tf in MT5_TIMEFRAME_CONSTANTS

    def test_unsupported_timeframe_rejected(self) -> None:
        fake = FakeMT5()
        with pytest.raises(UnsupportedTimeframeError):
            resolve_mt5_timeframe(fake, "M30")  # mapped but not platform-enabled

    def test_mapping_is_extensible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Extending = whitelist entry + mapping entry; no code changes."""

        fake = FakeMT5()
        monkeypatch.setattr(
            mtf, "PLATFORM_MT5_TIMEFRAMES", (*mtf.PLATFORM_MT5_TIMEFRAMES, "M30")
        )
        assert resolve_mt5_timeframe(fake, "M30") == fake.TIMEFRAME_M30

    def test_missing_constant_in_package_reported(self) -> None:
        class BrokenModule:
            pass  # no TIMEFRAME_* attributes at all

        with pytest.raises(UnsupportedTimeframeError, match="does not define"):
            resolve_mt5_timeframe(BrokenModule(), "M1")


class TestModeDefaults:
    def test_forex_default_ladder(self) -> None:
        assert default_timeframes_for_mode("forex") == ["D1", "H4", "H1", "M15", "M5", "M1"]

    def test_binary_options_default_is_m1_only(self) -> None:
        assert default_timeframes_for_mode("binary_options") == ["M1"]

    def test_defaults_come_from_configuration_data(self) -> None:
        """The Binary Options mode never auto-requests higher timeframes."""

        assert set(MODE_TIMEFRAME_PROFILES["binary_options"]) == {"M1"}

    def test_unknown_mode_raises(self) -> None:
        with pytest.raises(UnsupportedTimeframeError):
            default_timeframes_for_mode("stocks")
