/**
 * Phase 5 — overlay derivation tests.
 *
 * Guarantees verified:
 * - every overlay traces to report data (observations / condition evidence);
 * - timeframe filtering (nothing from other timeframes is drawn);
 * - HH/HL/LH/LL label alignment for both full and truncated swing lists;
 * - BOS segments span reference_time → break time at the break level;
 * - FVG zones come from condition evidence, filled gaps are NOT extended;
 * - MSS/CHoCH levels come from mss_choch condition evidence;
 * - strategy price zones come from observations.zones only;
 * - nothing is fabricated when the report has no observations, and
 *   order-block/breaker concepts are reported as engine-unsupported.
 */

import { describe, expect, it } from "vitest";
import {
  buildOverlays,
  countOverlaysByKind,
  labelForSwings,
} from "@/lib/chart/overlay-model";
import type {
  AnalysisReport,
  EvaluationNode,
  TimeframeObservations,
} from "@/lib/api/types";

const ANALYSIS_ID = "11111111-2222-3333-4444-555555555555";

function conditionNode(
  id: string,
  type: string,
  timeframe: string,
  evidence: Record<string, unknown>,
  status: "met" | "not_met" | "inconclusive" | "unsupported" = "met",
): EvaluationNode {
  return {
    kind: "condition",
    id,
    satisfied: status === "met",
    status,
    description: `${type} on ${timeframe}`,
    evidence: { condition_type: type, timeframe, ...evidence },
  };
}

function groupNode(logic: "AND" | "OR", children: EvaluationNode[]): EvaluationNode {
  return {
    kind: "group",
    id: "root",
    satisfied: true,
    status: "met",
    description: `${logic} group`,
    logic,
    evidence: {},
    children,
  };
}

const OBSERVATIONS: TimeframeObservations[] = [
  {
    timeframe: "M15",
    completeness: {
      timeframe: "M15",
      total: 120,
      closed: 120,
      forming: 0,
      first_time: 1000,
      last_time: 9000,
      gap_count: 0,
      largest_gap_intervals: 0,
    },
    latest_candle: null,
    swings: {
      strength: 2,
      highs: [
        { time: 2000, price: 1.101, confirmed_at_time: 3000 },
        { time: 4000, price: 1.103, confirmed_at_time: 5000 },
        { time: 6000, price: 1.102, confirmed_at_time: 7000 },
      ],
      lows: [
        { time: 3000, price: 1.095, confirmed_at_time: 4000 },
        { time: 5000, price: 1.097, confirmed_at_time: 6000 },
      ],
      high_structure: {
        kind: "high",
        labels: ["HH", "LH"],
        structure_trend: "mixed",
        swing_count: 3,
      },
      low_structure: {
        kind: "low",
        labels: ["HL"],
        structure_trend: "bullish",
        swing_count: 2,
      },
    },
    structure_breaks: [
      {
        time: 6500,
        side: "bullish",
        level: 1.101,
        reference_time: 2000,
        close: 1.1015,
      },
    ],
    zones: [
      { zone_low: 1.09, zone_high: 1.095, position: "above", distance: 0.005, touch_count: 3, first_touch_time: 1100, last_touch_time: 1200 },
    ],
  },
  {
    timeframe: "H1",
    completeness: {
      timeframe: "H1",
      total: 60,
      closed: 60,
      forming: 0,
      first_time: 500,
      last_time: 9000,
      gap_count: 0,
      largest_gap_intervals: 0,
    },
    latest_candle: null,
    swings: null,
    structure_breaks: [],
  },
];

function baseReport(overrides?: Partial<AnalysisReport>): AnalysisReport {
  return {
    analysis_id: ANALYSIS_ID,
    report_schema_version: "1.0",
    engine_version: "4.0.0",
    symbol: "EURUSD",
    mode: "forex",
    strategy: {
      id: "s-1",
      name: "Demo",
      mode: "forex",
      direction: "bullish",
      strategy_status: "active",
      version: 2,
      version_status: "published",
      immutable_definition: true,
      rule_set: {
        schema_version: "2.0",
        mode: "forex",
        timeframes: ["M15"],
        direction: "bullish",
        conditions: { logic: "AND", conditions: [], groups: [] },
        confirmation: null,
        invalidation: null,
      },
    },
    required_timeframes: ["M15"],
    evaluated_timeframes: ["M15"],
    data: {
      provider: "mt5",
      quality_status: "ok",
      closed_candles_only: true,
      forming_candle_used: false,
      timeframes: [],
      warnings: [],
      source_note: "test",
    },
    evaluated_at: "2026-01-01T00:00:00+00:00",
    regime: {
      primary_timeframe: "M15",
      primary: {
        timeframe: "M15",
        regime: "uptrend",
        volatility_state: "normal",
        directional_bias: "bullish",
        applicable_buckets: [],
        evidence: {},
      },
      by_timeframe: [],
      agreement: { status: "single_timeframe", detail: "", biases: {} },
      applicability: {
        required: "any",
        classified: "uptrend",
        applicable: true,
        matched_buckets: [],
        note: "",
      },
      note: "",
    },
    observations: {
      by_timeframe: JSON.parse(JSON.stringify(OBSERVATIONS)) as TimeframeObservations[],
      note: "test",
    },
    evaluation: {
      direction: "bullish",
      setup_status: "conditions_met",
      core: groupNode("AND", []),
      confirmation: null,
      invalidation: null,
      warnings: [],
      summary: [],
      evaluated_at: "2026-01-01T00:00:00+00:00",
    },
    limitations: [],
    ...overrides,
  } as AnalysisReport;
}

describe("buildOverlays — honesty", () => {
  it("returns empty catalog plus unsupported kinds for a null report", () => {
    const catalog = buildOverlays(null, "M15");
    expect(catalog.markers).toHaveLength(0);
    expect(catalog.levels).toHaveLength(0);
    expect(catalog.bands).toHaveLength(0);
    expect(catalog.availableKinds).toHaveLength(0);
    expect(catalog.unsupportedKinds.map((u) => u.kind)).toEqual([
      "order_block",
      "breaker_block",
    ]);
  });

  it("draws nothing when the report has no observations for the timeframe", () => {
    const report = baseReport();
    const catalog = buildOverlays(report, "H4"); // not in observations
    expect(catalog.markers).toHaveLength(0);
    expect(catalog.levels).toHaveLength(0);
    expect(catalog.bands).toHaveLength(0);
    expect(catalog.availableKinds).toHaveLength(0);
  });

  it("traces every overlay to the analysis id and timeframe", () => {
    const catalog = buildOverlays(baseReport(), "M15");
    for (const marker of catalog.markers) {
      expect(marker.source.analysisId).toBe(ANALYSIS_ID);
      expect(marker.source.timeframe).toBe("M15");
    }
    for (const level of catalog.levels) {
      expect(level.source.analysisId).toBe(ANALYSIS_ID);
      expect(level.source.timeframe).toBe("M15");
    }
  });
});

describe("buildOverlays — swings and structure labels", () => {
  it("creates swing markers with confirmation timing preserved", () => {
    const catalog = buildOverlays(baseReport(), "M15");
    const highs = catalog.markers.filter((m) => m.kind === "swing_high");
    const lows = catalog.markers.filter((m) => m.kind === "swing_low");
    expect(highs).toHaveLength(3);
    expect(lows).toHaveLength(2);
    expect(highs[0]).toMatchObject({
      time: 2000,
      price: 1.101,
      confirmedAtTime: 3000,
      anchor: "above",
    });
    expect(lows[0].anchor).toBe("below");
  });

  it("aligns HH/LH labels with the full swing list (labels.length == swings-1)", () => {
    const catalog = buildOverlays(baseReport(), "M15");
    const labels = catalog.markers.filter((m) => m.kind === "structure_label");
    const text = labels.map((m) => m.text);
    // highs: [no label, HH, LH]; lows: [no label, HL]
    expect(text).toEqual(["HH", "LH", "HL"]);
  });

  it("aligns labels with a truncated swing list (swing_count > returned)", () => {
    const report = baseReport();
    report.observations.by_timeframe[0].swings!.high_structure.labels = [
      "HH",
      "HH",
      "LH",
      "LH",
      "HH",
      "LH",
    ];
    report.observations.by_timeframe[0].swings!.high_structure.swing_count = 7;
    const catalog = buildOverlays(report, "M15");
    const highLabels = catalog.markers.filter(
      (m) => m.kind === "structure_label" && m.anchor === "above",
    );
    // Last 3 labels ("LH", "HH", "LH") pair with the 3 returned swings.
    expect(highLabels.map((m) => m.text)).toEqual(["LH", "HH", "LH"]);
  });

  it("skips labels entirely when they cannot be aligned", () => {
    const report = baseReport();
    report.observations.by_timeframe[0].swings!.high_structure.labels = ["HH"];
    const catalog = buildOverlays(report, "M15");
    const highLabels = catalog.markers.filter(
      (m) => m.kind === "structure_label" && m.anchor === "above",
    );
    expect(highLabels).toHaveLength(0);
    // Swing markers are still drawn — the swing fact stands on its own.
    expect(catalog.markers.filter((m) => m.kind === "swing_high")).toHaveLength(3);
  });
});

describe("buildOverlays — BOS, MSS/CHoCH, FVG, zones", () => {
  it("creates BOS segments from reference_time to break time at the level", () => {
    const catalog = buildOverlays(baseReport(), "M15");
    const bos = catalog.levels.filter((l) => l.kind === "bos");
    expect(bos).toHaveLength(1);
    expect(bos[0]).toMatchObject({
      time1: 2000,
      time2: 6500,
      price: 1.101,
      tone: "bullish",
    });
  });

  it("extracts MSS/CHoCH levels from condition evidence on the displayed timeframe", () => {
    const report = baseReport({
      evaluation: {
        direction: "bullish",
        setup_status: "conditions_met",
        core: groupNode("AND", [
          conditionNode("choch-1", "mss_choch", "M15", {
            reference_swing_price: 1.101,
            reference_swing_time: 2000,
            direction: "bullish",
            structure_shifted: true,
          }),
          conditionNode("choch-other-tf", "mss_choch", "H1", {
            reference_swing_price: 9.9,
            reference_swing_time: 1000,
            direction: "bearish",
            structure_shifted: false,
          }),
        ]),
        confirmation: null,
        invalidation: null,
        warnings: [],
        summary: [],
        evaluated_at: "x",
      } as AnalysisReport["evaluation"],
    });
    const catalog = buildOverlays(report, "M15");
    const mss = catalog.levels.filter((l) => l.kind === "mss_choch");
    expect(mss).toHaveLength(1); // H1 evidence filtered out
    expect(mss[0]).toMatchObject({ price: 1.101, time1: 2000, time2: 9000 });
    expect(mss[0].source.conditionId).toBe("choch-1");
  });

  it("draws FVG zones from evidence; filled gaps stay within origin window", () => {
    const report = baseReport({
      evaluation: {
        direction: "bullish",
        setup_status: "conditions_met",
        core: groupNode("AND", [
          conditionNode("fvg-1", "fvg", "M15", {
            recent_gaps: [
              {
                gap_low: 1.096,
                gap_high: 1.098,
                direction: "bullish",
                created_at_time: 4500,
                left_time: 4000,
                right_time: 4500,
                mitigation: "none",
                filled: false,
              },
              {
                gap_low: 1.092,
                gap_high: 1.093,
                direction: "bearish",
                created_at_time: 3000,
                left_time: 2500,
                right_time: 3000,
                mitigation: "full",
                filled: true,
              },
            ],
          }),
        ]),
        confirmation: null,
        invalidation: null,
        warnings: [],
        summary: [],
        evaluated_at: "x",
      } as AnalysisReport["evaluation"],
    });
    const catalog = buildOverlays(report, "M15");
    const bands = catalog.bands.filter((b) => b.kind === "fvg");
    expect(bands).toHaveLength(2);
    const unfilled = bands.find((b) => b.price1 === 1.096)!;
    expect(unfilled.time2).toBe(9000); // extended to last observed candle
    const filled = bands.find((b) => b.price1 === 1.092)!;
    expect(filled.time2).toBe(3000); // NOT extended beyond its origin
    expect(filled.tone).toBe("bearish");
  });

  it("draws strategy price zones spanning the observed data window", () => {
    const catalog = buildOverlays(baseReport(), "M15");
    const zones = catalog.bands.filter((b) => b.kind === "price_zone");
    expect(zones).toHaveLength(1);
    expect(zones[0]).toMatchObject({
      time1: 1000,
      time2: 9000,
      price1: 1.09,
      price2: 1.095,
      tone: "neutral",
    });
  });

  it("does not draw zones when the timeframe has no observed window", () => {
    const report = baseReport();
    report.observations.by_timeframe[1].completeness.first_time = null;
    report.observations.by_timeframe[1].completeness.last_time = null;
    report.observations.by_timeframe[1].zones = [
      { zone_low: 1, zone_high: 2, position: null, distance: null, touch_count: 0, first_touch_time: null, last_touch_time: null },
    ];
    const catalog = buildOverlays(report, "H1");
    expect(catalog.bands).toHaveLength(0);
  });

  it("reports available kinds with non-zero counts", () => {
    const catalog = buildOverlays(baseReport(), "M15");
    expect(catalog.availableKinds).toEqual(
      expect.arrayContaining(["swing_high", "swing_low", "structure_label", "bos", "price_zone"]),
    );
    expect(catalog.availableKinds).not.toContain("mss_choch");
    expect(catalog.availableKinds).not.toContain("fvg");
    const counts = countOverlaysByKind(catalog);
    expect(counts.swing_high).toBe(3);
    expect(counts.structure_label).toBe(3);
    expect(counts.bos).toBe(1);
  });
});

describe("labelForSwings", () => {
  const swings = [
    { time: 1, price: 1, confirmed_at_time: 2 },
    { time: 3, price: 2, confirmed_at_time: 4 },
    { time: 5, price: 1.5, confirmed_at_time: 6 },
  ];

  it("handles the all-swings-present case", () => {
    expect(labelForSwings(swings, ["HH", "LH"])).toEqual([undefined, "HH", "LH"]);
  });

  it("handles the truncated case", () => {
    expect(labelForSwings(swings, ["LH", "HH", "LH", "HH", "LH"])).toEqual([
      "LH",
      "HH",
      "LH",
    ]);
  });

  it("returns no labels when alignment is impossible", () => {
    expect(labelForSwings(swings, [])).toEqual([undefined, undefined, undefined]);
  });

  it("returns empty for an empty swing list", () => {
    expect(labelForSwings([], ["HH"])).toEqual([]);
  });
});
