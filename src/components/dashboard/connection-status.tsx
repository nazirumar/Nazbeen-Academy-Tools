'use client'

/**
 * Honest connection & freshness indicator.
 *
 * Visual states are distinct and never overstate reality:
 * - "Live" only while the LAST fetch succeeded and the backend marked the
 *   data fresh;
 * - "Stale" when the backend itself flagged staleness (its data-quality
 *   gate, not a frontend guess);
 * - "Not connected" when the last request failed — the chart keeps showing
 *   the last REAL candles but the label stops claiming liveness;
 * - "No data" when the terminal answered but has no candles.
 */

import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, CircleDashed, Clock, HelpCircle, WifiOff } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { formatUnixTime } from '@/lib/platform/modes'
import type { FeedStatus } from '@/hooks/use-candle-feed'
import type { ApiError, CandleSeries } from '@/lib/api/types'

const STATUS_META: Record<
  FeedStatus,
  { label: string; className: string; icon: React.ReactNode }
> = {
  idle: {
    label: 'Idle',
    className: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
    icon: <CircleDashed className="h-3.5 w-3.5" aria-hidden />,
  },
  loading: {
    label: 'Loading',
    className: 'bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300',
    icon: <CircleDashed className="h-3.5 w-3.5 animate-spin" aria-hidden />,
  },
  live: {
    label: 'Live data',
    className: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/50 dark:text-emerald-300',
    icon: <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />,
  },
  stale: {
    label: 'Stale data',
    className: 'bg-orange-100 text-orange-700 dark:bg-orange-900/50 dark:text-orange-300',
    icon: <AlertTriangle className="h-3.5 w-3.5" aria-hidden />,
  },
  no_data: {
    label: 'No candles',
    className: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
    icon: <HelpCircle className="h-3.5 w-3.5" aria-hidden />,
  },
  disconnected: {
    label: 'Not connected',
    className: 'bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-300',
    icon: <WifiOff className="h-3.5 w-3.5" aria-hidden />,
  },
  error: {
    label: 'Request failed',
    className: 'bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-300',
    icon: <AlertTriangle className="h-3.5 w-3.5" aria-hidden />,
  },
}

function formatIso(value: string): string {
  return value.replace('T', ' ').slice(0, 19) + ' UTC'
}

function secondsSince(iso: string | null, now: number): number | null {
  if (!iso) return null
  const then = Date.parse(iso)
  if (Number.isNaN(then)) return null
  return Math.max(0, Math.round((now - then) / 1000))
}

export interface ConnectionStatusProps {
  status: FeedStatus
  lastSuccessAt: string | null
  dataStatus: CandleSeries['data_status'] | null
  provenance: CandleSeries['data_provenance'] | null
  newestClosedAgeSeconds: number | null
  error: ApiError | null
  lastClosedCandleTime?: number | null
}

export function ConnectionStatus({
  status,
  lastSuccessAt,
  dataStatus,
  provenance,
  newestClosedAgeSeconds,
  error,
  lastClosedCandleTime,
}: ConnectionStatusProps) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    // "seconds since last update" ticker — stops when the component unmounts.
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])

  const meta = STATUS_META[status]
  const since = secondsSince(lastSuccessAt, now)

  return (
    <div
      className="flex flex-wrap items-center gap-2 text-xs"
      data-testid="connection-status"
      aria-live="polite"
    >
      <span
        className={cn(
          'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-medium',
          meta.className,
        )}
      >
        {meta.icon}
        {meta.label}
      </span>
      {status !== 'live' && since !== null && (
        <span className="inline-flex items-center gap-1 text-muted-foreground">
          <Clock className="h-3 w-3" aria-hidden />
          last success {since}s ago
        </span>
      )}
      {dataStatus && (
        <Badge variant="outline" className="font-mono text-[10px]">
          data: {dataStatus}
        </Badge>
      )}
      {provenance && (
        <Badge variant="secondary" className="font-mono text-[10px]">
          {provenance === 'mt5_live' ? 'MT5 live' : 'PostgreSQL cache'}
        </Badge>
      )}
      {newestClosedAgeSeconds != null && status === 'live' && (
        <span className="text-muted-foreground">
          newest closed candle {Math.round(newestClosedAgeSeconds)}s old
        </span>
      )}
      {lastClosedCandleTime != null && (
        <span className="text-muted-foreground">
          last closed {formatUnixTime(lastClosedCandleTime)}
        </span>
      )}
      {lastSuccessAt && status === 'live' && (
        <span className="text-muted-foreground">updated {formatIso(lastSuccessAt)}</span>
      )}
      {error && (
        <span className="text-destructive" title={error.message}>
          {error.code}
        </span>
      )}
    </div>
  )
}
