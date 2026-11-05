/**
 * Phase 8 — research workspace tests.
 *
 * Covers: loading / empty / error / completed states of the run history,
 * the configuration panel's size warnings and validation gating, the run
 * detail views (candidates ranking, walk-forward windows, robustness
 * scenarios, summary disclaimers), the save-candidate-as-draft dialog
 * (explicit action, never automatic), and the shared helpers
 * (combinations/walks estimates, "Not available" semantics).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ResearchWorkspace } from "@/components/research/research-workspace";
import { ResearchRunDetail } from "@/components/research/research-run-detail";
import {
  estimateCombinations,
  estimateWalks,
  fmtScore,
} from "@/components/research/research-shared";
import {
  parseValues,
  ResearchConfigPanel,
} from "@/components/research/research-config-panel";
import { ApiError } from "@/lib/api/types";
import type {
  PaginatedResearchCandidates,
  PaginatedResearchRuns,
  PaginatedResearchWindows,
  PaginatedStrategies,
  ResearchRun,
  ResearchScenariosPayload,
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

const runListItem = {
  id: "run-1",
  mode: "forex" as const,
  symbol: "EURUSD",
  timeframe: "M15",
  validation_mode: "holdout" as const,
  strategy_id: "11111111-1111-1111-1111-111111111111",
  strategy_name: "London breakout",
  strategy_version: 2,
  status: "completed" as const,
  objective_metric: "net_pnl",
  combination_count: 3,
  simulation_mode: "trade_simulation" as const,
  start_time: "2025-01-01T00:00:00.000Z",
  end_time: "2025-03-01T00:00:00.000Z",
  candidates_total: 3,
  candidates_evaluated: 3,
  windows_completed: null,
  best_candidate: {
    candidate_index: 2,
    label: "px.params.value=1.15",
    rank: 1,
    score: 120.5,
    times_selected: 0,
  },
  error: null,
  created_at: "2026-01-01T00:00:00Z",
  completed_at: "2026-01-01T00:05:00Z",
};

const historyPage: PaginatedResearchRuns = {
  items: [runListItem],
  page: { page: 1, page_size: 25, total: 1, pages: 1 },
};

const fullRun: ResearchRun = {
  id: "run-1",
  owner_id: "local",
  mode: "forex",
  symbol: "EURUSD",
  timeframe: "M15",
  validation_mode: "holdout",
  strategy_id: "11111111-1111-1111-1111-111111111111",
  strategy_name: "London breakout",
  strategy_version: 2,
  strategy_version_id: "v-1",
  status: "completed",
  start_time: "2025-01-01T00:00:00.000Z",
  end_time: "2025-03-01T00:00:00.000Z",
  simulation_mode: "trade_simulation",
  objective: { metric: "net_pnl", note: "Simulated net P&L after the configured costs." },
  combination_count: 3,
  walk_budget: 1500,
  progress: {
    phase: "completed",
    candidates_total: 3,
    candidates_evaluated: 3,
    candidates_failed: 0,
    candidates_invalid: 0,
    windows_total: 0,
    windows_completed: 0,
    windows_skipped: 0,
    scenarios_total: 3,
    scenarios_completed: 3,
    walks_executed: 8,
  },
  walks_executed: 8,
  aggregate: {
    objective: {
      metric: "net_pnl",
      direction: "maximize",
      note: "Simulated net P&L after the configured costs.",
      constraints: { min_decided_trades: null, max_drawdown_pct: null },
    },
    best_candidate: {
      candidate_index: 2,
      label: "px.params.value=1.15",
      rank: 1,
      score: 120.5,
      times_selected: 0,
    },
    out_of_sample: {
      windows_aggregated: 1,
      windows_with_trades: 1,
      trades: {
        total: 5,
        decided_trades: 5,
        wins: 3,
        losses: 2,
        win_rate: 0.6,
        net_pnl: 42.0,
        expectancy: 8.4,
        profit_factor: null,
      },
      max_drawdown: { max_per_window_pct: 1.8, note: "per window" },
      per_window: [],
    },
    stability_views: {
      time_periods: { note: "Compare in-sample vs out-of-sample months." },
      market_regimes: { note: "Regime labels from the deterministic classifier." },
      data_quality: { overall_status: "ok", warning_codes: [], note: "No warnings." },
    },
    warnings: [],
    disclaimers: [
      "Optimization and validation results are historical simulations; they do not guarantee future performance.",
    ],
  },
  data_quality: {},
  error: null,
  config: {},
  research_version: "8.0.0",
  engine_version: "6.0.0",
  research_schema_version: "1.0",
  created_at: "2026-01-01T00:00:00Z",
  started_at: "2026-01-01T00:00:01Z",
  completed_at: "2026-01-01T00:05:00Z",
};

const candidatesPage: PaginatedResearchCandidates = {
  items: [
    {
      id: "c-0",
      run_id: "run-1",
      candidate_index: 0,
      status: "evaluated",
      params: { patch: [], label: "original parameters" },
      score: 80.0,
      rank: 2,
      selectable: true,
      constraint_violations: [],
      rank_note: null,
      times_selected: 0,
      in_sample_trades: {
        decided_trades: 40,
        win_rate: 0.55,
        net_pnl: 80.0,
        expectancy: 2.0,
        profit_factor: null,
      },
      out_of_sample_trades: null,
      error: null,
    },
    {
      id: "c-1",
      run_id: "run-1",
      candidate_index: 1,
      status: "evaluated",
      params: {
        patch: [{ condition_id: "px", field: "params.value", value: 1.15 }],
        label: "px.params.value=1.15",
      },
      score: 120.5,
      rank: 1,
      selectable: true,
      constraint_violations: [],
      rank_note: null,
      times_selected: 0,
      in_sample_trades: {
        decided_trades: 38,
        win_rate: 0.58,
        net_pnl: 95.0,
        expectancy: 2.5,
        profit_factor: null,
      },
      out_of_sample_trades: {
        decided_trades: 5,
        win_rate: 0.6,
        net_pnl: 42.0,
        expectancy: 8.4,
        profit_factor: null,
      },
      error: null,
    },
  ],
  page: { page: 1, page_size: 25, total: 2, pages: 1 },
};

const windowsPage: PaginatedResearchWindows = {
  items: [
    {
      id: "w-0",
      window_index: 0,
      status: "completed",
      train_start: "2025-01-01T00:00:00.000Z",
      train_end: "2025-01-20T00:00:00.000Z",
      test_start: "2025-01-20T00:00:00.000Z",
      test_end: "2025-01-30T00:00:00.000Z",
      train_evaluations: [],
      selected_candidate_index: 1,
      selected_params: { patch: [], label: "px.params.value=1.15" },
      selection_score: 30.0,
      test_trades: { decided_trades: 5, win_rate: 0.6, net_pnl: 42.0, expectancy: 8.4 },
      note: null,
      error: null,
    },
  ],
  page: { page: 1, page_size: 50, total: 1, pages: 1 },
};

const scenariosPayload: ResearchScenariosPayload = {
  items: [
    {
      id: "s-0",
      run_id: "run-1",
      scenario_index: 0,
      category: "cost_variant",
      label: "Cost variant 1: commission_per_lot_per_side=5.0",
      assumptions: { kind: "execution_cost", market: "forex", overrides: { commission_per_lot_per_side: 5.0 } },
      metrics: { out_of_sample: {} },
      delta: {
        net_pnl: { baseline: 42.0, scenario: 30.0, change: -12.0 },
        win_rate: { baseline: 0.6, scenario: 0.6, change_pp: 0 },
      },
      status: "completed",
      note: "Shows sensitivity to friction — not proof of robustness.",
      error: null,
    },
  ],
};

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

describe("research shared helpers", () => {
  it("estimates combinations including the original candidate", () => {
    expect(estimateCombinations([{ values: [1, 2, 3] }, { values: [10, 20] }])).toBe(7);
    expect(estimateCombinations([])).toBe(0);
  });

  it("estimates the walk budget per validation mode", () => {
    const base = {
      combinations: 3,
      costVariants: 0,
      paramCount: 1,
      paramSensitivity: true,
      bootstrap: false,
    };
    // neighbours = 2 per parameter (param_sensitivity on) are included.
    expect(estimateWalks({ ...base, validationMode: "in_sample_only", windows: 0 })).toBe(6);
    expect(estimateWalks({ ...base, validationMode: "holdout", windows: 0 })).toBe(7);
    expect(
      estimateWalks({ ...base, validationMode: "walk_forward", windows: 4 }),
    ).toBe(16);
  });

  it("parses value lists keeping numeric types", () => {
    expect(parseValues("1.10, 1.15, 1.2")).toEqual([1.1, 1.15, 1.2]);
    expect(parseValues("above\nbelow")).toEqual(["above", "below"]);
  });

  it("formats scores with metric-specific semantics", () => {
    expect(fmtScore("win_rate", 0.6)).toBe("60.0%");
    expect(fmtScore("net_pnl", 12.345)).toBe("12.35");
    expect(fmtScore("net_pnl", null)).toBe("Not available");
  });
});

// ---------------------------------------------------------------------------
// Workspace: history + states
// ---------------------------------------------------------------------------

describe("ResearchWorkspace", () => {
  it("shows the empty state and the config panel when no runs exist", async () => {
    getMock.mockImplementation((path: string) => {
      if (path === "/api/v1/research/optimization") {
        return Promise.resolve({ items: [], page: { page: 1, page_size: 25, total: 0, pages: 1 } });
      }
      if (path === "/api/v1/strategies") return Promise.resolve(strategyList);
      if (path.includes("/versions")) return Promise.resolve([]);
      return Promise.resolve({ parameters: [] });
    });
    render(<ResearchWorkspace mode="forex" />);
    await waitFor(() => expect(screen.getByTestId("research-history-empty")).toBeInTheDocument());
    expect(screen.getByText("Configure research run")).toBeInTheDocument();
  });

  it("renders the completed run history", async () => {
    getMock.mockImplementation((path: string) => {
      if (path === "/api/v1/research/optimization") return Promise.resolve(historyPage);
      if (path === "/api/v1/strategies") return Promise.resolve(strategyList);
      if (path.includes("/versions")) return Promise.resolve([]);
      return Promise.resolve({ parameters: [] });
    });
    render(<ResearchWorkspace mode="forex" />);
    await waitFor(() => expect(screen.getByTestId("research-history-table")).toBeInTheDocument());
    const table = screen.getByTestId("research-history-table");
    expect(table).toHaveTextContent("London breakout");
    expect(table).toHaveTextContent("Holdout (IS → OOS)");
    expect(table).toHaveTextContent("Completed");
  });

  it("surfaces backend errors with a toast", async () => {
    getMock.mockImplementation(() =>
      Promise.reject(new ApiError({ code: "BACKEND_UNREACHABLE", message: "Backend unreachable", status: 0 })),
    );
    render(<ResearchWorkspace mode="forex" />);
    await waitFor(() => expect(toastMock).toHaveBeenCalled());
    expect(toastMock.mock.calls[0][0]).toMatchObject({ title: "Could not load research history" });
  });
});

// ---------------------------------------------------------------------------
// Config panel: warnings and gating
// ---------------------------------------------------------------------------

describe("ResearchConfigPanel", () => {
  function mockStrategyData() {
    getMock.mockImplementation((path: string) => {
      if (path === "/api/v1/strategies") return Promise.resolve(strategyList);
      if (path.includes("/versions")) {
        return Promise.resolve([
          {
            id: "v-2",
            version: 2,
            status: "published",
            rule_set: {
              schema_version: "2.0",
              mode: "forex",
              timeframes: ["M15"],
              direction: "bullish",
              conditions: { logic: "AND", conditions: [], groups: [] },
            },
            changelog: "init",
          },
        ]);
      }
      if (path.includes("parameter-schema")) {
        return Promise.resolve({
          parameters: [
            {
              condition_id: "px",
              condition_type: "price_level",
              timeframe: "M15",
              fields: [{ field: "params.value", current: 1.1, type: "number" }],
            },
          ],
        });
      }
      return Promise.resolve({});
    });
  }

  it("warns when the search space exceeds the platform limits", async () => {
    mockStrategyData();
    render(<ResearchConfigPanel mode="forex" onRunCreated={() => {}} />);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByLabelText("Strategy")).toBeInTheDocument());
    await user.click(screen.getByLabelText("Strategy"));
    await user.click(await screen.findByRole("option", { name: "London breakout" }));
    await waitFor(() => expect(screen.getByLabelText("Strategy version")).toBeEnabled());
    await waitFor(() => expect(screen.getByLabelText("Add parameter")).toBeInTheDocument());
    // Radix Select: add the inventory parameter.
    await user.click(screen.getByLabelText("Add parameter"));
    await waitFor(() => screen.getByRole("option", { name: /px · value/ }));
    await user.click(screen.getByRole("option", { name: /px · value/ }));
    const values = screen.getByLabelText(/Allowed values for px · value/);
    const many = Array.from({ length: 220 }, (_, i) => (1 + i * 0.01).toFixed(2)).join(",");
    fireEvent.change(values, { target: { value: many } });
    await waitFor(() =>
      expect(screen.getByText(/exceed the 200 limit/)).toBeInTheDocument(),
    );
    // Submit stays gated while the configuration is too large.
    expect(screen.getByRole("button", { name: /Start research run/ })).toBeDisabled();
  });

  it("blocks submission with client-side errors when nothing is configured", async () => {
    mockStrategyData();
    render(<ResearchConfigPanel mode="forex" onRunCreated={() => {}} />);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByRole("button", { name: /Start research run/ })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /Start research run/ }));
    await waitFor(() =>
      expect(screen.getByText("Select a strategy.")).toBeInTheDocument(),
    );
    expect(postMock).not.toHaveBeenCalled();
  });

  it("submits a valid holdout configuration to the research endpoint", async () => {
    mockStrategyData();
    postMock.mockResolvedValue(runListItem as unknown as ResearchRun);
    render(<ResearchConfigPanel mode="forex" onRunCreated={() => {}} />);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByLabelText("Strategy")).toBeInTheDocument());
    await user.click(screen.getByLabelText("Strategy"));
    await user.click(await screen.findByRole("option", { name: "London breakout" }));
    await waitFor(() => expect(screen.getByLabelText("Strategy version")).toBeEnabled());
    await waitFor(() => expect(screen.getByLabelText("Add parameter")).toBeInTheDocument());
    await user.click(screen.getByLabelText("Add parameter"));
    await waitFor(() => screen.getByRole("option", { name: /px · value/ }));
    await user.click(screen.getByRole("option", { name: /px · value/ }));
    await user.type(screen.getByLabelText(/Allowed values for px · value/), "1.10, 1.15");
    await user.type(screen.getByLabelText("Start (UTC)"), "2025-01-01T00:00");
    await user.type(screen.getByLabelText("End (UTC)"), "2025-03-01T00:00");

    await user.click(screen.getByRole("button", { name: /Start research run/ }));
    await waitFor(() => expect(postMock).toHaveBeenCalled());
    const [path, body] = postMock.mock.calls[0];
    expect(path).toBe("/api/v1/research/optimization");
    expect(body.validation_mode).toBe("holdout");
    expect(body.objective).toEqual({ metric: "conditions_met_count", constraints: {} });
    expect(body.parameters).toEqual([
      { condition_id: "px", field: "params.value", values: [1.1, 1.15] },
    ]);
    expect(body.holdout).toEqual({ in_sample_pct: 0.7 });
  });
});

// ---------------------------------------------------------------------------
// Run detail: candidates / walk-forward / robustness / summary
// ---------------------------------------------------------------------------

describe("ResearchRunDetail", () => {
  it("renders loading, then the candidate ranking with IS and OOS columns", async () => {
    getMock.mockImplementation((path: string) => {
      if (path.endsWith("/run-1")) return Promise.resolve(fullRun);
      if (path.includes("/candidates") && !path.includes("/candidates/1")) {
        return Promise.resolve(candidatesPage);
      }
      if (path.endsWith("/candidates/1")) {
        return Promise.resolve({ ...candidatesPage.items[1], in_sample: {}, out_of_sample: {}, rule_set: {} });
      }
      if (path.includes("/robustness")) return Promise.resolve(scenariosPayload);
      if (path.includes("/walk-forward")) return Promise.resolve(windowsPage);
      return Promise.resolve({});
    });
    render(<ResearchRunDetail runId="run-1" onBack={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("research-run-detail")).toBeInTheDocument());
    // Candidates load in a separate request after the run detail shell; wait
    // for the ranking rather than racing the second fetch.
    expect(await screen.findByText("px.params.value=1.15")).toBeInTheDocument();
    expect(screen.getByText("original parameters")).toBeInTheDocument();
    expect(
      screen.getByText(/undefined metrics are never ranked as zero/i),
    ).toBeInTheDocument();
    // OOS values render for the walked candidate, "not walked" for the other.
    expect(screen.getByText("42.00")).toBeInTheDocument();
    expect(screen.getByText("not walked")).toBeInTheDocument();
    // Sample sizes are visible next to scores.
    expect(screen.getAllByText("40").length).toBeGreaterThan(0);
  });

  it("shows walk-forward windows with explicit train/test periods", async () => {
    const wfRun: ResearchRun = {
      ...fullRun,
      validation_mode: "walk_forward",
    };
    getMock.mockImplementation((path: string) => {
      if (path.endsWith("/run-1")) return Promise.resolve(wfRun);
      if (path.includes("/candidates")) return Promise.resolve(candidatesPage);
      if (path.includes("/robustness")) return Promise.resolve(scenariosPayload);
      if (path.includes("/walk-forward")) return Promise.resolve(windowsPage);
      return Promise.resolve({});
    });
    render(<ResearchRunDetail runId="run-1" onBack={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("research-run-detail")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("tab", { name: "Walk-forward" }));
    await waitFor(() => expect(screen.getAllByText(/2025-01-20 00:00 UTC/).length).toBeGreaterThanOrEqual(2));
    expect(
      screen.getByText(/train_end always equals test_start/i),
    ).toBeInTheDocument();
  });

  it("renders robustness scenarios with deltas and documented limits", async () => {
    getMock.mockImplementation((path: string) => {
      if (path.endsWith("/run-1")) return Promise.resolve(fullRun);
      if (path.includes("/candidates")) return Promise.resolve(candidatesPage);
      if (path.includes("/robustness")) return Promise.resolve(scenariosPayload);
      return Promise.resolve({});
    });
    render(<ResearchRunDetail runId="run-1" onBack={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("research-run-detail")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("tab", { name: "Robustness" }));
    await waitFor(() => expect(screen.getAllByTestId("research-scenario")[0]).toBeInTheDocument());
    expect(screen.getByText(/Cost variant 1/)).toBeInTheDocument();
    expect(screen.getByText(/not proof of robustness/i)).toBeInTheDocument();
  });

  it("shows the aggregate disclaimers in the summary tab", async () => {
    getMock.mockImplementation((path: string) => {
      if (path.endsWith("/run-1")) return Promise.resolve(fullRun);
      if (path.includes("/candidates")) return Promise.resolve(candidatesPage);
      if (path.includes("/robustness")) return Promise.resolve(scenariosPayload);
      return Promise.resolve({});
    });
    render(<ResearchRunDetail runId="run-1" onBack={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("research-run-detail")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("tab", { name: "Summary" }));
    await waitFor(() =>
      expect(
        screen.getByText(/do not guarantee future performance/i),
      ).toBeInTheDocument(),
    );
    expect(screen.getByTestId("oos-summary")).toBeInTheDocument();
  });

  it("saves a candidate as a NEW draft version via the explicit dialog", async () => {
    getMock.mockImplementation((path: string) => {
      if (path.endsWith("/run-1")) return Promise.resolve(fullRun);
      if (path.includes("/candidates") && !path.includes("/candidates/1")) {
        return Promise.resolve(candidatesPage);
      }
      if (path.endsWith("/candidates/1")) {
        return Promise.resolve({
          ...candidatesPage.items[1],
          in_sample: {},
          out_of_sample: {},
          rule_set: { schema_version: "2.0" },
        });
      }
      if (path === "/api/v1/strategies") return Promise.resolve(strategyList);
      if (path.includes("/robustness")) return Promise.resolve(scenariosPayload);
      return Promise.resolve({});
    });
    postMock.mockResolvedValue({});
    render(<ResearchRunDetail runId="run-1" onBack={() => {}} />);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /Save as draft/ }).length).toBe(2));
    await user.click(screen.getAllByRole("button", { name: /Save as draft/ })[1]);
    await waitFor(() => expect(screen.getByText(/Save candidate as new draft version/)).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Save draft version" }));
    await waitFor(() => expect(postMock).toHaveBeenCalled());
    const [path, body] = postMock.mock.calls[0];
    expect(path).toBe("/api/v1/strategies/11111111-1111-1111-1111-111111111111/versions");
    expect(body.rule_set).toEqual({ schema_version: "2.0" });
  });

  it("cancels a running run from the header", async () => {
    const running: ResearchRun = { ...fullRun, status: "running" };
    getMock.mockImplementation((path: string) => {
      if (path.endsWith("/run-1")) return Promise.resolve(running);
      return Promise.resolve({});
    });
    postMock.mockResolvedValue({});
    render(<ResearchRunDetail runId="run-1" onBack={() => {}} />);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByRole("button", { name: /Cancel/ })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /Cancel/ }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/api/v1/research/optimization/run-1/cancel"));
  });
});
