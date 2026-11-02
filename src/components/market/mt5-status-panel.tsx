'use client'

import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  RefreshCw,
  Terminal,
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
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api/client'
import { ApiError, type MT5Status } from '@/lib/api/types'
import { useMarketStore } from '@/lib/platform/market-store'

type StatusState =
  | { kind: 'loading' }
  | { kind: 'ok'; value: MT5Status }
  | { kind: 'error'; error: ApiError }

const POLL_INTERVAL_MS = 15_000

function ConnectionBadge({ status }: { status: MT5Status | null }) {
  if (!status) {
    return (
      <Badge variant="outline" className="gap-1">
        <RefreshCw className="h-3 w-3 animate-spin" aria-hidden /> Checking…
      </Badge>
    )
  }
  if (!status.enabled) {
    return (
      <Badge variant="outline" className="gap-1 text-muted-foreground">
        <CircleDashed className="h-3 w-3" aria-hidden /> Disabled
      </Badge>
    )
  }
  if (status.connected) {
    return (
      <Badge className="gap-1 bg-emerald-600 text-white hover:bg-emerald-600">
        <CheckCircle2 className="h-3 w-3" aria-hidden /> Connected
      </Badge>
    )
  }
  return (
    <Badge variant="destructive" className="gap-1">
      <WifiOff className="h-3 w-3" aria-hidden /> Disconnected
    </Badge>
  )
}

function formatIso(value: string | null | undefined): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 19) + ' UTC'
}

function ageOf(value: string | null | undefined): string {
  if (!value) return 'never'
  const then = Date.parse(value)
  if (Number.isNaN(then)) return 'unknown'
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000))
  if (seconds < 90) return `${seconds}s ago`
  if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`
  return `${Math.round(seconds / 3600)} h ago`
}

/**
 * Honest MT5 connection panel: connected/disconnected, terminal
 * availability, last successful retrieval, the market preview's selected
 * symbol/timeframe, data freshness and the latest error — exactly as the
 * backend reports it. Nothing here is inferred or decorated.
 */
export function MT5StatusPanel() {
  const [state, setState] = useState<StatusState>({ kind: 'loading' })
  const [busy, setBusy] = useState(false)
  const symbol = useMarketStore((s) => s.symbol)
  const timeframe = useMarketStore((s) => s.timeframe)
  const lastFetchedAt = useMarketStore((s) => s.lastFetchedAt)
  const lastDataStatus = useMarketStore((s) => s.lastDataStatus)

  const refresh = useCallback(async () => {
    setBusy(true)
    const next = await api
      .get<MT5Status>('/api/v1/mt5/status')
      .then((value) => ({ kind: 'ok', value }) as StatusState)
      .catch((error) => ({ kind: 'error', error }) as StatusState)
    setState(next)
    setBusy(false)
  }, [])

  useEffect(() => {
    const initial = setTimeout(() => void refresh(), 0)
    const timer = setInterval(() => void refresh(), POLL_INTERVAL_MS)
    return () => {
      clearTimeout(initial)
      clearInterval(timer)
    }
  }, [refresh])

  const status = state.kind === 'ok' ? state.value : null

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <Terminal className="h-4 w-4 text-muted-foreground" aria-hidden />
            MetaTrader 5 terminal
          </CardTitle>
          <ConnectionBadge status={status} />
        </div>
        <CardDescription>
          Live market data source · package{' '}
          {status?.package_version ?? 'not detected'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {state.kind === 'loading' && <Skeleton className="h-5 w-3/4" />}
        {state.kind === 'error' && (
          <p className="text-xs text-destructive">
            {state.error.message}
            {state.error.code === 'BACKEND_UNREACHABLE' && (
              <> — run <code className="rounded bg-muted px-1">make backend</code>.</>
            )}
          </p>
        )}
        {status && (
          <>
            <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">Terminal:</span>
                <span className="font-medium">
                  {status.terminal?.accessible
                    ? `${status.terminal.name ?? 'MT5'}${status.terminal.build ? ` (build ${status.terminal.build})` : ''}`
                    : 'not available'}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">Broker link:</span>
                <span
                  className={
                    status.terminal?.broker_connected
                      ? 'font-medium text-emerald-600 dark:text-emerald-400'
                      : 'font-medium text-amber-600 dark:text-amber-400'
                  }
                >
                  {status.terminal?.broker_connected ? 'online' : 'offline'}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">Selected:</span>
                <span className="font-mono text-[13px] font-medium">
                  {symbol} · {timeframe}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">Last retrieval:</span>
                <span className="font-medium">
                  {lastFetchedAt ? ageOf(lastFetchedAt) : 'no fetch yet'}
                  {lastDataStatus ? ` (${lastDataStatus})` : ''}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">Last terminal check:</span>
                <span className="font-medium">{formatIso(status.checked_at)}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">Last success:</span>
                <span className="font-medium">{ageOf(status.last_success_at)}</span>
              </div>
            </div>
            {status.last_error && (
              <p className="flex items-start gap-1.5 rounded-md bg-destructive/10 px-2 py-1.5 text-xs text-destructive">
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                <span>{status.last_error}</span>
              </p>
            )}
            {!status.last_error && status.detail && (
              <p className="text-xs text-muted-foreground">{status.detail}</p>
            )}
          </>
        )}
        <div className="pt-1 text-right">
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={busy}>
            <RefreshCw className={busy ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} aria-hidden />
            Check now
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
