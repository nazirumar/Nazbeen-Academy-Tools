'use client'

/**
 * Research run detail (Phase 8).
 *
 * Shows the run's configuration/progress, the candidate ranking (with
 * sample sizes and in/out-of-sample results side by side), the
 * chronological walk-forward windows (training vs testing made explicit)
 * and the robustness scenarios with their documented assumptions.
 *
 * "Save as draft" pushes a candidate's persisted rule set through the
 * EXISTING Strategy Builder draft-version endpoint — the original
 * strategy version is never modified and nothing is published
 * automatically.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  ArrowLeft,
  BookOpenCheck,
  Loader2,
  RefreshCw,
  Save,
  ShieldCheck,
  XCircle,
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api/client'
import {
  type PaginatedStrategies,
  type OOSAggregate,
  type PaginatedResearchCandidates,
  type PaginatedResearchWindows,
  type ResearchCandidateDetail,
  type ResearchCandidateSummary,
  type ResearchRun,
  type ResearchScenario,
  type ResearchScenariosPayload,
  type ResearchWindowSummary,
  type Strategy,
} from '@/lib/api/types'
import {
  RESEARCH_STATUS_META,
  errorText,
  fmtNum,
  fmtPct,
  fmtScore,
  fmtUtc,
} from './research-shared'

interface Props {
  runId: string
  onBack: () => void
}

export function ResearchRunDetail({ runId, onBack }: Props) {
  const [run, setRun] = useState<ResearchRun | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await api.get<ResearchRun>(`/api/v1/research/optimization/${runId}`)
      setRun(data)
      setLoadError(null)
    } catch (e) {
      setLoadError(errorText(e))
    } finally {
      setLoading(false)
    }
  }, [runId])

  useEffect(() => {
    setLoading(true)
    void load()
    const active = run?.status === 'queued' || run?.status === 'running'
    if (!active) return
    const timer = setInterval(() => void load(), 2000)
    return () => clearInterval(timer)
  }, [load, run?.status])

  const cancel = async () => {
    setCancelling(true)
    try {
      await api.post(`/api/v1/research/optimization/${runId}/cancel`)
      toast({ title: 'Cancellation requested', description: 'The run stops at the next engine step.' })
      await load()
    } catch (e) {
      toast({ title: 'Could not cancel the run', description: errorText(e), variant: 'destructive' })
    } finally {
      setCancelling(false)
    }
  }

  if (loading && !run) {
    return (
      <div className="flex items-center justify-center py-10 text-muted-foreground" data-testid="research-detail-loading">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading run…
      </div>
    )
  }
  if (loadError || !run) {
    return (
      <Alert variant="destructive">
        <AlertDescription className="flex items-center justify-between gap-2">
          <span>{loadError ?? 'Run not found.'}</span>
          <Button size="sm" variant="outline" onClick={onBack}>
            <ArrowLeft className="h-4 w-4" aria-hidden /> Back
          </Button>
        </AlertDescription>
      </Alert>
    )
  }

  const statusMeta = RESEARCH_STATUS_META[run.status]
  const progress = run.progress
  const objectiveMetric = run.objective?.metric ?? ''

  return (
    <div className="space-y-4" data-testid="research-run-detail">
      <Card>
        <CardHeader className="pb-2">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                <Button variant="ghost" size="icon" aria-label="Back to run list" onClick={onBack}>
                  <ArrowLeft className="h-4 w-4" aria-hidden />
                </Button>
                {run.strategy_name} v{run.strategy_version}
                <Badge className={statusMeta.className}>{statusMeta.label}</Badge>
                <Badge variant="outline">{run.validation_mode.replace('_', ' ')}</Badge>
                <Badge variant="secondary">{run.mode === 'forex' ? 'Forex' : 'Binary Options'}</Badge>
              </CardTitle>
              <CardDescription>
                {run.symbol} · {run.timeframe} · {fmtUtc(run.start_time)} → {fmtUtc(run.end_time)} ·
                objective: {objectiveMetric || '—'} · {run.combination_count ?? '—'} combinations ·
                walks {run.walks_executed}/{run.walk_budget ?? '—'}
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <Button size="sm" variant="outline" onClick={() => void load()} aria-label="Refresh run">
                <RefreshCw className="h-4 w-4" aria-hidden /> Refresh
              </Button>
              {run.status === 'queued' || run.status === 'running' ? (
                <Button size="sm" variant="destructive" onClick={cancel} disabled={cancelling}>
                  {cancelling ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <XCircle className="h-4 w-4" aria-hidden />}
                  Cancel
                </Button>
              ) : null}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {progress ? (
            <div className="flex flex-wrap gap-2 text-xs" data-testid="research-progress">
              <Badge variant="outline">phase: {progress.phase}</Badge>
              <Badge variant="secondary">
                candidates {progress.candidates_evaluated}/{progress.candidates_total}
              </Badge>
              {progress.candidates_failed > 0 ? (
                <Badge variant="destructive">{progress.candidates_failed} failed</Badge>
              ) : null}
              {run.validation_mode === 'walk_forward' ? (
                <Badge variant="secondary">
                  windows {progress.windows_completed}/{progress.windows_total}
                  {progress.windows_skipped > 0 ? ` (${progress.windows_skipped} skipped)` : ''}
                </Badge>
              ) : null}
              {progress.scenarios_total > 0 ? (
                <Badge variant="secondary">
                  scenarios {progress.scenarios_completed}/{progress.scenarios_total}
                </Badge>
              ) : null}
            </div>
          ) : null}
          {run.error ? (
            <Alert variant="destructive">
              <AlertDescription>
                {run.error.code ? `${run.error.code}: ` : ''}
                {run.error.message}
              </AlertDescription>
            </Alert>
          ) : null}
          {run.aggregate?.warnings.map((w, i) => (
            <Alert key={i}>
              <AlertDescription>
                <span className="font-medium">{w.code}:</span> {w.message}
              </AlertDescription>
            </Alert>
          ))}
        </CardContent>
      </Card>

      <Tabs defaultValue="candidates">
        <TabsList>
          <TabsTrigger value="candidates">Candidates</TabsTrigger>
          {run.validation_mode === 'walk_forward' ? (
            <TabsTrigger value="walkforward">Walk-forward</TabsTrigger>
          ) : null}
          <TabsTrigger value="robustness">Robustness</TabsTrigger>
          <TabsTrigger value="summary">Summary</TabsTrigger>
        </TabsList>

        <TabsContent value="candidates" className="mt-0">
          <CandidatesPanel
            runId={runId}
            objectiveMetric={objectiveMetric}
            validationMode={run.validation_mode}
            mode={run.mode}
          />
        </TabsContent>

        {run.validation_mode === 'walk_forward' ? (
          <TabsContent value="walkforward" className="mt-0">
            <WalkForwardPanel runId={runId} />
          </TabsContent>
        ) : null}

        <TabsContent value="robustness" className="mt-0">
          <RobustnessPanel runId={runId} />
        </TabsContent>

        <TabsContent value="summary" className="mt-0">
          <SummaryPanel run={run} />
        </TabsContent>
      </Tabs>
    </div>
  )
}

// --------------------------------------------------------------------------- //

function CandidatesPanel({
  runId,
  objectiveMetric,
  validationMode,
  mode,
}: {
  runId: string
  objectiveMetric: string
  validationMode: string
  mode: 'forex' | 'binary_options'
}) {
  const [candidates, setCandidates] = useState<ResearchCandidateSummary[]>([])
  const [page, setPage] = useState(1)
  const [pageMeta, setPageMeta] = useState<{ total: number; pages: number } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saveTarget, setSaveTarget] = useState<ResearchCandidateDetail | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.get<PaginatedResearchCandidates>(
        `/api/v1/research/optimization/${runId}/candidates`,
        { page: String(page), page_size: '25', sort: validationMode === 'walk_forward' ? 'index' : 'rank' },
      )
      setCandidates(data.items)
      setPageMeta({ total: data.page.total, pages: data.page.pages })
      setError(null)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setLoading(false)
    }
  }, [runId, page, validationMode])

  useEffect(() => {
    void load()
  }, [load])

  const openSave = async (index: number) => {
    try {
      const detail = await api.get<ResearchCandidateDetail>(
        `/api/v1/research/optimization/${runId}/candidates/${index}`,
      )
      setSaveTarget(detail)
    } catch (e) {
      toast({ title: 'Could not load the candidate', description: errorText(e), variant: 'destructive' })
    }
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">Candidate configurations</CardTitle>
        <CardDescription>
          {validationMode === 'walk_forward'
            ? 'Walk-forward run: selection happens per training window — no global ranking exists. times_selected counts how often each candidate won a training window.'
            : 'Ranked by the selected objective on the in-sample/selection window. Sample sizes are shown next to every score; undefined metrics are never ranked as zero.'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {loading ? (
          <div className="flex items-center justify-center py-6 text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading candidates…
          </div>
        ) : error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-14">Rank</TableHead>
                  <TableHead>Parameters</TableHead>
                  <TableHead className="text-right">Score</TableHead>
                  <TableHead className="text-right">Decided trades (IS)</TableHead>
                  <TableHead className="text-right">Net P&amp;L (IS)</TableHead>
                  <TableHead className="text-right">Win rate (IS)</TableHead>
                  {validationMode !== 'walk_forward' ? (
                    <>
                      <TableHead className="text-right">Net P&amp;L (OOS)</TableHead>
                      <TableHead className="text-right">Win rate (OOS)</TableHead>
                    </>
                  ) : (
                    <TableHead className="text-right">Times selected</TableHead>
                  )}
                  <TableHead>Status</TableHead>
                  <TableHead aria-label="Save as draft" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {candidates.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell className="tabular-nums">{c.rank ?? '—'}</TableCell>
                    <TableCell className="max-w-64">
                      <span className="text-xs font-medium">{c.params.label}</span>
                      {c.constraint_violations.length > 0 ? (
                        <span className="ml-1 text-xs text-amber-600 dark:text-amber-400">
                          ({c.constraint_violations.map((v) => v.code).join(', ')})
                        </span>
                      ) : null}
                      {c.error ? (
                        <span className="ml-1 text-xs text-red-500">({c.error.code})</span>
                      ) : null}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-xs">{fmtScore(objectiveMetric, c.score)}</TableCell>
                    <TableCell className="text-right tabular-nums text-xs">
                      {fmtNum(c.in_sample_trades?.decided_trades ?? null, 0)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-xs">{fmtNum(c.in_sample_trades?.net_pnl ?? null)}</TableCell>
                    <TableCell className="text-right tabular-nums text-xs">
                      {c.in_sample_trades?.win_rate == null ? 'N/A' : fmtPct(c.in_sample_trades.win_rate * 100)}
                    </TableCell>
                    {validationMode !== 'walk_forward' ? (
                      <>
                        <TableCell className="text-right tabular-nums text-xs">
                          {c.out_of_sample_trades ? fmtNum(c.out_of_sample_trades.net_pnl) : <span className="text-muted-foreground">not walked</span>}
                        </TableCell>
                        <TableCell className="text-right tabular-nums text-xs">
                          {c.out_of_sample_trades?.win_rate == null
                            ? 'N/A'
                            : fmtPct(c.out_of_sample_trades.win_rate * 100)}
                        </TableCell>
                      </>
                    ) : (
                      <TableCell className="text-right tabular-nums text-xs">{c.times_selected}</TableCell>
                    )}
                    <TableCell>
                      <Badge variant={c.status === 'evaluated' ? 'secondary' : 'destructive'}>{c.status}</Badge>
                    </TableCell>
                    <TableCell>
                      <Button size="sm" variant="outline" onClick={() => void openSave(c.candidate_index)}>
                        <Save className="h-3.5 w-3.5" aria-hidden /> Save as draft
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
        {pageMeta && pageMeta.pages > 1 ? (
          <div className="flex items-center justify-end gap-2 text-xs">
            <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              Previous
            </Button>
            <span className="text-muted-foreground">
              Page {page} of {pageMeta.pages} ({pageMeta.total} candidates)
            </span>
            <Button size="sm" variant="outline" disabled={page >= pageMeta.pages} onClick={() => setPage((p) => p + 1)}>
              Next
            </Button>
          </div>
        ) : null}
        {saveTarget ? (
          <SaveCandidateDialog
            candidate={saveTarget}
            mode={mode}
            onClose={() => setSaveTarget(null)}
          />
        ) : null}
      </CardContent>
    </Card>
  )
}

// --------------------------------------------------------------------------- //

function SaveCandidateDialog({
  candidate,
  mode,
  onClose,
}: {
  candidate: ResearchCandidateDetail
  mode: 'forex' | 'binary_options'
  onClose: () => void
}) {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [strategyId, setStrategyId] = useState('')
  const [changelog, setChangelog] = useState(
    `From research candidate #${candidate.candidate_index}`,
  )
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let active = true
    api
      .get<PaginatedStrategies>('/api/v1/strategies', { mode, status: 'active', page_size: '100' })
      .then((data) => {
        if (active) {
          setStrategies(data.items)
          if (data.items.length > 0) setStrategyId(data.items[0].id)
        }
      })
      .catch(() => {
        // The dialog stays usable with a manual ID fallback.
      })
    return () => {
      active = false
    }
  }, [mode])

  const save = async () => {
    if (!strategyId) return
    setSaving(true)
    try {
      await api.post(`/api/v1/strategies/${strategyId}/versions`, {
        rule_set: candidate.rule_set,
        changelog,
      })
      toast({
        title: 'Draft version saved',
        description:
          'The candidate was saved as a new DRAFT strategy version via the Strategy Builder. Publish it there explicitly when you are ready.',
      })
      onClose()
    } catch (e) {
      toast({ title: 'Could not save the draft version', description: errorText(e), variant: 'destructive' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(v) => (!v ? onClose() : null)}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <BookOpenCheck className="h-4 w-4" aria-hidden /> Save candidate as new draft version
          </DialogTitle>
          <DialogDescription>
            Candidate #{candidate.candidate_index} ({candidate.params.label}) is saved as a NEW
            draft version of an existing strategy. The research run and the original strategy
            version stay untouched; nothing is published automatically.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="save-strategy-id">Target strategy</Label>
            {strategies.length > 0 ? (
              <Select value={strategyId} onValueChange={setStrategyId}>
                <SelectTrigger id="save-strategy-id" aria-label="Target strategy">
                  <SelectValue placeholder="Select strategy" />
                </SelectTrigger>
                <SelectContent>
                  {strategies.map((s) => (
                    <SelectItem key={s.id} value={s.id}>
                      {s.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <Input
                id="save-strategy-id"
                value={strategyId}
                onChange={(e) => setStrategyId(e.target.value)}
                placeholder="Paste the strategy ID (from the Strategies tab)"
              />
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="save-changelog">Changelog</Label>
            <Textarea id="save-changelog" value={changelog} onChange={(e) => setChangelog(e.target.value)} rows={2} />
          </div>
          <Alert>
            <AlertDescription className="flex items-start gap-2 text-xs">
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              A best-scoring candidate is NOT a validated strategy. Out-of-sample evidence in this
              run is limited; review, validate and publish deliberately in the Strategies tab.
            </AlertDescription>
          </Alert>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={save} disabled={saving || !strategyId.trim()}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Save className="h-4 w-4" aria-hidden />}
            Save draft version
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// --------------------------------------------------------------------------- //

function WalkForwardPanel({ runId }: { runId: string }) {
  const [windows, setWindows] = useState<ResearchWindowSummary[]>([])
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    const loadPage = async () => {
      setLoading(true)
      try {
        const data = await api.get<PaginatedResearchWindows>(
          `/api/v1/research/optimization/${runId}/walk-forward`,
          { page: String(page), page_size: '50' },
        )
        if (!active) return
        setWindows(data.items)
        setPages(data.page.pages)
        setError(null)
      } catch (e) {
        if (active) setError(errorText(e))
      } finally {
        if (active) setLoading(false)
      }
    }
    void loadPage()
    return () => {
      active = false
    }
  }, [runId, page])

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">Walk-forward windows</CardTitle>
        <CardDescription>
          Chronological windows: parameters are selected on the TRAINING period only, then frozen
          and evaluated on the following TEST period. train_end always equals test_start — test
          windows never overlap, so out-of-sample observations are never double-counted.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {loading ? (
          <div className="flex items-center justify-center py-6 text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading windows…
          </div>
        ) : error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-14">#</TableHead>
                  <TableHead>Training period (selection)</TableHead>
                  <TableHead>Test period (held out)</TableHead>
                  <TableHead>Frozen parameters</TableHead>
                  <TableHead className="text-right">Selection score</TableHead>
                  <TableHead className="text-right">Test net P&amp;L</TableHead>
                  <TableHead className="text-right">Test win rate</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {windows.map((w) => (
                  <TableRow key={w.id}>
                    <TableCell className="tabular-nums">{w.window_index}</TableCell>
                    <TableCell className="whitespace-nowrap text-xs">{fmtUtc(w.train_start)} → {fmtUtc(w.train_end)}</TableCell>
                    <TableCell className="whitespace-nowrap text-xs">{fmtUtc(w.test_start)} → {fmtUtc(w.test_end)}</TableCell>
                    <TableCell className="max-w-48 truncate text-xs" title={w.selected_params?.label ?? w.note ?? ''}>
                      {w.selected_params?.label ?? w.note ?? '—'}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-xs">{fmtNum(w.selection_score)}</TableCell>
                    <TableCell className="text-right tabular-nums text-xs">{fmtNum(w.test_trades?.net_pnl ?? null)}</TableCell>
                    <TableCell className="text-right tabular-nums text-xs">
                      {w.test_trades?.win_rate == null ? 'N/A' : fmtPct(w.test_trades.win_rate * 100)}
                    </TableCell>
                    <TableCell>
                      <Badge variant={w.status === 'completed' ? 'secondary' : 'destructive'}>{w.status}</Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
        {pages > 1 ? (
          <div className="flex items-center justify-end gap-2 text-xs">
            <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              Previous
            </Button>
            <span className="text-muted-foreground">Page {page} of {pages}</span>
            <Button size="sm" variant="outline" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
              Next
            </Button>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}

// --------------------------------------------------------------------------- //

function RobustnessPanel({ runId }: { runId: string }) {
  const [scenarios, setScenarios] = useState<ResearchScenario[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    const loadScenarios = async () => {
      setLoading(true)
      try {
        const data = await api.get<ResearchScenariosPayload>(
          `/api/v1/research/optimization/${runId}/robustness`,
        )
        if (!active) return
        setScenarios(data.items)
        setError(null)
      } catch (e) {
        if (active) setError(errorText(e))
      } finally {
        if (active) setLoading(false)
      }
    }
    void loadScenarios()
    return () => {
      active = false
    }
  }, [runId])

  const CATEGORY_LABEL: Record<ResearchScenario['category'], string> = {
    cost_variant: 'Execution-cost scenario',
    param_neighbor: 'Parameter sensitivity',
    bootstrap: 'Seeded bootstrap stress test',
    selection_stability: 'Walk-forward selection stability',
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">Robustness scenarios</CardTitle>
        <CardDescription>
          Each scenario re-runs the SAME strategy and data with explicit, documented changes.
          Surviving scenarios do NOT prove robustness — they only show sensitivity to the changed
          assumptions.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {loading ? (
          <div className="flex items-center justify-center py-6 text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading scenarios…
          </div>
        ) : error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : scenarios.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No robustness scenarios were configured or completed for this run.
          </p>
        ) : (
          scenarios.map((s) => (
            <div key={s.id} className="rounded-lg border p-3 space-y-2" data-testid="research-scenario">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">{CATEGORY_LABEL[s.category]}</Badge>
                <span className="text-sm font-medium">{s.label}</span>
                <Badge variant={s.status === 'completed' ? 'secondary' : 'destructive'}>{s.status}</Badge>
              </div>
              {s.delta ? (
                <div className="flex flex-wrap gap-3 text-xs" data-testid="scenario-delta">
                  {['net_pnl', 'expectancy', 'win_rate', 'decided_trades'].map((key) => {
                    const entry = s.delta?.[key] as
                      | { baseline?: number | null; scenario?: number | null; change?: number | null; change_pp?: number | null }
                      | undefined
                    if (!entry) return null
                    return (
                      <span key={key} className="tabular-nums">
                        <span className="text-muted-foreground">{key.replace('_', ' ')}:</span>{' '}
                        {key === 'win_rate'
                          ? `${fmtPct((entry.baseline ?? 0) * 100)} → ${fmtPct((entry.scenario ?? 0) * 100)} (${entry.change_pp ?? 0} pp)`
                          : `${fmtNum(entry.baseline ?? null)} → ${fmtNum(entry.scenario ?? null)} (${entry.change != null && entry.change > 0 ? '+' : ''}${fmtNum(entry.change ?? null)})`}
                      </span>
                    )
                  })}
                </div>
              ) : s.metrics && typeof s.metrics === 'object' ? (
                <p className="text-xs text-muted-foreground">Scenario metrics recorded (see assumptions).</p>
              ) : null}
              {s.note ? <p className="text-xs text-muted-foreground">{s.note}</p> : null}
              {s.error ? (
                <p className="text-xs text-red-500">
                  {s.error.code}: {s.error.message}
                </p>
              ) : null}
            </div>
          ))
        )}
      </CardContent>
    </Card>
  )
}

// --------------------------------------------------------------------------- //

function SummaryPanel({ run }: { run: ResearchRun }) {
  const aggregate = run.aggregate
  const oos = aggregate?.out_of_sample as OOSAggregate | null | undefined
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">Run summary</CardTitle>
        <CardDescription>Reproducible configuration and honest aggregate results.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {aggregate?.best_candidate ? (
          <p className="text-sm">
            Best candidate (on selection data):{' '}
            <span className="font-medium">
              #{aggregate.best_candidate.candidate_index} {aggregate.best_candidate.label}
            </span>{' '}
            {aggregate.best_candidate.rank != null ? `(rank ${aggregate.best_candidate.rank})` : ''}
            {aggregate.best_candidate.times_selected > 0
              ? ` · selected in ${aggregate.best_candidate.times_selected} walk-forward window(s)`
              : ''}
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">No candidate could be ranked for this run.</p>
        )}
        {oos && oos.trades ? (
          <div className="flex flex-wrap gap-2 text-xs" data-testid="oos-summary">
            <Badge variant="secondary">OOS trades: {oos.trades.decided_trades}</Badge>
            <Badge variant="outline">OOS win rate: {oos.trades.win_rate == null ? 'N/A' : fmtPct(oos.trades.win_rate * 100)}</Badge>
            <Badge variant="outline">OOS net P&amp;L: {fmtNum(oos.trades.net_pnl)}</Badge>
            {oos.skipped_windows && oos.skipped_windows.length > 0 ? (
              <Badge variant="destructive">{oos.skipped_windows.length} window(s) skipped</Badge>
            ) : null}
          </div>
        ) : null}
        {aggregate?.stability_views ? (
          <div className="grid gap-2 text-xs sm:grid-cols-3">
            <div className="rounded-lg border p-2">
              <p className="font-medium">Time-period stability</p>
              <p className="text-muted-foreground">
                {String((aggregate.stability_views.time_periods as { note?: string }).note ?? '—')}
              </p>
            </div>
            <div className="rounded-lg border p-2">
              <p className="font-medium">Market-regime stability</p>
              <p className="text-muted-foreground">
                {String((aggregate.stability_views.market_regimes as { note?: string }).note ?? '—')}
              </p>
            </div>
            <div className="rounded-lg border p-2">
              <p className="font-medium">Data quality</p>
              <p className="text-muted-foreground">
                {(aggregate.stability_views.data_quality.warning_codes ?? []).join(', ') ||
                  'no warnings'}
              </p>
            </div>
          </div>
        ) : null}
        {aggregate?.disclaimers ? (
          <Alert>
            <AlertDescription>
              <ul className="list-disc space-y-1 pl-4 text-xs">
                {aggregate.disclaimers.map((d, i) => (
                  <li key={i}>{d}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  )
}
