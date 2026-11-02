"""Unit tests: deterministic backtest engine, simulators, metrics, export.

Focus areas (Phase 6 section 12):

* chronological processing, zero look-ahead (future-data leakage tests),
* swing/structure confirmation timing,
* closed vs forming candles, duplicate/missing candles,
* unsupported/insufficient rules never treated as success,
* Forex cost assumptions + ambiguous SL/TP ordering,
* Binary expiry, payout, tie handling,
* metric denominators + "Not available" semantics,
* deterministic reproducibility,
* CSV formula-injection protection.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domains.backtest import csv_export
from app.domains.backtest.binary import evaluate_trade as binary_evaluate
from app.domains.backtest.engine import (
    CancelledError,
    _TimeframeSeries,
    run_walk,
)
from app.domains.backtest.forex import (
    open_trade,
    resolve_exit,
    settle,
    infer_pip_size,
)
from app.domains.backtest.metrics import compute_metrics, max_drawdown
from app.domains.backtest.schemas import (
    BacktestConfigPayload,
    BinaryOptionsConfig,
    ForexExecutionConfig,
)
from app.domains.backtest.data import assess_series_quality
from app.domains.strategies.rules import validate_rule_set

START = 1_700_000_000  # 2023-11-14 22:13:20 UTC (arbitrary, M1-aligned enough)
STEP = 60


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def candle(i: int, o: float, h: float, low: float, c: float, forming: bool | None = None, step: int = STEP, start: int = START) -> dict:
    row = {
        "time": start + i * step,
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "volume": None,
    }
    if forming is not None:
        row["forming"] = forming
    return row


def flat_series(
    closes: list[float], start: int = START, step: int = STEP, spread: float = 0.5
) -> list[dict]:
    """OHLC where each candle touches only its own close +- spread."""

    rows = []
    for i, c in enumerate(closes):
        rows.append(candle(i, c - 0.1, c + spread, c - spread, c, step=step, start=start))
    return rows


def config_payload(
    rule_set: dict,
    *,
    mode: str = "forex",
    timeframe: str = "M1",
    start: int = START + 20 * STEP,
    end: int | None = None,
    candles: int = 10,
    forex: ForexExecutionConfig | None = None,
    binary: BinaryOptionsConfig | None = None,
) -> BacktestConfigPayload:
    if end is None:
        end = start + candles * STEP
    payload = {
        "mode": mode,
        "symbol": "EURUSD",
        "timeframe": timeframe,
        "start": datetime.fromtimestamp(start, tz=timezone.utc).isoformat(
            timespec="milliseconds"
        ),
        "end": datetime.fromtimestamp(end, tz=timezone.utc).isoformat(
            timespec="milliseconds"
        ),
        "warmup_candles": 150,
        "max_candles": 20000,
        "data_source": "postgres_candle_cache",
        "strategy_id": "00000000-0000-0000-0000-000000000001",
        "strategy_name": "Test Strategy",
        "strategy_version": 1,
        "strategy_version_status": "published",
        "rule_set": rule_set,
        "direction": rule_set.get("direction", "bullish"),
        "notes": None,
        "forex_execution": forex.model_dump() if forex else None,
        "binary": binary.model_dump() if binary else None,
        "simulation_mode": "setup_only",
    }
    return BacktestConfigPayload.model_validate(payload)


def price_rule(direction: str = "bullish", value: float = 100.0, operator: str = "above") -> dict:
    return validate_rule_set(
        {
            "schema_version": "1.0",
            "mode": "forex",
            "timeframes": ["M1"],
            "direction": direction,
            "conditions": {
                "logic": "AND",
                "conditions": [
                    {
                        "id": "c1",
                        "type": "price_level",
                        "field": "close",
                        "operator": operator,
                        "value": value,
                        "timeframe": "M1",
                        "lookback": 3,
                    }
                ],
                "groups": [],
            },
        }
    )


def swing_rule(kind: str = "high", strength: int = 1, mode: str = "forex") -> dict:
    return validate_rule_set(
        {
            "schema_version": "2.0",
            "mode": mode,
            "timeframes": ["M1"],
            "direction": "bullish",
            "conditions": {
                "logic": "AND",
                "conditions": [
                    {
                        "id": "sw",
                        "type": "swing_point",
                        "params": {"kind": kind, "strength": strength},
                        "timeframe": "M1",
                        "lookback": 10,
                    }
                ],
                "groups": [],
            },
        }
    )


# --------------------------------------------------------------------------- #
# Chronological processing + point-in-time windows
# --------------------------------------------------------------------------- #


class TestChronology:
    def test_records_strictly_chronological(self) -> None:
        rule_set = price_rule(value=1.0)  # always met
        closes = [100 + i for i in range(60)]
        series = {"M1": _TimeframeSeries("M1", flat_series(closes))}
        output = run_walk(
            config=config_payload(rule_set.to_dict(), candles=30),
            rule_set=rule_set,
            series_by_tf=series,
        )
        times = [r["result_time"] for r in output.records]
        assert times == sorted(times)
        assert len(set(times)) == len(times)  # no duplicate evaluations
        setups = [r for r in output.records if r["kind"] == "setup"]
        # [start, end] is inclusive on both ends: 30*STEP + 1 candles.
        assert len(setups) == 31

    def test_evaluation_instant_is_close_time(self) -> None:
        rule_set = price_rule(value=1.0)
        series = {"M1": _TimeframeSeries("M1", flat_series([100] * 40))}
        output = run_walk(
            config=config_payload(rule_set.to_dict(), candles=5),
            rule_set=rule_set,
            series_by_tf=series,
        )
        first = output.records[0]
        assert first["result_time"] == START + 20 * STEP + STEP  # close of candle 20

    def test_no_duplicate_evaluation_of_same_candle(self) -> None:
        rule_set = price_rule(value=1.0)
        series = {"M1": _TimeframeSeries("M1", flat_series([100] * 40))}
        output = run_walk(
            config=config_payload(rule_set.to_dict(), candles=10),
            rule_set=rule_set,
            series_by_tf=series,
        )
        setup_times = [r["result_time"] for r in output.records if r["kind"] == "setup"]
        assert len(setup_times) == len(set(setup_times))


# --------------------------------------------------------------------------- #
# Look-ahead bias (the critical section)
# --------------------------------------------------------------------------- #


class TestLookAhead:
    def test_future_closes_never_leak_into_earlier_evaluation(self) -> None:
        # closes alternate after index 21; the rule "close above 105" must
        # be met EXACTLY when the current close is > 105 even though many
        # LATER candles close above 105 (a leaked future window would mark
        # every evaluation met).
        closes = [104.0] * 20 + [104.9] + [90.0, 130.0, 140.0, 150.0] * 10
        rule_set = price_rule(value=105.0)
        series = {"M1": _TimeframeSeries("M1", flat_series(closes))}
        output = run_walk(
            config=config_payload(rule_set.to_dict(), candles=25),
            rule_set=rule_set,
            series_by_tf=series,
        )
        setups = [r for r in output.records if r["kind"] == "setup"]
        assert len(setups) == 26
        for i in range(20, 46):
            record = next(
                r for r in setups if r["result_time"] == START + (i + 1) * STEP
            )
            expected = "conditions_met" if closes[i] > 105.0 else "conditions_missing"
            assert record["setup_status"] == expected, (i, record["setup_status"])

    def test_higher_timeframe_forming_candle_is_invisible(self) -> None:
        # M15 primary + H1 secondary. The H1 candle covering the M15
        # evaluation instant is still forming: its CLOSE must never be
        # visible. The rule requires the H1 close above 100; the third H1
        # candle closes at 130 only at START+10800, and every M15
        # evaluation below happens BEFORE that instant.
        h1_step = 3600
        h1_closes = [90.0, 99.0, 130.0]  # third H1 candle closes at +3h
        h1_rows = []
        for i, c in enumerate(h1_closes):
            h1_rows.append(
                candle(i, c - 0.1, c + 5, c - 5, c, step=h1_step)
            )
        m15_rows = flat_series([99.0] * 40, step=900)
        series = {
            "M15": _TimeframeSeries("M15", m15_rows),
            "H1": _TimeframeSeries("H1", h1_rows),
        }
        rule_dict = {
            "schema_version": "1.0",
            "mode": "forex",
            "timeframes": ["M15", "H1"],
            "direction": "bullish",
            "conditions": {
                "logic": "AND",
                "conditions": [
                    {
                        "id": "htf",
                        "type": "price_level",
                        "field": "close",
                        "operator": "above",
                        "value": 100.0,
                        "timeframe": "H1",
                        "lookback": 2,
                    }
                ],
                "groups": [],
            },
        }
        rule_set = validate_rule_set(rule_dict)
        cfg = config_payload(
            rule_set.to_dict(),
            timeframe="M15",
            start=START + 3600,
            end=START + 9000,
            forex=None,
        )
        output = run_walk(config=cfg, rule_set=rule_set, series_by_tf=series)
        # Every M15 evaluation happens before the third H1 close: the
        # visible H1 closes are 90 and 99 only -> never met. A leaked
        # forming H1 candle (or its final 130 close) would flip results.
        assert all(
            r["setup_status"] == "conditions_missing"
            for r in output.records
            if r["kind"] == "setup"
        )

    def test_structure_break_not_backdated_before_confirmation(self) -> None:
        # A swing LOW (5.0 at index 1) confirmed only at index 2 must not
        # produce a bearish break (close below it) earlier than its
        # confirmation instant.
        rows = [
            candle(0, 10, 10.5, 9.5, 10),
            candle(1, 6, 10.5, 5.0, 6.0),   # swing-low candidate (low 5)
            candle(2, 11, 11.5, 11.0, 11.0),  # confirms the swing low
            candle(3, 5.5, 10.5, 4.0, 4.5),   # close 4.5 breaks below 5
            candle(4, 5.5, 10.5, 4.0, 4.8),
        ]
        rule_dict = {
            "schema_version": "2.0",
            "mode": "forex",
            "timeframes": ["M1"],
            "direction": "bearish",
            "conditions": {
                "logic": "AND",
                "conditions": [
                    {
                        "id": "bos",
                        "type": "market_structure_break",
                        "params": {"strength": 1, "side": "bearish"},
                        "timeframe": "M1",
                        "lookback": 10,
                    }
                ],
                "groups": [],
            },
        }
        rule_set = validate_rule_set(rule_dict)
        series = {"M1": _TimeframeSeries("M1", rows)}
        cfg = config_payload(
            rule_set.to_dict(),
            start=START,
            end=START + 4 * STEP,
        )
        output = run_walk(config=cfg, rule_set=rule_set, series_by_tf=series)
        setups = [r for r in output.records if r["kind"] == "setup"]
        # Confirmation instant = close time of candle index 2.
        confirm_instant = START + 2 * STEP + STEP
        for record in setups:
            if record["result_time"] < confirm_instant:
                # missing OR inconclusive (early windows lack lookback
                # history) — but NEVER met, and never a break event.
                assert record["setup_status"] in (
                    "conditions_missing",
                    "inconclusive",
                ), record
        # After confirmation the break below the swing low resolves.
        assert any(
            r["setup_status"] == "conditions_met" for r in setups
        )

    def test_swing_confirmation_timing_respected_in_engine(self) -> None:
        # Spike at index 5 (high 30), strength 1 -> confirmed by candle 6.
        highs = [10, 10, 10, 10, 10, 30, 9, 9, 9, 9, 9]
        # Valid OHLC: high always >= open/close; the spike stands out.
        rows = [candle(i, 10, max(h, 10.5), 9.5, 10) for i, h in enumerate(highs)]
        rule_set = swing_rule("high", strength=1)
        series = {"M1": _TimeframeSeries("M1", rows)}
        cfg = config_payload(rule_set.to_dict(), start=START + 1 * STEP, end=START + 10 * STEP)
        output = run_walk(config=cfg, rule_set=rule_set, series_by_tf=series)
        setups = [r for r in output.records if r["kind"] == "setup"]
        confirm_instant = START + 6 * STEP + STEP  # close of candle 6
        for record in setups:
            if record["result_time"] < confirm_instant:
                assert record["setup_status"] in (
                    "conditions_missing",
                    "inconclusive",
                ), record
            else:
                assert record["setup_status"] == "conditions_met", record


# --------------------------------------------------------------------------- #
# Closed vs forming, duplicates, gaps
# --------------------------------------------------------------------------- #


class TestSeriesIntegrity:
    def test_forming_candles_excluded_from_walk(self) -> None:
        rows = flat_series([100] * 30)
        rows.append(candle(30, 100, 100.5, 99.5, 100, forming=True))
        series = {"M1": _TimeframeSeries("M1", rows)}
        rule_set = price_rule(value=1.0)
        output = run_walk(
            config=config_payload(rule_set.to_dict(), candles=5),
            rule_set=rule_set,
            series_by_tf=series,
        )
        assert all(r["kind"] == "setup" for r in output.records)
        # Evaluations never reach the forming candle's close instant.
        forming_close = START + 30 * STEP + STEP
        assert all(r["result_time"] != forming_close for r in output.records)

    def test_duplicates_removed_and_reported(self) -> None:
        rows = flat_series([100] * 25)
        rows.append(dict(rows[-1]))  # exact duplicate timestamp
        series = _TimeframeSeries("M1", rows)
        assert series.duplicate_count == 1
        assert len(series.candles) == 25

    def test_gap_reported_in_quality(self) -> None:
        rows = flat_series([100] * 20)
        # remove candles 20,21 -> gap of 2 intervals before index 22
        rows.extend(flat_series([100] * 3, start=START + 22 * STEP))
        s = _TimeframeSeries("M1", rows)
        quality = assess_series_quality(
            s.candles,
            "M1",
            datetime.fromtimestamp(START, tz=timezone.utc),
            datetime.fromtimestamp(START + 24 * STEP, tz=timezone.utc),
        )
        assert quality.gap_count >= 1
        codes = {w["code"] for w in quality.warnings}
        assert "DATA_GAPS" in codes

    def test_invalid_ohlc_rows_excluded(self) -> None:
        rows = flat_series([100] * 10)
        rows.append(candle(10, 100, 90, 95, 100))  # high < low -> invalid
        series = _TimeframeSeries("M1", rows)
        assert len(series.candles) == 10


# --------------------------------------------------------------------------- #
# Insufficient / unsupported data semantics
# --------------------------------------------------------------------------- #


class TestInsufficientAndUnsupported:
    def test_missing_secondary_timeframe_is_insufficient_not_success(self) -> None:
        rule_dict = {
            "schema_version": "1.0",
            "mode": "forex",
            "timeframes": ["M1", "H1"],
            "direction": "bullish",
            "conditions": {
                "logic": "AND",
                "conditions": [
                    {
                        "id": "htf",
                        "type": "price_level",
                        "field": "close",
                        "operator": "above",
                        "value": 50.0,
                        "timeframe": "H1",
                        "lookback": 2,
                    }
                ],
                "groups": [],
            },
        }
        rule_set = validate_rule_set(rule_dict)
        series = {"M1": _TimeframeSeries("M1", flat_series([100] * 30))}
        output = run_walk(
            config=config_payload(rule_set.to_dict(), candles=5),
            rule_set=rule_set,
            series_by_tf={"M1": series["M1"], "H1": _TimeframeSeries("H1", [])},
        )
        statuses = {r["setup_status"] for r in output.records}
        assert statuses == {"insufficient_data"}

    def test_cancellation_stops_walk(self) -> None:
        rule_set = price_rule(value=1.0)
        series = {"M1": _TimeframeSeries("M1", flat_series([100] * 60))}
        state = {"calls": 0}

        def cancel_after_two() -> None:
            state["calls"] += 1
            if state["calls"] > 2:
                raise CancelledError()

        with pytest.raises(CancelledError):
            run_walk(
                config=config_payload(rule_set.to_dict(), candles=10),
                rule_set=rule_set,
                series_by_tf=series,
                cancel_check=cancel_after_two,
            )


# --------------------------------------------------------------------------- #
# Forex simulator
# --------------------------------------------------------------------------- #


class TestForexSimulator:
    def cfg(self, **kw) -> ForexExecutionConfig:
        defaults = dict(
            enabled=True,
            spread_pips=1.0,
            slippage_pips=0.5,
            commission_per_lot_per_side=0.0,
            stop_loss_pips=2.0,
            take_profit_pips=4.0,
            risk_mode="fixed_lot",
            fixed_lot_size=1.0,
            pip_value_per_lot=1.0,
            pip_size=1.0,  # 1 pip == 1.0 price unit for test readability
            account_start_balance=1000.0,
            sl_tp_same_candle_policy="conservative",
        )
        defaults.update(kw)
        return ForexExecutionConfig(**defaults)

    def test_entry_prices_declared_costs(self) -> None:
        cfg = self.cfg()
        signal = candle(0, 99.5, 100.5, 99.0, 100.0)
        long = open_trade(
            cfg, seq=0, direction="long", signal_candle=signal, entry_time=STEP,
            balance=1000.0, pip_size=1.0,
        )
        assert long.entry_price == 101.5  # close 100 + spread 1 + slip 0.5
        assert long.stop_price == 99.5
        assert long.target_price == 105.5
        short = open_trade(
            cfg, seq=1, direction="short", signal_candle=signal, entry_time=STEP,
            balance=1000.0, pip_size=1.0,
        )
        assert short.entry_price == 99.5  # close 100 - slippage
        assert short.stop_price == 101.5
        assert short.target_price == 95.5

    def test_take_profit_fill_next_candle(self) -> None:
        cfg = self.cfg(commission_per_lot_per_side=1.0)
        signal = candle(0, 99.5, 100.5, 99.0, 100.0)
        trade = open_trade(
            cfg, seq=0, direction="long", signal_candle=signal, entry_time=STEP,
            balance=1000.0, pip_size=1.0,
        )
        exits = [candle(1, 104, 106, 103.5, 105.5)]
        trade = resolve_exit(trade, exits, 0, cfg, 1.0)
        assert trade.exit_reason == "take_profit"
        assert trade.exit_price == 105.5  # passive limit: no slippage
        trade = settle(trade, cfg, 1.0, 1000.0)
        # pnl = 4 pips * 1 lot * 1 pip_value - 2*1 commission
        assert trade.pnl == 2.0
        assert trade.outcome == "win"

    def test_stop_loss_slips_adversely(self) -> None:
        cfg = self.cfg(slippage_pips=0.5)
        signal = candle(0, 99.5, 100.5, 99.0, 100.0)
        trade = open_trade(
            cfg, seq=0, direction="long", signal_candle=signal, entry_time=STEP,
            balance=1000.0, pip_size=1.0,
        )
        exits = [candle(1, 100.0, 100.2, 98.0, 98.5)]
        trade = resolve_exit(trade, exits, 0, cfg, 1.0)
        assert trade.exit_reason == "stop_loss"
        assert trade.exit_price == 99.0  # stop 99.5 - slip 0.5
        trade = settle(trade, cfg, 1.0, 1000.0)
        assert trade.pnl == -2.5  # (99.0 - 101.5)
        assert trade.outcome == "loss"

    def test_same_candle_sl_tp_conservative_default(self) -> None:
        cfg = self.cfg(sl_tp_same_candle_policy="conservative")
        signal = candle(0, 99.5, 100.5, 99.0, 100.0)
        trade = open_trade(
            cfg, seq=0, direction="long", signal_candle=signal, entry_time=STEP,
            balance=1000.0, pip_size=1.0,
        )
        # One candle touches BOTH stop (99.5) and target (105.5).
        exits = [candle(1, 99.0, 106.0, 98.0, 102.0)]
        trade = resolve_exit(trade, exits, 0, cfg, 1.0)
        assert trade.exit_reason == "stop_loss"
        trade = settle(trade, cfg, 1.0, 1000.0)
        assert trade.outcome == "loss"

    def test_same_candle_sl_tp_optimistic_policy(self) -> None:
        cfg = self.cfg(sl_tp_same_candle_policy="optimistic")
        signal = candle(0, 99.5, 100.5, 99.0, 100.0)
        trade = open_trade(
            cfg, seq=0, direction="long", signal_candle=signal, entry_time=STEP,
            balance=1000.0, pip_size=1.0,
        )
        exits = [candle(1, 99.0, 106.0, 98.0, 102.0)]
        trade = resolve_exit(trade, exits, 0, cfg, 1.0)
        assert trade.exit_reason == "take_profit"
        trade = settle(trade, cfg, 1.0, 1000.0)
        assert trade.outcome == "win"

    def test_same_candle_sl_tp_ambiguous_policy_excluded(self) -> None:
        cfg = self.cfg(sl_tp_same_candle_policy="ambiguous")
        signal = candle(0, 99.5, 100.5, 99.0, 100.0)
        trade = open_trade(
            cfg, seq=0, direction="long", signal_candle=signal, entry_time=STEP,
            balance=1000.0, pip_size=1.0,
        )
        exits = [candle(1, 99.0, 106.0, 98.0, 102.0)]
        trade = resolve_exit(trade, exits, 0, cfg, 1.0)
        assert trade.ambiguous is True
        trade = settle(trade, cfg, 1.0, 1000.0)
        assert trade.pnl is None
        assert trade.outcome == "ambiguous"

    def test_open_at_data_end_is_not_realized(self) -> None:
        cfg = self.cfg()
        signal = candle(0, 99.5, 100.5, 99.0, 100.0)
        trade = open_trade(
            cfg, seq=0, direction="long", signal_candle=signal, entry_time=STEP,
            balance=1000.0, pip_size=1.0,
        )
        exits = [candle(1, 100.0, 101.0, 100.2, 100.8)]
        trade = resolve_exit(trade, exits, 0, cfg, 1.0)
        assert trade.exit_reason is None
        trade = settle(trade, cfg, 1.0, 1000.0)
        assert trade.pnl is None
        assert trade.outcome == "open"

    def test_fixed_fractional_sizing_and_swap(self) -> None:
        cfg = self.cfg(
            risk_mode="fixed_fractional",
            risk_pct_per_trade=1.0,
            stop_loss_pips=10.0,
            pip_value_per_lot=10.0,
            swap_per_lot_per_night=-0.5,
            account_start_balance=10_000.0,
        )
        signal = candle(0, 99.5, 100.5, 99.0, 100.0)
        trade = open_trade(
            cfg, seq=0, direction="long", signal_candle=signal, entry_time=START + STEP,
            balance=10_000.0, pip_size=1.0,
        )
        assert trade.risk_amount == 100.0
        assert trade.lots == 1.0  # 100 / (10 * 10)
        # Exit 2 UTC days later (candle 2879 closes at START+2 days).
        exits = [candle(2879, 104, 106, 103.5, 104.5)]
        trade = resolve_exit(trade, exits, 0, cfg, 1.0)
        trade = settle(trade, cfg, 1.0, 10_000.0)
        assert trade.swap == -1.0  # -0.5 * 1.0 lots * 2 nights
        # pnl = 4*1*10 - swap 1 = 39
        assert trade.pnl == 39.0

    def test_engine_suppresses_entries_while_position_open(self) -> None:
        rule_set = price_rule(value=1.0)
        # spread 0.2 keeps every low above the stop and every high below
        # the target -> the first trade can NEVER resolve and suppresses
        # all later entries.
        rows = flat_series([100.0] * 40, spread=0.2)
        forex = self.cfg(sl_tp_same_candle_policy="ambiguous")
        series = {"M1": _TimeframeSeries("M1", rows)}
        output = run_walk(
            config=config_payload(
                rule_set.to_dict(), candles=15, forex=forex
            ),
            rule_set=rule_set,
            series_by_tf=series,
        )
        trades = [r for r in output.records if r["kind"] == "trade"]
        assert len(trades) == 1  # first entry never resolves -> stays open
        assert trades[0]["outcome"] == "open"
        assert trades[0]["pnl"] is None

    def test_pip_size_inference(self) -> None:
        assert infer_pip_size("EURUSD", None) == 0.0001
        assert infer_pip_size("USDJPY", None) == 0.01
        assert infer_pip_size("EURUSD", 0.5) == 0.5


# --------------------------------------------------------------------------- #
# Binary simulator
# --------------------------------------------------------------------------- #


class TestBinarySimulator:
    def base_cfg(self, **kw) -> BinaryOptionsConfig:
        defaults = dict(expiry_candles=1, payout_pct=0.8, stake=10.0,
                        tie_policy="refund", fees_per_trade=0.0)
        defaults.update(kw)
        return BinaryOptionsConfig(**defaults)

    def test_call_win(self) -> None:
        cfg = self.base_cfg()
        candles = flat_series([100.0, 101.0])
        trade = binary_evaluate(
            cfg, seq=0, direction="bullish", signal_candle=candles[0],
            interval=STEP, candles=candles, signal_index=0,
        )
        assert trade.outcome == "win"
        assert trade.pnl == 8.0  # 10 * 0.8

    def test_put_win(self) -> None:
        cfg = self.base_cfg()
        candles = flat_series([100.0, 99.0])
        trade = binary_evaluate(
            cfg, seq=0, direction="bearish", signal_candle=candles[0],
            interval=STEP, candles=candles, signal_index=0,
        )
        assert trade.outcome == "win"
        assert trade.pnl == 8.0

    def test_loss(self) -> None:
        cfg = self.base_cfg()
        candles = flat_series([100.0, 99.0])
        trade = binary_evaluate(
            cfg, seq=0, direction="bullish", signal_candle=candles[0],
            interval=STEP, candles=candles, signal_index=0,
        )
        assert trade.outcome == "loss"
        assert trade.pnl == -10.0

    def test_tie_refund_default(self) -> None:
        cfg = self.base_cfg()
        candles = flat_series([100.0, 100.0])
        trade = binary_evaluate(
            cfg, seq=0, direction="bullish", signal_candle=candles[0],
            interval=STEP, candles=candles, signal_index=0,
        )
        assert trade.outcome == "tie"
        assert trade.pnl == 0.0

    def test_tie_loss_policy(self) -> None:
        cfg = self.base_cfg(tie_policy="loss")
        candles = flat_series([100.0, 100.0])
        trade = binary_evaluate(
            cfg, seq=0, direction="bullish", signal_candle=candles[0],
            interval=STEP, candles=candles, signal_index=0,
        )
        assert trade.outcome == "tie"
        assert trade.pnl == -10.0

    def test_fees_subtracted(self) -> None:
        cfg = self.base_cfg(fees_per_trade=0.5)
        candles = flat_series([100.0, 101.0])
        trade = binary_evaluate(
            cfg, seq=0, direction="bullish", signal_candle=candles[0],
            interval=STEP, candles=candles, signal_index=0,
        )
        assert trade.pnl == 7.5

    def test_direction_both_never_traded(self) -> None:
        cfg = self.base_cfg()
        candles = flat_series([100.0, 101.0])
        trade = binary_evaluate(
            cfg, seq=0, direction="both", signal_candle=candles[0],
            interval=STEP, candles=candles, signal_index=0,
        )
        assert trade.outcome == "skipped"
        assert trade.pnl is None
        assert "CALL/PUT" in (trade.skip_reason or "")

    def test_expiry_beyond_data_unresolved(self) -> None:
        cfg = self.base_cfg(expiry_candles=3)
        candles = flat_series([100.0, 101.0])
        trade = binary_evaluate(
            cfg, seq=0, direction="bullish", signal_candle=candles[0],
            interval=STEP, candles=candles, signal_index=0,
        )
        assert trade.outcome == "unresolved"
        assert trade.expiry_price is None

    def test_expiry_candle_count_uses_index_not_clock(self) -> None:
        # Candle-count semantics: gaps do not shift the expiry candle.
        cfg = self.base_cfg(expiry_candles=2)
        candles = flat_series([100.0, 50.0, 101.0, 102.0])
        # a gap between index 0 and 1 would exist in real data; the expiry
        # candle is still index 2 by count.
        trade = binary_evaluate(
            cfg, seq=0, direction="bullish", signal_candle=candles[0],
            interval=STEP, candles=candles, signal_index=0,
        )
        assert trade.expiry_candle_time == candles[2]["time"]
        assert trade.outcome == "win"


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #


class TestMetrics:
    def test_no_trades_means_not_available(self) -> None:
        metrics = compute_metrics(
            mode="forex",
            setup_records=[],
            trade_records=[],
            total_candles_evaluated=0,
            simulation_mode="setup_only",
        )
        trades = metrics["trades"]
        assert trades["win_rate"] is None
        assert trades["net_pnl"] is None
        assert trades["profit_factor"] is None
        assert metrics["equity"]["max_drawdown"] is None

    def test_win_rate_denominator_excludes_ties(self) -> None:
        records = [
            {"seq": 1, "result_time": 1, "outcome": "win", "pnl": 8.0,
             "return_pct": 0.8, "regime": None, "direction": "bullish",
             "execution": {}, "equity_after": 1008.0},
            {"seq": 2, "result_time": 2, "outcome": "win", "pnl": 8.0,
             "return_pct": 0.8, "regime": None, "direction": "bullish",
             "execution": {}, "equity_after": 1016.0},
            {"seq": 3, "result_time": 3, "outcome": "loss", "pnl": -10.0,
             "return_pct": -1.0, "regime": None, "direction": "bullish",
             "execution": {}, "equity_after": 1006.0},
            {"seq": 4, "result_time": 4, "outcome": "tie", "pnl": 0.0,
             "return_pct": 0.0, "regime": None, "direction": "bullish",
             "execution": {}, "equity_after": 1006.0},
            {"seq": 5, "result_time": 5, "outcome": "unresolved", "pnl": None,
             "return_pct": None, "regime": None, "direction": "bullish",
             "execution": {}, "equity_after": None},
        ]
        metrics = compute_metrics(
            mode="binary_options",
            setup_records=[],
            trade_records=records,
            total_candles_evaluated=10,
            simulation_mode="trade_simulation",
        )
        trades = metrics["trades"]
        assert trades["win_rate"] == pytest.approx(2 / 3)
        assert trades["decided_trades"] == 3
        assert trades["net_pnl"] == 6.0
        # gross_profit 16, gross_loss -10 -> factor 1.6
        assert trades["profit_factor"] == 1.6
        assert metrics["equity"]["max_drawdown"]["max_drawdown"] == 10.0

    def test_profit_factor_none_without_losses(self) -> None:
        records = [
            {"seq": 1, "result_time": 1, "outcome": "win", "pnl": 5.0,
             "return_pct": 0.5, "regime": None, "direction": "bullish",
             "execution": {}, "equity_after": 1005.0},
        ]
        metrics = compute_metrics(
            mode="forex", setup_records=[], trade_records=records,
            total_candles_evaluated=5, simulation_mode="trade_simulation",
        )
        assert metrics["trades"]["profit_factor"] is None

    def test_max_drawdown(self) -> None:
        assert max_drawdown([]) is None
        assert max_drawdown([100.0]) is None
        dd = max_drawdown([100.0, 120.0, 90.0, 110.0])
        assert dd["max_drawdown"] == 30.0
        assert dd["max_drawdown_pct"] == pytest.approx(25.0)

    def test_forex_pnl_never_from_setup_counts(self) -> None:
        # setup-only runs have NO financial metrics even with 100 setups.
        setup_rows = [
            {"seq": i, "result_time": i, "setup_status": "conditions_met",
             "direction": "bullish", "regime": "uptrend", "evaluation": {}}
            for i in range(100)
        ]
        metrics = compute_metrics(
            mode="forex", setup_records=setup_rows, trade_records=[],
            total_candles_evaluated=100, simulation_mode="setup_only",
        )
        assert metrics["trades"]["net_pnl"] is None
        assert metrics["setups"]["by_status"]["conditions_met"] == 100
        assert metrics["simulation_mode"] == "setup_only"

    def test_breakdowns_by_regime_and_session(self) -> None:
        records = [
            {"seq": 1, "result_time": START, "outcome": "win", "pnl": 8.0,
             "return_pct": 0.8, "regime": "uptrend", "direction": "bullish",
             "execution": {}, "equity_after": 1008.0},
        ]
        metrics = compute_metrics(
            mode="forex", setup_records=[], trade_records=records,
            total_candles_evaluated=5, simulation_mode="trade_simulation",
        )
        by_regime = metrics["trades"]["breakdowns"]["by_regime"]
        assert by_regime[0]["key"] == "uptrend"
        assert by_regime[0]["net_pnl"] == 8.0
        assert "by_session" in metrics["trades"]["breakdowns"]


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


class TestDeterminism:
    def test_identical_inputs_identical_outputs(self) -> None:
        rule_set = swing_rule("high", 1)
        highs = [10, 12, 11, 13, 12, 14, 13, 15, 14, 16, 15, 14, 13, 12,
                 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24,
                 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36]
        rows = [candle(i, 10, max(h, 10.5), 9.5, 10) for i, h in enumerate(highs)]
        forex = ForexExecutionConfig(
            enabled=True, stop_loss_pips=2.0, take_profit_pips=3.0,
            risk_mode="fixed_lot", fixed_lot_size=0.1, pip_size=1.0,
            pip_value_per_lot=1.0, spread_pips=1.0, slippage_pips=0.5,
        )
        series = {"M1": _TimeframeSeries("M1", rows)}
        cfg = config_payload(rule_set.to_dict(), candles=10, forex=forex)
        out1 = run_walk(config=cfg, rule_set=rule_set, series_by_tf=series)
        out2 = run_walk(config=cfg, rule_set=rule_set, series_by_tf=series)
        assert out1.records == out2.records
        assert out1.metrics == out2.metrics
        assert out1.data_quality == out2.data_quality

    def test_evaluated_at_is_simulated_not_wall_clock(self) -> None:
        rule_set = price_rule(value=1.0)
        series = {"M1": _TimeframeSeries("M1", flat_series([100] * 30))}
        output = run_walk(
            config=config_payload(rule_set.to_dict(), candles=3),
            rule_set=rule_set,
            series_by_tf=series,
        )
        for record in output.records:
            if record["kind"] == "setup":
                expected = datetime.fromtimestamp(
                    record["result_time"], tz=timezone.utc
                ).isoformat(timespec="milliseconds")
                assert record["evaluation"]["evaluated_at"] == expected


# --------------------------------------------------------------------------- #
# Engine-level binary + setup-only integration of the walk
# --------------------------------------------------------------------------- #


class TestEngineWalkModes:
    def test_binary_walk_one_trade_per_met_setup(self) -> None:
        rule_dict = {
            "schema_version": "1.0",
            "mode": "binary_options",
            "timeframes": ["M1"],
            "direction": "bullish",
            "conditions": {
                "logic": "AND",
                "conditions": [
                    {
                        "id": "c1",
                        "type": "price_level",
                        "field": "close",
                        "operator": "above",
                        "value": 50.0,
                        "timeframe": "M1",
                        "lookback": 3,
                    }
                ],
                "groups": [],
            },
        }
        rule_set = validate_rule_set(rule_dict)
        # Ascending closes: every evaluation is met and every expiry close
        # is one pip above its entry close -> every trade wins.
        rows = flat_series([100.0 + i for i in range(36)])
        binary = BinaryOptionsConfig(expiry_candles=1, payout_pct=0.8, stake=10.0)
        series = {"M1": _TimeframeSeries("M1", rows)}
        cfg = config_payload(
            rule_set.to_dict(), mode="binary_options", candles=10, binary=binary
        )
        output = run_walk(config=cfg, rule_set=rule_set, series_by_tf=series)
        trades = [r for r in output.records if r["kind"] == "trade"]
        assert len(trades) == 11  # inclusive window: 10*STEP + 1 candles
        assert all(t["outcome"] == "win" for t in trades)
        # balance: start 1000 + 11 * 8 = 1088
        assert trades[-1]["equity_after"] == 1088.0

    def test_setup_only_run_has_no_trades(self) -> None:
        rule_set = price_rule(value=1.0)
        series = {"M1": _TimeframeSeries("M1", flat_series([100.0] * 40))}
        output = run_walk(
            config=config_payload(rule_set.to_dict(), candles=8, forex=None),
            rule_set=rule_set,
            series_by_tf=series,
        )
        assert all(r["kind"] == "setup" for r in output.records)
        assert output.metrics["simulation_mode"] == "setup_only"
        assert output.metrics["trades"]["total"] == 0


# --------------------------------------------------------------------------- #
# CSV export
# --------------------------------------------------------------------------- #


class TestCsvExport:
    def test_formula_injection_neutralised(self) -> None:
        assert csv_export.safe_cell("=SUM(A1:A2)") == "'=SUM(A1:A2)"
        assert csv_export.safe_cell("+1") == "'+1"
        assert csv_export.safe_cell("-2") == "'-2"
        assert csv_export.safe_cell("@cmd") == "'@cmd"
        assert csv_export.safe_cell("normal") == "normal"

    def test_results_csv_includes_assumptions_and_records(self) -> None:
        run_row = {
            "id": "run-1",
            "strategy_name": "Breakout = risky",
            "strategy_version": 3,
            "mode": "forex",
            "symbol": "EURUSD",
            "timeframe": "M15",
            "backtest_schema_version": "1.0",
            "engine_version": "6.0.0",
            "exported_at": "2026-11-02T00:00:00Z",
            "config": {
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-02-01T00:00:00Z",
                "data_source": "postgres_candle_cache",
                "simulation_mode": "trade_simulation",
                "strategy_version_status": "published",
                "forex_execution": {"spread_pips": 1.0, "slippage_pips": 0.5},
            },
        }
        records = [
            {
                "seq": 0,
                "result_time": "2026-01-05T10:00:00Z",
                "kind": "setup",
                "setup_status": "conditions_met",
                "direction": "bullish",
                "regime": "uptrend",
                "outcome": None,
                "pnl": None,
                "return_pct": None,
                "equity_after": None,
                "evaluation": {"conditions_summary": {"met": 2, "not_met": 1}},
                "execution": None,
                "warnings": [{"code": "DATA_GAPS", "message": "gap of 2 intervals"}],
                "note": "=SUM(A1:A2)",
            },
            {
                "seq": 1,
                "result_time": "2026-01-05T10:15:00Z",
                "kind": "trade",
                "setup_status": "conditions_met",
                "direction": "bullish",
                "regime": "uptrend",
                "outcome": "win",
                "pnl": 12.5,
                "return_pct": 0.0125,
                "equity_after": 1012.5,
                "evaluation": None,
                "execution": {"mode": "forex", "entry_price": 1.1},
                "warnings": None,
                "note": None,
            },
        ]
        text = csv_export.results_csv(run_row, records)
        lines = text.splitlines()
        assert lines[0] == "# backtest_export"
        assert any("strategy_version,3" in ln for ln in lines)
        assert any("assumption,forex.spread_pips=1.0" in ln for ln in lines)
        # formula injection in text cells is neutralised
        assert "'=SUM(A1:A2)" in text
        # the strategy name is exported verbatim (no leading formula char)
        assert "Breakout = risky" in text
        # numeric columns remain numeric
        assert ",12.5," in text

    def test_summary_csv_flattens_metrics(self) -> None:
        run_row = {
            "id": "run-1",
            "strategy_name": "S",
            "strategy_version": 1,
            "mode": "binary_options",
            "symbol": "EURUSD",
            "timeframe": "M1",
            "config": {
                "start": "a", "end": "b",
                "data_source": "postgres_candle_cache",
                "simulation_mode": "trade_simulation",
            },
            "metrics": {
                "trades": {"win_rate": 0.75, "net_pnl": 12.5,
                           "win_rate_note": "win_rate = wins / (wins + losses)"},
            },
        }
        text = csv_export.summary_csv(run_row)
        assert "metric.trades.win_rate,0.75" in text
        assert "metric.trades.net_pnl,12.5" in text
        assert "win_rate_note" not in text
