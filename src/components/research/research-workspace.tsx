'use client'

/**
 * Research workspace (Phase 8) — configuration, run history and the run
 * detail views (candidates / walk-forward / robustness).
 *
 * The workspace mirrors the Phase 6 backtest workspace conventions:
 * owner-scoped history, honest status badges, polling refresh for
 * in-process runs, and warnings wherever results must not be over-read.
 */

import { useCallback, useEffect, useState } from 'react'
import { ChevronRight, History, Loader2, Plus } from 'lucide-react'
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
  type PaginatedResearchRuns,
  type PlatformMode,
  type ResearchRun,
  type ResearchRunListItem,
} from '@/lib/api/types'
import { ResearchConfigPanel } from './research-config-panel'
import { ResearchRunDetail } from './research-run-detail'
import {
  RESEARCH_STATUS_META,
  VALIDATION_MODE_META,
  errorText,
  fmtScore,
  fmtUtc,
} from './research-shared'
import { cn } from '@/lib/utils'

interface Props {
  mode: PlatformMode
}

export function ResearchWorkspace({ mode }: Props) {
  const [runs, setRuns] = useState<ResearchRunListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const [showConfig, setShowConfig] = useState(true)

  const loadRuns = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.get<PaginatedResearchRuns>('/api/v1/research/optimization', {
        mode,
        page_size: '25',
      })
      setRuns(data.items)
    } catch (e) {
      toast({ title: 'Could not load research history', description: errorText(e), variant: 'destructive' })
    } finally {
      setLoading(false)
    }
  }, [mode])

  useEffect(() => {
    setActiveRunId(null)
    setShowConfig(true)
    void loadRuns()
  }, [loadRuns])

  // Poll while any run is queued/running so progress stays live.
  useEffect(() => {
    if (!runs.some((r) => r.status === 'queued' || r.status === 'running')) return
    const timer = setInterval(() => void loadRuns(), 3000)
    return () => clearInterval(timer)
  }, [runs, loadRuns])

  const onRunCreated = (run: ResearchRun) => {
    setActiveRunId(run.id)
    setShowConfig(false)
    void loadRuns()
  }

  return (
    <div className="space-y-4">
      <Alert>
        <AlertDescription>
          Research runs test explicitly selected strategy parameters over stored candles:
          in-sample vs out-of-sample results, walk-forward validation and robustness scenarios.
          Nothing here places orders, publishes a strategy, or guarantees profitability.
        </AlertDescription>
      </Alert>

      {/* Run history */}
      <Card>
        <CardHeader className="pb-2">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <CardTitle className="flex items-center gap-2 text-base">
                <History className="h-4 w-4" aria-hidden /> Research history
              </CardTitle>
              <CardDescription>
                Previous optimization and validation runs for this mode.
              </CardDescription>
            </div>
            <Button
              size="sm"
              variant={showConfig ? 'secondary' : 'default'}
              onClick={() => {
                setShowConfig((v) => !v)
                if (showConfig) setActiveRunId(null)
              }}
            >
              <Plus className="h-4 w-4" aria-hidden /> New research run
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center justify-center py-8 text-muted-foreground" data-testid="research-history-loading">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading runs…
            </div>
          ) : runs.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground" data-testid="research-history-empty">
              No research runs yet for this mode. Configure your first run below.
            </p>
          ) : (
            <div className="overflow-x-auto" data-testid="research-history-table">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Strategy</TableHead>
                    <TableHead>Symbol / TF</TableHead>
                    <TableHead>Validation</TableHead>
                    <TableHead>Objective</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Candidates</TableHead>
                    <TableHead className="text-right">Best score</TableHead>
                    <TableHead>Window (UTC)</TableHead>
                    <TableHead aria-label="Open run" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runs.map((r) => (
                    <TableRow
                      key={r.id}
                      className={cn('cursor-pointer', activeRunId === r.id && 'bg-muted/50')}
                      onClick={() => {
                        setActiveRunId(r.id)
                        setShowConfig(false)
                      }}
                    >
                      <TableCell className="text-xs">
                        <span className="font-medium">{r.strategy_name ?? '—'}</span>{' '}
                        <span className="text-muted-foreground">v{r.strategy_version}</span>
                      </TableCell>
                      <TableCell className="text-xs">
                        {r.symbol} · {r.timeframe}
                      </TableCell>
                      <TableCell className="text-xs">
                        {VALIDATION_MODE_META[r.validation_mode].label}
                      </TableCell>
                      <TableCell className="text-xs">{r.objective_metric ?? '—'}</TableCell>
                      <TableCell>
                        <Badge className={RESEARCH_STATUS_META[r.status].className}>
                          {RESEARCH_STATUS_META[r.status].label}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-xs">
                        {r.candidates_evaluated ?? 0}/{r.candidates_total ?? 0}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-xs">
                        {r.best_candidate
                          ? fmtScore(r.objective_metric ?? '', r.best_candidate.score)
                          : '—'}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-xs">
                        {fmtUtc(r.start_time)} → {fmtUtc(r.end_time)}
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

      {/* Configuration (hidden while inspecting a run) */}
      {showConfig ? (
        <ResearchConfigPanel mode={mode} onRunCreated={onRunCreated} />
      ) : activeRunId ? null : (
        <Alert>
          <AlertDescription>Create a new research run or pick one from the history above.</AlertDescription>
        </Alert>
      )}

      {/* Active run detail */}
      {activeRunId ? (
        <ResearchRunDetail runId={activeRunId} onBack={() => setActiveRunId(null)} />
      ) : null}
    </div>
  )
}
