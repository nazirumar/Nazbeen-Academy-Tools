/**
 * Platform-level helpers shared across the UI. Mirrors the backend
 * timeframes module; the live whitelists are always fetched from
 * GET /api/v1/meta (these constants are only optimistic defaults).
 */

import type {
  ConditionStatus,
  ModeProfile,
  PlatformMode,
  SetupStatus,
} from "@/lib/api/types";

export const MODES: ModeProfile[] = [
  { name: "forex", default_timeframes: ["D1", "H4", "H1", "M15", "M5", "M1"] },
  { name: "binary_options", default_timeframes: ["M1"] },
];

export const MODE_LABELS: Record<PlatformMode, string> = {
  forex: "Forex",
  binary_options: "Binary Options",
};

export function timeframesForMode(mode: PlatformMode): string[] {
  return MODES.find((m) => m.name === mode)?.default_timeframes ?? [];
}

export function isValidMode(value: string): value is PlatformMode {
  return value === "forex" || value === "binary_options";
}

export const SETUP_STATUS_META: Record<
  SetupStatus,
  { label: string; tone: "success" | "warning" | "danger" | "muted" }
> = {
  conditions_met: { label: "Conditions met", tone: "success" },
  conditions_missing: { label: "Conditions missing", tone: "warning" },
  invalidated: { label: "Invalidated", tone: "danger" },
  insufficient_data: { label: "Insufficient data", tone: "muted" },
  inconclusive: { label: "Inconclusive", tone: "muted" },
  regime_mismatch: { label: "Regime mismatch", tone: "warning" },
};

/**
 * Phase 4: distinct, accessible visual states for the four condition
 * outcomes. `not_met` (evaluated and failed) is visually DIFFERENT from
 * `inconclusive` (could not be evaluated) and `unsupported` (no engine
 * implementation) — unknown conditions are never shown as success.
 */
export const CONDITION_STATUS_META: Record<
  ConditionStatus,
  { label: string; className: string }
> = {
  met: {
    label: "met",
    className: "text-emerald-600 dark:text-emerald-400",
  },
  not_met: {
    label: "not met",
    className: "text-red-500",
  },
  inconclusive: {
    label: "inconclusive",
    className: "text-amber-600 dark:text-amber-400",
  },
  unsupported: {
    label: "unsupported",
    className: "text-slate-500 dark:text-slate-400",
  },
};

/** Format a unix-seconds timestamp for evidence tables (UTC, no locale). */
export function formatUnixTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const d = new Date(seconds * 1000);
  return d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
}
