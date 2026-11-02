"""CSV export for backtest results — with spreadsheet formula protection.

* Authentication + ownership are enforced by the router/service (the
  export function receives owner-scoped rows only).
* Formula-injection protection: any TEXT cell whose value could be
  interpreted as a formula by spreadsheet software (leading ``=``, ``+``,
  ``-``, ``@``, TAB or CR) is prefixed with a single quote (OWASP
  guidance). NUMERIC cells are emitted as plain numbers — negative
  numbers in numeric columns stay numeric.
* The export leads with a declared assumptions block (strategy version,
  window, symbol/timeframe, data source, simulation assumptions, schema
  versions) so no exported table can be mistaken for verified broker
  records or a profitability guarantee.
"""

from __future__ import annotations

import csv
import io
from typing import Any

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> str:
    """Sanitize a TEXT cell against CSV formula injection."""

    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _num(value: Any) -> str:
    """Format a numeric cell (None -> empty, never fabricated zero)."""

    if value is None:
        return ""
    return f"{value:g}" if isinstance(value, float) else str(value)


_RESULT_COLUMNS: list[tuple[str, str]] = [
    # (header, extractor key)
    ("seq", "seq"),
    ("time_utc", "result_time"),
    ("kind", "kind"),
    ("setup_status", "setup_status"),
    ("direction", "direction"),
    ("regime", "regime"),
    ("outcome", "outcome"),
    ("pnl", "pnl"),
    ("return_pct", "return_pct"),
    ("equity_after", "equity_after"),
]


def _execution_text(execution: dict[str, Any] | None) -> str:
    if not execution:
        return ""
    parts: list[str] = []
    for key in (
        "mode",
        "side",
        "direction",
        "entry_time",
        "entry_price",
        "expiry_time",
        "expiry_reference_price",
        "exit_time",
        "exit_price",
        "exit_reason",
        "lots",
        "stake",
        "pnl",
    ):
        value = execution.get(key)
        if value is not None:
            parts.append(f"{key}={safe_cell(value)}")
    return "; ".join(parts)


def _warnings_text(warnings: list[dict[str, Any]] | None) -> str:
    if not warnings:
        return ""
    return " | ".join(
        f"{w.get('code', 'WARNING')}: {w.get('message', '')}" for w in warnings
    )


def results_csv(
    run_row: dict[str, Any],
    records: list[dict[str, Any]],
) -> str:
    """Build the full export: assumptions block + chronological records."""

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    config = run_row.get("config") or {}
    assumptions = []
    forex = config.get("forex_execution") or {}
    binary = config.get("binary") or {}
    if forex:
        assumptions.extend(
            f"forex.{key}={safe_cell(value)}"
            for key, value in sorted(forex.items())
            if key != "note" and value is not None
        )
    if binary:
        assumptions.extend(
            f"binary.{key}={safe_cell(value)}"
            for key, value in sorted(binary.items())
            if value is not None
        )

    writer.writerow(["# backtest_export"])
    writer.writerow(["export_generated_at_utc", safe_cell(run_row.get("exported_at"))])
    writer.writerow(["run_id", safe_cell(run_row.get("id"))])
    writer.writerow(["strategy_id", safe_cell(run_row.get("strategy_id"))])
    writer.writerow(["strategy_name", safe_cell(run_row.get("strategy_name"))])
    writer.writerow(["strategy_version", safe_cell(run_row.get("strategy_version"))])
    writer.writerow(
        ["strategy_version_status", safe_cell((config or {}).get("strategy_version_status"))]
    )
    writer.writerow(["mode", safe_cell(run_row.get("mode"))])
    writer.writerow(["symbol", safe_cell(run_row.get("symbol"))])
    writer.writerow(["timeframe", safe_cell(run_row.get("timeframe"))])
    writer.writerow(["window_start_utc", safe_cell(config.get("start"))])
    writer.writerow(["window_end_utc", safe_cell(config.get("end"))])
    writer.writerow(["data_source", safe_cell(config.get("data_source"))])
    writer.writerow(["simulation_mode", safe_cell(config.get("simulation_mode"))])
    writer.writerow(["backtest_schema_version", safe_cell(run_row.get("backtest_schema_version"))])
    writer.writerow(["engine_version", safe_cell(run_row.get("engine_version"))])
    for assumption in assumptions:
        writer.writerow(["assumption", assumption])
    writer.writerow(
        [
            "disclaimer",
            safe_cell(
                "Simulated results on stored MT5 candle data. Not broker "
                "execution records. Historical simulation statistics only — "
                "no prediction, no probability of profit, no guarantee."
            ),
        ]
    )
    writer.writerow([])

    writer.writerow([header for header, _ in _RESULT_COLUMNS] + [
        "conditions_summary",
        "execution_details",
        "warnings",
        "note",
    ])
    for record in records:
        evaluation = record.get("evaluation") or {}
        row = [safe_cell(record.get(key)) for _, key in _RESULT_COLUMNS]
        # Numeric columns stay numeric.
        for pos, (_, key) in enumerate(_RESULT_COLUMNS):
            if key in ("pnl", "return_pct", "equity_after"):
                row[pos] = _num(record.get(key))
            elif key == "seq":
                row[pos] = record.get("seq", "")
        conditions = evaluation.get("conditions_summary")
        row.append(
            "; ".join(
                f"{status}={count}" for status, count in sorted((conditions or {}).items())
            )
        )
        row.append(_execution_text(record.get("execution")))
        row.append(safe_cell(_warnings_text(record.get("warnings"))))
        row.append(safe_cell(record.get("note")))
        writer.writerow(row)
    return buffer.getvalue()


def summary_csv(run_row: dict[str, Any]) -> str:
    """Compact metrics export (same declared-assumptions header)."""

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    config = run_row.get("config") or {}
    metrics = run_row.get("metrics") or {}
    writer.writerow(["# backtest_summary"])
    writer.writerow(["run_id", safe_cell(run_row.get("id"))])
    writer.writerow(["strategy_name", safe_cell(run_row.get("strategy_name"))])
    writer.writerow(["strategy_version", safe_cell(run_row.get("strategy_version"))])
    writer.writerow(["mode", safe_cell(run_row.get("mode"))])
    writer.writerow(["symbol", safe_cell(run_row.get("symbol"))])
    writer.writerow(["timeframe", safe_cell(run_row.get("timeframe"))])
    writer.writerow(["window_start_utc", safe_cell(config.get("start"))])
    writer.writerow(["window_end_utc", safe_cell(config.get("end"))])
    writer.writerow(["data_source", safe_cell(config.get("data_source"))])
    writer.writerow(["simulation_mode", safe_cell(config.get("simulation_mode"))])
    writer.writerow([])

    def flatten(prefix: str, node: Any) -> list[tuple[str, Any]]:
        rows: list[tuple[str, Any]] = []
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("note", "win_rate_note", "expectancy_note",
                           "profit_factor_note", "avg_r_note"):
                    continue
                rows.extend(flatten(f"{prefix}.{key}" if prefix else key, value))
        elif isinstance(node, list):
            for i, value in enumerate(node):
                rows.extend(flatten(f"{prefix}[{i}]", value))
        else:
            rows.append((prefix, node))
        return rows

    for path, value in flatten("metric", metrics):
        if isinstance(value, (int,)) and not isinstance(value, bool):
            writer.writerow([safe_cell(path), value])
        elif isinstance(value, float):
            writer.writerow([safe_cell(path), _num(value)])
        else:
            writer.writerow([safe_cell(path), safe_cell(value)])
    writer.writerow([])
    writer.writerow(
        [
            "disclaimer",
            safe_cell(
                "Metrics derive from this historical simulation only; None "
                "means not available. Not future performance; not advice."
            ),
        ]
    )
    return buffer.getvalue()


__all__ = ["results_csv", "safe_cell", "summary_csv"]
