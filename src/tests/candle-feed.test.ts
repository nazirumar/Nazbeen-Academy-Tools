/**
 * Phase 5 — candle feed tests.
 *
 * Pure merge logic: dedup by time, closed-over-forming replacement,
 * out-of-order arrival, no-change reference stability.
 * Hook behaviour (fake timers): interval polling, visibility pause,
 * duplicate prevention while in-flight, honest status transitions,
 * unmount cleanup (no state updates / timers after unmount).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { mergeCandles, useCandleFeed, type FeedState } from "@/hooks/use-candle-feed";
import { ApiError } from "@/lib/api/types";
import type { CandlePoint, CandleSeries } from "@/lib/api/types";

function candle(time: number, close: number, forming = false): CandlePoint {
  return {
    time,
    time_utc: new Date(time * 1000).toISOString(),
    open: close - 0.001,
    high: close + 0.002,
    low: close - 0.002,
    close,
    volume: 100,
    forming,
  };
}

function series(candles: CandlePoint[], overrides?: Partial<CandleSeries>): CandleSeries {
  return {
    symbol: "EURUSD",
    timeframe: "M1",
    timezone: "MT5_SERVER",
    timezone_note: "",
    candles,
    count: candles.length,
    requested_count: candles.length,
    closed_only: false,
    includes_forming: candles.some((c) => c.forming),
    first_time: candles[0]?.time ?? null,
    last_time: candles[candles.length - 1]?.time ?? null,
    newest_closed_time: candles[candles.length - 1]?.time ?? null,
    newest_closed_age_seconds: 5,
    data_status: "ok",
    data_provenance: "mt5_live",
    retrieved_at: new Date().toISOString(),
    warnings: [],
    ...overrides,
  };
}

describe("mergeCandles", () => {
  it("sorts an initial batch ascending", () => {
    const merged = mergeCandles([], [candle(3000, 1.1), candle(1000, 1.0), candle(2000, 1.05)]);
    expect(merged.map((c) => c.time)).toEqual([1000, 2000, 3000]);
  });

  it("replaces the forming candle when its closed candle arrives", () => {
    const existing = [candle(1000, 1.0), candle(2000, 1.05, true)];
    const incoming = [candle(2000, 1.06, false), candle(3000, 1.07)];
    const merged = mergeCandles(existing, incoming);
    expect(merged).toHaveLength(3);
    expect(merged.find((c) => c.time === 2000)?.forming).toBe(false);
    expect(merged.find((c) => c.time === 2000)?.close).toBe(1.06);
  });

  it("drops out-of-order duplicates and keeps one candle per time", () => {
    const existing = [candle(1000, 1.0), candle(2000, 1.05)];
    const merged = mergeCandles(existing, [candle(2000, 1.05), candle(500, 0.99), candle(2000, 1.051)]);
    expect(merged.map((c) => c.time)).toEqual([500, 1000, 2000]);
    expect(merged.find((c) => c.time === 2000)?.close).toBe(1.051); // newest wins
  });

  it("returns the SAME array reference when nothing changed", () => {
    const existing = [candle(1000, 1.0), candle(2000, 1.05)];
    const same = [candle(1000, 1.0), candle(2000, 1.05)];
    expect(mergeCandles(existing, same)).toBe(existing);
  });

  it("returns existing reference when the backend sends an empty batch", () => {
    const existing = [candle(1000, 1.0)];
    expect(mergeCandles(existing, [])).toBe(existing);
  });
});

// ---------------------------------------------------------------------------
// Hook behaviour
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

describe("useCandleFeed", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function feedOf(hook: { current: { feed: FeedState; refresh: () => Promise<void> } }): FeedState {
    return hook.current.feed;
  }

  it("fetches once on mount and reports live status with candles", async () => {
    getMock.mockResolvedValue(series([candle(1000, 1.0), candle(2000, 1.05)]));
    const { result, unmount } = renderHook(() =>
      useCandleFeed({ symbol: "EURUSD", timeframe: "M1", limit: 100, closedOnly: true, intervalMs: 0 }),
    );
    await waitFor(() => expect(feedOf(result).status).toBe("live"));
    expect(feedOf(result).candles).toHaveLength(2);
    expect(feedOf(result).lastSuccessAt).toBeTypeOf("string");
    expect(getMock).toHaveBeenCalledTimes(1);
    unmount();
  });

  it("marks stale when the backend flags STALE_DATA — not the frontend", async () => {
    getMock.mockResolvedValue(series([candle(1000, 1.0)], {
      data_status: "stale",
      warnings: [{ code: "STALE_DATA", severity: "warning", message: "old data" }],
    }));
    const { result, unmount } = renderHook(() =>
      useCandleFeed({ symbol: "EURUSD", timeframe: "M1", limit: 100, closedOnly: true, intervalMs: 0 }),
    );
    await waitFor(() => expect(feedOf(result).status).toBe("stale"));
    unmount();
  });

  it("marks no_data when the terminal answers without candles", async () => {
    getMock.mockResolvedValue(series([], { data_status: "empty" }));
    const { result, unmount } = renderHook(() =>
      useCandleFeed({ symbol: "EURUSD", timeframe: "M1", limit: 100, closedOnly: true, intervalMs: 0 }),
    );
    await waitFor(() => expect(feedOf(result).status).toBe("no_data"));
    unmount();
  });

  it("keeps real candles visible when history is short", async () => {
    getMock.mockResolvedValue(
      series([candle(1000, 1.0)], {
        data_status: "insufficient",
        count: 1,
        requested_count: 100,
        warnings: [
          {
            code: "INSUFFICIENT_HISTORY",
            severity: "warning",
            message: "Only 1 validated candle(s) available but 100 were requested.",
          },
        ],
      }),
    );
    const { result, unmount } = renderHook(() =>
      useCandleFeed({ symbol: "EURUSD", timeframe: "M1", limit: 100, closedOnly: true, intervalMs: 0 }),
    );
    await waitFor(() => expect(feedOf(result).status).toBe("partial"));
    expect(feedOf(result).candles).toHaveLength(1);
    expect(feedOf(result).dataStatus).toBe("insufficient");
    unmount();
  });

  it("goes disconnected on backend failure but KEEPS the last real candles", async () => {
    getMock.mockResolvedValueOnce(series([candle(1000, 1.0)]));
    const { result, unmount } = renderHook(() =>
      useCandleFeed({ symbol: "EURUSD", timeframe: "M1", limit: 100, closedOnly: true, intervalMs: 0 }),
    );
    await waitFor(() => expect(feedOf(result).status).toBe("live"));

    getMock.mockRejectedValue(
      new ApiError({ code: "BACKEND_UNREACHABLE", message: "down", status: 0 }),
    );
    await act(async () => {
      await result.current.refresh();
    });
    expect(feedOf(result).status).toBe("disconnected");
    expect(feedOf(result).candles).toHaveLength(1); // real data retained
    expect(feedOf(result).error?.code).toBe("BACKEND_UNREACHABLE");
    unmount();
  });

  it("maps non-transport API errors to the error state", async () => {
    getMock.mockRejectedValue(
      new ApiError({ code: "SYMBOL_NOT_FOUND", message: "unknown", status: 404 }),
    );
    const { result, unmount } = renderHook(() =>
      useCandleFeed({ symbol: "BAD", timeframe: "M1", limit: 100, closedOnly: true, intervalMs: 0 }),
    );
    await waitFor(() => expect(feedOf(result).status).toBe("error"));
    expect(feedOf(result).error?.code).toBe("SYMBOL_NOT_FOUND");
    unmount();
  });

  it("merges polled updates without duplicates and switches to closed", async () => {
    getMock.mockResolvedValueOnce(series([candle(1000, 1.0), candle(2000, 1.05, true)]));
    const { result, unmount } = renderHook(() =>
      useCandleFeed({ symbol: "EURUSD", timeframe: "M1", limit: 100, closedOnly: false, intervalMs: 0 }),
    );
    await waitFor(() => expect(feedOf(result).includesForming).toBe(true));

    getMock.mockResolvedValueOnce(
      series([candle(1000, 1.0), candle(2000, 1.06), candle(3000, 1.07)]),
    );
    await act(async () => {
      await result.current.refresh();
    });
    const candles = feedOf(result).candles;
    expect(candles.map((c) => c.time)).toEqual([1000, 2000, 3000]);
    expect(candles.find((c) => c.time === 2000)?.forming).toBe(false);
    expect(feedOf(result).includesForming).toBe(false);
    unmount();
  });

  it("resets the series when symbol/timeframe changes", async () => {
    getMock.mockResolvedValue(series([candle(1000, 1.0)]));
    const { result, rerender, unmount } = renderHook(
      ({ symbol }) =>
        useCandleFeed({ symbol, timeframe: "M1", limit: 100, closedOnly: true, intervalMs: 0 }),
      { initialProps: { symbol: "EURUSD" } },
    );
    await waitFor(() => expect(feedOf(result).candles).toHaveLength(1));

    getMock.mockResolvedValue(series([candle(5000, 120.5)], { symbol: "USDJPY" }));
    rerender({ symbol: "USDJPY" });
    await waitFor(() => {
      expect(feedOf(result).candles[0]?.time).toBe(5000);
    });
    expect(feedOf(result).candles).toHaveLength(1);
    unmount();
  });

  it("polls on the interval, skips while hidden, and stops after unmount", async () => {
    getMock.mockResolvedValue(series([candle(1000, 1.0)]));
    const { unmount } = renderHook(() =>
      useCandleFeed({ symbol: "EURUSD", timeframe: "M1", limit: 100, closedOnly: true, intervalMs: 1000 }),
    );
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(1));

    // Visible: polls.
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    act(() => {
      vi.advanceTimersByTime(2500);
    });
    expect(getMock.mock.calls.length).toBeGreaterThanOrEqual(2);

    const callsBeforeHide = getMock.mock.calls.length;
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(getMock.mock.calls.length).toBe(callsBeforeHide); // paused while hidden

    unmount();
    const callsAfterUnmount = getMock.mock.calls.length;
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    expect(getMock.mock.calls.length).toBe(callsAfterUnmount); // no polling after unmount
  });

  it("never starts a second request while one is in flight", async () => {
    let resolve!: (value: CandleSeries) => void;
    getMock.mockReturnValue(
      new Promise<CandleSeries>((res) => {
        resolve = res;
      }),
    );
    const { result, unmount } = renderHook(() =>
      useCandleFeed({ symbol: "EURUSD", timeframe: "M1", limit: 100, closedOnly: true, intervalMs: 1000 }),
    );
    await act(async () => {
      const p1 = result.current.refresh();
      const p2 = result.current.refresh();
      resolve(series([candle(1000, 1.0)]));
      await Promise.all([p1, p2]);
    });
    expect(getMock).toHaveBeenCalledTimes(1); // second call suppressed
    unmount();
  });

  it("requests the expected query parameters", async () => {
    getMock.mockResolvedValue(series([candle(1000, 1.0)]));
    const { unmount } = renderHook(() =>
      useCandleFeed({ symbol: "EURUSD", timeframe: "H1", limit: 250, closedOnly: true, intervalMs: 0 }),
    );
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    const [path, query] = getMock.mock.calls[0] as [string, Record<string, string>];
    expect(path).toBe("/api/v1/market/candles");
    expect(query).toMatchObject({
      symbol: "EURUSD",
      timeframe: "H1",
      limit: "250",
      closed_only: "true",
    });
    unmount();
  });
});
