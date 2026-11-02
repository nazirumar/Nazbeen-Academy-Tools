'use client'

/**
 * Backtest run detail (Phase 6): status polling, metrics overview, charts,
 * filterable chronological results and per-record evidence inspection.
 *
 * Honesty rules mirrored from the backend:
 * - Metrics that are null render as "Not available" — never zero.
 * - Charts render ONLY backend-persisted values; a run without a valid
 *   financial simulation shows an explicit empty state instead of a chart.
 * - Cancel is cooperative; a disconnected backend never looks "live".
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ChevronLeft,
  Download,
  Loader2,
  RefreshCw,
  XCircle,
} from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as ReTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { toast } from '@/hooks/use-toast'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { api } from '@/lib/api/client'
import {
  ApiError,
  type BacktestEquityPayload,
  type BacktestMetrics,
  type BacktestResultDetail,
  type BacktestResultSummary,
  type BacktestRun,
  type EvaluationResult,
  type PaginatedBacktestResults,
} from '@/lib/api/types'
import { CONDITION_STATUS_META, SETUP_STATUS_META } from '@/lib/platform/modes'
import type { SetupStatus } from '@/lib/api/types'
import {
  OUTCOME_META,
  RUN_STATUS_META,
  drawdownSeries,
  exportUrl,
  fmtNum,
  fmtPct,
  fmtUtc,
} from './backtest-shared'
import { cn } from '@/lib/utils'

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = e.details.length
      ? ` (${e.details.map((d) => `${d.path || ''} ${d.message ?? ''}`).join('; ')})`
      : ''
    return `${e.message}${detail}`
  }
  return e instanceof Error ? e.message : 'Unexpected error'
}

function MetricCell({
  label,
  value,
  hint,
}: {
  label: string
  value: string
  hint?: string
}) {
  const unavailable = value === 'Not available'
  return (
    <div className="rounded-lg border p-3" title={hint}>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p
        className={cn(
          'text-sm font-semibold tabular-nums',
          unavailable && 'font-normal text-muted-foreground',
        )}
      >
        {value}
      </p>
    </div>
  )
}

function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return <span className="text-muted-foreground">—</span>
  const meta = OUTCOME_META[outcome as keyof typeof OUTCOME_META]
  if (!meta) return <Badge variant="outline">{outcome}</Badge>
  return (
    <span className={cn('text-xs font-medium', meta.className)}>{meta.label}</span>
  )
}

function StatusBadge({ status }: { status: string | null }) {
  if (!status) return <span className="text-muted-foreground">—</span>
  const meta = SETUP_STATUS_META[status as SetupStatus]
  if (!meta) return <Badge variant="outline">{status}</Badge>
  return (
    <span className="text-xs font-medium">{meta.label}</span>
  )
}

interface Props {
  runId: string
  onBack: () => void
}

export function BacktestRunDetail({ runId, onBack }: Props) {
  const [run, setRun] = useState<BacktestRun | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [equity, setEquity] = useState<BacktestEquityPayload | null>(null)
  const [results, setResults] = useState<BacktestResultSummary[]>([])
  const [page, setPage] = useState(1)
  const [pageMeta, setPageMeta] = useState({ page: 1, page_size: 25, total: 0, pages: 1 })
  const [kindFilter, setKindFilter] = useState<string>('all')
  const [outcomeFilter, setOutcomeFilter] = useState<string>('all')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [selected, setSelected] = useState<BacktestResultDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [resultsLoading, setResultsLoading] = useState(true)

  const loadRun = useCallback(async () => {
    try {
      const data = await api.get<BacktestRun>(`/api/v1/backtests/${runId}`)
      setRun(data)
      setLoadError(null)
      return data
    } catch (e) {
      setLoadError(errorText(e))
      return null
    }
  }, [runId])

  // Run status: poll while queued/running (cooperative cancel means the
  // status endpoint is authoritative; nothing is assumed to be finished).
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | null = null
    const tick = async () => {
      const data = await loadRun()
      if (!active) return
      if (data && (data.status === 'queued' || data.status === 'running')) {
        timer = setTimeout(tick, 1000)
      }
    }
    void tick()
    return () => {
      active = false
      if (timer) clearTimeout(timer)
    }
  }, [loadRun])

  // Equity path (may not exist for setup-only runs — that is honest).
  useEffect(() => {
    let active = true
    api
      .get<BacktestEquityPayload>(`/api/v1/backtests/${runId}/equity`)
      .then((data) => {
        if (active) setEquity(data)
      })
      .catch(() => {
        if (active) setEquity(null)
      })
    return () => {
      active = false
    }
  }, [runId])

  // Results page (filterable, paginated).
  const loadResults = useCallback(async () => {
    setResultsLoading(true)
    try {
      const query: Record<string, string> = {
        page: String(page),
        page_size: '25',
      }
      if (kindFilter !== 'all') query.kind = kindFilter
      if (outcomeFilter !== 'all') query.outcome = outcomeFilter
      if (statusFilter !== 'all') query.setup_status = statusFilter
      const data = await api.get<PaginatedBacktestResults>(
        `/api/v1/backtests/${runId}/results`,
        query,
      )
      setResults(data.items)
      setPageMeta(data.page)
    } catch (e) {
      toast({ title: 'Could not load results', description: errorText(e), variant: 'destructive' })
    } finally {
      setResultsLoading(false)
    }
  }, [runId, page, kindFilter, outcomeFilter, statusFilter])

  useEffect(() => {
    void loadResults()
  }, [loadResults])

  const openDetail = async (seq: number) => {
    setDetailLoading(true)
    try {
      const data = await api.get<BacktestResultDetail>(
        `/api/v1/backtests/${runId}/results/${seq}`,
      )
      setSelected(data)
    } catch (e) {
      toast({ title: 'Could not load the record', description: errorText(e), variant: 'destructive' })
    } finally {
      setDetailLoading(false)
    }
  }

  const cancelRun = async () => {
    try {
      await api.post(`/api/v1/backtests/${runId}/cancel`)
      toast({ title: 'Cancellation requested', description: 'The engine stops at the next evaluation boundary.' })
      void loadRun()
    } catch (e) {
      toast({ title: 'Could not cancel', description: errorText(e), variant: 'destructive' })
    }
  }

  const refresh = async () => {
    await loadRun()
    await loadResults()
  }

  const metrics: BacktestMetrics | null = run?.metrics ?? null
  const activeStatus = run?.status ?? 'queued'

  const equityData = useMemo(
    () =>
      (equity?.points ?? []).map((p) => ({
        time_unix: p.time_unix,
        equity: p.equity,
      })),
    [equity],
  )
  const drawdownData = useMemo(() => drawdownSeries(equityData), [equityData])

  const outcomeData = useMemo(() => {
    if (!metrics) return []
    return (Object.entries(metrics.trades.by_outcome) as [string, number][])
      .filter(([, count]) => count > 0)
      .map(([outcome, count]) => ({ outcome, count }))
  }, [metrics])

  const regimeData = useMemo(() => {
    const source =
      metrics?.trades.total && metrics.trades.total > 0
        ? metrics.trades.breakdowns.by_regime
        : metrics?.setups.breakdowns.by_regime
    return (source ?? []).map((entry) => ({
      regime: entry.key,
      count: entry.count,
      net_pnl: entry.net_pnl ?? null,
    }))
  }, [metrics])

  if (loadError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <XCircle className="h-4 w-4 text-red-500" aria-hidden /> Run unavailable
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Alert variant="destructive">
            <AlertDescription>{loadError}</AlertDescription>
          </Alert>
          <Button variant="outline" size="sm" onClick={onBack}>
            <ChevronLeft className="h-4 w-4" aria-hidden /> Back to runs
          </Button>
        </CardContent>
      </Card>
    )
  }

  if (!run) {
    return (
      <div className="flex items-center justify-center py-16 text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading run…
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onBack} aria-label="Back to runs">
                  <ChevronLeft className="h-4 w-4" aria-hidden />
                </Button>
                <span className="truncate">
                  {run.strategy_name} v{run.strategy_version}
                </span>
                <Badge className={RUN_STATUS_META[activeStatus].className}>
                  {RUN_STATUS_META[activeStatus].label}
                </Badge>
                <Badge variant="outline">{run.mode === 'forex' ? 'Forex' : 'Binary Options'}</Badge>
                <Badge variant="outline">{run.simulation_mode === 'trade_simulation' ? 'Trade simulation' : 'Setup-only'}</Badge>
              </CardTitle>
              <CardDescription className="mt-1">
                {run.symbol} · {run.timeframe} · {fmtUtc(run.start_time)} → {fmtUtc(run.end_time)} ·
                {' '}{run.candles_evaluated.toLocaleString()} candles evaluated · engine {run.engine_version}
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={refresh}>
                <RefreshCw className="h-4 w-4" aria-hidden /> Refresh
              </Button>
              {(activeStatus === 'queued' || activeStatus === 'running') && (
                <Button variant="destructive" size="sm" onClick={cancelRun}>
                  Cancel
                </Button>
              )}
              {activeStatus === 'completed' && (
                <Button variant="outline" size="sm" asChild>
                  <a href={exportUrl(`/api/v1/backtests/${runId}/export`)} download>
                    <Download className="h-4 w-4" aria-hidden /> CSV
                  </a>
                </Button>
              )}
            </div>
          </div>
        </CardHeader>
        {(activeStatus === 'queued' || activeStatus === 'running') && (
          <CardContent className="pt-0">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              {activeStatus === 'queued'
                ? 'Waiting for the runner (runs execute one at a time in this process)…'
                : 'Walking historical candles chronologically…'}
            </div>
          </CardContent>
        )}
        {run.error ? (
          <CardContent className="pt-0">
            <Alert variant="destructive">
              <AlertDescription>
                <span className="font-semibold">{run.error.code}</span> — {run.error.message}
              </AlertDescription>
            </Alert>
          </CardContent>
        ) : null}
      </Card>

      {/* Data quality */}
      {run.data_quality ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Data source & quality</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p className="text-muted-foreground">
              {run.data_quality.source_note ?? 'Stored historical candle cache.'}
              {run.data_quality.overall_status
                ? ` Overall status: ${run.data_quality.overall_status}.`
                : ''}
            </p>
            {(run.data_quality.timeframes ?? []).some(
              (tf) => (tf.warnings ?? []).length > 0 || tf.gap_count,
            ) ? (
              <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
                {(run.data_quality.timeframes ?? []).flatMap((tf) =>
                  (tf.warnings ?? []).map((w) => (
                    <li key={`${tf.timeframe}-${w.code}`}>
                      {tf.timeframe}: <span className="font-medium">{w.code}</span> — {w.message}
                    </li>
                  )),
                )}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">No data-quality warnings for this window.</p>
            )}
          </CardContent>
        </Card>
      ) : null}

      {/* Metrics overview */}
      {metrics ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Results overview</CardTitle>
            <CardDescription>{metrics.note}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
              <MetricCell
                label="Setups evaluated"
                value={metrics.setups.total_evaluated.toLocaleString()}
              />
              <MetricCell label="Conditions met" value={String(metrics.setups.by_status.conditions_met ?? 0)} />
              <MetricCell
                label="Simulated trades"
                value={
                  metrics.simulation_mode === 'setup_only'
                    ? 'Not available'
                    : metrics.trades.total.toLocaleString()
                }
                hint={metrics.simulation_mode === 'setup_only' ? 'Setup-only run: no trades simulated.' : undefined}
              />
              <MetricCell
                label="Win rate"
                value={metrics.trades.win_rate === null ? 'Not available' : fmtPct(metrics.trades.win_rate * 100)}
                hint={metrics.trades.win_rate_denominator}
              />
              <MetricCell
                label="Net P&L"
                value={fmtNum(metrics.trades.net_pnl)}
              />
              <MetricCell
                label="Max drawdown"
                value={
                  metrics.equity.max_drawdown
                    ? `${fmtNum(metrics.equity.max_drawdown.max_drawdown)} (${fmtPct(metrics.equity.max_drawdown.max_drawdown_pct)})`
                    : 'Not available'
                }
                hint={metrics.equity.note}
              />
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
              <MetricCell label="Avg win" value={fmtNum(metrics.trades.avg_win)} />
              <MetricCell label="Avg loss" value={fmtNum(metrics.trades.avg_loss)} />
              <MetricCell
                label="Expectancy / trade"
                value={fmtNum(metrics.trades.expectancy)}
                hint={metrics.trades.expectancy_note}
              />
              <MetricCell
                label="Profit factor"
                value={fmtNum(metrics.trades.profit_factor)}
                hint={metrics.trades.profit_factor_note}
              />
              <MetricCell
                label="Avg holding (candles)"
                value={fmtNum(metrics.trades.avg_holding_candles, 1)}
              />
              <MetricCell label="Exposure" value={fmtPct(metrics.trades.exposure_pct)} />
            </div>
            <p className="text-xs text-muted-foreground">
              {metrics.simulation_mode === 'setup_only'
                ? 'Setup-only run: financial metrics are intentionally unavailable — the strategy defines setups, not trades, and the platform never fabricates trade records.'
                : 'All financial figures derive from the declared execution assumptions of THIS run. Historical simulation statistics only — never expected future performance.'}
            </p>

            {/* Charts — only from persisted backend values */}
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="rounded-lg border p-3">
                <p className="mb-2 text-xs font-medium">Equity path (closed trades)</p>
                {equityData.length >= 2 ? (
                  <ResponsiveContainer width="100%" height={180}>
                    <LineChart data={equityData} margin={{ top: 5, right: 8, bottom: 5, left: 8 }}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                      <XAxis
                        dataKey="time_unix"
                        tickFormatter={(t: number) => new Date(t * 1000).toISOString().slice(5, 10)}
                        fontSize={10}
                      />
                      <YAxis fontSize={10} domain={['auto', 'auto']} />
                      <ReTooltip
                        labelFormatter={(t) => fmtUtc(new Date(Number(t) * 1000).toISOString())}
                        formatter={(value) => fmtNum(Number(value))}
                      />
                      <Line type="monotone" dataKey="equity" stroke="hsl(var(--primary))" dot={false} strokeWidth={2} />
                    </LineChart>
                  </ResponsiveContainer>
                ) : (
                  <p className="py-10 text-center text-xs text-muted-foreground">
                    {metrics.equity.note}
                  </p>
                )}
              </div>
              <div className="rounded-lg border p-3">
                <p className="mb-2 text-xs font-medium">Drawdown from equity path</p>
                {drawdownData.length >= 2 ? (
                  <ResponsiveContainer width="100%" height={180}>
                    <LineChart data={drawdownData} margin={{ top: 5, right: 8, bottom: 5, left: 8 }}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                      <XAxis
                        dataKey="time_unix"
                        tickFormatter={(t: number) => new Date(t * 1000).toISOString().slice(5, 10)}
                        fontSize={10}
                      />
                      <YAxis fontSize={10} domain={['auto', 'auto']} />
                      <ReTooltip
                        labelFormatter={(t) => fmtUtc(new Date(Number(t) * 1000).toISOString())}
                        formatter={(value) => fmtNum(Number(value))}
                      />
                      <Line type="monotone" dataKey="drawdown" stroke="#ef4444" dot={false} strokeWidth={2} />
                    </LineChart>
                  </ResponsiveContainer>
                ) : (
                  <p className="py-10 text-center text-xs text-muted-foreground">
                    No equity series — drawdown is not derivable and is never invented.
                  </p>
                )}
              </div>
              {outcomeData.length > 0 ? (
                <div className="rounded-lg border p-3">
                  <p className="mb-2 text-xs font-medium">Outcome distribution</p>
                  <ResponsiveContainer width="100%" height={180}>
                    <BarChart data={outcomeData} margin={{ top: 5, right: 8, bottom: 5, left: 8 }}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                      <XAxis dataKey="outcome" fontSize={10} />
                      <YAxis fontSize={10} allowDecimals={false} />
                      <ReTooltip formatter={(value) => String(value)} />
                      <Bar dataKey="count" fill="hsl(var(--primary))" radius={4} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : null}
              {regimeData.length > 0 ? (
                <div className="rounded-lg border p-3">
                  <p className="mb-2 text-xs font-medium">
                    {metrics.trades.total > 0 ? 'Trades' : 'Setups'} by market regime
                  </p>
                  <ResponsiveContainer width="100%" height={180}>
                    <BarChart data={regimeData} margin={{ top: 5, right: 8, bottom: 5, left: 8 }}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                      <XAxis dataKey="regime" fontSize={10} />
                      <YAxis fontSize={10} allowDecimals={false} />
                      <ReTooltip
                        formatter={(value, name) =>
                          name === 'net_pnl' ? fmtNum(Number(value)) : String(value)
                        }
                      />
                      <Bar dataKey="count" fill="hsl(var(--primary))" radius={4} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : null}
            </div>
          </CardContent>
        </Card>
      ) : activeStatus === 'completed' ? (
        <Alert>
          <AlertDescription>Run completed but produced no metrics payload.</AlertDescription>
        </Alert>
      ) : null}

      {/* Detailed results */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Detailed results</CardTitle>
          <CardDescription>
            Chronological records (ascending). Open a row to inspect the full
            four-state evaluation evidence.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <Select value={kindFilter} onValueChange={(v) => { setKindFilter(v); setPage(1) }}>
              <SelectTrigger className="w-36" aria-label="Record kind filter">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All records</SelectItem>
                <SelectItem value="setup">Setups</SelectItem>
                <SelectItem value="trade">Trades</SelectItem>
              </SelectContent>
            </Select>
            <Select value={statusFilter} onValueChange={(v) => { setStatusFilter(v); setPage(1) }}>
              <SelectTrigger className="w-44" aria-label="Setup status filter">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Any setup status</SelectItem>
                {Object.entries(SETUP_STATUS_META).map(([value, meta]) => (
                  <SelectItem key={value} value={value}>
                    {meta.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={outcomeFilter} onValueChange={(v) => { setOutcomeFilter(v); setPage(1) }}>
              <SelectTrigger className="w-40" aria-label="Outcome filter">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Any outcome</SelectItem>
                {Object.entries(OUTCOME_META).map(([value, meta]) => (
                  <SelectItem key={value} value={value}>
                    {meta.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {resultsLoading ? (
            <div className="flex items-center justify-center py-10 text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading records…
            </div>
          ) : results.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              No records match these filters for this run.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-14">#</TableHead>
                    <TableHead>Time (UTC)</TableHead>
                    <TableHead>Kind</TableHead>
                    <TableHead>Setup status</TableHead>
                    <TableHead>Outcome</TableHead>
                    <TableHead className="text-right">P&L</TableHead>
                    <TableHead className="text-right">Return</TableHead>
                    <TableHead className="text-right">Equity</TableHead>
                    <TableHead>Regime</TableHead>
                    <TableHead className="text-right">Warnings</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {results.map((r) => (
                    <TableRow
                      key={r.id}
                      className="cursor-pointer"
                      onClick={() => void openDetail(r.seq)}
                    >
                      <TableCell className="tabular-nums">{r.seq}</TableCell>
                      <TableCell className="whitespace-nowrap text-xs">
                        {fmtUtc(r.result_time)}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{r.kind}</Badge>
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={r.setup_status} />
                      </TableCell>
                      <TableCell>
                        <OutcomeBadge outcome={r.outcome} />
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{fmtNum(r.pnl)}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {r.return_pct === null ? '—' : fmtPct(r.return_pct * 100)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {r.equity_after === null ? '—' : fmtNum(r.equity_after)}
                      </TableCell>
                      <TableCell className="text-xs">{r.regime ?? '—'}</TableCell>
                      <TableCell className="text-right text-xs">
                        {r.warning_count > 0 ? (
                          <span className="inline-flex items-center gap-1 text-amber-600 dark:text-amber-400">
                            <AlertTriangle className="h-3 w-3" aria-hidden /> {r.warning_count}
                          </span>
                        ) : (
                          '0'
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          {/* Pagination */}
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">
              Page {pageMeta.page} of {pageMeta.pages} · {pageMeta.total.toLocaleString()} records
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1 || resultsLoading}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= pageMeta.pages || resultsLoading}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Evidence sheet */}
      <Sheet open={selected !== null} onOpenChange={(open) => !open && setSelected(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
          {selected ? (
            <>
              <SheetHeader>
                <SheetTitle className="text-base">
                  Record #{selected.seq} · {selected.kind}
                </SheetTitle>
                <SheetDescription>
                  {fmtUtc(selected.result_time)} · {selected.setup_status ? SETUP_STATUS_META[selected.setup_status as SetupStatus]?.label : ''}
                </SheetDescription>
              </SheetHeader>
              <div className="mt-4 space-y-4">
                {selected.note ? (
                  <Alert>
                    <AlertDescription>{selected.note}</AlertDescription>
                  </Alert>
                ) : null}
                {selected.execution ? (
                  <div>
                    <p className="mb-1 text-xs font-semibold">Execution details (declared assumptions)</p>
                    <pre className="max-h-48 overflow-auto rounded-lg bg-muted p-2 text-[11px] leading-relaxed">
                      {JSON.stringify(selected.execution, null, 2)}
                    </pre>
                  </div>
                ) : null}
                {selected.warnings && selected.warnings.length > 0 ? (
                  <div>
                    <p className="mb-1 text-xs font-semibold">Data-quality warnings</p>
                    <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
                      {selected.warnings.map((w, i) => (
                        <li key={i}>
                          <span className="font-medium">{w.code}</span> ({w.timeframe}) — {w.message}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {selected.evaluation ? (
                  <EvidenceTree node={selected.evaluation.core} label="Core conditions" />
                ) : (
                  <p className="text-xs text-muted-foreground">
                    Trade records reference their originating setup; the full
                    evaluation tree lives on the setup record.
                  </p>
                )}
              </div>
            </>
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  )
}

function EvidenceTree({ node, label }: { node: EvaluationResult['core']; label: string }) {
  return (
    <div>
      <p className="mb-1 text-xs font-semibold">{label}</p>
      <div className="space-y-1">
        <NodeRow node={node} depth={0} />
      </div>
    </div>
  )
}

function NodeRow({ node, depth }: { node: EvaluationResult['core']; depth: number }) {
  const status = node.status ?? 'not_met'
  const meta = CONDITION_STATUS_META[status]
  return (
    <div>
      <div
        className="flex items-start gap-2 rounded px-1 py-0.5"
        style={{ paddingLeft: `${depth * 14 + 4}px` }}
      >
        <span className={cn('mt-0.5 text-xs font-medium', meta.className)}>
          [{meta.label}]
        </span>
        <div className="min-w-0">
          <p className="text-xs leading-snug">{node.description}</p>
          {node.evidence?.criterion ? (
            <p className="text-[11px] text-muted-foreground">{node.evidence.criterion}</p>
          ) : null}
          {node.evidence?.reason ? (
            <p className="text-[11px] text-muted-foreground">{node.evidence.reason}</p>
          ) : null}
        </div>
      </div>
      {(node.children ?? []).map((child, i) => (
        <NodeRow key={`${child.id}-${i}`} node={child} depth={depth + 1} />
      ))}
    </div>
  )
}
