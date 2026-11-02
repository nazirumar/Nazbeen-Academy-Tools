/**
 * Shared display helpers for the backtest workspace (Phase 6).
 *
 * The frontend NEVER derives market values: every number shown comes from
 * the backend's persisted run payload. Only presentational derivations
 * (e.g. a drawdown series from the backend's own equity path) happen here,
 * and "not available" (null) is rendered verbatim as Not available.
 */

import type { BacktestRunStatus, TradeOutcome } from "@/lib/api/types";

export const RUN_STATUS_META: Record<
  BacktestRunStatus,
  { label: string; className: string }
> = {
  queued: {
    label: "Queued",
    className: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  },
  running: {
    label: "Running",
    className: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  },
  completed: {
    label: "Completed",
    className: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  },
  failed: {
    label: "Failed",
    className: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
  },
  cancelled: {
    label: "Cancelled",
    className: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  },
};

export const OUTCOME_META: Record<
  TradeOutcome,
  { label: string; className: string }
> = {
  win: { label: "Win", className: "text-emerald-600 dark:text-emerald-400" },
  loss: { label: "Loss", className: "text-red-500" },
  tie: { label: "Tie", className: "text-amber-600 dark:text-amber-400" },
  open: { label: "Open", className: "text-slate-500 dark:text-slate-400" },
  unresolved: { label: "Unresolved", className: "text-slate-500 dark:text-slate-400" },
  ambiguous: { label: "Ambiguous", className: "text-amber-600 dark:text-amber-400" },
  skipped: { label: "Skipped", className: "text-slate-500 dark:text-slate-400" },
};

/** null-safe number formatting: null/undefined render as "Not available". */
export function fmtNum(
  value: number | null | undefined,
  digits = 2,
  suffix = "",
): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "Not available";
  }
  return `${value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}${suffix}`;
}

export function fmtPct(value: number | null | undefined, digits = 1): string {
  return fmtNum(value, digits, "%");
}

export function fmtUtc(iso: string | null | undefined): string {
  if (!iso) return "—";
  return iso.replace("T", " ").slice(0, 16) + " UTC";
}

/**
 * Presentational derivation from the backend's own equity path (closed
 * trade balances): running peak and peak-to-trough drawdown per point.
 * Returns [] when the backend produced no equity series.
 */
export function drawdownSeries(
  points: { time_unix: number; equity: number }[],
): { time_unix: number; drawdown: number; drawdown_pct: number }[] {
  let peak = points.length ? points[0].equity : 0;
  return points.map((p) => {
    peak = Math.max(peak, p.equity);
    const dd = peak - p.equity;
    return {
      time_unix: p.time_unix,
      drawdown: dd,
      drawdown_pct: peak > 0 ? (dd / peak) * 100 : 0,
    };
  });
}

/** Build a download URL for the CSV export (keeps gateway routing intact). */
export function exportUrl(path: string, query?: Record<string, string>): string {
  const search = new URLSearchParams(query ?? {});
  const port = process.env.NEXT_PUBLIC_BACKEND_PORT ?? "8000";
  const origin = (process.env.NEXT_PUBLIC_BACKEND_ORIGIN ?? "").replace(/\/$/, "");
  if (!origin && !search.has("XTransformPort")) search.set("XTransformPort", port);
  const qs = search.toString();
  return `${origin}${path}${qs ? `?${qs}` : ""}`;
}
