'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  ChartCandlestick,
  Info,
  RefreshCw,
  WifiOff,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import {
  ResponsiveContainer,
  ComposedChart,
  Area,
  Line,
  ReferenceDot,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '@/lib/api/client'
import { ApiError } from '@/lib/api/types'
import type { CandleSeries, TickPayload } from '@/lib/api/types'
import { formatUnixTime, timeframesForMode } from '@/lib/platform/modes'
import { useMarketStore } from '@/lib/platform/market-store'
import { useModeStore } from '@/lib/platform/mode-store'

type SeriesState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ok'; series: CandleSeries }
  | { kind: 'error'; error: ApiError }

const AUTO_REFRESH_MS = 30_000
const DEFAULT_LIMIT = 120

const DATA_STATUS_TONE: Record<string, string> = {
  ok: 'bg-emerald-600 text-white hover:bg-emerald-600',
  partial: 'bg-amber-600 text-white hover:bg-amber-600',
  stale: 'bg-orange-600 text-white hover:bg-orange-600',
  insufficient: 'bg-slate-500 text-white hover:bg-slate-500',
  empty: 'bg-destructive text-white hover:bg-destructive',
}

function ChartTooltip({ payload }: { payload?: Array<{ payload: Record<string, unknown> }> }) {
  const row = payload?.[0]?.payload as
    | { time: number; open: number; high: number; low: number; close: number; forming: boolean }
    | undefined
  if (!row) return null
  return (
    <div className="rounded-md border bg-popover px-3 py-2 text-xs shadow-md">
      <p className="font-medium">{formatUnixTime(row.time)}</p>
      <div className="mt-1 grid grid-cols-2 gap-x-4 gap-y-0.5 font-mono">
        <span>O {row.open}</span>
        <span>H {row.high}</span>
        <span>L {row.low}</span>
        <span>C {row.close}</span>
      </div>
      {row.forming && (
        <p className="mt-1 text-[11px] text-amber-600 dark:text-amber-400">
          forming — values are NOT final
        </p>
      )}
    </div>
  )
}

/**
 * Market-data preview backed EXCLUSIVELY by real candles from the backend
 * (`/api/v1/market/candles`). When MT5 is disconnected the card shows a
 * clear empty state — no demo candles, no fabricated prices, ever.
 */
export function MarketPreview() {
  const mode = useModeStore((state) => state.mode)
  const symbol = useMarketStore((s) => s.symbol)
  const timeframe = useMarketStore((s) => s.timeframe)
  const setSymbol = useMarketStore((s) => s.setSymbol)
  const setTimeframe = useMarketStore((s) => s.setTimeframe)
  const recordFetch = useMarketStore((s) => s.recordFetch)

  const [symbolDraft, setSymbolDraft] = useState(symbol)
  const [limit, setLimit] = useState(DEFAULT_LIMIT)
  const [closedOnly, setClosedOnly] = useState(false)
  const [auto, setAuto] = useState(false)
  const [state, setState] = useState<SeriesState>({ kind: 'idle' })
  const [tick, setTick] = useState<TickPayload | null>(null)
  const inFlight = useRef(false)

  const timeframes = timeframesForMode(mode)

  useEffect(() => {
    // Keep the draft in sync when the shared store changes (mode switch etc.)
    setSymbolDraft(symbol)
  }, [symbol])

  const fetchCandles = useCallback(async () => {
    if (inFlight.current) return
    inFlight.current = true
    setState({ kind: 'loading' })
    try {
      const series = await api.get<CandleSeries>('/api/v1/market/candles', {
        symbol,
        timeframe,
        limit: String(limit),
        closed_only: closedOnly ? 'true' : 'false',
      })
      setState({ kind: 'ok', series })
      recordFetch(series.retrieved_at, series.data_status)
      if (series.count > 0) {
        api
          .get<TickPayload>('/api/v1/market/tick', { symbol })
          .then(setTick)
          .catch(() => setTick(null))
      } else {
        setTick(null)
      }
    } catch (error) {
      setState({
        kind: 'error',
        error:
          error instanceof ApiError
            ? error
            : new ApiError({
                code: 'UNKNOWN_ERROR',
                message: 'Unexpected client error.',
                status: 0,
              }),
      })
      setTick(null)
    } finally {
      inFlight.current = false
    }
  }, [symbol, timeframe, limit, closedOnly, recordFetch])

  // Initial fetch on mount and whenever the selection changes.
  useEffect(() => {
    void fetchCandles()
  }, [fetchCandles])

  // Optional gentle auto-refresh (30s) for connected workflows.
  useEffect(() => {
    if (!auto) return
    const timer = setInterval(() => void fetchCandles(), AUTO_REFRESH_MS)
    return () => clearInterval(timer)
  }, [auto, fetchCandles])

  const series = state.kind === 'ok' ? state.series : null

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <ChartCandlestick className="h-4 w-4 text-muted-foreground" aria-hidden />
            Market data preview
          </CardTitle>
          {series && (
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge className={DATA_STATUS_TONE[series.data_status] ?? ''}>
                {series.data_status}
              </Badge>
              <Badge variant="secondary">
                {series.data_provenance === 'mt5_live' ? 'MT5 live' : 'PostgreSQL cache'}
              </Badge>
              {series.includes_forming && (
                <Badge
                  variant="outline"
                  className="border-amber-500 text-amber-600 dark:text-amber-400"
                >
                  includes forming candle
                </Badge>
              )}
            </div>
          )}
        </div>
        <CardDescription>
          Real candles from the connected MetaTrader 5 terminal — timestamps
          follow the MT5 server-time convention (rendered as UTC), documented
          in every response.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* ------------------------------------------------- controls -- */}
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <Label htmlFor="market-symbol" className="text-xs text-muted-foreground">
              Symbol
            </Label>
            <Input
              id="market-symbol"
              value={symbolDraft}
              onChange={(e) => setSymbolDraft(e.target.value.toUpperCase())}
              onBlur={() => setSymbol(symbolDraft || 'EURUSD')}
              onKeyDown={(e) => {
                if (e.key === 'Enter') setSymbol(symbolDraft || 'EURUSD')
              }}
              className="w-32 font-mono"
              placeholder="EURUSD"
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Timeframe</Label>
            <Select
              value={timeframe}
              onValueChange={(value) => setTimeframe(value)}
            >
              <SelectTrigger className="w-24 font-mono">
                <SelectValue placeholder="TF" />
              </SelectTrigger>
              <SelectContent>
                {timeframes.map((tf) => (
                  <SelectItem key={tf} value={tf} className="font-mono">
                    {tf}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="market-limit" className="text-xs text-muted-foreground">
              Candles
            </Label>
            <Input
              id="market-limit"
              type="number"
              min={1}
              max={5000}
              value={limit}
              onChange={(e) => setLimit(Math.max(1, Number(e.target.value) || 1))}
              className="w-24"
            />
          </div>
          <div className="flex items-center gap-2 pb-2">
            <Switch
              id="closed-only"
              checked={closedOnly}
              onCheckedChange={setClosedOnly}
            />
            <Label htmlFor="closed-only" className="text-xs text-muted-foreground">
              Closed only
            </Label>
          </div>
          <div className="flex items-center gap-2 pb-2">
            <Switch id="auto-refresh" checked={auto} onCheckedChange={setAuto} />
            <Label htmlFor="auto-refresh" className="text-xs text-muted-foreground">
              Auto 30s
            </Label>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="mb-0.5"
            onClick={() => void fetchCandles()}
            disabled={state.kind === 'loading'}
          >
            <RefreshCw
              className={state.kind === 'loading' ? 'h-4 w-4 animate-spin' : 'h-4 w-4'}
              aria-hidden
            />
            Fetch
          </Button>
        </div>

        {/* ---------------------------------------------------- states -- */}
        {state.kind === 'loading' && !series && (
          <Skeleton className="h-64 w-full" />
        )}

        {state.kind === 'error' && (
          <div className="flex h-64 flex-col items-center justify-center gap-3 rounded-lg border border-dashed text-center">
            <WifiOff className="h-8 w-8 text-muted-foreground" aria-hidden />
            <div className="max-w-md space-y-1 px-4">
              <p className="text-sm font-medium">
                {state.error.code === 'MARKET_DATA_UNAVAILABLE'
                  ? 'Market data is unavailable'
                  : state.error.code === 'SYMBOL_NOT_FOUND'
                    ? `Symbol "${symbol}" is unknown to the terminal`
                    : 'Market data request failed'}
              </p>
              <p className="text-xs text-muted-foreground">{state.error.message}</p>
              {state.error.code === 'MARKET_DATA_UNAVAILABLE' && (
                <p className="text-xs text-muted-foreground">
                  No candles are shown because none exist — the platform never
                  fabricates prices. Connect an MT5 terminal (see README) and
                  press Fetch.
                </p>
              )}
              {state.error.requestId && (
                <p className="text-[11px] text-muted-foreground">
                  request_id: <code className="rounded bg-muted px-1">{state.error.requestId}</code>
                </p>
              )}
            </div>
          </div>
        )}

        {series && (
          <>
            {/* ---------------------------------------------- chart -- */}
            <div className="h-64 w-full">
              {series.count === 0 ? (
                <div className="flex h-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed">
                  <Info className="h-6 w-6 text-muted-foreground" aria-hidden />
                  <p className="text-sm text-muted-foreground">
                    No {closedOnly ? 'closed ' : ''}candles available for{' '}
                    <span className="font-mono">{series.symbol} {series.timeframe}</span>.
                  </p>
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart
                    data={series.candles}
                    margin={{ top: 8, right: 12, bottom: 0, left: 0 }}
                  >
                    <XAxis
                      dataKey="time"
                      tickFormatter={(t: number) => formatUnixTime(t).slice(11, 16)}
                      minTickGap={48}
                      fontSize={11}
                      stroke="currentColor"
                      className="text-muted-foreground"
                      tickLine={false}
                    />
                    <YAxis
                      domain={['auto', 'auto']}
                      width={72}
                      fontSize={11}
                      stroke="currentColor"
                      className="text-muted-foreground"
                      tickLine={false}
                      tickFormatter={(v: number) => v.toFixed(series.candles[0].open < 20 ? 5 : 2)}
                    />
                    <RechartsTooltip content={<ChartTooltip />} />
                    <Area
                      type="linear"
                      dataKey="high"
                      stroke="none"
                      fill="currentColor"
                      className="text-emerald-500/10"
                      isAnimationActive={false}
                    />
                    <Area
                      type="linear"
                      dataKey="low"
                      stroke="none"
                      fill="var(--background)"
                      fillOpacity={1}
                      isAnimationActive={false}
                    />
                    <Line
                      type="linear"
                      dataKey="close"
                      stroke="currentColor"
                      className="text-emerald-600 dark:text-emerald-400"
                      strokeWidth={1.6}
                      dot={false}
                      isAnimationActive={false}
                    />
                    {series.candles.some((c) => c.forming) && (
                      <ReferenceDot
                        x={series.candles[series.candles.length - 1].time}
                        y={series.candles[series.candles.length - 1].close}
                        r={4}
                        fill="#f59e0b"
                        stroke="none"
                      />
                    )}
                  </ComposedChart>
                </ResponsiveContainer>
              )}
            </div>

            {/* --------------------------------------- tick + facts -- */}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
              {tick?.bid && tick?.ask ? (
                <span className="font-mono">
                  bid {tick.bid} / ask {tick.ask}
                  {tick.spread?.points != null ? ` · spread ${tick.spread.points} pt` : ''}
                </span>
              ) : (
                <span>no tick available (market closed or symbol idle)</span>
              )}
              <span>
                {series.count}/{series.requested_count} candles
              </span>
              {series.newest_closed_age_seconds != null && (
                <span>
                  newest closed candle: {Math.round(series.newest_closed_age_seconds)}s old
                </span>
              )}
              <span>retrieved {formatIso(series.retrieved_at)}</span>
            </div>

            {/* ------------------------------------------ warnings -- */}
            {series.warnings.length > 0 && (
              <div className="space-y-1.5">
                {series.warnings.map((w, i) => (
                  <p
                    key={`${w.code}-${i}`}
                    className={`flex items-start gap-1.5 rounded-md px-2 py-1.5 text-xs ${
                      w.severity === 'error'
                        ? 'bg-destructive/10 text-destructive'
                        : w.severity === 'warning'
                          ? 'bg-amber-500/10 text-amber-700 dark:text-amber-400'
                          : 'bg-muted text-muted-foreground'
                    }`}
                  >
                    {w.severity === 'info' ? (
                      <Info className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                    ) : (
                      <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                    )}
                    <span>
                      <span className="font-medium">{w.code}:</span> {w.message}
                    </span>
                  </p>
                ))}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

function formatIso(value: string): string {
  return value.replace('T', ' ').slice(0, 19) + ' UTC'
}
