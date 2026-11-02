/**
 * Overlay derivation — the ONLY place that turns analysis results into
 * chart overlay geometry.
 *
 * Honesty rules (Phase 5):
 *
 * 1. Every overlay traces back to a fact the backend analysis engine
 *    returned (report `observations` or per-condition `evidence`). The
 *    frontend never re-computes swing detection, structure labels, breaks
 *    or zones, and never invents an overlay for a concept just because a
 *    toggle exists (`unsupportedKinds` reports engine gaps instead).
 * 2. `displayedTimeframe` filters everything: an observation from another
 *    timeframe is never drawn on this chart.
 * 3. HH/HL/LH/LL labels are aligned with the swing list the backend sent
 *    using index arithmetic documented in `labelForSwings` — no geometry
 *    is recomputed; when the labels cannot be aligned unambiguously no
 *    label is drawn.
 * 4. Swing confirmation timing is preserved (`confirmedAtTime`) so the UI
 *    can show that a swing was only KNOWN after its confirmation candle
 *    (no look-ahead).
 */

import type {
  AnalysisReport,
  ConditionEvidenceWithFvg,
  EvaluationNode,
  FvgGapEvidence,
  SwingObservation,
  SwingStructureObservation,
} from "@/lib/api/types";

export type OverlayTone = "bullish" | "bearish" | "neutral";

export type OverlayKind =
  | "swing_high"
  | "swing_low"
  | "structure_label"
  | "bos"
  | "mss_choch"
  | "fvg"
  | "price_zone";

export interface OverlaySource {
  analysisId: string;
  timeframe: string;
  /** Condition id when the overlay comes from a rule's evidence. */
  conditionId?: string;
}

export interface ChartMarkerOverlay {
  kind: "swing_high" | "swing_low" | "structure_label";
  id: string;
  time: number;
  price: number;
  /** HH / LH / HL / LL — only when alignable with the backend label list. */
  text?: string;
  /** Where the marker sits relative to the candle bar. */
  anchor: "above" | "below";
  /** Engine-recorded confirmation candle time (no look-ahead). */
  confirmedAtTime: number;
  tone: OverlayTone;
  source: OverlaySource;
}

export interface ChartLevelOverlay {
  kind: "bos" | "mss_choch";
  id: string;
  time1: number;
  time2: number;
  price: number;
  label: string;
  tone: OverlayTone;
  source: OverlaySource;
}

export interface ChartBandOverlay {
  kind: "fvg" | "price_zone";
  id: string;
  time1: number;
  time2: number;
  price1: number;
  price2: number;
  label: string;
  tone: OverlayTone;
  source: OverlaySource;
}

export interface OverlayCatalog {
  markers: ChartMarkerOverlay[];
  levels: ChartLevelOverlay[];
  bands: ChartBandOverlay[];
  /** Overlay kinds that have at least one feature in THIS report. */
  availableKinds: OverlayKind[];
  /** Concepts the engine does not provide for this report — never drawn. */
  unsupportedKinds: { kind: string; reason: string }[];
}

export const ALL_OVERLAY_KINDS: OverlayKind[] = [
  "swing_high",
  "swing_low",
  "structure_label",
  "bos",
  "mss_choch",
  "fvg",
  "price_zone",
];

export const OVERLAY_KIND_LABELS: Record<OverlayKind, string> = {
  swing_high: "Swing highs",
  swing_low: "Swing lows",
  structure_label: "HH / LH / HL / LL labels",
  bos: "Structure breaks (BOS)",
  mss_choch: "MSS / CHoCH levels",
  fvg: "Fair value gaps",
  price_zone: "Strategy price zones",
};

/**
 * Align engine structure labels with the swing list that was returned.
 *
 * The engine returns the LAST 5 swings per side and labels for the FULL
 * sequence (`labels[i]` compares swing i+1 against swing i). Two cases:
 * - all swings present (`labels.length === swings.length - 1`): `labels[i]`
 *   belongs to `swings[i + 1]`;
 * - truncated list (`labels.length >= swings.length`): the last
 *   `swings.length` labels pair 1:1 with the returned swings.
 * Anything else cannot be aligned honestly -> no labels.
 */
export function labelForSwings(
  swings: SwingObservation[],
  labels: SwingStructureObservation["labels"],
): (string | undefined)[] {
  if (swings.length === 0) return [];
  if (labels.length === swings.length - 1) {
    return [undefined, ...labels];
  }
  if (labels.length >= swings.length) {
    return labels.slice(labels.length - swings.length);
  }
  return swings.map(() => undefined);
}

function traverseConditions(
  node: EvaluationNode | null | undefined,
  visit: (evidence: ConditionEvidenceWithFvg, conditionId: string) => void,
): void {
  if (!node) return;
  if (node.kind === "condition") {
    visit(node.evidence as ConditionEvidenceWithFvg, node.id);
  }
  for (const child of node.children ?? []) traverseConditions(child, visit);
}

/**
 * Build the full overlay catalog for one chart from one persisted report.
 * Returns empty collections (plus the honest `unsupportedKinds`) when the
 * report has no observations — nothing is fabricated.
 */
export function buildOverlays(
  report: AnalysisReport | null | undefined,
  displayedTimeframe: string,
): OverlayCatalog {
  const unsupportedKinds = [
    {
      kind: "order_block",
      reason:
        "Order blocks are a planned rule type — the analysis engine does not detect them yet.",
    },
    {
      kind: "breaker_block",
      reason:
        "Breaker blocks are a planned rule type — the analysis engine does not detect them yet.",
    },
  ];
  if (!report || !displayedTimeframe) {
    return { markers: [], levels: [], bands: [], availableKinds: [], unsupportedKinds };
  }

  // Legacy reports may predate the typed observations block — optional
  // chaining keeps malformed/missing payloads honest (no overlays drawn).
  const obs = report.observations?.by_timeframe?.find(
    (o) => o.timeframe === displayedTimeframe,
  );

  const markers: ChartMarkerOverlay[] = [];
  const levels: ChartLevelOverlay[] = [];
  const bands: ChartBandOverlay[] = [];
  const sourceBase = { analysisId: report.analysis_id, timeframe: displayedTimeframe };

  // --- swings + structure labels ------------------------------------------
  // Swing markers and HH/LH/HL/LL labels are SEPARATE overlays so each can
  // be toggled independently: the label is additional text at the same
  // coordinate, never a replacement for the swing fact itself.
  if (obs?.swings) {
    const highLabels = labelForSwings(obs.swings.highs, obs.swings.high_structure.labels);
    obs.swings.highs.forEach((s, i) => {
      markers.push({
        kind: "swing_high",
        id: `swing_high:${s.time}:${s.price}`,
        time: s.time,
        price: s.price,
        anchor: "above",
        confirmedAtTime: s.confirmed_at_time,
        tone: "neutral",
        source: { ...sourceBase },
      });
      if (highLabels[i]) {
        markers.push({
          kind: "structure_label",
          id: `label_high:${s.time}:${s.price}`,
          time: s.time,
          price: s.price,
          text: highLabels[i],
          anchor: "above",
          confirmedAtTime: s.confirmed_at_time,
          tone: highLabels[i] === "HH" ? "bullish" : "bearish",
          source: { ...sourceBase },
        });
      }
    });
    const lowLabels = labelForSwings(obs.swings.lows, obs.swings.low_structure.labels);
    obs.swings.lows.forEach((s, i) => {
      markers.push({
        kind: "swing_low",
        id: `swing_low:${s.time}:${s.price}`,
        time: s.time,
        price: s.price,
        anchor: "below",
        confirmedAtTime: s.confirmed_at_time,
        tone: "neutral",
        source: { ...sourceBase },
      });
      if (lowLabels[i]) {
        markers.push({
          kind: "structure_label",
          id: `label_low:${s.time}:${s.price}`,
          time: s.time,
          price: s.price,
          text: lowLabels[i],
          anchor: "below",
          confirmedAtTime: s.confirmed_at_time,
          tone: lowLabels[i] === "HL" ? "bullish" : "bearish",
          source: { ...sourceBase },
        });
      }
    });
  }

  // --- structure breaks (BOS-style events) ---------------------------------
  for (const brk of obs?.structure_breaks ?? []) {
    levels.push({
      kind: "bos",
      id: `bos:${brk.time}:${brk.reference_time}`,
      time1: brk.reference_time,
      time2: brk.time,
      price: brk.level,
      label: brk.side === "bullish" ? "BOS ↑" : "BOS ↓",
      tone: brk.side,
      source: { ...sourceBase },
    });
  }

  // --- MSS / CHoCH levels from condition evidence --------------------------
  const mssSeen = new Set<string>();
  for (const root of [
    report.evaluation?.core,
    report.evaluation?.confirmation,
    report.evaluation?.invalidation,
  ]) {
    traverseConditions(root, (evidence, conditionId) => {
      if (evidence.condition_type !== "mss_choch") return;
      if (evidence.timeframe !== displayedTimeframe) return;
      if (typeof evidence.reference_swing_price !== "number") return;
      const key = `mss:${conditionId}:${evidence.reference_swing_price}`;
      if (mssSeen.has(key)) return;
      mssSeen.add(key);
      levels.push({
        kind: "mss_choch",
        id: key,
        time1:
          typeof evidence.reference_swing_time === "number"
            ? evidence.reference_swing_time
            : (obs?.completeness.first_time ?? 0),
        time2: obs?.completeness.last_time ?? 0,
        price: evidence.reference_swing_price,
        label: `MSS/CHoCH (${evidence.direction ?? "?"}, ${
          evidence.structure_shifted ? "shifted" : "not shifted"
        })`,
        tone: evidence.direction === "bullish" ? "bullish" : "bearish",
        source: { ...sourceBase, conditionId },
      });
    });
  }

  // --- FVG zones from condition evidence -----------------------------------
  const lastDataTime = obs?.completeness.last_time ?? null;
  const fvgSeen = new Set<string>();
  for (const root of [
    report.evaluation?.core,
    report.evaluation?.confirmation,
    report.evaluation?.invalidation,
  ]) {
    traverseConditions(root, (evidence, conditionId) => {
      if (evidence.condition_type !== "fvg") return;
      if (evidence.timeframe !== displayedTimeframe) return;
      for (const gap of evidence.recent_gaps ?? []) {
        const key = `fvg:${conditionId}:${gap.left_time}:${gap.gap_low}:${gap.gap_high}`;
        if (fvgSeen.has(key)) continue;
        fvgSeen.add(key);
        // A filled gap is only drawn over its origin window; an unfilled gap
        // extends to the last candle the report actually observed.
        const end =
          gap.filled || lastDataTime === null ? gap.created_at_time : lastDataTime;
        bands.push({
          kind: "fvg",
          id: key,
          time1: gap.left_time,
          time2: end,
          price1: gap.gap_low,
          price2: gap.gap_high,
          label: `FVG (${gap.direction}, ${gap.mitigation})`,
          tone: gap.direction,
          source: { ...sourceBase, conditionId },
        });
      }
    });
  }

  // --- user-defined strategy price zones -----------------------------------
  const first = obs?.completeness.first_time;
  const last = obs?.completeness.last_time;
  if (typeof first === "number" && typeof last === "number") {
    for (const zone of obs?.zones ?? []) {
      bands.push({
        kind: "price_zone",
        id: `zone:${zone.zone_low}:${zone.zone_high}`,
        time1: first,
        time2: last,
        price1: zone.zone_low,
        price2: zone.zone_high,
        label: `Strategy zone (${zone.position ?? "unknown"})`,
        tone: "neutral",
        source: { ...sourceBase },
      });
    }
  }

  const counts: Record<OverlayKind, number> = {
    swing_high: 0,
    swing_low: 0,
    structure_label: 0,
    bos: 0,
    mss_choch: 0,
    fvg: 0,
    price_zone: 0,
  };
  for (const m of markers) counts[m.kind] += 1;
  for (const l of levels) counts[l.kind] += 1;
  for (const b of bands) counts[b.kind] += 1;
  const availableKinds = ALL_OVERLAY_KINDS.filter((k) => counts[k] > 0);

  return { markers, levels, bands, availableKinds, unsupportedKinds };
}

export function countOverlaysByKind(catalog: OverlayCatalog): Record<OverlayKind, number> {
  const counts = Object.fromEntries(ALL_OVERLAY_KINDS.map((k) => [k, 0])) as Record<
    OverlayKind,
    number
  >;
  for (const m of catalog.markers) counts[m.kind] += 1;
  for (const l of catalog.levels) counts[l.kind] += 1;
  for (const b of catalog.bands) counts[b.kind] += 1;
  return counts;
}
