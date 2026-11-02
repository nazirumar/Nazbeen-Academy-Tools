"""Unit tests: timeframe / mode profiles."""

from __future__ import annotations

import pytest

from app.domains.market_data.timeframes import (
    MODE_TIMEFRAME_PROFILES,
    SUPPORTED_MODES,
    SUPPORTED_TIMEFRAMES,
    is_timeframe_supported,
    timeframes_for_mode,
    timeframe_seconds,
    validate_mode_timeframes,
)


def test_forex_defaults_match_product_spec() -> None:
    assert MODE_TIMEFRAME_PROFILES["forex"] == ["D1", "H4", "H1", "M15", "M5", "M1"]


def test_binary_options_is_m1_only() -> None:
    assert MODE_TIMEFRAME_PROFILES["binary_options"] == ["M1"]


def test_supported_timeframes_whitelist() -> None:
    assert set(SUPPORTED_TIMEFRAMES) == {"M1", "M5", "M15", "H1", "H4", "D1"}


def test_timeframe_seconds_mapping() -> None:
    assert timeframe_seconds("M1") == 60
    assert timeframe_seconds("H4") == 4 * 3600
    assert timeframe_seconds("D1") == 86400


def test_unknown_timeframe_raises() -> None:
    with pytest.raises(ValueError):
        timeframe_seconds("W1")
    assert not is_timeframe_supported("W1")


def test_unknown_mode_raises() -> None:
    with pytest.raises(ValueError):
        timeframes_for_mode("stocks")


def test_mode_validation_messages() -> None:
    errors = validate_mode_timeframes("binary_options", ["H1", "M1"])
    assert any("M1" in e for e in errors)
    errors = validate_mode_timeframes("forex", ["D1", "W1"])
    assert any("W1" in e for e in errors)
    errors = validate_mode_timeframes("forex", ["H1", "H1"])
    assert any("duplicates" in e for e in errors)
    assert validate_mode_timeframes("forex", []) == []  # empty allowed (rules layer enforces non-empty)
    assert set(SUPPORTED_MODES) == {"forex", "binary_options"}
