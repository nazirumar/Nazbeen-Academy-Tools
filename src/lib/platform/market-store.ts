/**
 * Market-view store (Phase 2).
 *
 * Keeps the selected symbol/timeframe and the last successful data fetch so
 * the MT5 status panel and the market preview stay in sync. Not persisted:
 * the selection is session-local.
 */

import { create } from 'zustand'
import type { CandleDataStatus } from '@/lib/api/types'

interface MarketState {
  symbol: string
  timeframe: string
  lastFetchedAt: string | null
  lastDataStatus: CandleDataStatus | null
  setSymbol: (symbol: string) => void
  setTimeframe: (timeframe: string) => void
  recordFetch: (at: string, status: CandleDataStatus) => void
}

export const DEFAULT_SYMBOL = 'EURUSD'

export const useMarketStore = create<MarketState>((set) => ({
  symbol: DEFAULT_SYMBOL,
  timeframe: 'M1',
  lastFetchedAt: null,
  lastDataStatus: null,
  setSymbol: (symbol) => set({ symbol: symbol.toUpperCase() }),
  setTimeframe: (timeframe) => set({ timeframe }),
  recordFetch: (lastFetchedAt, lastDataStatus) =>
    set({ lastFetchedAt, lastDataStatus }),
}))
