/**
 * Phase 5 — market dashboard integration tests (API + chart stubbed).
 *
 * Covers: Forex vs Binary timeframe behaviour, symbol suggestions,
 * strategy/version loading, analysis run + four-state condition sections,
 * disconnected / stale / no-data honesty, overlay toggles traceability,
 * history access, and accessible controls.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, cleanup, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MarketDashboard } from "@/components/dashboard/market-dashboard";
import { ApiError } from "@/lib/api/types";
import { useMarketStore } from "@/lib/platform/market-store";
import type {
  AnalysisReport,
  CandleSeries,
  PaginatedAnalysisReports,
  PaginatedStrategies,
  StrategyVersion,
  SymbolList,
} from "@/lib/api/types";
import type { TimeframeObservations } from "@/lib/api/types";

// The chart is covered by its own test file; stub it here and expose the
// props it received so overlay wiring can be asserted end-to-end.
const chartStub = vi.fn();
vi.mock("@/components/chart/candle-chart", () => ({
  CandleChart: (props: {
    candles: unknown[];
    seriesKey: string;
    overlays: { markers: unknown[]; levels: unknown[]; bands: unknown[] };
    visible: Record<string, boolean>;
  }) => {
    chartStub(props);
    return (
      <div
        data-testid="candle-chart-stub"
        data-series-key={props.seriesKey}
        data-candles={props.candles.length}
        data-markers={props.overlays.markers.length}
        data-levels={props.overlays.levels.length}
        data-bands={props.overlays.bands.length}
      />
    );
  },
}));

const getMock = vi.fn();
const postMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    get: (...args: unknown[]) => getMock(...args),
    post: (...args: unknown[]) => postMock(...args),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const T0 = 1_700_000_000;

function candle(time: number, close: number, forming = false) {
  return {
    time,
    time_utc: new Date(time * 1000).toISOString(),
    open: close - 0.001,
    high: close + 0.002,
    low: close - 0.002,
    close,
    volume: 10,
    forming,
  };
}

function candleSeries(overrides: Partial<CandleSeries> = {}): CandleSeries {
  return {
    symbol: "EURUSD",
    timeframe: "M15",
    timezone: "MT5_SERVER",
    timezone_note: "",
    candles: [candle(T0, 1.1), candle(T0 + 900, 1.101), candle(T0 + 1800, 1.102)],
    count: 3,
    requested_count: 3,
    closed_only: true,
    includes_forming: false,
    first_time: T0,
    last_time: T0 + 1800,
    newest_closed_time: T0 + 1800,
    newest_closed_age_seconds: 30,
    data_status: "ok",
    data_provenance: "mt5_live",
    retrieved_at: new Date().toISOString(),
    warnings: [],
    ...overrides,
  };
}

const STRATEGIES: PaginatedStrategies = {
  items: [
    {
      id: "strat-1",
      name: "Trend pullback",
      mode: "forex",
      direction: "bullish",
      status: "active",
      description: null,
      category_id: null,
      current_version: 3,
      published_version_number: 2,
      created_at: "",
      updated_at: "",
    },
  ],
  page: { page: 1, page_size: 100, total: 1, pages: 1 },
};

const VERSIONS: StrategyVersion[] = [
  {
    id: "v2",
    strategy_id: "strat-1",
    version: 2,
    status: "published",
    changelog: null,
    rule_set: {
      schema_version: "2.0",
      mode: "forex",
      timeframes: ["M15"],
      direction: "bullish",
      conditions: { logic: "AND", conditions: [], groups: [] },
      confirmation: null,
      invalidation: null,
    },
    created_at: "",
  },
  {
    id: "v3",
    strategy_id: "strat-1",
    version: 3,
    status: "draft",
    changelog: null,
    rule_set: {
      schema_version: "2.0",
      mode: "forex",
      timeframes: ["M15"],
      direction: "bullish",
      conditions: { logic: "AND", conditions: [], groups: [] },
      confirmation: null,
      invalidation: null,
    },
    created_at: "",
  },
];

const OBSERVATIONS: TimeframeObservations[] = [
  {
    timeframe: "M15",
    completeness: {
      timeframe: "M15",
      total: 3,
      closed: 3,
      forming: 0,
      first_time: T0,
      last_time: T0 + 1800,
      gap_count: 0,
      largest_gap_intervals: 0,
    },
    latest_candle: null,
    swings: {
      strength: 2,
      highs: [{ time: T0 + 900, price: 1.103, confirmed_at_time: T0 + 1800 }],
      lows: [{ time: T0, price: 1.09, confirmed_at_time: T0 + 900 }],
      high_structure: { kind: "high", labels: [], structure_trend: "mixed", swing_count: 1 },
      low_structure: { kind: "low", labels: [], structure_trend: "mixed", swing_count: 1 },
    },
    structure_breaks: [],
    zones: [{ zone_low: 1.08, zone_high: 1.09, position: "above", distance: 0.01, touch_count: 1, first_touch_time: T0, last_touch_time: T0 }],
  },
];

function analysisReport(overrides: Partial<AnalysisReport> = {}): AnalysisReport {
  return {
    analysis_id: "aaaa-bbbb",
    report_schema_version: "1.0",
    engine_version: "4.0.0",
    symbol: "EURUSD",
    mode: "forex",
    strategy: {
      id: "strat-1",
      name: "Trend pullback",
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
      source_note: "",
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
      agreement: { status: "single_timeframe", detail: "one timeframe", biases: {} },
      applicability: {
        required: "any",
        classified: "uptrend",
        applicable: true,
        matched_buckets: [],
        note: "",
      },
      note: "",
    },
    observations: { by_timeframe: OBSERVATIONS, note: "" },
    evaluation: {
      direction: "bullish",
      setup_status: "conditions_met",
      core: {
        kind: "group",
        id: "root",
        satisfied: true,
        status: "met",
        description: "AND group",
        logic: "AND",
        evidence: {},
        children: [
          {
            kind: "condition",
            id: "swing-high-present",
            satisfied: true,
            status: "met",
            description: "Confirmed swing high exists",
            evidence: { condition_type: "swing_point", timeframe: "M15" },
          },
          {
            kind: "condition",
            id: "ma-above",
            satisfied: false,
            status: "not_met",
            description: "EMA20 above value",
            evidence: { condition_type: "moving_average", timeframe: "M15" },
          },
          {
            kind: "condition",
            id: "no-data-rule",
            satisfied: false,
            status: "inconclusive",
            description: "Cannot evaluate: no candles",
            evidence: { condition_type: "price_level", timeframe: "H4" },
          },
          {
            kind: "condition",
            id: "ob-rule",
            satisfied: false,
            status: "unsupported",
            description: "Order block rule",
            evidence: { condition_type: "order_block", timeframe: "M15" },
          },
        ],
      },
      confirmation: null,
      invalidation: null,
      conditions_summary: { met: 1, not_met: 1, inconclusive: 1, unsupported: 1 },
      warnings: [],
      summary: [],
      evaluated_at: "2026-01-01T00:00:00+00:00",
    },
    limitations: ["No probability of profit is implied."],
    ...overrides,
  } as AnalysisReport;
}

const HISTORY: PaginatedAnalysisReports = {
  items: [
    {
      id: "rep-1",
      symbol: "EURUSD",
      mode: "forex",
      strategy_id: "strat-1",
      strategy_name: "Trend pullback",
      strategy_version: 2,
      setup_status: "conditions_met",
      direction: "bullish",
      regime: "uptrend",
      report_schema_version: "1.0",
      engine_version: "4.0.0",
      evaluated_at: "2026-01-01T00:00:00+00:00",
      created_at: "2026-01-01T00:00:00+00:00",
    },
  ],
  page: { page: 1, page_size: 10, total: 1, pages: 1 },
};

const SYMBOLS: SymbolList = {
  symbols: [
    { name: "EURUSD", description: "Euro vs US Dollar", visible: true, selectable: true, trade_mode: 0, digits: 5, point: 0.00001, spread: 10, currency_base: "EUR", currency_profit: "USD" },
    { name: "GBPUSD", description: "Pound vs US Dollar", visible: true, selectable: true, trade_mode: 0, digits: 5, point: 0.00001, spread: 12, currency_base: "GBP", currency_profit: "USD" },
  ],
  count: 2,
  search: "EUR*",
  truncated: false,
};

function installDefaultHandlers() {
  getMock.mockImplementation((path: string) => {
    if (path === "/api/v1/market/candles") return Promise.resolve(candleSeries());
    if (path === "/api/v1/market/symbols") return Promise.resolve(SYMBOLS);
    if (path.startsWith("/api/v1/strategies/strat-1/versions")) return Promise.resolve(VERSIONS);
    if (path === "/api/v1/strategies") return Promise.resolve(STRATEGIES);
    if (path === "/api/v1/analysis/reports") return Promise.resolve(HISTORY);
    if (path.startsWith("/api/v1/analysis/reports/")) return Promise.resolve(analysisReport());
    throw new Error(`unexpected GET ${path}`);
  });
}

beforeEach(() => {
  chartStub.mockClear();
  getMock.mockReset();
  postMock.mockReset();
  useMarketStore.setState({ symbol: "EURUSD", timeframe: "M1" }); // shared store isolation
  installDefaultHandlers();
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("MarketDashboard — Forex mode", () => {
  it("loads candles, symbol suggestions, strategies and history", async () => {
    render(<MarketDashboard mode="forex" />);
    await waitFor(() => expect(screen.getByTestId("candle-chart-stub")).toBeInTheDocument());
    const stub = screen.getByTestId("candle-chart-stub");
    expect(stub).toHaveAttribute("data-series-key", "EURUSD:M1");
    expect(stub).toHaveAttribute("data-candles", "3");
    expect(getMock).toHaveBeenCalledWith(
      "/api/v1/market/candles",
      expect.objectContaining({ symbol: "EURUSD", timeframe: "M1" }),
    );
    // Strategy panel and history render.
    expect(await screen.findByText("Trend pullback")).toBeInTheDocument();
    expect(await screen.findByTestId("analysis-history")).toBeInTheDocument();
  });

  it("renders the four condition states after a run — never fake probabilities", async () => {
    postMock.mockResolvedValue(analysisReport());
    const user = userEvent.setup();
    render(<MarketDashboard mode="forex" />);
    await user.click(await screen.findByRole("button", { name: /run analysis/i }));
    await waitFor(() => expect(screen.getByTestId("condition-sections")).toBeInTheDocument());
    expect(screen.getByTestId("conditions-met")).toHaveTextContent("Confirmed swing high exists");
    expect(screen.getByTestId("conditions-not_met")).toHaveTextContent("EMA20 above value");
    // Inconclusive / unsupported sections start collapsed — expand them.
    await user.click(screen.getByTestId("conditions-inconclusive").getElementsByTagName("button")[0]);
    await user.click(screen.getByTestId("conditions-unsupported").getElementsByTagName("button")[0]);
    expect(screen.getByTestId("conditions-inconclusive")).toHaveTextContent(
      "Cannot evaluate: no candles",
    );
    expect(screen.getByTestId("conditions-unsupported")).toHaveTextContent("Order block rule");
    // Counts: one per state.
    expect(within(screen.getByTestId("conditions-met")).getByText("1")).toBeInTheDocument();
    // No probability/confidence PERCENTAGE claims anywhere (the honest
    // disclaimer that negates profit probabilities is expected to exist).
    expect(screen.queryByText(/[0-9]+\s?% confidence/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/probability of profit[:\s]*[0-9]/i)).not.toBeInTheDocument();
    expect(screen.getByText(/no probability of profit/i)).toBeInTheDocument();
  });

  it("derives chart overlays only from the selected report", async () => {
    // The fixture report carries M15 observations — display M15 as well.
    useMarketStore.setState({ symbol: "EURUSD", timeframe: "M15" });
    postMock.mockResolvedValue(analysisReport());
    const user = userEvent.setup();
    render(<MarketDashboard mode="forex" />);
    // Before a report: overlays all zero, toggles disabled.
    await waitFor(() => expect(screen.getByTestId("overlay-controls")).toBeInTheDocument());
    const swingSwitch = screen.getByRole("switch", { name: /swing highs \(0 in report\)/i });
    expect(swingSwitch).toBeDisabled();

    await user.click(await screen.findByRole("button", { name: /run analysis/i }));
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: /swing highs \(1 in report\)/i })).toBeEnabled(),
    );
    // Chart stub received overlays from the report (2 swing markers + 1 zone).
    await waitFor(() => {
      const stub = screen.getByTestId("candle-chart-stub");
      expect(stub).toHaveAttribute("data-markers", "2");
      expect(stub).toHaveAttribute("data-bands", "1");
    });
  });

  it("shows stale-data and request-error states honestly", async () => {
    getMock.mockImplementation((path: string) => {
      if (path === "/api/v1/market/candles") {
        return Promise.resolve(
          candleSeries({
            data_status: "stale",
            warnings: [
              { code: "STALE_DATA", severity: "warning", message: "Newest closed candle is too old." },
            ],
          }),
        );
      }
      if (path === "/api/v1/market/symbols") return Promise.resolve(SYMBOLS);
      if (path.startsWith("/api/v1/strategies/strat-1/versions")) return Promise.resolve(VERSIONS);
      if (path === "/api/v1/strategies") return Promise.resolve(STRATEGIES);
      if (path === "/api/v1/analysis/reports") return Promise.resolve(HISTORY);
      if (path === "/api/v1/ai/status")
        return Promise.resolve({
          enabled: false,
          provider_configured: false,
          provider: "openai_compatible",
          model: null,
          timeout_seconds: 45,
          max_retries: 2,
          rate_limit_per_minute: 10,
          max_input_chars: 24000,
          max_output_tokens: 1400,
          explanations_endpoint: "/api/v1/ai/explanations",
          chat_endpoint: "/api/v1/ai/research/chat",
          note: "test",
        });
      throw new Error(`unexpected GET ${path}`);
    });
    render(<MarketDashboard mode="forex" />);
    expect(await screen.findByText(/stale data/i)).toBeInTheDocument();
    expect(screen.getByText(/STALE_DATA/)).toBeInTheDocument();
    // The chart still shows the (real, stale) candles — never hidden, never faked.
    expect(screen.getByTestId("candle-chart-stub")).toBeInTheDocument();
  });

  it("shows the disconnected state with no fabricated candles when MT5 is down", async () => {
    getMock.mockImplementation((path: string) => {
      if (path === "/api/v1/market/candles") {
        return Promise.reject(
          new ApiError({
            code: "MARKET_DATA_UNAVAILABLE",
            message: "MT5 disabled",
            status: 503,
          }),
        );
      }
      if (path === "/api/v1/market/symbols") return Promise.resolve(SYMBOLS);
      if (path.startsWith("/api/v1/strategies/strat-1/versions")) return Promise.resolve(VERSIONS);
      if (path === "/api/v1/strategies") return Promise.resolve(STRATEGIES);
      if (path === "/api/v1/analysis/reports") return Promise.resolve(HISTORY);
      if (path === "/api/v1/ai/status")
        return Promise.resolve({
          enabled: false,
          provider_configured: false,
          provider: "openai_compatible",
          model: null,
          timeout_seconds: 45,
          max_retries: 2,
          rate_limit_per_minute: 10,
          max_input_chars: 24000,
          max_output_tokens: 1400,
          explanations_endpoint: "/api/v1/ai/explanations",
          chat_endpoint: "/api/v1/ai/research/chat",
          note: "test",
        });
      throw new Error(`unexpected GET ${path}`);
    });
    render(<MarketDashboard mode="forex" />);
    expect(await screen.findByText(/chart is NOT live/i)).toBeInTheDocument();
    expect(screen.queryByTestId("candle-chart-stub")).not.toBeInTheDocument();
    expect(screen.getByText(/never fabricates prices/i)).toBeInTheDocument();
  });

  it("opens a persisted report from history", async () => {
    const user = userEvent.setup();
    render(<MarketDashboard mode="forex" />);
    const history = await screen.findByTestId("analysis-history");
    await user.click(within(history).getByRole("button", { name: /open analysis report/i }));
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/api/v1/analysis/reports/rep-1"));
    // The full-report sheet content becomes available.
    expect(await screen.findByText(/Condition outcomes/i)).toBeInTheDocument();
  });

  it("keeps accessible labels on the primary controls", async () => {
    render(<MarketDashboard mode="forex" />);
    expect(await screen.findByLabelText(/symbol \(type to search/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Timeframe")).toBeInTheDocument();
    expect(screen.getByLabelText(/refresh interval/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/closed candles only/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /refresh now/i })).toBeInTheDocument();
    expect(screen.getByTestId("connection-status")).toHaveAttribute("aria-live", "polite");
  });
});

describe("MarketDashboard — Binary Options mode", () => {
  it("defaults to M1-only timeframes and shows the broker-feed disclaimer", async () => {
    render(<MarketDashboard mode="binary_options" />);
    await waitFor(() => expect(screen.getByTestId("candle-chart-stub")).toBeInTheDocument());
    expect(screen.getByText(/MT5 data is NOT the broker execution feed/i)).toBeInTheDocument();
    // The M1 default is kept — no higher-timeframe candle fetch is issued.
    expect(getMock).toHaveBeenCalledWith(
      "/api/v1/market/candles",
      expect.objectContaining({ timeframe: "M1" }),
    );
    const candleCalls = getMock.mock.calls.filter(([path]) => path === "/api/v1/market/candles");
    const requestedTimeframes = new Set(candleCalls.map(([, q]) => (q as { timeframe: string }).timeframe));
    expect(Array.from(requestedTimeframes)).toEqual(["M1"]);
  });

  it("lists strategies of the binary mode only", async () => {
    render(<MarketDashboard mode="binary_options" />);
    await waitFor(() =>
      expect(getMock).toHaveBeenCalledWith("/api/v1/strategies", expect.objectContaining({ mode: "binary_options" })),
    );
  });
});
