'use client'

/**
 * Replay configuration panel (Phase 11).
 *
 * The panel only selects a historical window. Coverage is a real backend
 * query against stored PostgreSQL candles, and a session is never created
 * over an empty window. Replay does not fetch live MT5 data.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { CalendarRange, Database, Loader2, PlayCircle, ShieldCheck } from 'lucide-react'
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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { api } from '@/lib/api/client'
import {
  type PlatformMode,
  type ReplayCoverage,
  type ReplaySession,
} from '@/lib/api/types'
import { timeframesForMode } from '@/lib/platform/modes'
import {
  defaultReplayTimeframe,
  defaultReplayWindow,
  errorText,
  fmtReplayTime,
  inputValueToIso,
} from './replay-shared'

interface Props {
  mode: PlatformMode
  onCreated: (session: ReplaySession) => void
}

const SPEEDS = [0.5, 1, 2, 4, 8, 16, 30]

function coverageTone(status: string | undefined): string {
  if (status === 'ok')
    return 'border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-300'
  return 'border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300'
}

export function ReplayConfigPanel({ mode, onCreated }: Props) {
  const timeframes = useMemo(() => timeframesForMode(mode), [mode])
  const [defaults] = useState(() => defaultReplayWindow())
  const [symbol, setSymbol] = useState('EURUSD')
  const [timeframe, setTimeframe] = useState(() => defaultReplayTimeframe(mode))
  const [start, setStart] = useState(defaults.start)
  const [end, setEnd] = useState(defaults.end)
  const [speed, setSpeed] = useState('1')
  const [coverage, setCoverage] = useState<ReplayCoverage | null>(null)
  const [checking, setChecking] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [clientErrors, setClientErrors] = useState<string[]>([])

  useEffect(() => {
    const next = defaultReplayTimeframe(mode)
    setTimeframe(next)
    setCoverage(null)
    setError(null)
  }, [mode])

  useEffect(() => {
    if (!timeframes.includes(timeframe) && timeframes.length > 0) {
      setTimeframe(timeframes[0])
    }
  }, [timeframe, timeframes])

  const windowBody = useCallback(
    () => ({
      mode,
      symbol: symbol.trim().toUpperCase(),
      timeframe,
      start: inputValueToIso(start),
      end: inputValueToIso(end),
      playback_speed: Number(speed),
    }),
    [mode, symbol, timeframe, start, end, speed],
  )

  const validateLocal = useCallback((): string[] => {
    const errors: string[] = []
    const body = windowBody()
    if (!body.symbol) errors.push('Enter a symbol.')
    if (!body.timeframe) errors.push('Select a timeframe.')
    if (!body.start || !body.end) errors.push('Set the historical window (UTC).')
    if (body.start && body.end && new Date(body.start) >= new Date(body.end)) {
      errors.push('Start must be earlier than end.')
    }
    if (!Number.isFinite(body.playback_speed) || body.playback_speed <= 0) {
      errors.push('Playback speed must be greater than zero.')
    }
    return errors
  }, [windowBody])

  const checkCoverage = async () => {
    const local = validateLocal()
    setClientErrors(local)
    setError(null)
    if (local.length > 0) return
    setChecking(true)
    try {
      const body = windowBody()
      const data = await api.get<ReplayCoverage>('/api/v1/replay/data-coverage', {
        symbol: body.symbol,
        timeframe: body.timeframe,
        start: body.start,
        end: body.end,
      })
      setCoverage(data)
    } catch (e) {
      setCoverage(null)
      setError(errorText(e))
    } finally {
      setChecking(false)
    }
  }

  const submit = async () => {
    const local = validateLocal()
    setClientErrors(local)
    setError(null)
    if (local.length > 0) return
    setSubmitting(true)
    try {
      const created = await api.post<ReplaySession>(
        '/api/v1/replay/sessions',
        windowBody(),
      )
      onCreated(created)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setSubmitting(false)
    }
  }

  const quality = coverage?.data_quality
  const warnings = quality?.warnings ?? []

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <PlayCircle className="h-4 w-4 text-muted-foreground" aria-hidden />
          New replay session
        </CardTitle>
        <CardDescription>
          Choose a stored historical window. The session starts paused on the
          first closed candle; no live data is fetched and future candles are
          never exposed.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="space-y-1.5">
            <Label htmlFor="replay-symbol">Symbol</Label>
            <Input
              id="replay-symbol"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              className="font-mono"
              placeholder="EURUSD"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="replay-timeframe">Timeframe</Label>
            <select
              id="replay-timeframe"
              aria-label="Replay timeframe"
              className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
              value={timeframe}
              onChange={(e) => {
                setTimeframe(e.target.value)
                setCoverage(null)
              }}
            >
              {timeframes.map((tf) => (
                <option key={tf} value={tf}>
                  {tf}
                </option>
              ))}
            </select>
            {mode === 'binary_options' ? (
              <p className="text-[11px] text-muted-foreground">Binary Options replay is M1-only.</p>
            ) : null}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="replay-start">Start (UTC)</Label>
            <Input
              id="replay-start"
              type="datetime-local"
              step={60}
              value={start}
              onChange={(e) => {
                setStart(e.target.value)
                setCoverage(null)
              }}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="replay-end">End (UTC)</Label>
            <Input
              id="replay-end"
              type="datetime-local"
              step={60}
              value={end}
              onChange={(e) => {
                setEnd(e.target.value)
                setCoverage(null)
              }}
            />
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="replay-speed">Playback speed</Label>
            <select
              id="replay-speed"
              aria-label="Playback speed"
              className="h-9 rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
              value={speed}
              onChange={(e) => setSpeed(e.target.value)}
            >
              {SPEEDS.map((value) => (
                <option key={value} value={String(value)}>
                  {value}× {value === 1 ? '(1 candle / second)' : ''}
                </option>
              ))}
            </select>
          </div>
          <Button
            type="button"
            variant="outline"
            onClick={() => void checkCoverage()}
            disabled={checking || submitting}
          >
            {checking ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <Database className="h-4 w-4" aria-hidden />
            )}
            Check stored data
          </Button>
          <Button
            type="button"
            onClick={() => void submit()}
            disabled={checking || submitting}
          >
            {submitting ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <CalendarRange className="h-4 w-4" aria-hidden />
            )}
            Create replay
          </Button>
        </div>

        {clientErrors.length > 0 ? (
          <Alert variant="destructive">
            <AlertDescription>
              <ul className="list-inside list-disc space-y-1">
                {clientErrors.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}

        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        {coverage ? (
          <div className="space-y-2 rounded-lg border p-3" data-testid="replay-coverage">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-medium">Stored data coverage</p>
              <Badge className={coverageTone(quality?.overall_status)}>
                {quality?.overall_status === 'ok' ? 'complete window' : 'partial window'}
              </Badge>
            </div>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
              <dt className="text-muted-foreground">Closed candles</dt>
              <dd className="tabular-nums">{coverage.candles_in_range.toLocaleString()}</dd>
              <dt className="text-muted-foreground">Expected</dt>
              <dd className="tabular-nums">
                {coverage.expected_candles == null
                  ? 'Not available'
                  : coverage.expected_candles.toLocaleString()}
              </dd>
              <dt className="text-muted-foreground">First candle</dt>
              <dd>{fmtReplayTime(quality?.first_time ? new Date(quality.first_time * 1000).toISOString() : null)}</dd>
              <dt className="text-muted-foreground">Last candle</dt>
              <dd>{fmtReplayTime(quality?.last_time ? new Date(quality.last_time * 1000).toISOString() : null)}</dd>
            </dl>
            {warnings.length > 0 ? (
              <ul className="space-y-1 text-xs text-amber-700 dark:text-amber-300">
                {warnings.map((warning) => (
                  <li key={`${warning.code}-${warning.message}`}>
                    {warning.code}: {warning.message}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="flex items-center gap-1 text-xs text-emerald-700 dark:text-emerald-300">
                <ShieldCheck className="h-3.5 w-3.5" aria-hidden /> No data-quality warnings for this window.
              </p>
            )}
            <p className="text-[11px] text-muted-foreground">
              Coverage is read from stored closed candles only. Gaps are
              reported, never filled; a forming candle is excluded.
            </p>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
