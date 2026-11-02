/**
 * Phase 2 frontend tests: MT5 status panel + market preview states.
 * The api client is mocked so no backend is needed; every state the UI can
 * be in (connected / disconnected / loading / error) is covered.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MT5StatusPanel } from "@/components/market/mt5-status-panel";
import { MarketPreview } from "@/components/market/market-preview";
import { ApiError } from "@/lib/api/types";
import { useMarketStore } from "@/lib/platform/market-store";
import type { CandleSeries, MT5Status } from "@/lib/api/types";

// jsdom lacks ResizeObserver; recharts' ResponsiveContainer requires it.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (typeof globalThis.ResizeObserver === "undefined") {
  (globalThis as unknown as { ResizeObserver: typeof ResizeObserverStub }).ResizeObserver =
    ResizeObserverStub;
}

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const getMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    get: (...args: unknown[]) => getMock(...args),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

const CONNECTED: MT5Status = {
  provider: "mt5",
  enabled: true,
  package_available: true,
  initialized: true,
  connected: true,
  package_version: "5.0.45",
  terminal: {
    accessible: true,
    name: "MetaTrader 5",
    build: 4400,
    broker_connected: true,
    trade_allowed: false,
  },
  account: {
    accessible: true,
    login: 123456,
    server: "Demo-Server",
    company: "Broker",
    currency: "USD",
    leverage: 100,
    trade_allowed: false,
  },
  last_success_at: new Date(Date.now() - 5_000).toISOString(),
  last_error: null,
  last_error_at: null,
  reconnect_attempts: 0,
  detail: "Connected to the MetaTrader 5 terminal (cached state).",
  checked_at: new Date().toISOString(),
  configuration: {},
};

const DISCONNECTED: MT5Status = {
  ...CONNECTED,
  connected: false,
  initialized: false,
  terminal: null,
  account: null,
  package_available: false,
  package_version: null,
  last_success_at: null,
  last_error: "The MetaTrader5 Python package is not installed on this host.",
  detail: "Not connected. Last error: package not installed.",
};

function candleSeries(overrides: Partial<CandleSeries> = {}): CandleSeries {
  const now = Math.floor(Date.now() / 60_000) * 60;
  const candles = Array.from({ length: 30 }, (_, i) => ({
    time: now - (29 - i) * 60,
    time_utc: new Date((now - (29 - i) * 60) * 1000).toISOString(),
    open: 1.1 + i * 0.0001,
    high: 1.1002 + i * 0.0001,
    low: 1.0998 + i * 0.0001,
    close: 1.1001 + i * 0.0001,
    volume: 100,
    forming: i === 29,
  }));
  return {
    symbol: "EURUSD",
    timeframe: "M1",
    timezone: "MT5_SERVER",
    timezone_note: "MT5 server-time convention.",
    candles,
    count: 30,
    requested_count: 30,
    closed_only: false,
    includes_forming: true,
    first_time: candles[0].time,
    last_time: candles[29].time,
    newest_closed_time: candles[28].time,
    newest_closed_age_seconds: 12.5,
    data_status: "ok",
    data_provenance: "mt5_live",
    retrieved_at: new Date().toISOString(),
    warnings: [],
    ...overrides,
  };
}

beforeEach(() => {
  getMock.mockReset();
  useMarketStore.setState({
    symbol: "EURUSD",
    timeframe: "M1",
    lastFetchedAt: null,
    lastDataStatus: null,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// MT5StatusPanel
// ---------------------------------------------------------------------------

describe("MT5StatusPanel", () => {
  it("shows connected state with terminal details", async () => {
    getMock.mockResolvedValue(CONNECTED);
    render(<MT5StatusPanel />);
    await waitFor(() =>
      expect(screen.getByText(/MetaTrader 5 \(build 4400\)/)).toBeInTheDocument()
    );
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("online")).toBeInTheDocument();
    expect(screen.getByText("EURUSD · M1")).toBeInTheDocument(); // shared selection
  });

  it("shows disconnected state and the backend's latest error verbatim", async () => {
    getMock.mockResolvedValue(DISCONNECTED);
    render(<MT5StatusPanel />);
    await waitFor(() => expect(screen.getByText("Disconnected")).toBeInTheDocument());
    expect(screen.getByText("not available")).toBeInTheDocument();
    expect(
      screen.getByText(/package is not installed on this host/)
    ).toBeInTheDocument();
  });

  it("shows disabled state when MT5_ENABLED=false", async () => {
    getMock.mockResolvedValue({ ...CONNECTED, enabled: false, connected: false });
    render(<MT5StatusPanel />);
    await waitFor(() => expect(screen.getByText("Disabled")).toBeInTheDocument());
  });

  it("shows an error note when the backend is unreachable", async () => {
    getMock.mockRejectedValue(
      new ApiError({
        code: "BACKEND_UNREACHABLE",
        message: "The analysis backend is unreachable.",
        status: 0,
      })
    );
    render(<MT5StatusPanel />);
    await waitFor(() =>
      expect(screen.getByText(/backend is unreachable/)).toBeInTheDocument()
    );
  });

  it("reflects the last market fetch (freshness) from the shared store", async () => {
    useMarketStore.setState({
      lastFetchedAt: new Date(Date.now() - 30_000).toISOString(),
      lastDataStatus: "ok",
    });
    getMock.mockResolvedValue(CONNECTED);
    render(<MT5StatusPanel />);
    await waitFor(() => expect(screen.getByText(/30s ago \(ok\)/)).toBeInTheDocument());
  });
});

// ---------------------------------------------------------------------------
// MarketPreview
// ---------------------------------------------------------------------------

describe("MarketPreview", () => {
  it("renders real candles with status and provenance badges", async () => {
    getMock.mockImplementation((_path: string, query?: Record<string, string>) => {
      if (_path === "/api/v1/market/tick") {
        return Promise.resolve({
          symbol: "EURUSD",
          time: 1_700_000_000,
          time_utc: "2023-11-14T22:13:20.000+00:00",
          bid: 1.09123,
          ask: 1.09133,
          spread: { points: 10, from_bid_ask: 10, floating: true },
          symbol_digits: 5,
          retrieved_at: new Date().toISOString(),
        });
      }
      expect(query?.symbol).toBe("EURUSD");
      expect(query?.timeframe).toBe("M1");
      return Promise.resolve(candleSeries());
    });
    render(<MarketPreview />);
    await waitFor(() => expect(screen.getByText("ok")).toBeInTheDocument());
    expect(screen.getByText("MT5 live")).toBeInTheDocument();
    expect(screen.getByText(/includes forming candle/)).toBeInTheDocument();
    expect(screen.getByText(/bid 1.09123 \/ ask 1.09133/)).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledWith(
      "/api/v1/market/candles",
      expect.objectContaining({ symbol: "EURUSD", timeframe: "M1" })
    );
  });

  it("shows the honest empty state when MT5 is disconnected", async () => {
    getMock.mockRejectedValue(
      new ApiError({
        code: "MARKET_DATA_UNAVAILABLE",
        message: "Market data is unavailable: the MetaTrader 5 provider is not connected.",
        status: 503,
      })
    );
    render(<MarketPreview />);
    await waitFor(() =>
      expect(screen.getByText("Market data is unavailable")).toBeInTheDocument()
    );
    expect(screen.getByText(/never fabricates prices/)).toBeInTheDocument();
    // No candles anywhere:
    expect(screen.queryByText("MT5 live")).not.toBeInTheDocument();
  });

  it("shows symbol-not-found errors from the backend", async () => {
    getMock.mockRejectedValue(
      new ApiError({
        code: "SYMBOL_NOT_FOUND",
        message: "Symbol 'NOPE' is unknown to this terminal.",
        status: 404,
      })
    );
    render(<MarketPreview />);
    await waitFor(() =>
      expect(
        screen.getByText('Symbol "EURUSD" is unknown to the terminal')
      ).toBeInTheDocument()
    );
  });

  it("renders data-quality warnings returned by the backend", async () => {
    getMock.mockResolvedValue(
      candleSeries({
        data_status: "partial",
        warnings: [
          {
            code: "INVALID_OHLC",
            severity: "error",
            message: "1 candle(s) violated OHLC consistency.",
          },
        ],
      })
    );
    render(<MarketPreview />);
    await waitFor(() => expect(screen.getByText("partial")).toBeInTheDocument());
    expect(screen.getByText(/INVALID_OHLC:/)).toBeInTheDocument();
  });

  it("refetches with the edited symbol when the user commits the input", async () => {
    getMock.mockResolvedValue(candleSeries({ symbol: "XAUUSD" }));
    render(<MarketPreview />);
    await waitFor(() =>
      expect(getMock).toHaveBeenCalledWith(
        "/api/v1/market/candles",
        expect.objectContaining({ symbol: "EURUSD" })
      )
    );
    const input = screen.getByLabelText("Symbol");
    await userEvent.clear(input);
    await userEvent.type(input, "XAUUSD");
    await userEvent.tab(); // commit on blur
    await waitFor(() =>
      expect(getMock).toHaveBeenCalledWith(
        "/api/v1/market/candles",
        expect.objectContaining({ symbol: "XAUUSD" })
      )
    );
    expect(useMarketStore.getState().symbol).toBe("XAUUSD");
  });
});
