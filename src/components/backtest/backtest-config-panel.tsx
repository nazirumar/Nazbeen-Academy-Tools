'use client'

/**
 * Backtest configuration panel (Phase 6).
 *
 * Validation happens twice: light client-side gating (visible feedback)
 * and the authoritative server-side cascade on submit. The data-coverage
 * check is a real backend query — the UI never guesses data availability.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { CalendarRange, FlaskConical, Loader2, Play } from 'lucide-react'
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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { api } from '@/lib/api/client'
import {
  ApiError,
  type BacktestRun,
  type DataCoverage,
  type PaginatedStrategies,
  type PlatformMode,
  type Strategy,
  type StrategyVersion,
} from '@/lib/api/types'
import { fmtPct, fmtUtc } from './backtest-shared'

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = e.details.length
      ? ` (${e.details.map((d) => `${d.path || ''} ${d.message ?? ''}`).join('; ')})`
      : ''
    return `${e.message}${detail}`
  }
  return e instanceof Error ? e.message : 'Unexpected error'
}

export interface BacktestFormState {
  strategyId: string
  version: string
  symbol: string
  timeframe: string
  start: string
  end: string
  simulateTrades: boolean
  spreadPips: string
  slippagePips: string
  commission: string
  stopLossPips: string
  takeProfitPips: string
  riskPct: string
  accountBalance: string
  slTpPolicy: 'conservative' | 'optimistic' | 'ambiguous'
  expiryCandles: string
  payoutPct: string
  stake: string
  tiePolicy: 'refund' | 'loss'
}

export function defaultFormState(mode: PlatformMode): BacktestFormState {
  return {
    strategyId: '',
    version: '',
    symbol: mode === 'binary_options' ? 'EURUSD' : 'EURUSD',
    timeframe: mode === 'binary_options' ? 'M1' : '',
    start: '',
    end: '',
    simulateTrades: false,
    spreadPips: '1.0',
    slippagePips: '0.5',
    commission: '0',
    stopLossPips: '20',
    takeProfitPips: '40',
    riskPct: '1.0',
    accountBalance: mode === 'binary_options' ? '1000' : '10000',
    slTpPolicy: 'conservative',
    expiryCandles: '1',
    payoutPct: '0.80',
    stake: '10',
    tiePolicy: 'refund',
  }
}

function toIsoLocal(date: Date): string {
  // datetime-local value in UTC terms (the backend expects ISO-8601 with tz;
  // we submit an explicit UTC offset by constructing the ISO string).
  return date.toISOString().slice(0, 16)
}

interface Props {
  mode: PlatformMode
  onRunCreated: (run: BacktestRun) => void
}

export function BacktestConfigPanel({ mode, onRunCreated }: Props) {
  const [form, setForm] = useState<BacktestFormState>(() => defaultFormState(mode))
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [versions, setVersions] = useState<StrategyVersion[]>([])
  const [loadingStrategies, setLoadingStrategies] = useState(true)
  const [checkingCoverage, setCheckingCoverage] = useState(false)
  const [coverage, setCoverage] = useState<DataCoverage | null>(null)
  const [coverageError, setCoverageError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [clientErrors, setClientErrors] = useState<string[]>([])

  const update = useCallback(
    <K extends keyof BacktestFormState>(key: K, value: BacktestFormState[K]) => {
      setForm((prev) => ({ ...prev, [key]: value }))
    },
    [],
  )

  // Load strategies for the active mode (published ones are backtestable).
  useEffect(() => {
    let active = true
    setLoadingStrategies(true)
    api
      .get<PaginatedStrategies>('/api/v1/strategies', {
        mode,
        status: 'active',
        page_size: '100',
      })
      .then((data) => {
        if (active) setStrategies(data.items)
      })
      .catch((e) => {
        if (active) toast({ title: 'Could not load strategies', description: errorText(e), variant: 'destructive' })
      })
      .finally(() => {
        if (active) setLoadingStrategies(false)
      })
    return () => {
      active = false
    }
  }, [mode])

  // Load versions when a strategy is selected (immutable version picker).
  useEffect(() => {
    if (!form.strategyId) {
      setVersions([])
      return
    }
    let active = true
    api
      .get<StrategyVersion[]>(`/api/v1/strategies/${form.strategyId}/versions`)
      .then((data) => {
        if (!active) return
        const immutable = data.filter((v) => v.status !== 'draft')
        setVersions(immutable)
        const published = immutable.find((v) => v.status === 'published')
        update(
          'version',
          published ? String(published.version) : immutable.length
            ? String(immutable[0].version)
            : '',
        )
        if (immutable.length === 0) {
          setClientErrors(['This strategy has no published version — publish one first.'])
        }
      })
      .catch((e) => toast({ title: 'Could not load versions', description: errorText(e), variant: 'destructive' }))
    return () => {
      active = false
    }
  }, [form.strategyId, update])

  // Reset mode-dependent defaults when mode flips.
  useEffect(() => {
    setForm((prev) => ({
      ...defaultFormState(mode),
      strategyId: '',
      symbol: prev.symbol,
      start: prev.start,
      end: prev.end,
    }))
    setCoverage(null)
  }, [mode])

  const selectedVersion = useMemo(
    () => versions.find((v) => String(v.version) === form.version) ?? null,
    [versions, form.version],
  )

  const declaredTimeframes = selectedVersion?.rule_set?.timeframes ?? []

  // Client-side gate (the server cascade stays authoritative).
  const validateLocal = (): string[] => {
    const errors: string[] = []
    if (!form.strategyId) errors.push('Select a strategy.')
    if (!form.version) errors.push('Select an immutable strategy version.')
    if (!form.symbol.trim()) errors.push('Enter a symbol.')
    if (!form.timeframe) errors.push('Select a timeframe.')
    if (!form.start || !form.end) errors.push('Set the historical date range.')
    if (form.start && form.end && new Date(form.start) >= new Date(form.end))
      errors.push('Start must be earlier than end.')
    if (mode === 'forex' && form.simulateTrades) {
      const hasSl = form.stopLossPips !== '' && Number(form.stopLossPips) > 0
      const hasTp = form.takeProfitPips !== '' && Number(form.takeProfitPips) > 0
      if (hasSl !== hasTp)
        errors.push('Stop-loss and take-profit pips must be provided together.')
    }
    return errors
  }

  const checkCoverage = async () => {
    if (!form.symbol || !form.timeframe || !form.start || !form.end) {
      setCoverageError('Symbol, timeframe and date range are needed for the coverage check.')
      return
    }
    setCheckingCoverage(true)
    setCoverageError(null)
    try {
      const data = await api.get<DataCoverage>('/api/v1/backtests/data-coverage', {
        symbol: form.symbol.trim().toUpperCase(),
        timeframe: form.timeframe,
        start: new Date(form.start + ':00Z').toISOString(),
        end: new Date(form.end + ':00Z').toISOString(),
      })
      setCoverage(data)
    } catch (e) {
      setCoverage(null)
      setCoverageError(errorText(e))
    } finally {
      setCheckingCoverage(false)
    }
  }

  const submit = async () => {
    const errors = validateLocal()
    setClientErrors(errors)
    if (errors.length > 0) return
    setSubmitting(true)
    try {
      const body: Record<string, unknown> = {
        mode,
        strategy_id: form.strategyId,
        strategy_version: Number(form.version),
        symbol: form.symbol.trim().toUpperCase(),
        timeframe: form.timeframe,
        start: new Date(form.start + ':00Z').toISOString(),
        end: new Date(form.end + ':00Z').toISOString(),
      }
      if (mode === 'forex' && form.simulateTrades) {
        body.forex_execution = {
          enabled: true,
          spread_pips: Number(form.spreadPips) || 0,
          slippage_pips: Number(form.slippagePips) || 0,
          commission_per_lot_per_side: Number(form.commission) || 0,
          stop_loss_pips: Number(form.stopLossPips),
          take_profit_pips: Number(form.takeProfitPips),
          risk_mode: 'fixed_fractional',
          risk_pct_per_trade: Number(form.riskPct) || 1.0,
          pip_value_per_lot: 10.0,
          account_start_balance: Number(form.accountBalance) || 10000,
          sl_tp_same_candle_policy: form.slTpPolicy,
        }
      }
      if (mode === 'binary_options') {
        body.binary = {
          expiry_candles: Number(form.expiryCandles) || 1,
          payout_pct: Number(form.payoutPct),
          stake: Number(form.stake),
          tie_policy: form.tiePolicy,
          fees_per_trade: 0,
          account_start_balance: Number(form.accountBalance) || 1000,
        }
      }
      const run = await api.post<BacktestRun>('/api/v1/backtests', body)
      toast({ title: 'Backtest queued', description: `Run for ${run.symbol} ${run.timeframe}.` })
      onRunCreated(run)
    } catch (e) {
      toast({ title: 'Could not create the backtest run', description: errorText(e), variant: 'destructive' })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <FlaskConical className="h-4 w-4" aria-hidden /> Configure backtest
        </CardTitle>
        <CardDescription>
          Runs a saved, immutable strategy version over stored historical
          candles. Without trade assumptions the run reports setup
          evaluations only — the engine never invents trades or fills gaps.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {loadingStrategies ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading strategies…
          </div>
        ) : strategies.length === 0 ? (
          <Alert>
            <AlertDescription>
              No active {mode === 'forex' ? 'Forex' : 'Binary Options'} strategies
              found. Create and publish one in the Strategies tab first.
            </AlertDescription>
          </Alert>
        ) : null}

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="bt-strategy">Strategy</Label>
            <Select
              value={form.strategyId}
              onValueChange={(v) => update('strategyId', v)}
            >
              <SelectTrigger id="bt-strategy" aria-label="Strategy">
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
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="bt-version">Immutable version</Label>
            <Select
              value={form.version}
              onValueChange={(v) => update('version', v)}
              disabled={versions.length === 0}
            >
              <SelectTrigger id="bt-version" aria-label="Strategy version">
                <SelectValue placeholder="Select version" />
              </SelectTrigger>
              <SelectContent>
                {versions.map((v) => (
                  <SelectItem key={v.id} value={String(v.version)}>
                    v{v.version} · {v.status}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="bt-symbol">Symbol</Label>
            <Input
              id="bt-symbol"
              value={form.symbol}
              onChange={(e) => update('symbol', e.target.value.toUpperCase())}
              placeholder="EURUSD"
              maxLength={40}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="bt-timeframe">Timeframe (declared by the strategy)</Label>
            <Select
              value={form.timeframe}
              onValueChange={(v) => update('timeframe', v)}
              disabled={declaredTimeframes.length === 0}
            >
              <SelectTrigger id="bt-timeframe" aria-label="Timeframe">
                <SelectValue
                  placeholder={
                    declaredTimeframes.length ? 'Select timeframe' : 'Select a strategy first'
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {declaredTimeframes.map((tf) => (
                  <SelectItem key={tf} value={tf}>
                    {tf}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="bt-start">Start (UTC)</Label>
            <Input
              id="bt-start"
              type="datetime-local"
              value={form.start}
              onChange={(e) => update('start', e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="bt-end">End (UTC)</Label>
            <Input
              id="bt-end"
              type="datetime-local"
              value={form.end}
              onChange={(e) => update('end', e.target.value)}
            />
          </div>
        </div>

        {/* Mode-specific assumptions */}
        {mode === 'forex' ? (
          <div className="rounded-lg border p-3 space-y-3">
            <div className="flex items-center justify-between">
              <Label htmlFor="bt-simulate" className="text-sm">
                Simulate trades (spread / slippage / SL / TP)
              </Label>
              <Switch
                id="bt-simulate"
                checked={form.simulateTrades}
                onCheckedChange={(v) => update('simulateTrades', v)}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              Off: setup evaluations only (no trades are recorded). On: every
              cost below is a declared assumption persisted with the run.
            </p>
            {form.simulateTrades ? (
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="space-y-1.5">
                  <Label htmlFor="bt-spread">Spread (pips)</Label>
                  <Input id="bt-spread" type="number" min="0" step="0.1"
                    value={form.spreadPips}
                    onChange={(e) => update('spreadPips', e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="bt-slippage">Slippage (pips)</Label>
                  <Input id="bt-slippage" type="number" min="0" step="0.1"
                    value={form.slippagePips}
                    onChange={(e) => update('slippagePips', e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="bt-commission">Commission / lot / side</Label>
                  <Input id="bt-commission" type="number" min="0" step="0.1"
                    value={form.commission}
                    onChange={(e) => update('commission', e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="bt-sl">Stop-loss (pips)</Label>
                  <Input id="bt-sl" type="number" min="0" step="1"
                    value={form.stopLossPips}
                    onChange={(e) => update('stopLossPips', e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="bt-tp">Take-profit (pips)</Label>
                  <Input id="bt-tp" type="number" min="0" step="1"
                    value={form.takeProfitPips}
                    onChange={(e) => update('takeProfitPips', e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="bt-risk">Risk per trade (%)</Label>
                  <Input id="bt-risk" type="number" min="0.1" step="0.1"
                    value={form.riskPct}
                    onChange={(e) => update('riskPct', e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="bt-balance">Start balance</Label>
                  <Input id="bt-balance" type="number" min="1" step="1"
                    value={form.accountBalance}
                    onChange={(e) => update('accountBalance', e.target.value)} />
                </div>
                <div className="space-y-1.5 sm:col-span-2">
                  <Label>If one candle touches both SL and TP</Label>
                  <Select value={form.slTpPolicy}
                    onValueChange={(v) => update('slTpPolicy', v as BacktestFormState['slTpPolicy'])}>
                    <SelectTrigger aria-label="SL/TP same-candle policy">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="conservative">Assume stop (conservative)</SelectItem>
                      <SelectItem value="optimistic">Assume target (optimistic)</SelectItem>
                      <SelectItem value="ambiguous">Mark ambiguous (excluded)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            ) : null}
          </div>
        ) : (
          <div className="rounded-lg border p-3 space-y-3">
            <p className="text-xs text-muted-foreground">
              Binary options simulation: outcomes compare the signal candle
              close with the expiry candle close on stored MT5 candles.
              MT5 is NOT your broker&apos;s execution feed, and OTC feeds
              are never simulated.
            </p>
            <div className="grid gap-3 sm:grid-cols-4">
              <div className="space-y-1.5">
                <Label htmlFor="bt-expiry">Expiry (candles)</Label>
                <Input id="bt-expiry" type="number" min="1" step="1"
                  value={form.expiryCandles}
                  onChange={(e) => update('expiryCandles', e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="bt-payout">Payout (0–1)</Label>
                <Input id="bt-payout" type="number" min="0" max="1" step="0.05"
                  value={form.payoutPct}
                  onChange={(e) => update('payoutPct', e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="bt-stake">Stake</Label>
                <Input id="bt-stake" type="number" min="0.01" step="1"
                  value={form.stake}
                  onChange={(e) => update('stake', e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label>If entry equals expiry price</Label>
                <Select value={form.tiePolicy}
                  onValueChange={(v) => update('tiePolicy', v as BacktestFormState['tiePolicy'])}>
                  <SelectTrigger aria-label="Tie policy">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="refund">Refund stake</SelectItem>
                    <SelectItem value="loss">Count as loss</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>
        )}

        {/* Client-side validation */}
        {clientErrors.length > 0 ? (
          <Alert variant="destructive">
            <AlertDescription>
              <ul className="list-disc pl-4 space-y-1">
                {clientErrors.map((err) => (
                  <li key={err}>{err}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}

        {/* Data coverage */}
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={checkCoverage} disabled={checkingCoverage}>
            {checkingCoverage ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <CalendarRange className="h-4 w-4" aria-hidden />
            )}
            Check data coverage
          </Button>
          <Button size="sm" onClick={submit} disabled={submitting || loadingStrategies}>
            {submitting ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <Play className="h-4 w-4" aria-hidden />
            )}
            Start backtest
          </Button>
        </div>
        {coverageError ? (
          <Alert variant="destructive">
            <AlertDescription>{coverageError}</AlertDescription>
          </Alert>
        ) : null}
        {coverage ? (
          <Alert>
            <AlertDescription className="flex flex-wrap items-center gap-2 text-sm">
              <Badge variant="secondary">
                {coverage.candles_in_range.toLocaleString()} candles in range
              </Badge>
              <Badge variant="outline">coverage {fmtPct(coverage.coverage_ratio * 100)}</Badge>
              <span className="text-muted-foreground">
                {coverage.primary_timeframe} · checked {fmtUtc(coverage.checked_at)}
              </span>
            </AlertDescription>
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  )
}

export { toIsoLocal }
