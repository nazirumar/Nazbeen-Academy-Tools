'use client'

/**
 * Polling candle feed — the live-update engine of the dashboard.
 *
 * The backend exposes DOCUMENTED POLLING endpoints (no WebSocket exists),
 * so this hook drives `GET /api/v1/market/candles` on a configurable
 * interval with these guarantees:
 *
 * - no duplicate or out-of-order candles are ever handed to the chart
 *   (`mergeCandles` sorts by time and replaces same-time candles — a
 *   closed candle always replaces its forming predecessor);
 * - polling pauses while the tab is hidden and stops on unmount;
 * - at most one request is in flight (no pile-ups on slow backends);
 * - the connection state is honest: `disconnected` after failures, never
 *   "live" while the last request errored; `stale` is derived from the
 *   backend's own data-status, not from frontend guesses;
 * - the forming candle is preserved as `forming: true` — the chart labels
 *   it and analysis defaults exclude it.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api/client'
import { ApiError } from '@/lib/api/types'
import type { CandlePoint, CandleSeries } from '@/lib/api/types'

export type FeedStatus =
  | 'idle'
  | 'loading'
  | 'live'
  | 'stale'
  | 'no_data'
  | 'disconnected'
  | 'error'

export interface FeedState {
  status: FeedStatus
  candles: CandlePoint[]
  /** Backend-reported data status of the most recent successful fetch. */
  dataStatus: CandleSeries['data_status'] | null
  provenance: CandleSeries['data_provenance'] | null
  warnings: CandleSeries['warnings']
  /** ISO timestamp of the last SUCCESSFUL fetch (server-side retrieved_at). */
  lastSuccessAt: string | null
  error: ApiError | null
  includesForming: boolean
  newestClosedAgeSeconds: number | null
}

export interface CandleFeedOptions {
  symbol: string
  timeframe: string
  limit: number
  closedOnly: boolean
  /** Refresh interval in ms; 0 disables polling after the initial fetch. */
  intervalMs: number
  /** When false no request is made at all (e.g. backend known-disconnected). */
  enabled?: boolean
}

/** Sort key: candle open time (seconds). */
function byTime(a: CandlePoint, b: CandlePoint): number {
  return a.time - b.time
}

/**
 * Merge freshly fetched candles into the existing series.
 * - same `time` -> the new candle replaces the old one (a closed candle
 *   overwrites the forming candle it closed; a re-sent closed candle is a
 *   no-op by value);
 * - result is strictly ascending by `time`, no duplicates;
 * - returns the SAME array reference when nothing changed, so callers can
 *   avoid unnecessary chart updates.
 */
export function mergeCandles(existing: CandlePoint[], incoming: CandlePoint[]): CandlePoint[] {
  if (incoming.length === 0) return existing
  if (existing.length === 0) {
    return [...incoming].sort(byTime)
  }
  const byId = new Map<number, CandlePoint>()
  for (const candle of existing) byId.set(candle.time, candle)
  let changed = false
  for (const candle of incoming) {
    const prev = byId.get(candle.time)
    if (prev && sameCandle(prev, candle)) continue
    byId.set(candle.time, candle)
    changed = true
  }
  if (!changed) return existing
  return Array.from(byId.values()).sort(byTime)
}

function sameCandle(a: CandlePoint, b: CandlePoint): boolean {
  return (
    a.open === b.open &&
    a.high === b.high &&
    a.low === b.low &&
    a.close === b.close &&
    a.volume === b.volume &&
    a.forming === b.forming
  )
}

/** Derive the honest feed status from a successful fetch. */
function statusFromSeries(series: CandleSeries): FeedStatus {
  const staleWarning = series.warnings.some((w) => w.code === 'STALE_DATA')
  if (staleWarning || series.data_status === 'stale') return 'stale'
  if (series.data_status === 'empty' || series.data_status === 'insufficient') {
    return 'no_data'
  }
  return 'live'
}

const INITIAL: FeedState = {
  status: 'idle',
  candles: [],
  dataStatus: null,
  provenance: null,
  warnings: [],
  lastSuccessAt: null,
  error: null,
  includesForming: false,
  newestClosedAgeSeconds: null,
}

export function useCandleFeed(options: CandleFeedOptions): {
  feed: FeedState
  refresh: () => Promise<void>
} {
  const { symbol, timeframe, limit, closedOnly, intervalMs, enabled = true } = options
  const [feed, setFeed] = useState<FeedState>(INITIAL)
  const inFlight = useRef(false)
  const mounted = useRef(true)
  // The values the running poll loop uses; kept in a ref so the interval
  // does not need to be torn down on every keystroke.
  const latest = useRef({ symbol, timeframe, limit, closedOnly })
  latest.current = { symbol, timeframe, limit, closedOnly }

  const refresh = useCallback(async () => {
    if (inFlight.current || !mounted.current) return
    const q = latest.current
    inFlight.current = true
    if (mounted.current) {
      setFeed((prev) => ({ ...prev, status: prev.candles.length ? prev.status : 'loading', error: null }))
    }
    try {
      const series = await api.get<CandleSeries>('/api/v1/market/candles', {
        symbol: q.symbol,
        timeframe: q.timeframe,
        limit: String(q.limit),
        closed_only: q.closedOnly ? 'true' : 'false',
      })
      if (!mounted.current) return
      setFeed((prev) => {
        const candles = mergeCandles(
          // A series identity change (symbol/timeframe switch handled by
          // the reset effect) or backend-provided full history always wins
          // over the merged cache.
          prev.candles,
          series.candles,
        )
        return {
          status: statusFromSeries(series),
          candles,
          dataStatus: series.data_status,
          provenance: series.data_provenance,
          warnings: series.warnings,
          lastSuccessAt: series.retrieved_at,
          error: null,
          includesForming: series.includes_forming,
          newestClosedAgeSeconds: series.newest_closed_age_seconds,
        }
      })
    } catch (e) {
      if (!mounted.current) return
      const error =
        e instanceof ApiError
          ? e
          : new ApiError({ code: 'UNKNOWN_ERROR', message: 'Unexpected client error.', status: 0 })
      const unreachable =
        error.code === 'BACKEND_UNREACHABLE' || error.status === 503 || error.status === 0
      setFeed((prev) => ({
        ...prev,
        status: unreachable ? 'disconnected' : 'error',
        error,
        // Candles are KEPT on display after a failure (they are real data
        // from the last success) but the status bar stops claiming "live".
      }))
    } finally {
      inFlight.current = false
    }
  }, [])

  // Series identity change: drop the old symbol/timeframe candles instantly
  // so the previous market is never mixed into the new one.
  useEffect(() => {
    mounted.current = true
    setFeed({ ...INITIAL, status: 'loading' })
    void refresh()
    return () => {
      mounted.current = false
    }
  }, [symbol, timeframe, closedOnly, refresh])

  // Polling loop.
  useEffect(() => {
    if (!enabled || intervalMs <= 0) return
    let timer: ReturnType<typeof setInterval> | null = null
    const start = () => {
      if (timer !== null) return
      timer = setInterval(() => {
        if (document.visibilityState === 'hidden') return // resume on visible
        void refresh()
      }, intervalMs)
    }
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer)
        timer = null
      }
    }
    const onVisibility = () => {
      if (document.visibilityState === 'visible') void refresh()
    }
    start()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [enabled, intervalMs, refresh])

  return { feed, refresh }
}
