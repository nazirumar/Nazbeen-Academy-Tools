'use client'

/**
 * Market dashboard (Phase 5) — the main surface for viewing REAL market
 * data and explainable analysis from the Phase 4 engine.
 *
 * Honesty contract kept end-to-end:
 * - candles come only from `/api/v1/market/candles`; when MT5 is not
 *   connected the chart area shows an explicit empty state — no demo
 *   candles, no fabricated prices, no "live" claim while disconnected;
 * - overlays come only from the selected analysis report
 *   (`buildOverlays`); strategy logic stays in the backend;
 * - the evaluation panel shows the four condition states and never
 *   invents probabilities, confidence or profitability;
 * - Binary Options mode defaults to M1-only and labels MT5-vs-broker-feed
 *   disclaimers.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity,
  ChartCandlestick,
  Clock,
  FileSearch,
  Info,
  Loader2,
  Play,
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
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { CandleChart } from '@/components/chart/candle-chart'
import { ConnectionStatus } from '@/components/dashboard/connection-status'
import { SymbolSelect } from '@/components/dashboard/symbol-select'
import { OverlayControls } from '@/components/dashboard/overlay-controls'
import { ConditionSections } from '@/components/dashboard/condition-sections'
import { AnalysisHistory } from '@/components/dashboard/analysis-history'
import { ReportView } from '@/components/analysis/engine-analysis'
import { useCandleFeed } from '@/hooks/use-candle-feed'
import { toast } from '@/hooks/use-toast'
import { api } from '@/lib/api/client'
import { ApiError } from '@/lib/api/types'
import type {
  AnalysisReport,
  PaginatedStrategies,
  PlatformMode,
  Strategy,
  StrategyVersion,
} from '@/lib/api/types'
import type { AnalysisReportWithObservations } from '@/lib/api/types'
import {
  MODE_LABELS,
  SETUP_STATUS_META,
  timeframesForMode,
} from '@/lib/platform/modes'
import { useMarketStore } from '@/lib/platform/market-store'
import {
  buildOverlays,
  type OverlayKind,
} from '@/lib/chart/overlay-model'
import { cn } from '@/lib/utils'

const INTERVALS = [
  { value: '0', label: 'Off (manual)' },
  { value: '10000', label: '10 s' },
  { value: '30000', label: '30 s' },
  { value: '60000', label: '60 s' },
]

const LIMIT_MIN = 50
const LIMIT_MAX = 1000
const BINARY_DEFAULT_TIMEFRAMES = ['M1']

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = e.details.length
      ? ` (${e.details.map((d) => `${d.path || ''} ${d.message ?? ''}`).join('; ')})`
      : ''
    return `${e.message}${detail}`
  }
  return e instanceof Error ? e.message : 'Unexpected error'
}

const DEFAULT_VISIBLE: Record<OverlayKind, boolean> = {
  swing_high: true,
  swing_low: true,
  structure_label: true,
  bos: true,
  mss_choch: true,
  fvg: true,
  price_zone: true,
}

export function MarketDashboard({ mode }: { mode: PlatformMode }) {
  const symbol = useMarketStore((s) => s.symbol)
  const timeframe = useMarketStore((s) => s.timeframe)
  const setSymbol = useMarketStore((s) => s.setSymbol)
  const setTimeframe = useMarketStore((s) => s.setTimeframe)

  // Chart/feed settings (session-local by design).
  const [limit, setLimit] = useState(200)
  const [closedOnly, setClosedOnly] = useState(true)
  const [intervalMs, setIntervalMs] = useState(30_000)

  // Strategy / evaluation state.
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [strategyId, setStrategyId] = useState('')
  const [versions, setVersions] = useState<StrategyVersion[]>([])
  const [version, setVersion] = useState<string>('latest')
  const [includeForming, setIncludeForming] = useState(false)
  const [running, setRunning] = useState(false)
  const [report, setReport] = useState<AnalysisReportWithObservations | null>(null)
  const [reportOpen, setReportOpen] = useState(false)
  const [historyKey, setHistoryKey] = useState(0)

  const [visibleOverlays, setVisibleOverlays] = useState<Record<OverlayKind, boolean>>({
    ...DEFAULT_VISIBLE,
  })

  const { feed, refresh: refreshFeed } = useCandleFeed({
    symbol,
    timeframe,
    limit,
    closedOnly,
    intervalMs,
  })

  // Timeframe options per mode. Binary Options: M1 by default; higher
  // timeframes only when the selected strategy version (or the evaluated
  // report) EXPLICITLY declares them — never fetched proactively.
  const timeframeOptions = useMemo(() => {
    if (mode === 'forex') return timeframesForMode('forex')
    const selected = versions.find((v) => String(v.version) === version)
    const declared = selected?.rule_set.timeframes ?? report?.strategy.rule_set.timeframes ?? []
    const required = declared.filter((tf) => tf !== 'M1')
    return Array.from(new Set([...BINARY_DEFAULT_TIMEFRAMES, ...required]))
  }, [mode, report, versions, version])

  // Clamp the timeframe into the allowed set whenever the mode changes.
  useEffect(() => {
    if (!timeframeOptions.includes(timeframe)) {
      setTimeframe(timeframeOptions[0] ?? 'M1')
    }
  }, [timeframeOptions, timeframe, setTimeframe])

  // Load strategies for this mode.
  useEffect(() => {
    let cancelled = false
    api
      .get<PaginatedStrategies>('/api/v1/strategies', { mode, page_size: '100' })
      .then((page) => {
        if (cancelled) return
        const usable = (page.items ?? []).filter((s) => s.current_version > 0)
        setStrategies(usable)
        setStrategyId((prev) => (usable.some((s) => s.id === prev) ? prev : usable[0]?.id ?? ''))
      })
      .catch(() => {
        if (!cancelled) setStrategies([])
      })
    return () => {
      cancelled = true
    }
  }, [mode])

  // Load versions for the selected strategy; default to the published
  // version, else the latest saved draft.
  useEffect(() => {
    if (!strategyId) {
      setVersions([])
      return
    }
    let cancelled = false
    setVersions([])
    api
      .get<StrategyVersion[]>(`/api/v1/strategies/${strategyId}/versions`)
      .then((list) => {
        if (cancelled) return
        const usable = list ?? []
        setVersions(usable)
        const published = usable.find((v) => v.status === 'published')
        const newest = usable.reduce(
          (acc, v) => (acc === 0 || v.version > acc ? v.version : acc),
          0,
        )
        setVersion(String(published?.version ?? newest ?? 'latest'))
      })
      .catch(() => {
        if (!cancelled) setVersions([])
      })
    return () => {
      cancelled = true
    }
  }, [strategyId])

  const runAnalysis = useCallback(async () => {
    if (!strategyId) {
      toast({
        title: 'No strategy selected',
        description: 'Create a strategy with a saved rule set first (Strategies tab).',
        variant: 'destructive',
      })
      return
    }
    setRunning(true)
    try {
      const result = await api.post<AnalysisReport>('/api/v1/analysis/run', {
        strategy_id: strategyId,
        ...(version !== 'latest' ? { version: Number(version) } : {}),
        symbol,
        include_forming: includeForming,
      })
      setReport(result as AnalysisReportWithObservations)
      setHistoryKey((k) => k + 1)
      toast({ title: SETUP_STATUS_META[result.evaluation.setup_status].label })
    } catch (e) {
      toast({ title: 'Analysis failed', description: errorText(e), variant: 'destructive' })
    } finally {
      setRunning(false)
    }
  }, [strategyId, version, symbol, includeForming])

  const openReport = useCallback(async (id: string) => {
    try {
      const stored = await api.get<AnalysisReport>(`/api/v1/analysis/reports/${id}`)
      setReport(stored as AnalysisReportWithObservations)
      setReportOpen(true)
    } catch (e) {
      toast({ title: 'Could not load report', description: errorText(e), variant: 'destructive' })
    }
  }, [])

  // Overlay derivation — ONLY from the selected report + displayed timeframe.
  const overlays = useMemo(() => buildOverlays(report, timeframe), [report, timeframe])

  const digits = useMemo(() => {
    const sample = feed.candles[feed.candles.length - 1]
    return sample && sample.close < 20 ? 5 : 2
  }, [feed.candles])

  const seriesKey = `${symbol}:${timeframe}`
  const hasCandles = feed.candles.length > 0
  const isBinary = mode === 'binary_options'

  return (
    <div className="space-y-4">
      {/* ------------------------------------------------ control bar -- */}
      <Card>
        <CardContent className="space-y-3 pt-4">
          <div className="flex flex-wrap items-end gap-3">
            <Badge variant="secondary" className="mb-2 font-mono text-[10px]">
              {MODE_LABELS[mode]} mode
            </Badge>
            <SymbolSelect value={symbol} onChange={setSymbol} />
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">Timeframe</Label>
              <Select value={timeframe} onValueChange={setTimeframe}>
                <SelectTrigger className="h-9 w-24 font-mono" aria-label="Timeframe">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {timeframeOptions.map((tf) => (
                    <SelectItem key={tf} value={tf} className="font-mono">
                      {tf}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="dash-limit" className="text-xs text-muted-foreground">
                Candles
              </Label>
              <Input
                id="dash-limit"
                type="number"
                min={LIMIT_MIN}
                max={LIMIT_MAX}
                value={limit}
                onChange={(e) =>
                  setLimit(Math.min(LIMIT_MAX, Math.max(LIMIT_MIN, Number(e.target.value) || LIMIT_MIN)))
                }
                className="h-9 w-24"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">Refresh</Label>
              <Select value={String(intervalMs)} onValueChange={(v) => setIntervalMs(Number(v))}>
                <SelectTrigger className="h-9 w-32" aria-label="Refresh interval">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {INTERVALS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center gap-2 pb-2">
              <Switch
                id="dash-closed-only"
                checked={closedOnly}
                onCheckedChange={setClosedOnly}
                aria-label="Closed candles only"
              />
              <Label htmlFor="dash-closed-only" className="text-xs text-muted-foreground">
                Closed only
              </Label>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="mb-0.5"
              onClick={() => void refreshFeed()}
              disabled={feed.status === 'loading'}
            >
              <RefreshCw
                className={feed.status === 'loading' ? 'h-4 w-4 animate-spin' : 'h-4 w-4'}
                aria-hidden
              />
              Refresh now
            </Button>
          </div>

          <Separator />

          <ConnectionStatus
            status={feed.status}
            lastSuccessAt={feed.lastSuccessAt}
            dataStatus={feed.dataStatus}
            provenance={feed.provenance}
            newestClosedAgeSeconds={feed.newestClosedAgeSeconds}
            error={feed.error}
            lastClosedCandleTime={
              feed.candles.length ? (feed.candles[feed.candles.length - 1].time ?? null) : null
            }
          />

          {feed.status === 'disconnected' && (
            <div className="rounded-md bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300" role="alert">
              <p className="font-medium">Not connected — the chart is NOT live.</p>
              <p className="mt-0.5">
                {feed.error?.code === 'BACKEND_UNREACHABLE'
                  ? 'The analysis backend is unreachable. Start it with `make backend` (or docker compose up).'
                  : (feed.error?.message ??
                    'Market data is unavailable. Nothing is fabricated while the terminal is offline.')}
              </p>
            </div>
          )}
          {feed.status === 'error' && feed.error && (
            <div className="rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400" role="alert">
              {feed.error.code === 'SYMBOL_NOT_FOUND'
                ? `Symbol "${symbol}" is unknown to the terminal — pick one from the suggestions.`
                : `${feed.error.code}: ${feed.error.message}`}
            </div>
          )}
          {feed.warnings.length > 0 && (
            <ul className="space-y-1">
              {feed.warnings.map((w, i) => (
                <li
                  key={`${w.code}-${i}`}
                  className={cn(
                    'rounded-md px-2.5 py-1.5 text-xs',
                    w.severity === 'error'
                      ? 'bg-destructive/10 text-destructive'
                      : w.severity === 'warning'
                        ? 'bg-amber-500/10 text-amber-700 dark:text-amber-400'
                        : 'bg-muted text-muted-foreground',
                  )}
                >
                  <span className="font-medium">{w.code}:</span> {w.message}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* ------------------------------------------------------ body -- */}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        {/* Left: chart + conditions ---------------------------------- */}
        <div className="min-w-0 space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <CardTitle className="flex items-center gap-2 text-base">
                  <ChartCandlestick className="h-4 w-4 text-muted-foreground" aria-hidden />
                  {symbol} · {timeframe}
                </CardTitle>
                {feed.includesForming && (
                  <Badge
                    variant="outline"
                    className="border-amber-500 text-amber-600 dark:text-amber-400"
                  >
                    includes forming candle
                  </Badge>
                )}
              </div>
              <CardDescription>
                Candle timestamps follow the MT5 server-time convention, rendered
                as UTC (documented in every API response).{isBinary && ' Binary Options mode: MT5 data is NOT the broker execution feed unless independently verified; OTC prices are never simulated.'}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {(feed.status === 'loading' || (feed.status === 'idle' && !hasCandles)) && (
                <Skeleton className="h-[420px] w-full" aria-label="Loading candles" />
              )}
              {feed.status === 'disconnected' && !hasCandles && (
                <div className="flex h-[420px] flex-col items-center justify-center gap-3 rounded-lg border border-dashed text-center">
                  <WifiOff className="h-8 w-8 text-muted-foreground" aria-hidden />
                  <div className="max-w-md space-y-1 px-4">
                    <p className="text-sm font-medium">No market data available</p>
                    <p className="text-xs text-muted-foreground">
                      No candles are shown because none exist — the platform never
                      fabricates prices. Connect an MT5 terminal (see README) and
                      press “Refresh now”.
                    </p>
                  </div>
                </div>
              )}
              {feed.status === 'no_data' && !hasCandles && (
                <div className="flex h-[420px] flex-col items-center justify-center gap-3 rounded-lg border border-dashed text-center">
                  <Info className="h-8 w-8 text-muted-foreground" aria-hidden />
                  <p className="max-w-md px-4 text-sm text-muted-foreground">
                    The terminal answered but has no candles for{' '}
                    <span className="font-mono">{symbol} {timeframe}</span> (market
                    closed, symbol idle or insufficient history). Nothing is
                    back-filled.
                  </p>
                </div>
              )}
              {hasCandles && (
                <CandleChart
                  candles={feed.candles}
                  seriesKey={seriesKey}
                  digits={digits}
                  overlays={overlays}
                  visible={visibleOverlays}
                  includesForming={feed.includesForming}
                />
              )}
            </CardContent>
          </Card>

          {report && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">Condition outcomes</CardTitle>
                <CardDescription>
                  met / not met / inconclusive / unsupported — a condition that
                  could not be evaluated is never a success.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <ConditionSections report={report} />
                <Sheet open={reportOpen} onOpenChange={setReportOpen}>
                  <SheetTrigger asChild>
                    <Button variant="outline" size="sm">
                      <FileSearch className="mr-1 h-4 w-4" aria-hidden />
                      Open full analysis report
                    </Button>
                  </SheetTrigger>
                  <SheetContent
                    side="bottom"
                    className="h-[85vh] overflow-y-auto"
                    aria-describedby={undefined}
                  >
                    <SheetHeader>
                      <SheetTitle>
                        Analysis report {report.analysis_id.slice(0, 8)}
                      </SheetTitle>
                      <SheetDescription>
                        Immutable, persisted engine report (schema{' '}
                        {report.report_schema_version}).
                      </SheetDescription>
                    </SheetHeader>
                    <div className="px-4 pb-6">
                      <ReportView report={report} />
                    </div>
                  </SheetContent>
                </Sheet>
              </CardContent>
            </Card>
          )}
        </div>

        {/* Right: strategy / overlays / history ----------------------- */}
        <div className="min-w-0 space-y-4">
          <Card data-testid="strategy-panel">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Strategy evaluation</CardTitle>
              <CardDescription>
                Runs the Phase 4 engine on real candles for every timeframe the
                strategy requires. No orders are ever placed.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="dash-strategy">Strategy</Label>
                <Select value={strategyId} onValueChange={setStrategyId}>
                  <SelectTrigger id="dash-strategy" aria-label="Strategy">
                    <SelectValue
                      placeholder={strategies.length ? undefined : 'No strategies yet'}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {strategies.map((s) => (
                      <SelectItem key={s.id} value={s.id}>
                        {s.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="dash-version">Version (immutable)</Label>
                <Select value={version} onValueChange={setVersion}>
                  <SelectTrigger id="dash-version" aria-label="Strategy version">
                    <SelectValue placeholder={versions.length ? undefined : 'No versions'} />
                  </SelectTrigger>
                  <SelectContent>
                    {versions.length === 0 && <SelectItem value="latest">latest</SelectItem>}
                    {versions
                      .slice()
                      .sort((a, b) => b.version - a.version)
                      .map((v) => (
                        <SelectItem key={v.id} value={String(v.version)}>
                          v{v.version} · {v.status}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
              <label className="flex items-center gap-2 text-sm text-muted-foreground">
                <Checkbox
                  id="dash-forming"
                  checked={includeForming}
                  onCheckedChange={(v) => setIncludeForming(v === true)}
                />
                Include forming candle (results labelled provisional)
              </label>
              <Button onClick={() => void runAnalysis()} disabled={running} className="w-full">
                {running ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  <Play className="h-4 w-4" aria-hidden />
                )}
                Run analysis
              </Button>

              {report && (
                <div className="space-y-2 rounded-lg border p-3" data-testid="evaluation-summary">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge
                      variant="outline"
                      className={cn(
                        'text-xs',
                        report.evaluation.setup_status === 'conditions_met' &&
                          'border-emerald-600/50 text-emerald-700 dark:text-emerald-400',
                        report.evaluation.setup_status === 'conditions_missing' &&
                          'border-amber-600/50 text-amber-700 dark:text-amber-400',
                        report.evaluation.setup_status === 'invalidated' &&
                          'border-red-600/50 text-red-700 dark:text-red-400',
                      )}
                    >
                      {SETUP_STATUS_META[report.evaluation.setup_status].label}
                    </Badge>
                    <span className="font-mono text-xs">{report.symbol}</span>
                    <span className="text-xs text-muted-foreground">
                      {report.strategy.name} v{report.strategy.version} (
                      {report.strategy.version_status})
                    </span>
                  </div>
                  <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Clock className="h-3 w-3" aria-hidden />
                    evaluated {report.evaluated_at}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Regime {report.regime.primary_timeframe}:{' '}
                    <span className="font-medium text-foreground">
                      {report.regime.primary.regime}
                    </span>{' '}
                    · volatility {report.regime.primary.volatility_state} · bias{' '}
                    {report.regime.primary.directional_bias}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Timeframe agreement: {report.regime.agreement.status} —{' '}
                    {report.regime.agreement.detail}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {report.strategy.rule_set.direction === 'both'
                      ? 'Direction: any'
                      : `Direction: ${report.strategy.rule_set.direction}`}{' '}
                    · timeframes {report.required_timeframes.join(', ')}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Technical conditions on historical data — not a prediction,
                    no probability of profit.
                  </p>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <Activity className="h-4 w-4 text-muted-foreground" aria-hidden />
                Chart overlays
              </CardTitle>
              <CardDescription>
                {report
                  ? `From report ${report.analysis_id.slice(0, 8)} · timeframe ${timeframe}`
                  : 'No analysis report selected yet.'}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {report &&
                overlays.availableKinds.length === 0 &&
                (report.observations?.by_timeframe?.some(
                  (o) => o.timeframe !== timeframe,
                ) ??
                  false) && (
                  <p className="mb-2 rounded-md bg-muted/50 px-2.5 py-1.5 text-xs text-muted-foreground">
                    This report has observations for{' '}
                    {report.observations.by_timeframe.map((o) => o.timeframe).join(', ')} but
                    none for the displayed timeframe ({timeframe}). Switch the chart timeframe
                    to see its overlays — none are drawn for the current one.
                  </p>
                )}
              <OverlayControls
                catalog={overlays}
                visible={visibleOverlays}
                hasReport={Boolean(report)}
                onToggle={(kind, value) =>
                  setVisibleOverlays((prev) => ({ ...prev, [kind]: value }))
                }
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <FileSearch className="h-4 w-4 text-muted-foreground" aria-hidden />
                Analysis history
              </CardTitle>
              <CardDescription>
                Persisted immutable reports ({MODE_LABELS[mode]}). Opening one
                shows the exact strategy version and data provenance used.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <AnalysisHistory
                mode={mode}
                activeReportId={report?.analysis_id ?? null}
                onOpen={(id) => void openReport(id)}
                refreshKey={historyKey}
              />
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
