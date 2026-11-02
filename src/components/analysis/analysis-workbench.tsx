'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleSlash2,
  FlaskConical,
  HelpCircle,
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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { EngineAnalysis } from '@/components/analysis/engine-analysis'
import { api } from '@/lib/api/client'
import {
  ApiError,
  type CandleRow,
  type EvaluationNode,
  type EvaluationResponse,
  type PaginatedStrategies,
  type PlatformMode,
  type Strategy,
  type StrategyDetail,
} from '@/lib/api/types'
import {
  SETUP_STATUS_META,
  formatUnixTime,
  timeframesForMode,
} from '@/lib/platform/modes'
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

const SAMPLE_NOTE =
  'Synthetic sample for testing the evaluator — NOT real market data.'

function sampleCandles(timeframe: string): CandleRow[] {
  // Clearly-labelled synthetic series (labelled again in the UI banner).
  const step = timeframe === 'M1' ? 60 : timeframe === 'M5' ? 300 : 900
  const start = Math.floor(Date.now() / 1000 / step) * step - 19 * step
  const out: CandleRow[] = []
  let price = 100
  for (let i = 0; i < 20; i++) {
    const drift = i < 10 ? -0.15 : 0.35
    const open = price
    const close = +(price + drift).toFixed(2)
    out.push({
      time: start + i * step,
      open,
      high: +Math.max(open, close) + 0.2,
      low: +Math.min(open, close) - 0.2,
      close,
      volume: null,
    })
    price = close
  }
  return out
}

function NodeIcon({ satisfied }: { satisfied: boolean }) {
  return satisfied ? (
    <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-label="satisfied" />
  ) : (
    <XCircle className="h-4 w-4 shrink-0 text-red-500" aria-label="not satisfied" />
  )
}

function EvidenceGrid({ node }: { node: EvaluationNode }) {
  const ev = node.evidence
  const rows: [string, string][] = []
  if (ev.timeframe) rows.push(['Timeframe', ev.timeframe])
  if (ev.candles_evaluated !== undefined)
    rows.push(['Candles evaluated', `${ev.candles_evaluated} (available: ${ev.candles_available ?? '—'})`])
  if (ev.current_value !== undefined) rows.push(['Current value', String(ev.current_value)])
  if (ev.previous_value !== undefined) rows.push(['Previous value', String(ev.previous_value)])
  if (ev.threshold !== undefined) rows.push(['Threshold', String(ev.threshold)])
  if (ev.matched_count !== undefined)
    rows.push(['Matched candles in window', `${ev.matched_count}/${ev.candles_evaluated ?? '?'}`])
  if (ev.criterion) rows.push(['Criterion', ev.criterion])
  if (ev.last_satisfied_time) rows.push(['Last satisfied', formatUnixTime(ev.last_satisfied_time)])
  if (ev.total_crosses_in_window !== undefined)
    rows.push(['Crosses in window', String(ev.total_crosses_in_window)])
  if (ev.last_cross_time) rows.push(['Last cross', formatUnixTime(ev.last_cross_time)])
  if (ev.reason) rows.push(['Reason', ev.reason])
  if (ev.missing_timeframes)
    rows.push(['Missing timeframes', ev.missing_timeframes.join(', ')])

  if (rows.length === 0) return null
  return (
    <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 rounded-md bg-muted/50 px-3 py-2 text-xs">
      {rows.map(([k, v]) => (
        <div key={k} className="col-span-2 grid grid-cols-subgrid items-baseline">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="break-words font-medium">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

export function EvaluationTree({ node, depth = 0 }: { node: EvaluationNode; depth?: number }) {
  const [open, setOpen] = useState(depth < 1)
  const hasChildren = node.children && node.children.length > 0
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
        <NodeIcon satisfied={node.satisfied} />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            {node.kind === 'group' ? (
              <Badge variant="secondary" className="font-mono text-[10px]">{node.logic}</Badge>
            ) : (
              <span className="font-mono text-[10px] text-muted-foreground">{node.id}</span>
            )}
            <span className="text-sm">{node.description}</span>
          </span>
          <EvidenceGrid node={node} />
        </span>
      </button>
      {open && hasChildren && (
        <div role="group" aria-label={`${node.logic} group children`}>
          {node.children!.map((child, i) => (
            <EvaluationTree key={`${child.id}-${i}`} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  )
}

interface AnalysisWorkbenchProps {
  mode: PlatformMode
}

/**
 * Explainable evaluation on user-supplied candles (Phase 1). The UI is
 * explicit about data provenance: nothing here is live market data.
 */
function InlineEvaluation({ mode }: AnalysisWorkbenchProps) {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [source, setSource] = useState<'stored' | 'inline'>('stored')
  const [selectedId, setSelectedId] = useState('')
  const [selectedVersion, setSelectedVersion] = useState<'current' | string>('current')
  const [ruleText, setRuleText] = useState('')
  const [candleInputs, setCandleInputs] = useState<Record<string, string>>({})
  const [candleTimeframes, setCandleTimeframes] = useState<string[]>(['M1'])
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<EvaluationResponse | null>(null)

  const defaultTimeframes = useMemo(() => timeframesForMode(mode), [mode])

  useEffect(() => {
    let cancelled = false
    api
      .get<PaginatedStrategies>('/api/v1/strategies', { mode, page_size: '100' })
      .then((page) => {
        if (cancelled) return
        const usable = (page.items ?? []).filter((s) => s.current_version > 0)
        setStrategies(usable)
        if (usable.length > 0 && !usable.some((s) => s.id === selectedId)) {
          setSelectedId(usable[0].id)
        }
      })
      .catch(() => setStrategies([]))
    return () => {
      cancelled = true
    }
  }, [mode])

  useEffect(() => {
    setCandleTimeframes(defaultTimeframes.slice(0, 2))
    setCandleInputs({})
    setResult(null)
  }, [defaultTimeframes])

  const addCandleField = useCallback(
    (tf: string) => {
      setCandleTimeframes((prev) => (prev.includes(tf) ? prev : [...prev, tf]))
    },
    []
  )

  const loadStoredRule = useCallback(async (id: string, version: 'current' | string) => {
    try {
      const detail = await api.get<StrategyDetail>(`/api/v1/strategies/${id}`)
      const ruleSet =
        version === 'current' || !detail.rule_set
          ? detail.rule_set
          : (await api.get<{ rule_set: StrategyDetail['rule_set'] }>(
              `/api/v1/strategies/${id}/versions/${version}`
            )).rule_set
      if (ruleSet) setRuleText(JSON.stringify(ruleSet, null, 2))
      else setRuleText('')
    } catch (e) {
      toast({ title: 'Could not load rule set', description: errorText(e), variant: 'destructive' })
    }
  }, [])

  async function runEvaluation() {
    setRunning(true)
    setResult(null)
    try {
      const candles: Record<string, CandleRow[]> = {}
      for (const [tf, text] of Object.entries(candleInputs)) {
        if (!text.trim()) continue
        candles[tf] = JSON.parse(text)
      }
      let body: Record<string, unknown>
      if (source === 'stored') {
        body = {
          strategy_id: selectedId,
          ...(selectedVersion !== 'current' ? { version: Number(selectedVersion) } : {}),
          candles,
        }
      } else {
        body = { rule_set: JSON.parse(ruleText), candles }
      }
      const evaluation = await api.post<EvaluationResponse>('/api/v1/analysis/evaluate', body)
      setResult(evaluation)
      const status = evaluation.evaluation.setup_status
      toast({
        title: SETUP_STATUS_META[status].label,
        description:
          evaluation.evaluation.warnings.length > 0
            ? `${evaluation.evaluation.warnings.length} data-quality warning(s)`
            : 'No data-quality warnings',
      })
    } catch (e) {
      if (e instanceof SyntaxError) {
        toast({ title: 'JSON syntax error', description: e.message, variant: 'destructive' })
      } else {
        toast({ title: 'Evaluation failed', description: errorText(e), variant: 'destructive' })
      }
    } finally {
      setRunning(false)
    }
  }

  const statusMeta = result ? SETUP_STATUS_META[result.evaluation.setup_status] : null

  return (
    <div className="grid gap-4 [&>*]:min-w-0 lg:grid-cols-2">
      {/* Inputs ----------------------------------------------------------- */}
      <div className="space-y-4">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">1 · Strategy source</CardTitle>
            <CardDescription>
              Stored (versioned) or inline rule set — validated against the engine whitelist.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex gap-2">
              <Button
                size="sm"
                variant={source === 'stored' ? 'default' : 'outline'}
                onClick={() => setSource('stored')}
                disabled={strategies.length === 0}
              >
                Stored strategy
              </Button>
              <Button size="sm" variant={source === 'inline' ? 'default' : 'outline'} onClick={() => setSource('inline')}>
                Inline JSON
              </Button>
            </div>

            {source === 'stored' && (
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>Strategy</Label>
                  <Select
                    value={selectedId}
                    onValueChange={(v) => {
                      setSelectedId(v)
                      setSelectedVersion('current')
                      void loadStoredRule(v, 'current')
                    }}
                  >
                    <SelectTrigger aria-label="Strategy"><SelectValue placeholder={strategies.length ? undefined : 'No strategies with rule sets yet'} /></SelectTrigger>
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
                  <Label>Version</Label>
                  <Select
                    value={selectedVersion}
                    onValueChange={(v) => {
                      setSelectedVersion(v)
                      if (selectedId) void loadStoredRule(selectedId, v)
                    }}
                  >
                    <SelectTrigger aria-label="Version"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="current">Current</SelectItem>
                      {(() => {
                        const current = strategies.find((s) => s.id === selectedId)?.current_version ?? 0
                        return Array.from({ length: current }, (_, i) => (
                          <SelectItem key={i + 1} value={String(i + 1)}>
                            v{i + 1}
                          </SelectItem>
                        ))
                      })()}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            )}

            {source === 'inline' && (
              <div className="space-y-1.5">
                <Label htmlFor="inline-rule">Rule set JSON</Label>
                <Textarea
                  id="inline-rule"
                  value={ruleText}
                  onChange={(e) => setRuleText(e.target.value)}
                  spellCheck={false}
                  className="min-h-40 w-full min-w-0 font-mono text-xs"
                  placeholder='{"schema_version":"1.0","mode":"binary_options","timeframes":["M1"],"direction":"bullish","conditions":{"logic":"AND","conditions":[{"id":"c1","type":"price_level","field":"close","operator":"above","value":1.1,"timeframe":"M1"}],"groups":[]}}'
                />
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">2 · Candle data (user-supplied)</CardTitle>
            <CardDescription>
              Paste OHLCV arrays per timeframe (unix seconds). The engine never invents candles.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-1.5">
              {defaultTimeframes.map((tf) => (
                <Button
                  key={tf}
                  size="sm"
                  variant={candleTimeframes.includes(tf) ? 'secondary' : 'outline'}
                  className="h-7 px-2 font-mono text-xs"
                  onClick={() => addCandleField(tf)}
                  aria-pressed={candleTimeframes.includes(tf)}
                >
                  {tf}
                </Button>
              ))}
            </div>
            {candleTimeframes.map((tf) => (
              <div key={tf} className="space-y-1">
                <div className="flex items-center justify-between">
                  <Label htmlFor={`candles-${tf}`} className="font-mono text-xs">{tf}</Label>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-xs text-muted-foreground"
                    onClick={() =>
                      setCandleInputs((prev) => ({
                        ...prev,
                        [tf]: JSON.stringify(sampleCandles(tf)),
                      }))
                    }
                  >
                    <FlaskConical className="mr-1 h-3 w-3" aria-hidden /> Insert sample
                  </Button>
                </div>
                <Textarea
                  id={`candles-${tf}`}
                  value={candleInputs[tf] ?? ''}
                  onChange={(e) =>
                    setCandleInputs((prev) => ({ ...prev, [tf]: e.target.value }))
                  }
                  spellCheck={false}
                  className="min-h-24 w-full min-w-0 font-mono text-[11px]"
                  placeholder={`[{"time":1700000000,"open":1.1,"high":1.102,"low":1.098,"close":1.101}]`}
                />
              </div>
            ))}
            <p className="text-xs text-muted-foreground">
              “Insert sample” loads a <strong>synthetic</strong> series for trying the
              evaluator. {SAMPLE_NOTE}
            </p>
            <Button onClick={() => void runEvaluation()} disabled={running} className="w-full sm:w-auto">
              {running ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}
              Run evaluation
            </Button>
          </CardContent>
        </Card>
      </div>

      {/* Results ----------------------------------------------------------- */}
      <div className="space-y-4">
        {!result && (
          <Card className="flex min-h-64 items-center justify-center">
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              <HelpCircle className="mx-auto mb-2 h-6 w-6" aria-hidden />
              Run an evaluation to see the explainable result tree:
              conditions met / missing, evidence and data-quality warnings.
            </CardContent>
          </Card>
        )}

        {result && (
          <>
            <Card>
              <CardHeader className="pb-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <CardTitle className="text-base">Result</CardTitle>
                  <Badge
                    variant="outline"
                    className={cn(
                      'text-xs',
                      statusMeta!.tone === 'success' && 'border-emerald-600/50 text-emerald-700 dark:text-emerald-400',
                      statusMeta!.tone === 'warning' && 'border-amber-600/50 text-amber-700 dark:text-amber-400',
                      statusMeta!.tone === 'danger' && 'border-red-600/50 text-red-700 dark:text-red-400'
                    )}
                  >
                    {statusMeta!.label}
                  </Badge>
                </div>
                <CardDescription>
                  {result.rule_set.direction === 'bullish' ? 'Bullish' : 'Bearish'} setup ·{' '}
                  {result.rule_set.mode === 'forex' ? 'Forex' : 'Binary Options'} · data:{' '}
                  {result.data_provenance === 'user_supplied_inline'
                    ? 'user-supplied (not live)'
                    : result.data_provenance}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <ul className="list-inside list-disc space-y-1 text-sm text-muted-foreground">
                  {result.evaluation.summary.map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ul>
                {result.evaluation.warnings.length > 0 && (
                  <div className="rounded-md border border-amber-600/40 bg-amber-500/5 p-3">
                    <p className="mb-1 flex items-center gap-1.5 text-xs font-medium text-amber-700 dark:text-amber-400">
                      <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                      Data-quality warnings ({result.evaluation.warnings.length})
                    </p>
                    <ul className="list-inside list-disc space-y-0.5 text-xs text-muted-foreground">
                      {result.evaluation.warnings.map((w, i) => (
                        <li key={i}>
                          <span className="font-mono text-[10px]">[{w.timeframe}]</span> {w.message}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">Conditions</CardTitle>
                <CardDescription>Expand a node to inspect its evidence.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <EvaluationTree node={result.evaluation.core} />
                {result.evaluation.confirmation && (
                  <div>
                    <p className="mb-1 px-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Confirmation rules
                    </p>
                    <EvaluationTree node={result.evaluation.confirmation} depth={1} />
                  </div>
                )}
                {result.evaluation.invalidation && (
                  <div>
                    <p className="mb-1 flex items-center gap-1.5 px-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      <CircleSlash2 className="h-3 w-3" aria-hidden /> Invalidation rules
                    </p>
                    <EvaluationTree node={result.evaluation.invalidation} depth={1} />
                  </div>
                )}
              </CardContent>
            </Card>

            <p className="text-xs text-muted-foreground">{result.note}</p>
          </>
        )}
      </div>
    </div>
  )
}

/**
 * Phase 4 analysis tab: the provider-backed engine run (live MT5 data,
 * persisted reports, history) next to the Phase 1 inline evaluation.
 */
export function AnalysisWorkbench({ mode }: AnalysisWorkbenchProps) {
  return (
    <Tabs defaultValue="engine" className="space-y-4">
      <TabsList>
        <TabsTrigger value="engine">Engine run (MT5)</TabsTrigger>
        <TabsTrigger value="inline">Inline evaluation</TabsTrigger>
      </TabsList>
      <TabsContent value="engine" className="mt-0">
        <EngineAnalysis mode={mode} />
      </TabsContent>
      <TabsContent value="inline" className="mt-0">
        <InlineEvaluation mode={mode} />
      </TabsContent>
    </Tabs>
  )
}
