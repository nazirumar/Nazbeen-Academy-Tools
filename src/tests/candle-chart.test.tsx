/**
 * Phase 5 — candle chart tests (lightweight-charts mocked).
 *
 * Verifies the chart contract without a real canvas:
 * - the chart is created ONCE per mount (never recreated on refresh);
 * - valid candle data reaches setData in ascending time order;
 * - polled tail updates use series.update(), history changes use setData;
 * - a series-key change (symbol/timeframe) triggers full setData + fit;
 * - unmount removes the chart exactly once (resource cleanup);
 * - empty and malformed payloads do not crash and produce no bad data calls;
 * - markers are only created for VISIBLE overlay kinds.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { CandleChart } from "@/components/chart/candle-chart";
import { buildOverlays, type OverlayKind } from "@/lib/chart/overlay-model";
import type { AnalysisReport, CandlePoint } from "@/lib/api/types";

// ---------------------------------------------------------------------------
// lightweight-charts mock (v5 API surface used by the component)
// ---------------------------------------------------------------------------

class MockSeries {
  setData = vi.fn();
  update = vi.fn();
  applyOptions = vi.fn();
  attachPrimitive = vi.fn();
  priceToCoordinate = vi.fn(() => 100);
}

class MockTimeScale {
  fitContent = vi.fn();
  timeToCoordinate = vi.fn(() => 50);
  subscribeVisibleLogicalRangeChange = vi.fn();
  unsubscribeVisibleLogicalRangeChange = vi.fn();
}

class MockMarkers {
  setMarkers = vi.fn();
}

class MockChart {
  static nextId = 0;
  id = MockChart.nextId++;
  removed = 0;
  series = new MockSeries();
  timeScaleInstance = new MockTimeScale();
  crosshairHandler: ((param: unknown) => void) | null = null;
  optionsApplied: unknown[] = [];

  timeScale(): MockTimeScale {
    return this.timeScaleInstance;
  }

  addSeries(): MockSeries {
    return this.series;
  }
  applyOptions(options: unknown): void {
    this.optionsApplied.push(options);
  }
  subscribeCrosshairMove(handler: (param: unknown) => void): void {
    this.crosshairHandler = handler;
  }
  unsubscribeCrosshairMove(handler: (param: unknown) => void): void {
    if (this.crosshairHandler === handler) this.crosshairHandler = null;
  }
  remove(): void {
    this.removed += 1;
  }
}

const chartInstances: MockChart[] = [];
const markerInstances: MockMarkers[] = [];
const primitiveInstances: { setRects: ReturnType<typeof vi.fn> }[] = [];

vi.mock("lightweight-charts", () => ({
  CandlestickSeries: { type: "Candlestick" },
  ColorType: { Solid: "solid" },
  CrosshairMode: { Normal: 0 },
  LineStyle: { Solid: 0, Dashed: 2 },
  createChart: (container: HTMLElement) => {
    void container;
    const chart = new MockChart();
    chartInstances.push(chart);
    return chart;
  },
  createSeriesMarkers: () => {
    const markers = new MockMarkers();
    markerInstances.push(markers);
    return markers;
  },
}));

vi.mock("@/lib/chart/band-primitive", () => ({
  BandPrimitive: class {
    setRects = vi.fn();
    constructor(_rects?: unknown) {
      primitiveInstances.push(this);
    }
    attached() {}
    detached() {}
    paneViews() {
      return [];
    }
  },
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function point(time: number, close: number, forming = false): CandlePoint {
  return {
    time,
    time_utc: "",
    open: close - 0.001,
    high: close + 0.002,
    low: close - 0.002,
    close,
    volume: 1,
    forming,
  };
}

const REPORT = {
  analysis_id: "abc",
  report_schema_version: "1.0",
  engine_version: "4",
  symbol: "EURUSD",
  mode: "forex",
  strategy: {
    id: "s",
    name: "S",
    mode: "forex",
    direction: "bullish",
    strategy_status: "active",
    version: 1,
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
    source_note: "",
  },
  evaluated_at: "x",
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
    agreement: { status: "single", detail: "", biases: {} },
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
    by_timeframe: [
      {
        timeframe: "M15",
        completeness: {
          timeframe: "M15",
          total: 10,
          closed: 10,
          forming: 0,
          first_time: 1000,
          last_time: 10000,
          gap_count: 0,
          largest_gap_intervals: 0,
        },
        latest_candle: null,
        swings: {
          strength: 2,
          highs: [{ time: 2000, price: 1.1, confirmed_at_time: 3000 }],
          lows: [{ time: 3000, price: 1.05, confirmed_at_time: 4000 }],
          high_structure: { kind: "high", labels: [], structure_trend: "mixed", swing_count: 1 },
          low_structure: { kind: "low", labels: [], structure_trend: "mixed", swing_count: 1 },
        },
        structure_breaks: [
          { time: 6000, side: "bullish", level: 1.1, reference_time: 2000, close: 1.101 },
        ],
      },
    ],
    note: "",
  },
  evaluation: {
    direction: "bullish",
    setup_status: "conditions_met",
    core: { kind: "group", id: "r", satisfied: true, description: "", evidence: {} },
    confirmation: null,
    invalidation: null,
    warnings: [],
    summary: [],
    evaluated_at: "x",
  },
  limitations: [],
} as unknown as AnalysisReport;

const ALL_VISIBLE: Record<OverlayKind, boolean> = {
  swing_high: true,
  swing_low: true,
  structure_label: true,
  bos: true,
  mss_choch: true,
  fvg: true,
  price_zone: true,
};

function renderChart(props: Partial<Parameters<typeof CandleChart>[0]> = {}) {
  const overlays = props.overlays ?? buildOverlays(REPORT, "M15");
  return render(
    <CandleChart
      candles={props.candles ?? [point(1000, 1.0), point(2000, 1.05)]}
      seriesKey={props.seriesKey ?? "EURUSD:M15"}
      overlays={overlays}
      visible={props.visible ?? ALL_VISIBLE}
      includesForming={props.includesForming ?? false}
    />,
  );
}

describe("CandleChart", () => {
  beforeEach(() => {
    chartInstances.length = 0;
    markerInstances.length = 0;
    primitiveInstances.length = 0;
  });

  afterEach(() => {
    cleanup();
  });

  it("creates ONE chart and pushes sorted candle data on mount", () => {
    renderChart({
      candles: [point(2000, 1.05), point(1000, 1.0), point(3000, 1.06)],
    });
    expect(chartInstances).toHaveLength(1);
    const chart = chartInstances[0];
    const data = chart.series.setData.mock.calls[0][0] as { time: number }[];
    expect(data.map((d) => d.time)).toEqual([1000, 2000, 3000]);
    expect(chart.timeScaleInstance.fitContent).toHaveBeenCalledTimes(1);
  });

  it("does NOT recreate the chart on polled refresh and uses update() for tail changes", () => {
    const { rerender } = renderChart({
      candles: [point(1000, 1.0), point(2000, 1.05)],
    });
    const chart = chartInstances[0];
    expect(chart.series.update).not.toHaveBeenCalled();

    chart.series.setData.mockClear();
    rerender(
      <CandleChart
        candles={[point(1000, 1.0), point(2000, 1.06), point(3000, 1.07)]}
        seriesKey="EURUSD:M15"
        overlays={buildOverlays(REPORT, "M15")}
        visible={ALL_VISIBLE}
        includesForming={false}
      />,
    );
    expect(chartInstances).toHaveLength(1); // same chart instance
    // Tail-only change: the closed candle (t=2000) AND the new candle
    // (t=3000) are pushed incrementally, in ascending time order.
    expect(chart.series.update).toHaveBeenCalledTimes(2);
    const first = chart.series.update.mock.calls[0][0] as { time: number; close: number };
    const second = chart.series.update.mock.calls[1][0] as { time: number };
    expect(first.time).toBe(2000);
    expect(first.close).toBe(1.06);
    expect(second.time).toBe(3000);
    expect(chart.series.setData).not.toHaveBeenCalled();
  });

  it("falls back to setData when history changed before the tail", () => {
    const { rerender } = renderChart({
      candles: [point(1000, 1.0), point(2000, 1.05)],
    });
    const chart = chartInstances[0];
    chart.series.setData.mockClear();
    rerender(
      <CandleChart
        candles={[point(1000, 0.9), point(2000, 1.05)]}
        seriesKey="EURUSD:M15"
        overlays={buildOverlays(REPORT, "M15")}
        visible={ALL_VISIBLE}
        includesForming={false}
      />,
    );
    expect(chart.series.setData).toHaveBeenCalledTimes(1);
    expect(chart.series.update).not.toHaveBeenCalled();
  });

  it("performs a full setData + fit when the series key changes", () => {
    const { rerender } = renderChart({ seriesKey: "EURUSD:M15" });
    const chart = chartInstances[0];
    chart.series.setData.mockClear();
    chart.timeScaleInstance.fitContent.mockClear();

    rerender(
      <CandleChart
        candles={[point(5000, 120.5)]}
        seriesKey="USDJPY:M15"
        overlays={buildOverlays(REPORT, "M15")}
        visible={ALL_VISIBLE}
        includesForming={false}
      />,
    );
    expect(chartInstances).toHaveLength(1);
    expect(chart.series.setData).toHaveBeenCalledTimes(1);
    expect(chart.timeScaleInstance.fitContent).toHaveBeenCalledTimes(1);
  });

  it("removes the chart exactly once on unmount", () => {
    const { unmount } = renderChart();
    const chart = chartInstances[0];
    unmount();
    expect(chart.removed).toBe(1);
  });

  it("renders an empty container without crashing when candles are empty", () => {
    renderChart({ candles: [] });
    const chart = chartInstances[0];
    expect(chart.series.setData).toHaveBeenCalledWith([]);
    expect(screen.getByTestId("candle-chart")).toBeInTheDocument();
  });

  it("skips malformed candles (NaN close) instead of crashing", () => {
    renderChart({
      candles: [{ ...point(1000, 1.0), close: Number.NaN }, point(2000, 1.05)],
    });
    const chart = chartInstances[0];
    const data = chart.series.setData.mock.calls[0][0] as { time: number }[];
    expect(data).toHaveLength(1);
    expect(data[0].time).toBe(2000);
  });

  it("sends markers only for visible overlay kinds", () => {
    const overlays = buildOverlays(REPORT, "M15");
    renderChart({ overlays, visible: { ...ALL_VISIBLE, swing_high: false, bos: false } });
    const markers = markerInstances[0].setMarkers.mock.calls[0][0] as {
      time: number;
    }[];
    // swing_high (t=2000) and the bos level are hidden; swing_low stays.
    expect(markers.map((m) => m.time)).toEqual([3000]);
    // Band rects: BOS level hidden -> no rects applied for it. The FVG/zone
    // kinds have no fixtures; only the primitive receives an empty list.
    expect(primitiveInstances.length).toBeGreaterThan(0);
  });

  it("updates band rects when overlays change", () => {
    const overlays = buildOverlays(REPORT, "M15");
    const { rerender } = renderChart({ overlays });
    const primitive = primitiveInstances[0];
    expect(primitive.setRects).toHaveBeenCalled();
    primitive.setRects.mockClear();

    rerender(
      <CandleChart
        candles={[point(1000, 1.0), point(2000, 1.05)]}
        seriesKey="EURUSD:M15"
        overlays={buildOverlays(null, "M15")}
        visible={ALL_VISIBLE}
        includesForming={false}
      />,
    );
    expect(primitive.setRects).toHaveBeenCalled();
  });
});
