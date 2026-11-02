'use client'

import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  RefreshCw,
  Server,
  ShieldAlert,
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
import {
  ApiError,
  type MarketDataStatusPayload,
  type PlatformMeta,
  type ProviderStatus,
  type ReadyPayload,
} from '@/lib/api/types'
import { MODE_LABELS } from '@/lib/platform/modes'

type FetchState<T> =
  | { kind: 'loading' }
  | { kind: 'ok'; value: T }
  | { kind: 'error'; error: ApiError }

interface BackendState {
  ready: FetchState<ReadyPayload>
  meta: FetchState<PlatformMeta>
}

const POLL_INTERVAL_MS = 15_000

function StatusBadge({
  state,
  okLabel,
}: {
  state: FetchState<unknown>
  okLabel?: string
}) {
  if (state.kind === 'loading') {
    return (
      <Badge variant="outline" className="gap-1">
        <RefreshCw className="h-3 w-3 animate-spin" aria-hidden /> Checking…
      </Badge>
    )
  }
  if (state.kind === 'error') {
    return (
      <Badge variant="destructive" className="gap-1">
        <WifiOff className="h-3 w-3" aria-hidden /> Unreachable
      </Badge>
    )
  }
  const value = state.value as { status?: string }
  if (value.status && value.status !== 'ready') {
    return (
      <Badge variant="destructive" className="gap-1">
        <AlertTriangle className="h-3 w-3" aria-hidden /> Not ready
      </Badge>
    )
  }
  return (
    <Badge className="gap-1 bg-emerald-600 text-white hover:bg-emerald-600">
      <CheckCircle2 className="h-3 w-3" aria-hidden /> {okLabel ?? 'Operational'}
    </Badge>
  )
}

function ErrorNote({ error }: { error: ApiError }) {
  return (
    <p className="mt-2 text-xs text-destructive">
      {error.message}
      {error.code === 'BACKEND_UNREACHABLE' && (
        <> — run <code className="rounded bg-muted px-1">make backend</code> or{' '}
          <code className="rounded bg-muted px-1">docker compose up</code>.</>
      )}
      {error.requestId && (
        <> (request_id: <code className="rounded bg-muted px-1">{error.requestId}</code>)</>
      )}
    </p>
  )
}

/**
 * Live platform status: backend API, PostgreSQL and the market-data provider.
 * The MT5 provider is reported exactly as the backend reports it — when it is
 * not connected, the UI says so plainly instead of hiding the fact.
 */
export function SystemStatus() {
  const [state, setState] = useState<BackendState>({
    ready: { kind: 'loading' },
    meta: { kind: 'loading' },
  })
  const [mt5, setMt5] = useState<FetchState<MarketDataStatusPayload>>({
    kind: 'loading',
  })
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    setBusy(true)
    const readyState = await api
      .get<ReadyPayload>('/health/ready')
      .then((value) => ({ kind: 'ok', value }) as FetchState<ReadyPayload>)
      .catch((e) => ({ kind: 'error', error: e }) as FetchState<ReadyPayload>)
    const meta = await api
      .get<PlatformMeta>('/api/v1/meta')
      .then((value) => ({ kind: 'ok', value }) as FetchState<PlatformMeta>)
      .catch((e) => ({ kind: 'error', error: e }) as FetchState<PlatformMeta>)
    const mt5State = await api
      .get<MarketDataStatusPayload>('/api/v1/market-data/status')
      .then((value) => ({ kind: 'ok', value }) as FetchState<MarketDataStatusPayload>)
      .catch((e) => ({ kind: 'error', error: e }) as FetchState<MarketDataStatusPayload>)
    setState({ ready: readyState, meta })
    setMt5(mt5State)
    setBusy(false)
  }, [])

  useEffect(() => {
    // Defer the first poll to a macrotask: no synchronous state updates on
    // mount, and the interval handles every subsequent refresh.
    const initial = setTimeout(() => void refresh(), 0)
    const timer = setInterval(() => void refresh(), POLL_INTERVAL_MS)
    return () => {
      clearTimeout(initial)
      clearInterval(timer)
    }
  }, [refresh])

  const ready = state.ready.kind === 'ok' ? state.ready.value : null
  const meta = state.meta.kind === 'ok' ? state.meta.value : null
  const db = ready?.checks.database

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-base">
              <Server className="h-4 w-4 text-muted-foreground" aria-hidden />
              Analysis backend
            </CardTitle>
            <StatusBadge state={state.ready} okLabel="Ready" />
          </div>
          <CardDescription>
            FastAPI service {meta ? `v${meta.version}` : ''} · PostgreSQL-backed
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {state.ready.kind === 'loading' && (
            <Skeleton className="h-5 w-3/4" />
          )}
          {state.ready.kind === 'error' && <ErrorNote error={state.ready.error} />}
          {ready && (
            <>
              <div className="flex items-center gap-2">
                <Database className="h-4 w-4 text-muted-foreground" aria-hidden />
                <span className="text-muted-foreground">PostgreSQL:</span>
                {db?.status === 'up' ? (
                  <span className="font-medium text-emerald-600 dark:text-emerald-400">
                    up ({db.latency_ms?.toFixed(1) ?? '—'} ms)
                  </span>
                ) : (
                  <span className="font-medium text-destructive">down</span>
                )}
              </div>
              {db?.status !== 'up' && db?.error && (
                <p className="text-xs text-destructive">{db.error}</p>
              )}
              <p className="text-xs text-muted-foreground">
                Rule engine whitelists: {(meta?.rule_format.condition_types.length ?? 0)}{' '}
                condition type(s), {meta?.rule_format.operators.length ?? 0} operators ·
                code execution: {meta?.rule_format.code_execution ? 'yes' : 'never'}
              </p>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-base">
              <ShieldAlert className="h-4 w-4 text-muted-foreground" aria-hidden />
              Market data
            </CardTitle>
            <StatusBadge state={mt5} okLabel={mt5.kind === 'ok' && mt5.value.connected ? 'Connected' : 'Reported'} />
          </div>
          <CardDescription>Provider: MetaTrader 5 (mt5)</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {mt5.kind === 'loading' && <Skeleton className="h-5 w-3/4" />}
          {mt5.kind === 'error' && <ErrorNote error={mt5.error} />}
          {mt5.kind === 'ok' && (
            <>
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">MT5 terminal:</span>
                {mt5.value.connected ? (
                  <span className="font-medium text-emerald-600 dark:text-emerald-400">
                    connected
                  </span>
                ) : (
                  <span className="font-medium text-amber-600 dark:text-amber-400">
                    not connected
                  </span>
                )}
              </div>
              <p className="text-xs text-muted-foreground">{mt5.value.detail}</p>
              <p className="text-xs text-muted-foreground">{mt5.value.note}</p>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Analysis modes</CardTitle>
          <CardDescription>Strictly separated configurations</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {(meta?.modes ?? []).map((m) => (
            <div key={m.name} className="space-y-1">
              <p className="font-medium">{MODE_LABELS[m.name]}</p>
              <div className="flex flex-wrap gap-1">
                {m.default_timeframes.map((tf) => (
                  <Badge key={tf} variant="secondary" className="font-mono text-[11px]">
                    {tf}
                  </Badge>
                ))}
              </div>
            </div>
          ))}
          {!meta && state.meta.kind === 'loading' && <Skeleton className="h-16 w-full" />}
          {state.meta.kind === 'error' && <ErrorNote error={state.meta.error} />}
        </CardContent>
      </Card>

      <Card className="lg:col-span-3">
        <CardContent className="flex flex-wrap items-center justify-between gap-3 pt-1 text-sm">
          <p className="text-muted-foreground">
            Phase 1 evaluates <strong>user-supplied candle data only</strong> — the
            platform never fabricates market data, never generates unsolicited
            signals and never places orders.
          </p>
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={busy}>
            <RefreshCw className={busy ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} aria-hidden />
            Refresh status
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
