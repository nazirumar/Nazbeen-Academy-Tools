'use client'

import { useCallback, useEffect, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  Ban,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  HelpCircle,
  History,
  Loader2,
  Play,
  XCircle,
} from 'lucide-react'
import { toast } from '@/hooks/use-toast'
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
import { api } from '@/lib/api/client'
import { ApiError } from '@/lib/api/types'
import { AIExplanationPanel } from '@/components/ai/ai-explanation-panel'
import type {
  AnalysisReport,
  AnalysisReportSummary,
  ConditionStatus,
  EvaluationNode,
  PaginatedAnalysisReports,
  PaginatedStrategies,
  PlatformMode,
  Strategy,
} from '@/lib/api/types'
import {
  SETUP_STATUS_META,
  formatUnixTime,
} from '@/lib/platform/modes'
import { cn } from '@/lib/utils'

/* Shared error formatter (same convention as the inline workbench). */
function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = e.details.length
      ? ` (${e.details.map((d) => `${d.path || ''} ${d.message ?? ''}`).join('; ')})`
      : ''
    return `${e.message}${detail}`
  }
  return e instanceof Error ? e.message : 'Unexpected error'
}

/* -------------------------------------------------------------------------- */
/* Four-state condition display: met / not_met / inconclusive / unsupported.  */
/* Inconclusive and unsupported are NEVER rendered as success.                */
/* -------------------------------------------------------------------------- */

function ConditionIcon({ status }: { status: ConditionStatus }) {
  if (status === 'met')
    return (
      <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-label="condition met" />
    )
  if (status === 'not_met')
    return <XCircle className="h-4 w-4 shrink-0 text-red-500" aria-label="condition not met" />
  if (status === 'inconclusive')
    return (
      <HelpCircle className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" aria-label="condition inconclusive" />
    )
  return <Ban className="h-4 w-4 shrink-0 text-slate-500 dark:text-slate-400" aria-label="rule type unsupported" />
}

function statusBadgeClass(status: ConditionStatus): string {
  switch (status) {
    case 'met':
      return 'border-emerald-600/50 text-emerald-700 dark:text-emerald-400'
    case 'not_met':
      return 'border-red-600/50 text-red-700 dark:text-red-400'
    case 'inconclusive':
      return 'border-amber-600/50 text-amber-700 dark:text-amber-400'
    default:
      return 'border-slate-500/50 text-slate-600 dark:text-slate-400'
  }
}

function StatusTree({ node, depth = 0 }: { node: EvaluationNode; depth?: number }) {
  const [open, setOpen] = useState(depth < 1)
  const hasChildren = node.children && node.children.length > 0
  const status: ConditionStatus = node.status ?? (node.satisfied ? 'met' : 'not_met')
  const ev = node.evidence
  return (
    <div className={cn(depth > 0 && 'ml-4 border-l pl-3')}>
      <button
        type="button"
        onClick={() => hasChildren && setOpen((o) => !o)}
        className="flex w-full items-start gap-2 rounded-md px-1.5 py-1 text-left hover:bg-muted/40"
        aria-expanded={open}
      >
        {hasChildren ? (
          open ? (
            <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          ) : (
            <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          )
        ) : (
          <span className="w-4 shrink-0" aria-hidden />
        )}
        <ConditionIcon status={status} />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            {node.kind === 'group' ? (
              <Badge variant="secondary" className="font-mono text-[10px]">{node.logic}</Badge>
            ) : (
              <span className="font-mono text-[10px] text-muted-foreground">{node.id}</span>
            )}
            <span className="text-sm">{node.description}</span>
            <Badge variant="outline" className={cn('text-[10px]', statusBadgeClass(status))}>
              {status}
            </Badge>
          </span>
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 rounded-md bg-muted/50 px-3 py-2 text-xs">
            {ev.timeframe && (
              <div className="col-span-2 grid grid-cols-subgrid items-baseline">
                <dt className="text-muted-foreground">Timeframe</dt>
                <dd className="font-medium">{ev.timeframe}</dd>
              </div>
            )}
            {ev.candles_evaluated !== undefined && (
              <div className="col-span-2 grid grid-cols-subgrid items-baseline">
                <dt className="text-muted-foreground">Candles evaluated</dt>
                <dd className="font-medium">
                  {ev.candles_evaluated}
                  {ev.candles_available !== undefined ? ` (available: ${ev.candles_available})` : ''}
                </dd>
              </div>
            )}
            {ev.last_candle_time !== undefined && (
              <div className="col-span-2 grid grid-cols-subgrid items-baseline">
                <dt className="text-muted-foreground">Last candle used</dt>
                <dd className="font-medium">
                  {formatUnixTime(ev.last_candle_time)}{' '}
                  {ev.last_candle_status ? `(${ev.last_candle_status})` : ''}
                </dd>
              </div>
            )}
            {ev.current_value !== undefined && (
              <div className="col-span-2 grid grid-cols-subgrid items-baseline">
                <dt className="text-muted-foreground">Current value</dt>
                <dd className="font-medium">{String(ev.current_value)}</dd>
              </div>
            )}
            {ev.reason && (
              <div className="col-span-2 grid grid-cols-subgrid items-baseline">
                <dt className="text-muted-foreground">Reason</dt>
                <dd className="break-words font-medium">{ev.reason}</dd>
              </div>
            )}
            {ev.status_reason && (
              <div className="col-span-2 grid grid-cols-subgrid items-baseline">
                <dt className="text-muted-foreground">Group status</dt>
                <dd className="break-words font-medium">{ev.status_reason}</dd>
              </div>
            )}
          </dl>
        </span>
      </button>
      {open && hasChildren && (
        <div role="group" aria-label={`${node.logic} group children`}>
          {node.children!.map((child, i) => (
            <StatusTree key={`${child.id}-${i}`} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Report rendering                                                           */
/* -------------------------------------------------------------------------- */

function DataPanel({ report }: { report: AnalysisReport }) {
  const { data } = report
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Market data</CardTitle>
        <CardDescription>{data.source_note}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge variant="outline" className="font-mono">
            quality: {data.quality_status}
          </Badge>
          <Badge variant="outline" className="font-mono">
            {data.closed_candles_only ? 'closed candles only' : 'forming candle included (provisional)'}
          </Badge>
        </div>
        <ul className="space-y-1 text-xs text-muted-foreground">
          {data.timeframes.map((tf) => (
            <li key={tf.timeframe} className="rounded-md bg-muted/40 px-2 py-1.5">
              <span className="font-mono font-semibold">{tf.timeframe}</span>{' '}
              — {tf.used_candle_count ?? tf.returned_candles} candles used ·{' '}
              {tf.provenance} · status {tf.provider_data_status}
              {tf.stale ? ' · STALE' : ''} ({tf.note})
            </li>
          ))}
        </ul>
        {data.warnings.length > 0 && (
          <div className="rounded-md border border-amber-600/40 bg-amber-500/5 p-3" role="alert">
            <p className="mb-1 flex items-center gap-1.5 text-xs font-medium text-amber-700 dark:text-amber-400">
              <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
              Data warnings ({data.warnings.length})
            </p>
            <ul className="list-inside list-disc space-y-0.5 text-xs text-muted-foreground">
              {data.warnings.map((w, i) => (
                <li key={i}>
                  <span className="font-mono text-[10px]">[{w.timeframe}]</span> {w.message}
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function RegimePanel({ report }: { report: AnalysisReport }) {
  const { regime } = report
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Market regime</CardTitle>
        <CardDescription>{regime.note}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="font-mono text-xs">
            {regime.primary_timeframe}: {regime.primary.regime}
          </Badge>
          <Badge variant="outline" className="font-mono text-xs">
            volatility: {regime.primary.volatility_state}
          </Badge>
          <Badge variant="outline" className="font-mono text-xs">
            bias: {regime.primary.directional_bias}
          </Badge>
          <Badge
            variant="outline"
            className={cn(
              'text-xs',
              regime.applicability.applicable
                ? 'border-emerald-600/50 text-emerald-700 dark:text-emerald-400'
                : 'border-amber-600/50 text-amber-700 dark:text-amber-400'
            )}
          >
            regime required: {regime.applicability.required} —{' '}
            {regime.applicability.applicable ? 'applicable' : 'NOT applicable'}
          </Badge>
        </div>
        <ul className="space-y-1 text-xs text-muted-foreground">
          {regime.by_timeframe.map((r) => (
            <li key={r.timeframe} className="rounded-md bg-muted/40 px-2 py-1.5">
              <span className="font-mono font-semibold">{r.timeframe}</span> — {r.regime} ·{' '}
              {r.directional_bias}
            </li>
          ))}
        </ul>
        <p className="text-xs text-muted-foreground">
          <span className="font-semibold uppercase tracking-wide">Timeframe agreement:</span>{' '}
          {regime.agreement.status} — {regime.agreement.detail}
        </p>
        <p className="text-xs text-muted-foreground">{regime.applicability.note}</p>
      </CardContent>
    </Card>
  )
}

function ConditionSummaryBar({ report }: { report: AnalysisReport }) {
  const summary = report.evaluation.conditions_summary
  if (!summary) return null
  const entries: [ConditionStatus, number][] = [
    ['met', summary.met ?? 0],
    ['not_met', summary.not_met ?? 0],
    ['inconclusive', summary.inconclusive ?? 0],
    ['unsupported', summary.unsupported ?? 0],
  ]
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="condition-summary">
      {entries.map(([status, count]) => (
        <Badge key={status} variant="outline" className={cn('text-xs', statusBadgeClass(status))}>
          {count} {status}
        </Badge>
      ))}
    </div>
  )
}

export function ReportView({ report }: { report: AnalysisReport }) {
  const statusMeta = SETUP_STATUS_META[report.evaluation.setup_status]
  return (
    <div className="space-y-4" data-testid="analysis-report">
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle className="text-base">
              {report.symbol} · {report.strategy.name} (v{report.strategy.version},{' '}
              {report.strategy.version_status})
            </CardTitle>
            <Badge
              variant="outline"
              className={cn(
                'text-xs',
                statusMeta.tone === 'success' && 'border-emerald-600/50 text-emerald-700 dark:text-emerald-400',
                statusMeta.tone === 'warning' && 'border-amber-600/50 text-amber-700 dark:text-amber-400',
                statusMeta.tone === 'danger' && 'border-red-600/50 text-red-700 dark:text-red-400'
              )}
            >
              {statusMeta.label}
            </Badge>
          </div>
          <CardDescription>
            {report.mode === 'forex' ? 'Forex' : 'Binary Options'} · timeframes{' '}
            {report.required_timeframes.join(', ')} · evaluated {report.evaluated_at} · engine{' '}
            {report.engine_version} (report schema {report.report_schema_version})
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <ConditionSummaryBar report={report} />
          <ul className="list-inside list-disc space-y-1 text-sm text-muted-foreground">
            {report.evaluation.summary.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </CardContent>
      </Card>

      <DataPanel report={report} />
      <RegimePanel report={report} />
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Conditions</CardTitle>
          <CardDescription>
            met / not met / inconclusive / unsupported — unknown conditions are never shown as success.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <StatusTree node={report.evaluation.core} />
          {report.evaluation.confirmation && (
            <div>
              <p className="mb-1 px-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Confirmation rules
              </p>
              <StatusTree node={report.evaluation.confirmation} depth={1} />
            </div>
          )}
          {report.evaluation.invalidation && (
            <div>
              <p className="mb-1 px-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Invalidation rules
              </p>
              <StatusTree node={report.evaluation.invalidation} depth={1} />
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Limitations</CardTitle>
          <CardDescription>What this report does and does not claim.</CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="list-inside list-disc space-y-1 text-xs text-muted-foreground">
            {report.limitations.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </CardContent>
      </Card>

      <AIExplanationPanel
        analysisId={report.analysis_id}
        reportLabel={`${report.symbol} · ${report.strategy.name} (v${report.strategy.version}) · evaluated ${report.evaluated_at}`}
      />
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Engine panel: run + history                                                */
/* -------------------------------------------------------------------------- */

export function EngineAnalysis({ mode }: { mode: PlatformMode }) {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [symbol, setSymbol] = useState('EURUSD')
  const [includeForming, setIncludeForming] = useState(false)
  const [running, setRunning] = useState(false)
  const [report, setReport] = useState<AnalysisReport | null>(null)
  const [history, setHistory] = useState<PaginatedAnalysisReports | null>(null)
  const [historyLoading, setHistoryLoading] = useState(false)

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true)
    try {
      const page = await api.get<PaginatedAnalysisReports>('/api/v1/analysis/reports', {
        page_size: '20',
      })
      setHistory(page)
    } catch {
      setHistory(null)
    } finally {
      setHistoryLoading(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    api
      .get<PaginatedStrategies>('/api/v1/strategies', { mode, page_size: '100' })
      .then((page) => {
        if (cancelled) return
        const usable = (page.items ?? []).filter((s) => s.current_version > 0)
        setStrategies(usable)
        setSelectedId((prev) =>
          usable.some((s) => s.id === prev) ? prev : usable[0]?.id ?? ''
        )
      })
      .catch(() => setStrategies([]))
    void loadHistory()
    return () => {
      cancelled = true
    }
  }, [mode, loadHistory])

  async function runAnalysis() {
    if (!selectedId) {
      toast({
        title: 'No strategy selected',
        description: 'Create a strategy with a saved rule set first.',
        variant: 'destructive',
      })
      return
    }
    setRunning(true)
    setReport(null)
    try {
      const result = await api.post<AnalysisReport>('/api/v1/analysis/run', {
        strategy_id: selectedId,
        symbol: symbol.trim().toUpperCase(),
        include_forming: includeForming,
      })
      setReport(result)
      toast({ title: SETUP_STATUS_META[result.evaluation.setup_status].label })
      void loadHistory()
    } catch (e) {
      toast({ title: 'Analysis failed', description: errorText(e), variant: 'destructive' })
    } finally {
      setRunning(false)
    }
  }

  async function openReport(id: string) {
    try {
      const stored = await api.get<AnalysisReport>(`/api/v1/analysis/reports/${id}`)
      setReport(stored)
    } catch (e) {
      toast({ title: 'Could not load report', description: errorText(e), variant: 'destructive' })
    }
  }

  return (
    <div className="grid gap-4 [&>*]:min-w-0 lg:grid-cols-2">
      {/* Run controls ---------------------------------------------------- */}
      <div className="space-y-4">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Engine run (live market data)</CardTitle>
            <CardDescription>
              Fetches real candles from the connected MetaTrader 5 terminal for every
              timeframe the strategy requires. Without a connected terminal the engine
              refuses to run — it never fabricates data.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Strategy</Label>
                <Select value={selectedId} onValueChange={setSelectedId}>
                  <SelectTrigger aria-label="Strategy">
                    <SelectValue
                      placeholder={strategies.length ? undefined : 'No strategies with rule sets yet'}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {strategies.map((s) => (
                      <SelectItem key={s.id} value={s.id}>
                        {s.name} (v{s.current_version})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="engine-symbol">Symbol</Label>
                <Input
                  id="engine-symbol"
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                  placeholder="EURUSD"
                  autoCapitalize="characters"
                  spellCheck={false}
                />
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <Checkbox
                id="include-forming"
                checked={includeForming}
                onCheckedChange={(v) => setIncludeForming(v === true)}
              />
              Include forming candle (results are labelled provisional)
            </label>
            <Button onClick={() => void runAnalysis()} disabled={running}>
              {running ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <Play className="h-4 w-4" aria-hidden />
              )}
              Run analysis
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <History className="h-4 w-4" aria-hidden /> Analysis history
            </CardTitle>
            <CardDescription>
              Persisted, immutable reports stored with the exact strategy version used.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {historyLoading && (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Loading history…
              </p>
            )}
            {!historyLoading && (!history || history.items.length === 0) && (
              <p className="text-sm text-muted-foreground">No stored analyses yet.</p>
            )}
            {!historyLoading && history && history.items.length > 0 && (
              <ul className="divide-y text-sm" data-testid="history-list">
                {history.items.map((item) => (
                  <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                    <span className="min-w-0">
                      <span className="font-mono text-xs font-semibold">{item.symbol}</span>{' '}
                      <span className="text-muted-foreground">
                        {item.strategy_name} (v{item.strategy_version})
                      </span>{' '}
                      <Badge
                        variant="outline"
                        className={cn(
                          'ml-1 text-[10px]',
                          item.setup_status === 'conditions_met' &&
                            'border-emerald-600/50 text-emerald-700 dark:text-emerald-400',
                          item.setup_status === 'invalidated' && 'border-red-600/50 text-red-700 dark:text-red-400',
                          (item.setup_status === 'inconclusive' || item.setup_status === 'insufficient_data') &&
                            'border-amber-600/50 text-amber-700 dark:text-amber-400'
                        )}
                      >
                        {SETUP_STATUS_META[item.setup_status]?.label ?? item.setup_status}
                      </Badge>
                      {item.regime && (
                        <span className="ml-1 text-xs text-muted-foreground">· {item.regime}</span>
                      )}
                      <span className="block text-xs text-muted-foreground">
                        <Clock className="mr-1 inline h-3 w-3" aria-hidden />
                        {item.evaluated_at}
                      </span>
                    </span>
                    <Button size="sm" variant="outline" onClick={() => void openReport(item.id)}>
                      <Activity className="mr-1 h-3.5 w-3.5" aria-hidden /> View report
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Report ------------------------------------------------------------ */}
      <div>
        {!report && (
          <Card className="flex min-h-64 items-center justify-center">
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              <HelpCircle className="mx-auto mb-2 h-6 w-6" aria-hidden />
              Run an analysis to see the explainable report: market regime, data quality,
              condition-by-condition results and limitations.
            </CardContent>
          </Card>
        )}
        {report && <ReportView report={report} />}
      </div>
    </div>
  )
}
