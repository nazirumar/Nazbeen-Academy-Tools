'use client'

/**
 * Backtesting workspace (Phase 6) — configuration, run history, run detail.
 *
 * Comparison is deliberately conservative: two runs can be compared
 * side-by-side and their CONFIGURATIONS are diffed field-by-field, with an
 * explicit warning that runs over different symbols, date ranges, data
 * windows or cost assumptions are never directly equivalent.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  ChevronRight,
  GitCompareArrows,
  History,
  Loader2,
  Plus,
  X,
} from 'lucide-react'
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
  type BacktestRun,
  type BacktestRunListItem,
  type PaginatedBacktestRuns,
  type PlatformMode,
} from '@/lib/api/types'
import { BacktestConfigPanel } from './backtest-config-panel'
import { BacktestRunDetail } from './backtest-run-detail'
import { RUN_STATUS_META, fmtNum, fmtUtc } from './backtest-shared'
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

const COMPARABLE_CONFIG_FIELDS: [keyof Record<string, unknown>, string][] = [
  ['symbol', 'Symbol'],
  ['timeframe', 'Timeframe'],
  ['start', 'Window start'],
  ['end', 'Window end'],
  ['simulation_mode', 'Simulation mode'],
  ['data_source', 'Data source'],
  ['strategy_version', 'Strategy version'],
]

interface Props {
  mode: PlatformMode
}

export function BacktestWorkspace({ mode }: Props) {
  const [runs, setRuns] = useState<BacktestRunListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const [showConfig, setShowConfig] = useState(true)
  const [compareIds, setCompareIds] = useState<string[]>([])
  const [compareRuns, setCompareRuns] = useState<BacktestRun[] | null>(null)
  const [comparing, setComparing] = useState(false)

  const loadRuns = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.get<PaginatedBacktestRuns>('/api/v1/backtests', {
        mode,
        page_size: '25',
      })
      setRuns(data.items)
    } catch (e) {
      toast({ title: 'Could not load run history', description: errorText(e), variant: 'destructive' })
    } finally {
      setLoading(false)
    }
  }, [mode])

  useEffect(() => {
    setActiveRunId(null)
    setCompareIds([])
    setCompareRuns(null)
    void loadRuns()
  }, [loadRuns])

  const toggleCompare = (id: string) => {
    setCompareIds((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id)
      if (prev.length >= 2) return [prev[1], id]
      return [...prev, id]
    })
  }

  const runComparison = async () => {
    if (compareIds.length !== 2) return
    setComparing(true)
    try {
      const [a, b] = await Promise.all(
        compareIds.map((id) => api.get<BacktestRun>(`/api/v1/backtests/${id}`)),
      )
      setCompareRuns([a, b])
    } catch (e) {
      toast({ title: 'Could not load runs for comparison', description: errorText(e), variant: 'destructive' })
    } finally {
      setComparing(false)
    }
  }

  const onRunCreated = (run: BacktestRun) => {
    setActiveRunId(run.id)
    setShowConfig(false)
    void loadRuns()
  }

  return (
    <div className="space-y-4">
      {/* Run history */}
      <Card>
        <CardHeader className="pb-2">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <CardTitle className="flex items-center gap-2 text-base">
                <History className="h-4 w-4" aria-hidden /> Run history
              </CardTitle>
              <CardDescription>
                Previous backtests for this mode. Select up to two runs to
                compare — configuration differences are shown explicitly.
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              {compareIds.length === 2 ? (
                <Button size="sm" variant="outline" onClick={runComparison} disabled={comparing}>
                  {comparing ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  ) : (
                    <GitCompareArrows className="h-4 w-4" aria-hidden />
                  )}
                  Compare selected
                </Button>
              ) : null}
              <Button
                size="sm"
                variant={showConfig ? 'secondary' : 'default'}
                onClick={() => {
                  setShowConfig((v) => !v)
                  if (showConfig) setActiveRunId(null)
                }}
              >
                <Plus className="h-4 w-4" aria-hidden /> New run
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center justify-center py-8 text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading runs…
            </div>
          ) : runs.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No backtests yet for this mode. Configure your first run below.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10" aria-label="Compare selection" />
                    <TableHead>Strategy</TableHead>
                    <TableHead>Symbol / TF</TableHead>
                    <TableHead>Window (UTC)</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Candles</TableHead>
                    <TableHead className="text-right">Win rate</TableHead>
                    <TableHead className="text-right">Net P&L</TableHead>
                    <TableHead aria-label="Open run" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runs.map((r) => (
                    <TableRow
                      key={r.id}
                      className={cn(
                        'cursor-pointer',
                        activeRunId === r.id && 'bg-muted/50',
                      )}
                      onClick={() => {
                        setActiveRunId(r.id)
                        setShowConfig(false)
                      }}
                    >
                      <TableCell onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          className="h-4 w-4"
                          aria-label={`Select run ${r.id} for comparison`}
                          checked={compareIds.includes(r.id)}
                          onChange={() => toggleCompare(r.id)}
                        />
                      </TableCell>
                      <TableCell className="text-xs">
                        <span className="font-medium">{r.strategy_name ?? '—'}</span>{' '}
                        <span className="text-muted-foreground">v{r.strategy_version}</span>
                      </TableCell>
                      <TableCell className="text-xs">
                        {r.symbol} · {r.timeframe}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-xs">
                        {fmtUtc(r.start_time)} → {fmtUtc(r.end_time)}
                      </TableCell>
                      <TableCell>
                        <Badge className={RUN_STATUS_META[r.status].className}>
                          {RUN_STATUS_META[r.status].label}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-xs">
                        {r.candles_evaluated.toLocaleString()}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-xs">
                        {r.win_rate === null ? (
                          <span className="text-muted-foreground">N/A</span>
                        ) : (
                          `${(r.win_rate * 100).toFixed(1)}%`
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-xs">
                        {r.net_pnl === null ? (
                          <span className="text-muted-foreground">N/A</span>
                        ) : (
                          fmtNum(r.net_pnl)
                        )}
                      </TableCell>
                      <TableCell>
                        <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Comparison */}
      {compareRuns && compareRuns.length === 2 ? (
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-start justify-between gap-2">
              <div>
                <CardTitle className="text-sm">Run comparison</CardTitle>
                <CardDescription>
                  {compareRuns[0].symbol === compareRuns[1].symbol &&
                  compareRuns[0].start_time === compareRuns[1].start_time &&
                  compareRuns[0].end_time === compareRuns[1].end_time &&
                  compareRuns[0].data_quality?.source === compareRuns[1].data_quality?.source
                    ? 'Same symbol and window — metrics are broadly comparable.'
                    : 'WARNING: these runs differ in symbol, window or data assumptions — their metrics are NOT directly equivalent.'}
                </CardDescription>
              </div>
              <Button variant="ghost" size="icon" onClick={() => setCompareRuns(null)} aria-label="Close comparison">
                <X className="h-4 w-4" aria-hidden />
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              {compareRuns.map((r, idx) => (
                <div key={r.id} className="rounded-lg border p-3 text-sm">
                  <p className="font-medium">
                    {r.strategy_name} v{r.strategy_version}{' '}
                    <span className="text-xs text-muted-foreground">({r.status})</span>
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {r.symbol} · {r.timeframe} · {fmtUtc(r.start_time)} → {fmtUtc(r.end_time)}
                  </p>
                  <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                    <dt className="text-muted-foreground">Setups</dt>
                    <dd className="tabular-nums">{r.metrics?.setups.total_evaluated ?? '—'}</dd>
                    <dt className="text-muted-foreground">Conditions met</dt>
                    <dd className="tabular-nums">{r.metrics?.setups.by_status.conditions_met ?? '—'}</dd>
                    <dt className="text-muted-foreground">Trades</dt>
                    <dd className="tabular-nums">{r.metrics?.trades.total ?? '—'}</dd>
                    <dt className="text-muted-foreground">Win rate</dt>
                    <dd className="tabular-nums">
                      {r.metrics?.trades.win_rate == null ? 'N/A' : `${(r.metrics.trades.win_rate * 100).toFixed(1)}%`}
                    </dd>
                    <dt className="text-muted-foreground">Net P&L</dt>
                    <dd className="tabular-nums">{fmtNum(r.metrics?.trades.net_pnl)}</dd>
                    <dt className="text-muted-foreground">Max drawdown</dt>
                    <dd className="tabular-nums">
                      {r.metrics?.equity.max_drawdown == null
                        ? 'N/A'
                        : fmtNum(r.metrics.equity.max_drawdown.max_drawdown)}
                    </dd>
                  </dl>
                  {idx === 0 ? null : null}
                </div>
              ))}
            </div>
            <ConfigDiff a={compareRuns[0]} b={compareRuns[1]} />
          </CardContent>
        </Card>
      ) : null}

      {/* Configuration (hidden while inspecting a run) */}
      {showConfig ? (
        <BacktestConfigPanel mode={mode} onRunCreated={onRunCreated} />
      ) : activeRunId ? null : (
        <Alert>
          <AlertDescription>
            Create a new run or pick one from the history above.
          </AlertDescription>
        </Alert>
      )}

      {/* Active run detail */}
      {activeRunId ? (
        <BacktestRunDetail runId={activeRunId} onBack={() => setActiveRunId(null)} />
      ) : null}
    </div>
  )
}

function ConfigDiff({ a, b }: { a: BacktestRun; b: BacktestRun }) {
  const rows: { field: string; a: string; b: string; differs: boolean }[] = []
  const ca = (a.config ?? {}) as Record<string, unknown>
  const cb = (b.config ?? {}) as Record<string, unknown>
  for (const [field, label] of COMPARABLE_CONFIG_FIELDS) {
    const va = String(ca[field] ?? '—')
    const vb = String(cb[field] ?? '—')
    rows.push({ field: label, a: va, b: vb, differs: va !== vb })
  }
  const fa = JSON.stringify(ca.forex_execution ?? ca.binary ?? null)
  const fb = JSON.stringify(cb.forex_execution ?? cb.binary ?? null)
  rows.push({
    field: 'Execution assumptions',
    a: fa === 'null' ? 'none (setup-only)' : 'configured',
    b: fb === 'null' ? 'none (setup-only)' : 'configured',
    differs: fa !== fb,
  })
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Configuration field</TableHead>
            <TableHead>Run A</TableHead>
            <TableHead>Run B</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.field}>
              <TableCell className="text-xs font-medium">{row.field}</TableCell>
              <TableCell
                className={cn(
                  'max-w-56 truncate text-xs',
                  row.differs && 'font-semibold text-amber-600 dark:text-amber-400',
                )}
              >
                {row.a}
              </TableCell>
              <TableCell
                className={cn(
                  'max-w-56 truncate text-xs',
                  row.differs && 'font-semibold text-amber-600 dark:text-amber-400',
                )}
              >
                {row.b}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
