/**
 * Phase 4 tests: engine-analysis UI.
 *
 * Mocks the API client (vi.hoisted pattern, same as strategy-builder
 * tests). Verifies: report rendering with DISTINCT met/not_met/
 * inconclusive/unsupported states, data + regime panels, warnings,
 * honest error handling when MT5 is disconnected, history loading and
 * report opening.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EngineAnalysis, ReportView } from "@/components/analysis/engine-analysis";
import type {
  AnalysisReport,
  PaginatedAnalysisReports,
  PaginatedStrategies,
} from "@/lib/api/types";

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}));
vi.mock("@/lib/api/client", () => ({ api, ApiError: class ApiError extends Error {} }));

vi.mock("@/hooks/use-toast", () => ({
  toast: vi.fn(),
}));

function strategyList(count = 1): PaginatedStrategies {
  return {
    items: Array.from({ length: count }, (_, i) => ({
      id: `11111111-1111-1111-1111-00000000000${i}`,
      name: `Strategy ${i}`,
      mode: "forex" as const,
      direction: "bullish" as const,
      status: "active",
      description: null,
      category_id: null,
      current_version: 1,
      published_version_number: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    })),
    page: { page: 1, page_size: 100, total: count, pages: 1 },
  };
}

function reportFixture(): AnalysisReport {
  return {
    analysis_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    report_schema_version: "1.0",
    engine_version: "4.0.0",
    symbol: "EURUSD",
    mode: "forex",
    strategy: {
      id: "11111111-1111-1111-1111-000000000000",
      name: "Strategy 0",
      mode: "forex",
      direction: "bullish",
      strategy_status: "active",
      version: 1,
      version_status: "published",
      immutable_definition: true,
      rule_set: {
        schema_version: "2.0",
        mode: "forex",
        timeframes: ["M1"],
        direction: "bullish",
        conditions: { logic: "AND", conditions: [], groups: [] },
        confirmation: null,
        invalidation: null,
      },
    },
    required_timeframes: ["M1"],
    evaluated_timeframes: ["M1"],
    data: {
      provider: "mt5",
      quality_status: "ok",
      closed_candles_only: true,
      forming_candle_used: false,
      timeframes: [
        {
          timeframe: "M1",
          requested_candles: 60,
          returned_candles: 60,
          provider_data_status: "ok",
          provenance: "mt5_live",
          provider_warnings: [],
          first_time: 1_700_000_000,
          last_time: 1_700_003_540,
          newest_closed_time: 1_700_003_480,
          stale: false,
          used_candle_count: 59,
        },
      ],
      warnings: [
        { code: "FORMING_CANDLE_EXCLUDED", timeframe: "M1", message: "The forming candle was excluded." },
      ],
      source_note: "Market data: MetaTrader 5 terminal.",
    },
    evaluated_at: "2026-01-01T00:00:00.000+00:00",
    regime: {
      primary_timeframe: "M1",
      primary: {
        timeframe: "M1",
        regime: "uptrend",
        volatility_state: "normal",
        directional_bias: "bullish",
        applicable_buckets: ["trending"],
        evidence: {},
      },
      by_timeframe: [
        {
          timeframe: "M1",
          regime: "uptrend",
          volatility_state: "normal",
          directional_bias: "bullish",
          applicable_buckets: ["trending"],
          evidence: {},
        },
      ],
      agreement: { status: "agreement", detail: "All classified timeframes agree: bullish.", biases: { M1: "bullish" } },
      applicability: {
        required: "any",
        classified: "uptrend",
        applicable: true,
        matched_buckets: ["trending"],
        note: "No regime requirement.",
      },
      note: "Heuristic classification; not a forecast.",
    },
    observations: { by_timeframe: [], note: "" },
    evaluation: {
      direction: "bullish",
      setup_status: "conditions_missing",
      core: {
        kind: "group",
        id: "group_and",
        status: "not_met",
        satisfied: false,
        description: "ALL of 3 condition(s) must hold",
        logic: "AND",
        evidence: {},
        children: [
          {
            kind: "condition",
            id: "c-met",
            status: "met",
            satisfied: true,
            description: "close above 1.10",
            evidence: { last_candle_status: "closed", last_candle_time: 1_700_003_480 },
          },
          {
            kind: "condition",
            id: "c-failed",
            status: "not_met",
            satisfied: false,
            description: "rsi above 50",
            evidence: { reason: "criterion failed" },
          },
          {
            kind: "condition",
            id: "c-unknown",
            status: "inconclusive",
            satisfied: false,
            description: "ema(50) needs history",
            evidence: { reason: "EMA(50) needs at least 50 candles in the window." },
          },
        ],
      },
      confirmation: null,
      invalidation: null,
      core_status: "not_met",
      conditions_summary: { met: 1, not_met: 1, inconclusive: 1, unsupported: 0 },
      closed_candles_only: true,
      forming_candle_used: false,
      warnings: [],
      summary: ["1 required condition(s) not satisfied yet."],
      evaluated_at: "2026-01-01T00:00:00.000+00:00",
    },
    limitations: [
      "This report describes technical conditions on historical market data. It is not a prediction, carries no probability of profit, and is not trading advice.",
    ],
  };
}

function historyFixture(): PaginatedAnalysisReports {
  return {
    items: [
      {
        id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        symbol: "EURUSD",
        mode: "forex",
        strategy_id: "11111111-1111-1111-1111-000000000000",
        strategy_name: "Strategy 0",
        strategy_version: 1,
        setup_status: "conditions_met",
        direction: "bullish",
        regime: "uptrend",
        report_schema_version: "1.0",
        engine_version: "4.0.0",
        evaluated_at: "2026-01-01T00:00:00.000+00:00",
        created_at: "2026-01-01T00:00:00.000+00:00",
      },
    ],
    page: { page: 1, page_size: 20, total: 1, pages: 1 },
  };
}

function setupApi() {
  api.get.mockImplementation((path: string) => {
    if (path === "/api/v1/strategies") return Promise.resolve(strategyList());
    if (path === "/api/v1/analysis/reports") return Promise.resolve(historyFixture());
    return Promise.reject(new Error(`unexpected GET ${path}`));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  setupApi();
});

describe("EngineAnalysis", () => {
  it("renders controls, strategy list and history", async () => {
    render(<EngineAnalysis mode="forex" />);
    expect(await screen.findByLabelText("Symbol")).toHaveValue("EURUSD");
    expect(await screen.findByRole("button", { name: /run analysis/i })).toBeEnabled();
    const history = await screen.findByTestId("history-list");
    expect(history).toHaveTextContent("Strategy 0 (v1)");
    expect(history).toHaveTextContent("Conditions met");
  });

  it("runs an analysis and renders the four-state condition display", async () => {
    const report = reportFixture();
    api.post.mockResolvedValueOnce(report);
    const user = userEvent.setup();
    render(<EngineAnalysis mode="forex" />);
    await user.click(await screen.findByRole("button", { name: /run analysis/i }));

    const tree = await screen.findByTestId("analysis-report");
    // Distinct, accessible states (group AND child can share a state, so
    // query for "at least one").
    expect(screen.getByLabelText("condition met")).toBeInTheDocument();
    expect(screen.getAllByLabelText("condition not met").length).toBeGreaterThan(0);
    expect(screen.getAllByLabelText("condition inconclusive").length).toBeGreaterThan(0);
    // Summary bar counts each state separately.
    expect(tree).toHaveTextContent("1 met");
    expect(tree).toHaveTextContent("1 not_met");
    expect(tree).toHaveTextContent("1 inconclusive");
    // Inconclusive is NEVER displayed as success:
    expect(tree).toHaveTextContent("Conditions missing");
  });

  it("renders regime, data quality and warnings", async () => {
    api.post.mockResolvedValueOnce(reportFixture());
    const user = userEvent.setup();
    render(<EngineAnalysis mode="forex" />);
    await user.click(await screen.findByRole("button", { name: /run analysis/i }));

    const tree = await screen.findByTestId("analysis-report");
    expect(tree).toHaveTextContent("M1: uptrend");
    expect(tree).toHaveTextContent("Timeframe agreement: agreement");
    expect(tree).toHaveTextContent("quality: ok");
    expect(tree).toHaveTextContent("closed candles only");
    expect(tree).toHaveTextContent("The forming candle was excluded.");
  });

  it("shows an honest error and no fake report when MT5 is disconnected", async () => {
    api.post.mockRejectedValueOnce(
      Object.assign(new Error("MetaTrader 5 terminal is not connected"), { code: "MARKET_DATA_UNAVAILABLE" })
    );
    const { toast } = await import("@/hooks/use-toast");
    const user = userEvent.setup();
    render(<EngineAnalysis mode="forex" />);
    await user.click(await screen.findByRole("button", { name: /run analysis/i }));
    await waitFor(() => {
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ title: "Analysis failed" })
      );
    });
    // No fabricated report appears.
    expect(screen.queryByTestId("analysis-report")).not.toBeInTheDocument();
  });

  it("opens a stored report from history", async () => {
    api.get.mockImplementation((path: string) => {
      if (path === "/api/v1/strategies") return Promise.resolve(strategyList());
      if (path === "/api/v1/analysis/reports") return Promise.resolve(historyFixture());
      if (path.startsWith("/api/v1/analysis/reports/")) {
        return Promise.resolve(reportFixture());
      }
      return Promise.reject(new Error(`unexpected GET ${path}`));
    });
    const user = userEvent.setup();
    render(<EngineAnalysis mode="forex" />);
    await user.click(await screen.findByRole("button", { name: /view report/i }));
    expect(await screen.findByTestId("analysis-report")).toBeInTheDocument();
  });
});

describe("ReportView", () => {
  it("renders binary-mode disclaimer in limitations", () => {
    const report = reportFixture();
    report.mode = "binary_options";
    report.limitations = [
      "Binary options mode: market data comes from a MetaTrader 5 terminal. It is NOT your binary options broker's execution feed; prices can differ between feeds. OTC broker feeds are unavailable and are never simulated.",
    ];
    render(<ReportView report={report} />);
    expect(screen.getByTestId("analysis-report")).toHaveTextContent(
      /NOT your binary options broker/i
    );
  });

  it("renders unsupported state distinctly from success", () => {
    const report = reportFixture();
    report.evaluation.core.children!.push({
      kind: "condition",
      id: "c-unsupported",
      status: "unsupported",
      satisfied: false,
      description: "order_block on M1",
      evidence: { reason: "No evaluator registered for this rule type." },
    });
    render(<ReportView report={report} />);
    expect(screen.getByLabelText("rule type unsupported")).toBeInTheDocument();
    expect(screen.getByTestId("analysis-report")).toHaveTextContent("0 unsupported");
  });
});
