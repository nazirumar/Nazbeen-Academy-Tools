'use client'

/**
 * Replay workspace (Phase 11).
 *
 * Playback is driven by the persisted backend cursor, not by a client-side
 * timer replaying an already-downloaded array. Every step/fetch is scoped by
 * the server, so a tab that was closed can be resumed without leaking future
 * candles. Speed only changes the presentation delay between steps.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Pause,
  Play,
  RotateCcw,
  SkipForward,
  Trash2,
  WifiOff,
} from 'lucide-react'
import { toast } from '@/hooks/use-toast'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { CandleChart } from '@/components/chart/candle-chart'
import { api } from '@/lib/api/client'
import type {
  CandlePoint,
  ReplayCandleSeries,
  ReplayDataQuality,
  ReplaySession,
} from '@/lib/api/types'
import type { OverlayCatalog } from '@/lib/chart/overlay-model'
import {
  REPLAY_STATUS_META,
  errorText,
  fmtReplayTime,
} from './replay-shared'

const EMPTY_OVERLAYS: OverlayCatalog = {
  markers: [],
  levels: [],
  bands: [],
  availableKinds: [],
  unsupportedKinds: [],
}
const EMPTY_VISIBLE: Record<string, boolean> = {}
const CHART_LIMIT = 2_000
const SPEEDS = [0.5, 1, 2, 4, 8, 16, 30]

interface Props {
  sessionId: string
  onBack: () => void
  onDeleted: () => void
  onProgress?: (session: ReplaySession) => void
}

function replayDigits(symbol: string): number {
  if (symbol.toUpperCase().includes('JPY')) return 3
  if (symbol.toUpperCase().startsWith('XAU')) return 2
  return 5
}

function qualityBadgeTone(status: string | undefined): string {
  return status === 'ok'
    ? 'border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-300'
    : 'border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300'
}

function QualityCard({ quality }: { quality: ReplayDataQuality }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-sm">Data quality</CardTitle>
          <Badge className={qualityBadgeTone(quality.overall_status)}>
            {quality.overall_status === 'ok' ? 'complete window' : 'partial window'}
          </Badge>
        </div>
        <CardDescription>
          Closed stored candles only. Missing bars are reported, never invented
          or silently filled.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-xs">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
          <dt className="text-muted-foreground">Candles available</dt>
          <dd className="tabular-nums">
            {quality.candles_available.toLocaleString()}
          </dd>
          <dt className="text-muted-foreground">Expected</dt>
          <dd className="tabular-nums">
            {quality.expected_candles == null
              ? 'Not available'
              : quality.expected_candles.toLocaleString()}
          </dd>
          <dt className="text-muted-foreground">Gaps</dt>
          <dd className="tabular-nums">{quality.gap_count}</dd>
          <dt className="text-muted-foreground">Largest gap</dt>
          <dd className="tabular-nums">{quality.largest_gap_intervals} bar(s)</dd>
          <dt className="text-muted-foreground">Duplicate rows</dt>
          <dd className="tabular-nums">{quality.duplicate_count}</dd>
          <dt className="text-muted-foreground">Invalid OHLC rows</dt>
          <dd className="tabular-nums">{quality.invalid_ohlc_count}</dd>
          <dt className="text-muted-foreground">Excluded forming</dt>
          <dd className="tabular-nums">{quality.forming_count}</dd>
        </dl>
        {quality.warnings.length > 0 ? (
          <ul className="space-y-1 text-amber-700 dark:text-amber-300">
            {quality.warnings.map((warning) => (
              <li key={`${warning.code}-${warning.message}`}>
                <span className="font-medium">{warning.code}</span>: {warning.message}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-emerald-700 dark:text-emerald-300">
            No data-quality warnings for this session window.
          </p>
        )}
        <p className="text-[11px] text-muted-foreground">{quality.note}</p>
      </CardContent>
    </Card>
  )
}

export function ReplaySessionDetail({
  sessionId,
  onBack,
  onDeleted,
  onProgress,
}: Props) {
  const [session, setSession] = useState<ReplaySession | null>(null)
  const [candles, setCandles] = useState<CandlePoint[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [seekValue, setSeekValue] = useState('0')
  const sessionRef = useRef<ReplaySession | null>(null)
  const busyRef = useRef(false)
  const completionNotified = useRef(false)

  useEffect(() => {
    sessionRef.current = session
  }, [session])

  const loadCandles = useCallback(async (id: string) => {
    const series = await api.get<ReplayCandleSeries>(
      `/api/v1/replay/sessions/${id}/candles`,
      { limit: String(CHART_LIMIT) },
    )
    setCandles(series.candles)
  }, [])

  const loadAll = useCallback(
    async (showLoading: boolean) => {
      if (showLoading) setLoading(true)
      setError(null)
      try {
        const next = await api.get<ReplaySession>(
          `/api/v1/replay/sessions/${sessionId}`,
        )
        setSession(next)
        sessionRef.current = next
        setSeekValue(String(Math.floor(Date.parse(next.cursor_time) / 1000)))
        await loadCandles(next.id)
      } catch (e) {
        setError(errorText(e))
      } finally {
        if (showLoading) setLoading(false)
      }
    },
    [loadCandles, sessionId],
  )

  useEffect(() => {
    completionNotified.current = false
    setSession(null)
    setCandles([])
    setSeekValue('0')
    void loadAll(true)
  }, [loadAll])

  const applyNext = useCallback(
    async (next: ReplaySession, options?: { notifyCompletion?: boolean }) => {
      setSession(next)
      sessionRef.current = next
      setSeekValue(String(Math.floor(Date.parse(next.cursor_time) / 1000)))
      onProgress?.(next)
      await loadCandles(next.id)
      if (next.status === 'completed' && !completionNotified.current) {
        completionNotified.current = true
        if (options?.notifyCompletion) {
          toast({
            title: 'Replay complete',
            description: 'The cursor reached the last stored closed candle.',
          })
        }
      } else if (next.status !== 'completed') {
        completionNotified.current = false
      }
    },
    [loadCandles, onProgress],
  )

  const stepOnce = useCallback(async () => {
    const current = sessionRef.current
    if (!current || busyRef.current || current.at_end) return
    busyRef.current = true
    setBusy(true)
    setError(null)
    try {
      const next = await api.post<ReplaySession>(
        `/api/v1/replay/sessions/${current.id}/step`,
        { count: 1 },
      )
      await applyNext(next, { notifyCompletion: true })
    } catch (e) {
      setError(errorText(e))
      // Do not keep pretending to play after a failed step.
      setSession((prev) => (prev ? { ...prev, status: 'paused' } : prev))
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }, [applyNext])

  // Presentation timer: one backend step at a time. 1x = 1 candle/second.
  useEffect(() => {
    if (!session || session.status !== 'active' || session.at_end) return
    const delay = Math.max(120, Math.round(1000 / Math.max(0.5, session.playback_speed)))
    const timer = setTimeout(() => {
      void stepOnce()
    }, delay)
    return () => clearTimeout(timer)
  }, [
    session,
    session?.status,
    session?.playback_speed,
    session?.at_end,
    session?.visible_candles,
    stepOnce,
  ])

  const mutate = useCallback(
    async (request: () => Promise<ReplaySession>, notifyCompletion = false) => {
      if (busyRef.current) return
      busyRef.current = true
      setBusy(true)
      setError(null)
      try {
        const next = await request()
        await applyNext(next, { notifyCompletion })
      } catch (e) {
        setError(errorText(e))
      } finally {
        busyRef.current = false
        setBusy(false)
      }
    },
    [applyNext],
  )

  const togglePlayback = () => {
    if (!session) return
    if (session.status === 'active') {
      void mutate(() =>
        api.post<ReplaySession>(`/api/v1/replay/sessions/${session.id}/pause`),
      )
    } else {
      void mutate(
        () =>
          api.post<ReplaySession>(`/api/v1/replay/sessions/${session.id}/resume`),
        false,
      )
    }
  }

  const reset = () => {
    if (!session) return
    completionNotified.current = false
    void mutate(() =>
      api.post<ReplaySession>(`/api/v1/replay/sessions/${session.id}/reset`),
    )
  }

  const seek = () => {
    if (!session) return
    const target = Number(seekValue)
    if (!Number.isFinite(target) || target <= 0) {
      setError('Choose a valid timestamp to seek to.')
      return
    }
    completionNotified.current = false
    void mutate(() =>
      api.post<ReplaySession>(`/api/v1/replay/sessions/${session.id}/seek`, {
        target_time: new Date(target * 1000).toISOString(),
      }),
    )
  }

  const changeSpeed = (value: string) => {
    if (!session) return
    void mutate(() =>
      api.patch<ReplaySession>(`/api/v1/replay/sessions/${session.id}`, {
        playback_speed: Number(value),
      }),
    )
  }

  const remove = async () => {
    if (!session) return
    if (!window.confirm('Delete this saved replay session?')) return
    busyRef.current = true
    setBusy(true)
    try {
      await api.delete(`/api/v1/replay/sessions/${session.id}`)
      toast({ title: 'Replay deleted' })
      onDeleted()
    } catch (e) {
      setError(errorText(e))
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  const firstEpoch = session
    ? Math.floor(Date.parse(session.first_candle_time) / 1000)
    : 0
  const lastEpoch = session
    ? Math.floor(Date.parse(session.last_candle_time) / 1000)
    : 0
  const interval = useMemo(() => {
    if (!session) return 60
    return Math.max(
      1,
      Math.floor(Date.parse(session.current_time) / 1000 - Date.parse(session.cursor_time) / 1000),
    )
  }, [session])

  if (loading && !session) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-16 text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading replay session…
        </CardContent>
      </Card>
    )
  }

  if (!session) {
    return (
      <Card>
        <CardContent className="space-y-3 py-12 text-center">
          <WifiOff className="mx-auto h-8 w-8 text-muted-foreground" aria-hidden />
          <p className="text-sm text-muted-foreground">
            {error ?? 'Replay session not available.'}
          </p>
          <Button variant="outline" onClick={onBack}>
            <ArrowLeft className="h-4 w-4" aria-hidden /> Back to saved replays
          </Button>
        </CardContent>
      </Card>
    )
  }

  const statusMeta = REPLAY_STATUS_META[session.status]
  const canResume = session.status !== 'completed' && !session.at_end

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-2">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onBack}
                  aria-label="Back to saved replays"
                >
                  <ChevronLeft className="h-4 w-4" aria-hidden /> Saved replays
                </Button>
                <Badge className={statusMeta.className}>{statusMeta.label}</Badge>
                <Badge variant="outline">{session.symbol} · {session.timeframe}</Badge>
              </div>
              <CardTitle className="mt-1 text-base">
                Historical replay · {session.symbol} {session.timeframe}
              </CardTitle>
              <CardDescription>
                Simulated now: {fmtReplayTime(session.current_time)} · persisted
                cursor {session.cursor_index + 1} of {session.total_candles}
              </CardDescription>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void remove()}
              disabled={busy}
              aria-label="Delete replay session"
            >
              <Trash2 className="h-4 w-4" aria-hidden /> Delete
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <Alert>
            <AlertTitle className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4" aria-hidden /> No future data
            </AlertTitle>
            <AlertDescription>
              The backend returns only candles at or before the persisted cursor.
              Playback speed affects presentation delay only; it can never change
              candle order or create fills. Trade execution and the journal are
              outside this first replay slice.
            </AlertDescription>
          </Alert>

          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-lg border p-3">
              <p className="text-xs text-muted-foreground">Visible candles</p>
              <p className="text-lg font-semibold tabular-nums" data-testid="replay-visible-count">
                {session.visible_candles.toLocaleString()}
                <span className="text-sm font-normal text-muted-foreground">
                  {' '}/ {session.total_candles.toLocaleString()}
                </span>
              </p>
            </div>
            <div className="rounded-lg border p-3">
              <p className="text-xs text-muted-foreground">Progress</p>
              <p className="text-lg font-semibold tabular-nums">{session.progress_pct.toFixed(1)}%</p>
            </div>
            <div className="rounded-lg border p-3">
              <p className="text-xs text-muted-foreground">Remaining</p>
              <p className="text-lg font-semibold tabular-nums">
                {session.remaining_candles.toLocaleString()}
              </p>
            </div>
            <div className="rounded-lg border p-3">
              <p className="text-xs text-muted-foreground">Playback speed</p>
              <p className="text-lg font-semibold tabular-nums">{session.playback_speed}×</p>
            </div>
          </div>

          <div className="h-2 overflow-hidden rounded-full bg-muted" aria-hidden>
            <div
              className="h-full rounded-full bg-primary transition-all"
              style={{ width: `${Math.min(100, Math.max(0, session.progress_pct))}%` }}
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              onClick={() => void stepOnce()}
              disabled={busy || session.at_end}
              aria-label="Step forward one candle"
            >
              <SkipForward className="h-4 w-4" aria-hidden /> Step
            </Button>
            <Button
              variant={session.status === 'active' ? 'secondary' : 'default'}
              onClick={togglePlayback}
              disabled={busy || (session.status === 'completed' && !canResume)}
            >
              {session.status === 'active' ? (
                <>
                  <Pause className="h-4 w-4" aria-hidden /> Pause
                </>
              ) : (
                <>
                  <Play className="h-4 w-4" aria-hidden /> {session.status === 'paused' ? 'Play' : 'Replay'}
                </>
              )}
            </Button>
            <Button variant="outline" onClick={reset} disabled={busy}>
              <RotateCcw className="h-4 w-4" aria-hidden /> Reset
            </Button>
            <div className="flex items-center gap-1.5">
              <label htmlFor="replay-detail-speed" className="text-xs text-muted-foreground">
                Speed
              </label>
              <select
                id="replay-detail-speed"
                aria-label="Playback speed"
                className="h-8 rounded-md border border-input bg-transparent px-2 text-xs shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
                value={String(session.playback_speed)}
                disabled={busy}
                onChange={(e) => changeSpeed(e.target.value)}
              >
                {SPEEDS.map((value) => (
                  <option key={value} value={String(value)}>
                    {value}×
                  </option>
                ))}
              </select>
            </div>
            {session.status === 'completed' || session.at_end ? (
              <Badge variant="outline" className="ml-auto">
                End of window
              </Badge>
            ) : null}
          </div>

          <div className="space-y-2 rounded-lg border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <label htmlFor="replay-seek" className="text-xs font-medium">
                Seek within the stored window
              </label>
              <span className="text-xs text-muted-foreground">
                {fmtReplayTime(new Date(Number(seekValue || firstEpoch) * 1000).toISOString())}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <input
                id="replay-seek"
                type="range"
                aria-label="Seek to time"
                className="min-w-40 flex-1 accent-primary"
                min={firstEpoch}
                max={lastEpoch}
                step={interval}
                value={seekValue}
                disabled={busy}
                onChange={(e) => setSeekValue(e.target.value)}
              />
              <Button variant="outline" size="sm" onClick={seek} disabled={busy}>
                <ChevronRight className="h-4 w-4" aria-hidden /> Seek
              </Button>
            </div>
            <p className="text-[11px] text-muted-foreground">
              Seeking to a timestamp between candles selects the latest stored
              candle at or before it. Seeking never enables candles after the
              chosen time.
            </p>
          </div>

          {candles.length > 0 ? (
            <CandleChart
              candles={candles}
              seriesKey={`${session.symbol} · ${session.timeframe} · replay`}
              digits={replayDigits(session.symbol)}
              overlays={EMPTY_OVERLAYS}
              visible={EMPTY_VISIBLE}
              includesForming={false}
              heightClass="h-[460px]"
              followLatest
            />
          ) : (
            <div className="rounded-lg border py-16 text-center text-sm text-muted-foreground">
              No candles returned for this cursor.
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <QualityCard quality={session.data_quality} />
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Session window</CardTitle>
            <CardDescription>Saved configuration and provenance.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-xs">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
              <dt className="text-muted-foreground">Requested start</dt>
              <dd>{fmtReplayTime(session.start_time)}</dd>
              <dt className="text-muted-foreground">Requested end</dt>
              <dd>{fmtReplayTime(session.end_time)}</dd>
              <dt className="text-muted-foreground">First closed candle</dt>
              <dd>{fmtReplayTime(session.first_candle_time)}</dd>
              <dt className="text-muted-foreground">Last closed candle</dt>
              <dd>{fmtReplayTime(session.last_candle_time)}</dd>
              <dt className="text-muted-foreground">Created</dt>
              <dd>{fmtReplayTime(session.created_at)}</dd>
              <dt className="text-muted-foreground">Last advanced</dt>
              <dd>{fmtReplayTime(session.last_advanced_at)}</dd>
              <dt className="text-muted-foreground">Session schema</dt>
              <dd>{session.session_schema_version}</dd>
              <dt className="text-muted-foreground">Engine</dt>
              <dd>{session.engine_version}</dd>
            </dl>
            <p className="text-[11px] text-muted-foreground">{session.note}</p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
