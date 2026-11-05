import { ApiError, type PlatformMode, type ReplaySessionStatus } from "@/lib/api/types";

export const REPLAY_STATUS_META: Record<
  ReplaySessionStatus,
  { label: string; className: string }
> = {
  active: {
    label: "Playing",
    className:
      "border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  },
  paused: {
    label: "Paused",
    className:
      "border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300",
  },
  completed: {
    label: "Completed",
    className:
      "border-slate-300 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300",
  },
};

export function defaultReplayTimeframe(mode: PlatformMode): string {
  return mode === "binary_options" ? "M1" : "M15";
}

function toDatetimeLocal(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return (
    `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}` +
    `T${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`
  );
}

/** A conservative default: the last 7 UTC days, clearly labelled in the UI. */
export function defaultReplayWindow(): { start: string; end: string } {
  const end = new Date();
  end.setUTCSeconds(0, 0);
  const start = new Date(end.getTime() - 7 * 24 * 60 * 60 * 1000);
  return { start: toDatetimeLocal(start), end: toDatetimeLocal(end) };
}

/** Treat a datetime-local value as UTC (the form labels it explicitly). */
export function inputValueToIso(value: string): string {
  if (!value) return "";
  const withSeconds = value.length === 16 ? `${value}:00` : value;
  const hasZone = /[zZ]|[+-]\d{2}:?\d{2}$/.test(withSeconds);
  const parsed = new Date(hasZone ? withSeconds : `${withSeconds}Z`);
  return Number.isNaN(parsed.getTime()) ? "" : parsed.toISOString();
}

export function isoToInputValue(value: string | null | undefined): string {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "" : toDatetimeLocal(parsed);
}

export function fmtReplayTime(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return `${parsed.toISOString().replace("T", " ").slice(0, 19)} UTC`;
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.details.length
      ? ` (${error.details
          .map((item) => `${item.path || ""} ${item.message ?? ""}`.trim())
          .join("; ")})`
      : "";
    return `${error.message}${detail}`;
  }
  return error instanceof Error ? error.message : "Unexpected error";
}
