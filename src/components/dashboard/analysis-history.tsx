'use client'

/**
 * Analysis history access — lists persisted, immutable engine reports with
 * symbol/strategy/mode filters and opens the full report on selection.
 * Pagination is bounded (page_size) and every fetch is cancelled on unmount.
 */

import { useCallback, useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, History, Loader2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  SETUP_STATUS_META,
} from '@/lib/platform/modes'
import { api } from '@/lib/api/client'
import type {
  PaginatedAnalysisReports,
  PlatformMode,
  SetupStatus,
} from '@/lib/api/types'
import { cn } from '@/lib/utils'

const STATUS_BADGE: Record<string, string> = {
  conditions_met: 'border-emerald-600/50 text-emerald-700 dark:text-emerald-400',
  conditions_missing: 'border-amber-600/50 text-amber-700 dark:text-amber-400',
  invalidated: 'border-red-600/50 text-red-700 dark:text-red-400',
  regime_mismatch: 'border-amber-600/50 text-amber-700 dark:text-amber-400',
  inconclusive: 'border-amber-600/50 text-amber-700 dark:text-amber-400',
  insufficient_data: 'border-slate-500/50 text-slate-600 dark:text-slate-400',
}

export interface AnalysisHistoryProps {
  mode: PlatformMode
  activeReportId?: string | null
  onOpen: (id: string) => void
  refreshKey?: number
}

export function AnalysisHistory({ mode, activeReportId, onOpen, refreshKey = 0 }: AnalysisHistoryProps) {
  const [data, setData] = useState<PaginatedAnalysisReports | null>(null)
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const [page, setPage] = useState(1)
  const [symbolFilter, setSymbolFilter] = useState('')

  const load = useCallback(
    async (targetPage: number, symbol: string) => {
      setLoading(true)
      setFailed(false)
      try {
        const result = await api.get<PaginatedAnalysisReports>('/api/v1/analysis/reports', {
          mode,
          page: String(targetPage),
          page_size: '10',
          ...(symbol ? { symbol } : {}),
        })
        setData(result)
      } catch {
        setFailed(true)
        setData(null)
      } finally {
        setLoading(false)
      }
    },
    [mode],
  )

  useEffect(() => {
    void load(page, symbolFilter.trim().toUpperCase())
  }, [load, page, symbolFilter, refreshKey])

  const items = data?.items ?? []

  return (
    <div className="space-y-3" data-testid="analysis-history">
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <Label htmlFor="history-symbol" className="text-xs text-muted-foreground">
            Filter by symbol
          </Label>
          <Input
            id="history-symbol"
            value={symbolFilter}
            onChange={(e) => {
              setPage(1)
              setSymbolFilter(e.target.value.toUpperCase())
            }}
            placeholder="EURUSD"
            className="h-8 w-32 font-mono"
          />
        </div>
        {loading && (
          <span className="flex items-center gap-1.5 pb-1.5 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> loading…
          </span>
        )}
      </div>

      {failed && (
        <p className="rounded-md bg-destructive/10 px-2.5 py-1.5 text-xs text-destructive" role="alert">
          History could not be loaded (backend unreachable or request failed).
        </p>
      )}
      {!failed && !loading && items.length === 0 && (
        <p className="rounded-md bg-muted/50 px-2.5 py-1.5 text-xs text-muted-foreground">
          No persisted analyses yet. Run the engine from the dashboard to create
          the first immutable report.
        </p>
      )}

      <ul className="divide-y text-sm">
        {items.map((item) => (
          <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
            <span className="min-w-0">
              <span className="font-mono text-xs font-semibold">{item.symbol}</span>{' '}
              <span className="text-muted-foreground">
                {item.strategy_name ?? 'inline'} (v{item.strategy_version})
              </span>{' '}
              <Badge
                variant="outline"
                className={cn('text-[10px]', STATUS_BADGE[item.setup_status])}
              >
                {SETUP_STATUS_META[item.setup_status as SetupStatus]?.label ?? item.setup_status}
              </Badge>
              {item.regime && (
                <span className="ml-1 text-xs text-muted-foreground">· {item.regime}</span>
              )}
              <span className="block text-xs text-muted-foreground">{item.evaluated_at}</span>
            </span>
            <Button
              size="sm"
              variant={item.id === activeReportId ? 'default' : 'outline'}
              onClick={() => onOpen(item.id)}
              aria-label={`Open analysis report for ${item.symbol} from ${item.evaluated_at}`}
            >
              <History className="mr-1 h-3.5 w-3.5" aria-hidden />
              View
            </Button>
          </li>
        ))}
      </ul>

      {data && data.page.pages > 1 && (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <Button
            size="sm"
            variant="outline"
            disabled={page <= 1 || loading}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            aria-label="Previous history page"
          >
            <ChevronLeft className="h-3.5 w-3.5" aria-hidden /> Prev
          </Button>
          <span>
            page {data.page.page} / {data.page.pages} ({data.page.total} reports)
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={page >= data.page.pages || loading}
            onClick={() => setPage((p) => Math.min(data.page.pages, p + 1))}
            aria-label="Next history page"
          >
            Next <ChevronRight className="h-3.5 w-3.5" aria-hidden />
          </Button>
        </div>
      )}
    </div>
  )
}
