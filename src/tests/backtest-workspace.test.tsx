/**
 * Phase 6 — backtesting workspace tests.
 *
 * Covers: loading / empty / error / completed states, config validation
 * gating, mode-specific fields, run history rendering, results table with
 * "Not available" semantics, record evidence sheet, run comparison with
 * configuration diffs, and the accessibility of core controls.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BacktestWorkspace } from "@/components/backtest/backtest-workspace";
import { BacktestRunDetail } from "@/components/backtest/backtest-run-detail";
import { drawdownSeries, fmtNum, exportUrl } from "@/components/backtest/backtest-shared";
import { ApiError } from "@/lib/api/types";
import type {
  BacktestEquityPayload,
  BacktestResultSummary,
  BacktestRun,
  PaginatedBacktestResults,
  PaginatedBacktestRuns,
  PaginatedStrategies,
  StrategyVersion,
} from "@/lib/api/types";

const getMock = vi.fn();
const postMock = vi.fn();
const toastMock = vi.fn();

vi.mock("@/hooks/use-toast", () => ({
  toast: (...args: unknown[]) => toastMock(...args),
}));

vi.mock("@/lib/api/client", () => ({
  api: {
    get: (...args: unknown[]) => getMock(...args),
    post: (...args: unknown[]) => postMock(...args),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

// Recharts in jsdom needs explicit width/height; stub the chart primitives.
vi.mock("recharts", () => {
  const Stub = ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="chart-stub">{children}</div>
  );
  return {
    ResponsiveContainer: Stub,
    LineChart: Stub,
    BarChart: Stub,
    Line: () => null,
    Bar: () => null,
    XAxis: () => null,
    YAxis: () => null,
    CartesianGrid: () => null,
    Tooltip: () => null,
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const strategyList: PaginatedStrategies = {
  items: [
    {
      id: "11111111-1111-1111-1111-111111111111",
      name: "London breakout",
      mode: "forex",
      direction: "bullish",
      status: "active",
      description: null,
      category_id: null,
      current_version: 2,
      published_version_number: 2,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
  ],
  page: { page: 1, page_size: 100, total: 1, pages: 1 },
};

const versions: StrategyVersion[] = [
  {
    id: "v1",
    strategy_id: "11111111-1111-1111-1111-111111111111",
    version: 1,
    status: "superseded",
    changelog: null,
    rule_set: {
      schema_version: "2.0",
      mode: "forex",
      timeframes: ["M15", "H1"],
      direction: "bullish",
      conditions: { logic: "AND", conditions: [], groups: [] },
      confirmation: null,
      invalidation: null,
    },
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "v2",
    strategy_id: "11111111-1111-1111-1111-111111111111",
    version: 2,
    status: "published",
    changelog: null,
    rule_set: {
      schema_version: "2.0",
      mode: "forex",
      timeframes: ["M15", "H1"],
      direction: "bullish",
      conditions: { logic: "AND", conditions: [], groups: [] },
      confirmation: null,
      invalidation: null,
    },
    created_at: "2026-01-02T00:00:00Z",
  },
];

const completedRun: BacktestRun = {
  id: "run-1",
  mode: "forex",
  symbol: "EURUSD",
  timeframe: "M15",
  strategy_id: "11111111-1111-1111-1111-111111111111",
  strategy_name: "London breakout",
  strategy_version: 2,
  status: "completed",
  start_time: "2026-01-05T00:00:00.000Z",
  end_time: "2026-01-06T00:00:00.000Z",
  simulation_mode: "trade_simulation",
  candles_evaluated: 96,
  records_total: 100,
  engine_version: "6.0.0",
  backtest_schema_version: "1.0",
  error: null,
  metrics: {
    note: "Metrics describe this historical simulation only.",
    win_rate_note: "win_rate = wins / (wins + losses)",
    simulation_mode: "trade_simulation",
    data: { total_candles_evaluated: 96, setup_records: 96, trade_records: 4 },
    setups: {
      total_evaluated: 96,
      by_status: { conditions_met: 4, conditions_missing: 90, invalidated: 0, insufficient_data: 2, inconclusive: 0, regime_mismatch: 0 },
      conditions_summary_totals: { met: 120, not_met: 40, inconclusive: 2, unsupported: 0 },
      first_evaluation_time: 1_700_000_000,
      last_evaluation_time: 1_700_100_000,
      breakdowns: { by_regime: [], by_direction: [], by_month: [] },
    },
    trades: {
      total: 4,
      by_outcome: { win: 3, loss: 1, tie: 0, open: 0, unresolved: 0, ambiguous: 0, skipped: 0 },
      decided_trades: 4,
      win_rate: 0.75,
      win_rate_denominator: "wins + losses (decided trades)",
      net_pnl: 12.5,
      gross_profit: 22.5,
      gross_loss: -10,
      avg_win: 7.5,
      avg_loss: -10,
      expectancy: 3.125,
      expectancy_note: "Mean realized P&L per decided trade",
      profit_factor: 2.25,
      profit_factor_note: "gross_profit / |gross_loss|",
      avg_return_pct_per_trade: null,
      avg_r_multiple: null,
      avg_r_note: "Mean realized P&L divided by declared risk amount",
      avg_holding_candles: 2.5,
      exposure_pct: 10.4,
      planned_risk_reward: null,
      breakdowns: {
        by_regime: [{ key: "uptrend", count: 3, net_pnl: 15, wins: 3, losses: 0, win_rate: 1 }],
        by_direction: [],
        by_month: [],
      },
    },
    equity: {
      points_count: 4,
      start_balance: 10000,
      end_balance: 10012.5,
      max_drawdown: { max_drawdown: 10, max_drawdown_pct: 0.1, points: 4 },
      note: "Equity path from realized (closed) trade balances only.",
    },
  },
  data_quality: {
    source: "postgres_candle_cache",
    source_note: "Stored historical candle cache.",
    overall_status: "ok",
    timeframes: [],
    closed_candles_only: true,
    forming_candles_used: false,
    note: "Closed candles only.",
  },
  config: {
    mode: "forex",
    symbol: "EURUSD",
    timeframe: "M15",
    start: "2026-01-05T00:00:00.000Z",
    end: "2026-01-06T00:00:00.000Z",
    warmup_candles: 150,
    max_candles: 20000,
    data_source: "postgres_candle_cache",
    strategy_id: "11111111-1111-1111-1111-111111111111",
    strategy_name: "London breakout",
    strategy_version: 2,
    strategy_version_status: "published",
    rule_set: versions[1].rule_set,
    direction: "bullish",
    notes: null,
    forex_execution: {
      enabled: true,
      spread_pips: 1,
      slippage_pips: 0.5,
      commission_per_lot_per_side: 0,
      stop_loss_pips: 20,
      take_profit_pips: 40,
      risk_mode: "fixed_fractional",
      risk_pct_per_trade: 1,
      fixed_lot_size: 0.01,
      pip_value_per_lot: 10,
      pip_size: null,
      swap_per_lot_per_night: null,
      account_start_balance: 10000,
      sl_tp_same_candle_policy: "conservative",
    },
    binary: null,
    simulation_mode: "trade_simulation",
  },
  created_at: "2026-01-02T10:00:00Z",
  started_at: "2026-01-02T10:00:01Z",
  completed_at: "2026-01-02T10:00:05Z",
};

const runList: PaginatedBacktestRuns = {
  items: [
    {
      id: "run-1",
      mode: "forex",
      symbol: "EURUSD",
      timeframe: "M15",
      strategy_id: "11111111-1111-1111-1111-111111111111",
      strategy_name: "London breakout",
      strategy_version: 2,
      status: "completed",
      simulation_mode: "trade_simulation",
      start_time: "2026-01-05T00:00:00.000Z",
      end_time: "2026-01-06T00:00:00.000Z",
      candles_evaluated: 96,
      records_total: 100,
      win_rate: 0.75,
      net_pnl: 12.5,
      conditions_met: 4,
      error: null,
      created_at: "2026-01-02T10:00:00Z",
      completed_at: "2026-01-02T10:00:05Z",
    },
  ],
  page: { page: 1, page_size: 25, total: 1, pages: 1 },
};

const resultPage: PaginatedBacktestResults = {
  items: [
    {
      id: "res-1",
      run_id: "run-1",
      seq: 0,
      kind: "setup",
      result_time: "2026-01-05T00:15:00.000Z",
      result_time_unix: 1_700_000_100,
      setup_status: "conditions_met",
      direction: "bullish",
      regime: "uptrend",
      outcome: null,
      pnl: null,
      return_pct: null,
      equity_after: null,
      conditions_summary: { met: 2, not_met: 0, inconclusive: 0, unsupported: 0 },
      execution_summary: null,
      warning_count: 1,
      note: null,
    },
    {
      id: "res-2",
      run_id: "run-1",
      seq: 1,
      kind: "trade",
      result_time: "2026-01-05T00:15:00.000Z",
      result_time_unix: 1_700_000_100,
      setup_status: "conditions_met",
      direction: "bullish",
      regime: "uptrend",
      outcome: "win",
      pnl: 7.5,
      return_pct: 0.0075,
      equity_after: 10007.5,
      conditions_summary: null,
      execution_summary: { mode: "forex", entry_price: 1.1 },
      warning_count: 0,
      note: null,
    },
  ],
  page: { page: 1, page_size: 25, total: 2, pages: 1 },
};

const equityPayload: BacktestEquityPayload = {
  run_id: "run-1",
  simulation_mode: "trade_simulation",
  points: [
    { seq: 1, time: "2026-01-05T01:00:00Z", time_unix: 1_700_000_400, equity: 10010, pnl: 10, outcome: "win" },
    { seq: 2, time: "2026-01-05T02:00:00Z", time_unix: 1_700_000_760, equity: 10000, pnl: -10, outcome: "loss" },
    { seq: 3, time: "2026-01-05T03:00:00Z", time_unix: 1_700_001_120, equity: 10012.5, pnl: 12.5, outcome: "win" },
  ],
  note: "Realized closed-trade balance path.",
};

function mockWorkspaceHappyPath() {
  getMock.mockImplementation((path: string) => {
    if (path.startsWith("/api/v1/strategies/1111")) {
      if (path.endsWith("/versions")) return Promise.resolve(versions);
      return Promise.resolve({
        ...strategyList.items[0],
        rule_set: versions[1].rule_set,
        summary: [],
      });
    }
    if (path === "/api/v1/strategies") return Promise.resolve(strategyList);
    if (path === "/api/v1/backtests") return Promise.resolve(runList);
    if (path.endsWith("/results")) return Promise.resolve(resultPage);
    if (path.endsWith("/equity")) return Promise.resolve(equityPayload);
    if (path.endsWith("/run-1")) return Promise.resolve(completedRun);
    return Promise.reject(new ApiError({ code: "NOT_FOUND", message: `unmocked ${path}`, status: 404 }));
  });
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

describe("backtest-shared helpers", () => {
  it("renders Not available for missing metrics", () => {
    expect(fmtNum(null)).toBe("Not available");
    expect(fmtNum(undefined)).toBe("Not available");
    expect(fmtNum(12.5)).toBe("12.50");
  });

  it("derives drawdown only from the backend equity path", () => {
    const dd = drawdownSeries(equityPayload.points);
    expect(dd).toHaveLength(3);
    expect(dd[1].drawdown).toBeCloseTo(10);
    expect(dd[2].drawdown).toBeCloseTo(0);
    expect(drawdownSeries([])).toEqual([]);
  });

  it("keeps gateway routing in export URLs", () => {
    delete process.env.NEXT_PUBLIC_BACKEND_ORIGIN;
    const url = exportUrl("/api/v1/backtests/run-1/export", { scope: "summary" });
    expect(url).toContain("/api/v1/backtests/run-1/export");
    expect(url).toContain("XTransformPort=8000");
    expect(url).toContain("scope=summary");
  });
});

// ---------------------------------------------------------------------------
// Workspace: history + config
// ---------------------------------------------------------------------------

describe("BacktestWorkspace", () => {
  beforeEach(() => {
    mockWorkspaceHappyPath();
  });

  it("renders run history with status, win rate and net P&L", async () => {
    render(<BacktestWorkspace mode="forex" />);
    await waitFor(() => expect(screen.getByText("London breakout")).toBeTruthy());
    expect(screen.getByText("Completed")).toBeTruthy();
    expect(screen.getByText("75.0%")).toBeTruthy();
    expect(screen.getByText("12.50")).toBeTruthy();
  });

  it("shows an empty state when no runs exist", async () => {
    getMock.mockImplementation((path: string) => {
      if (path === "/api/v1/strategies") return Promise.resolve({ ...strategyList, items: [] });
      if (path === "/api/v1/backtests")
        return Promise.resolve({ items: [], page: { page: 1, page_size: 25, total: 0, pages: 1 } });
      return Promise.resolve(strategyList);
    });
    render(<BacktestWorkspace mode="forex" />);
    await waitFor(() =>
      expect(screen.getByText(/No backtests yet for this mode/)).toBeTruthy(),
    );
  });

  it("surfaces API failures with a clear error toast", async () => {
    getMock.mockRejectedValue(
      new ApiError({ code: "BACKEND_UNREACHABLE", message: "backend unreachable", status: 0 }),
    );
    render(<BacktestWorkspace mode="forex" />);
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: "Could not load run history" }),
      ),
    );
  });

  it("gates submission on client-side validation", async () => {
    const user = userEvent.setup();
    render(<BacktestWorkspace mode="forex" />);
    await waitFor(() => expect(screen.getByText("London breakout")).toBeTruthy());
    const startButton = screen.getByRole("button", { name: /Start backtest/i });
    await user.click(startButton);
    const alerts = await screen.findAllByText(/Select a strategy\./);
    expect(alerts.length).toBeGreaterThan(0);
    expect(postMock).not.toHaveBeenCalled();
  });

  it("shows mode-specific assumption fields for forex", async () => {
    const user = userEvent.setup();
    render(<BacktestWorkspace mode="forex" />);
    await waitFor(() => expect(screen.getByLabelText(/Simulate trades/i)).toBeTruthy());
    await user.click(screen.getByLabelText(/Simulate trades/i));
    expect(screen.getByLabelText("Spread (pips)")).toBeTruthy();
    expect(screen.getByLabelText("Stop-loss (pips)")).toBeTruthy();
    expect(screen.queryByLabelText(/Expiry \(candles\)/)).toBeNull();
  });

  it("shows binary expiry/payout fields for binary mode", async () => {
    getMock.mockImplementation((path: string) => {
      if (path === "/api/v1/strategies")
        return Promise.resolve({
          ...strategyList,
          items: [{ ...strategyList.items[0], mode: "binary_options" }],
        });
      if (path === "/api/v1/backtests")
        return Promise.resolve({ items: [], page: { page: 1, page_size: 25, total: 0, pages: 1 } });
      return Promise.resolve(strategyList);
    });
    render(<BacktestWorkspace mode="binary_options" />);
    await waitFor(() => expect(screen.getByLabelText("Expiry (candles)")).toBeTruthy());
    expect(screen.getByLabelText("Payout (0–1)")).toBeTruthy();
    expect(screen.getByText(/MT5 is NOT your broker/i)).toBeTruthy();
    expect(screen.queryByLabelText("Spread (pips)")).toBeNull();
  });

  it("creates a run through the API when configuration is valid", async () => {
    const user = userEvent.setup();
    postMock.mockResolvedValue({ ...completedRun, status: "queued", metrics: null });
    render(<BacktestWorkspace mode="forex" />);
    await waitFor(() => expect(screen.getByText("London breakout")).toBeTruthy());
    // Select strategy, then version, then fill the rest.
    await user.click(screen.getByLabelText("Strategy"));
    await user.click(await screen.findByRole("option", { name: "London breakout" }));
    await user.click(screen.getByLabelText("Strategy version"));
    await user.click(await screen.findByRole("option", { name: "v2 · published" }));
    await user.click(screen.getByLabelText("Timeframe (declared by the strategy)"));
    await user.click(await screen.findByRole("option", { name: "M15" }));
    await user.type(screen.getByLabelText("Start (UTC)"), "2026-01-05T00:00");
    await user.type(screen.getByLabelText("End (UTC)"), "2026-01-06T00:00");
    await user.click(screen.getByRole("button", { name: /Start backtest/i }));
    await waitFor(() => expect(postMock).toHaveBeenCalled());
    const body = postMock.mock.calls[0][1] as Record<string, unknown>;
    expect(body.mode).toBe("forex");
    expect(body.strategy_version).toBe(2);
    expect(body.symbol).toBe("EURUSD");
    expect(body.forex_execution).toBeUndefined(); // simulate trades defaults OFF
  });
});

// ---------------------------------------------------------------------------
// Run detail
// ---------------------------------------------------------------------------

describe("BacktestRunDetail", () => {
  beforeEach(() => {
    mockWorkspaceHappyPath();
  });

  it("renders metrics overview with sample size and honesty notes", async () => {
    render(<BacktestRunDetail runId="run-1" onBack={() => {}} />);
    await waitFor(() => expect(screen.getByText("Results overview")).toBeTruthy());
    expect(screen.getAllByText("96").length).toBeGreaterThan(0); // candles evaluated
    expect(screen.getByText("75.0%")).toBeTruthy(); // win rate
    expect(screen.getByText("2.25")).toBeTruthy(); // profit factor
    expect(screen.getAllByText(/Historical simulation statistics only/i).length).toBeGreaterThan(0);
  });

  it("renders equity and drawdown charts from backend points", async () => {
    render(<BacktestRunDetail runId="run-1" onBack={() => {}} />);
    await waitFor(() => expect(screen.getAllByTestId("chart-stub").length).toBeGreaterThanOrEqual(2));
  });

  it("lists chronological results with outcomes and warnings", async () => {
    render(<BacktestRunDetail runId="run-1" onBack={() => {}} />);
    await waitFor(() => expect(screen.getByText("Detailed results")).toBeTruthy());
    await waitFor(() => expect(screen.getAllByText("Conditions met").length).toBeGreaterThan(0));
    expect(screen.getAllByText("Win").length).toBeGreaterThan(0);
    expect(screen.getAllByText("7.50").length).toBeGreaterThan(0);
    // warning indicator rendered for the setup row (amber count badge)
    const amber = document.querySelectorAll(".text-amber-600");
    expect(amber.length).toBeGreaterThan(0);
  });

  it("opens the evidence sheet for a record", async () => {
    getMock.mockImplementation((path: string) => {
      if (path.endsWith("/results/0")) {
        return Promise.resolve({
          ...resultPage.items[0],
          evaluation: {
            direction: "bullish",
            setup_status: "conditions_met",
            core: {
              kind: "group",
              id: "g",
              satisfied: true,
              status: "met",
              description: "ALL of 1 condition(s) must hold",
              evidence: {},
              children: [
                {
                  kind: "condition",
                  id: "c1",
                  satisfied: true,
                  status: "met",
                  description: "close above 1.10",
                  evidence: { criterion: "close > 1.10", current_value: 1.1002 },
                },
              ],
            },
            confirmation: null,
            invalidation: null,
            warnings: [{ code: "TIMESTAMP_GAPS", timeframe: "M15", message: "1 gap" }],
            summary: [],
            evaluated_at: "2026-01-05T00:15:00.000Z",
          },
          execution: null,
          warnings: [{ code: "TIMESTAMP_GAPS", timeframe: "M15", message: "1 gap" }],
        });
      }
      return mockWorkspaceHappyPath() as unknown as void;
    });
    // Reinstall the base mock, then override.
    mockWorkspaceHappyPath();
    getMock.mockImplementation((path: string) => {
      if (path.endsWith("/results/0")) {
        return Promise.resolve({
          ...resultPage.items[0],
          evaluation: {
            direction: "bullish",
            setup_status: "conditions_met",
            core: {
              kind: "group",
              id: "g",
              satisfied: true,
              status: "met",
              description: "ALL of 1 condition(s) must hold",
              evidence: {},
              children: [
                {
                  kind: "condition",
                  id: "c1",
                  satisfied: true,
                  status: "met",
                  description: "close above 1.10",
                  evidence: { criterion: "close > 1.10" },
                },
              ],
            },
            confirmation: null,
            invalidation: null,
            warnings: [],
            summary: [],
            evaluated_at: "2026-01-05T00:15:00.000Z",
          },
          execution: null,
          warnings: [{ code: "TIMESTAMP_GAPS", timeframe: "M15", message: "1 gap" }],
        });
      }
      if (path.startsWith("/api/v1/strategies/1111") && path.endsWith("/versions"))
        return Promise.resolve(versions);
      if (path === "/api/v1/strategies") return Promise.resolve(strategyList);
      if (path === "/api/v1/backtests") return Promise.resolve(runList);
      if (path.endsWith("/results")) return Promise.resolve(resultPage);
      if (path.endsWith("/equity")) return Promise.resolve(equityPayload);
      if (path.endsWith("/run-1")) return Promise.resolve(completedRun);
      return Promise.reject(new ApiError({ code: "NOT_FOUND", message: `unmocked ${path}`, status: 404 }));
    });

    const user = userEvent.setup();
    render(<BacktestRunDetail runId="run-1" onBack={() => {}} />);
    await waitFor(() => expect(screen.getByText("Detailed results")).toBeTruthy());
    await waitFor(async () => {
      const rows = screen.getAllByText("setup");
      expect(rows.length).toBeGreaterThan(0);
    });
    await user.click(screen.getAllByText("setup")[0]);
    await waitFor(() => expect(screen.getByText(/Record #0/)).toBeTruthy());
    expect(screen.getByText("close above 1.10")).toBeTruthy();
    expect(screen.getByText("TIMESTAMP_GAPS")).toBeTruthy();
  });

  it("renders failed runs with their persisted error", async () => {
    getMock.mockImplementation((path: string) => {
      if (path === "/api/v1/strategies") return Promise.resolve(strategyList);
      if (path.endsWith("/equity"))
        return Promise.resolve({ ...equityPayload, points: [], note: "No equity series exists." });
      if (path.endsWith("/results"))
        return Promise.resolve({ items: [], page: { page: 1, page_size: 25, total: 0, pages: 1 } });
      if (path.endsWith("/run-1"))
        return Promise.resolve({
          ...completedRun,
          status: "failed",
          metrics: null,
          error: { code: "INSUFFICIENT_HISTORY", message: "No closed candles available.", details: [] },
        });
      return Promise.reject(new ApiError({ code: "NOT_FOUND", message: path, status: 404 }));
    });
    render(<BacktestRunDetail runId="run-1" onBack={() => {}} />);
    await waitFor(() => expect(screen.getByText("INSUFFICIENT_HISTORY")).toBeTruthy());
    expect(screen.getByText(/No closed candles available/)).toBeTruthy();
  });

  it("shows queued status with an honest waiting message", async () => {
    getMock.mockImplementation((path: string) => {
      if (path === "/api/v1/strategies") return Promise.resolve(strategyList);
      if (path.endsWith("/equity"))
        return Promise.resolve({ ...equityPayload, points: [], note: "No equity series exists." });
      if (path.endsWith("/results"))
        return Promise.resolve({ items: [], page: { page: 1, page_size: 25, total: 0, pages: 1 } });
      if (path.endsWith("/run-1"))
        return Promise.resolve({ ...completedRun, status: "queued", metrics: null });
      return Promise.reject(new ApiError({ code: "NOT_FOUND", message: path, status: 404 }));
    });
    render(<BacktestRunDetail runId="run-1" onBack={() => {}} />);
    await waitFor(() =>
      expect(screen.getByText(/Waiting for the runner/)).toBeTruthy(),
    );
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
  });

  it("pages through results with pagination controls", async () => {
    const user = userEvent.setup();
    render(<BacktestRunDetail runId="run-1" onBack={() => {}} />);
    await waitFor(() => expect(screen.getByText(/Page 1 of 1/)).toBeTruthy());
    const next = screen.getByRole("button", { name: "Next" });
    expect((next as HTMLButtonElement).disabled).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Comparison
// ---------------------------------------------------------------------------

describe("run comparison", () => {
  it("flags non-equivalent windows in the comparison", async () => {
    const secondRun: BacktestRun = {
      ...completedRun,
      id: "run-2",
      symbol: "USDJPY",
      start_time: "2026-02-01T00:00:00.000Z",
      end_time: "2026-02-05T00:00:00.000Z",
      strategy_version: 1,
      simulation_mode: "setup_only",
      config: {
        ...completedRun.config!,
        symbol: "USDJPY",
        start_time: undefined,
        start: "2026-02-01T00:00:00.000Z",
        end: "2026-02-05T00:00:00.000Z",
        strategy_version: 1,
        simulation_mode: "setup_only",
        forex_execution: null,
      } as BacktestRun["config"],
      metrics: null,
    };
    const list2: PaginatedBacktestRuns = {
      items: [
        runList.items[0],
        {
          ...runList.items[0],
          id: "run-2",
          symbol: "USDJPY",
          strategy_version: 1,
          simulation_mode: "setup_only",
          win_rate: null,
          net_pnl: null,
        },
      ],
      page: runList.page,
    };
    getMock.mockImplementation((path: string) => {
      if (path === "/api/v1/strategies") return Promise.resolve(strategyList);
      if (path === "/api/v1/backtests") return Promise.resolve(list2);
      if (path.endsWith("/run-1")) return Promise.resolve(completedRun);
      if (path.endsWith("/run-2")) return Promise.resolve(secondRun);
      return Promise.reject(new ApiError({ code: "NOT_FOUND", message: path, status: 404 }));
    });

    const user = userEvent.setup();
    render(<BacktestWorkspace mode="forex" />);
    await waitFor(() => expect(screen.getAllByRole("checkbox")).toHaveLength(2));
    await user.click(screen.getAllByRole("checkbox")[0]);
    await user.click(screen.getAllByRole("checkbox")[1]);
    await user.click(screen.getByRole("button", { name: /Compare selected/i }));
    await waitFor(() => expect(screen.getByText("Run comparison")).toBeTruthy());
    expect(screen.getByText(/NOT directly equivalent/)).toBeTruthy();
    // configuration diff shows the differing fields
    expect(screen.getAllByText("Symbol").length).toBeGreaterThan(0);
  });
});

// silence unused-import lint for type-only fixtures
void (null as unknown as BacktestResultSummary | null);
