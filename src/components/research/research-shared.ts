/**
 * Shared display helpers for the research workspace (Phase 8).
 *
 * Same honesty contract as the backtest workspace: every number comes
 * from the backend's persisted payload; null renders verbatim as
 * "Not available"; only presentational derivations happen here.
 */

import type { ResearchRunStatus, ResearchValidationMode } from "@/lib/api/types";

export const RESEARCH_STATUS_META: Record<
  ResearchRunStatus,
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

export const VALIDATION_MODE_META: Record<
  ResearchValidationMode,
  { label: string; description: string }
> = {
  in_sample_only: {
    label: "In-sample only",
    description:
      "Every candidate is evaluated on the whole window. No out-of-sample claims exist — treat all rankings as in-sample fits.",
  },
  holdout: {
    label: "Holdout (IS → OOS)",
    description:
      "Candidates are selected on the in-sample portion; the held-out tail is walked once with the frozen winner.",
  },
  walk_forward: {
    label: "Walk-forward",
    description:
      "Chronological windows: selection on the training part only, frozen parameters tested on the next period.",
  },
};

export const OBJECTIVE_OPTIONS: {
  value: string;
  label: string;
  hint: string;
  needsTrades: boolean;
}[] = [
  {
    value: "net_pnl",
    label: "Net P&L (after costs)",
    hint: "Simulated net profit in account currency.",
    needsTrades: true,
  },
  {
    value: "expectancy",
    label: "Expectancy per trade",
    hint: "Mean P&L per decided trade.",
    needsTrades: true,
  },
  {
    value: "profit_factor",
    label: "Profit factor",
    hint: "Gross profit / gross loss (undefined when either side is absent).",
    needsTrades: true,
  },
  {
    value: "avg_return_pct_per_trade",
    label: "Avg return % per trade",
    hint: "Mean per-trade return in percent of risk/stake.",
    needsTrades: true,
  },
  {
    value: "max_drawdown_pct",
    label: "Max drawdown % (minimized)",
    hint: "Largest peak-to-trough equity decline; lower is better.",
    needsTrades: true,
  },
  {
    value: "win_rate",
    label: "Win rate (not recommended)",
    hint: "wins / (wins + losses) — ignores risk/reward sizes.",
    needsTrades: true,
  },
  {
    value: "conditions_met_count",
    label: "Setup trigger count",
    hint: "Frequency only — NOT a performance metric (setup-only runs).",
    needsTrades: false,
  },
];

/** null-safe number formatting (mirrors the backtest workspace). */
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

export function fmtScore(
  metric: string,
  value: number | null | undefined,
): string {
  if (value === null || value === undefined) return "Not available";
  if (metric === "win_rate") return fmtPct(value * 100);
  if (metric === "max_drawdown_pct") return fmtPct(value);
  if (metric === "avg_return_pct_per_trade") return fmtPct(value);
  return fmtNum(value);
}

export function errorText(e: unknown): string {
  const err = e as { message?: string; details?: { path?: string; message?: string }[] };
  if (err && typeof err === "object" && err.message) {
    const detail = Array.isArray(err.details) && err.details.length
      ? ` (${err.details.map((d) => `${d.path || ""} ${d.message ?? ""}`).join("; ")})`
      : "";
    return `${err.message}${detail}`;
  }
  return e instanceof Error ? e.message : "Unexpected error";
}

/** Human label for a condition/field pair ("px.params.value" -> "px · value"). */
export function fieldLabel(conditionId: string, field: string): string {
  if (field === "lookback") return `${conditionId} · lookback`;
  if (field.startsWith("params.")) return `${conditionId} · ${field.slice("params.".length)}`;
  return `${conditionId} · ${field}`;
}

/**
 * Client-side estimate of the candidate count (the authoritative bound is
 * the server cascade). Includes candidate 0 (the original parameterization).
 */
export function estimateCombinations(
  parameters: { values: (number | string | boolean)[] }[],
): number {
  if (parameters.length === 0) return 0;
  return parameters.reduce((acc, p) => acc * Math.max(1, p.values.length), 1) + 1;
}

/**
 * Rough walk budget estimate mirroring the server's creation check — used
 * ONLY for the "this configuration is too large" warning; the backend
 * stays authoritative.
 */
export function estimateWalks(input: {
  combinations: number;
  validationMode: ResearchValidationMode;
  windows: number;
  costVariants: number;
  paramCount: number;
  paramSensitivity: boolean;
  bootstrap: boolean;
}): number {
  const { combinations, validationMode, windows, costVariants, paramCount, paramSensitivity, bootstrap } =
    input;
  const perCandidate = validationMode === "walk_forward" ? Math.max(1, windows) : 1;
  const baselinePhases = validationMode === "walk_forward" ? windows : validationMode === "holdout" ? 2 : 1;
  const variantWalks =
    validationMode === "walk_forward"
      ? windows * costVariants
      : validationMode === "holdout"
        ? 2 * costVariants
        : costVariants;
  const neighbours = paramSensitivity && validationMode !== "walk_forward" ? 2 * paramCount : 0;
  const bootstraps = bootstrap ? (validationMode === "holdout" ? 2 : 1) : 0;
  return combinations * perCandidate + baselinePhases + variantWalks + neighbours + bootstraps;
}
