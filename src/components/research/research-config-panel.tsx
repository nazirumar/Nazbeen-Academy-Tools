'use client'

/**
 * Research configuration panel (Phase 8).
 *
 * Lets the user pick an immutable strategy version, the EXPLICIT
 * parameters to vary (from the backend's parameter inventory — new rules
 * can never be invented), the objective, the validation workflow
 * (in-sample-only / holdout / walk-forward), execution assumptions and
 * cost scenarios. The backend cascade stays authoritative; the client
 * only gates obviously-too-large configurations with a visible warning.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { FlaskConical, Loader2, Play, Plus, Trash2 } from 'lucide-react'
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
  type DataCoverage,
  type PaginatedStrategies,
  type PlatformMode,
  type ResearchRun,
  type Strategy,
  type StrategyVersion,
} from '@/lib/api/types'
import {
  OBJECTIVE_OPTIONS,
  VALIDATION_MODE_META,
  estimateCombinations,
  estimateWalks,
  errorText,
  fieldLabel,
  fmtPct,
  fmtUtc,
} from './research-shared'

export interface ParameterDraft {
  conditionId: string
  field: string
  valuesText: string
}

export interface ResearchFormState {
  strategyId: string
  version: string
  symbol: string
  timeframe: string
  start: string
  end: string
  validationMode: 'in_sample_only' | 'holdout' | 'walk_forward'
  inSamplePct: string
  trainCandles: string
  testCandles: string
  stepCandles: string
  windowMode: 'rolling' | 'expanding'
  objective: string
  minDecidedTrades: string
  maxDrawdownPct: string
  simulateTrades: boolean
  spreadPips: string
  slippagePips: string
  commission: string
  stopLossPips: string
  takeProfitPips: string
  riskPct: string
  accountBalance: string
  expiryCandles: string
  payoutPct: string
  stake: string
  tiePolicy: 'refund' | 'loss'
  costVariantsText: string
  paramSensitivity: boolean
  runBootstrap: boolean
  seed: string
}

export function defaultFormState(mode: PlatformMode): ResearchFormState {
  return {
    strategyId: '',
    version: '',
    symbol: 'EURUSD',
    timeframe: mode === 'binary_options' ? 'M1' : '',
    start: '',
    end: '',
    validationMode: 'holdout',
    inSamplePct: '0.7',
    trainCandles: '200',
    testCandles: '100',
    stepCandles: '100',
    windowMode: 'rolling',
    objective: mode === 'binary_options' ? 'net_pnl' : 'conditions_met_count',
    minDecidedTrades: '',
    maxDrawdownPct: '',
    simulateTrades: false,
    spreadPips: '1.0',
    slippagePips: '0.5',
    commission: '0',
    stopLossPips: '20',
    takeProfitPips: '40',
    riskPct: '1.0',
    accountBalance: mode === 'binary_options' ? '1000' : '10000',
    expiryCandles: '1',
    payoutPct: '0.80',
    stake: '10',
    tiePolicy: 'refund',
    costVariantsText: '',
    paramSensitivity: true,
    runBootstrap: true,
    seed: '7',
  }
}

/** Parse a comma/newline separated value list, keeping value types. */
export function parseValues(text: string): (number | string)[] {
  const out: (number | string)[] = []
  for (const raw of text.split(/[,\n]/)) {
    const token = raw.trim()
    if (!token) continue
    const asNum = Number(token)
    out.push(Number.isFinite(asNum) && token !== '' ? asNum : token)
  }
  return out
}

interface Props {
  mode: PlatformMode
  onRunCreated: (run: ResearchRun) => void
}

export function ResearchConfigPanel({ mode, onRunCreated }: Props) {
  const [form, setForm] = useState<ResearchFormState>(() => defaultFormState(mode))
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [versions, setVersions] = useState<StrategyVersion[]>([])
  const [loadingStrategies, setLoadingStrategies] = useState(true)
  const [parameters, setParameters] = useState<ParameterDraft[]>([])
  const [inventory, setInventory] = useState<
    { condition_id: string; fields: { field: string; current: number | string | boolean; type: string }[] }[]
  >([])
  const [checkingCoverage, setCheckingCoverage] = useState(false)
  const [coverage, setCoverage] = useState<DataCoverage | null>(null)
  const [coverageError, setCoverageError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [clientErrors, setClientErrors] = useState<string[]>([])

  const update = useCallback(
    <K extends keyof ResearchFormState>(key: K, value: ResearchFormState[K]) => {
      setForm((prev) => ({ ...prev, [key]: value }))
    },
    [],
  )

  useEffect(() => {
    let active = true
    setLoadingStrategies(true)
    api
      .get<PaginatedStrategies>('/api/v1/strategies', { mode, status: 'active', page_size: '100' })
      .then((data) => {
        if (active) setStrategies(data.items)
      })
      .catch((e) => toast({ title: 'Could not load strategies', description: errorText(e), variant: 'destructive' }))
      .finally(() => {
        if (active) setLoadingStrategies(false)
      })
    return () => {
      active = false
    }
  }, [mode])

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
          published ? String(published.version) : immutable.length ? String(immutable[0].version) : '',
        )
      })
      .catch((e) => toast({ title: 'Could not load versions', description: errorText(e), variant: 'destructive' }))
    return () => {
      active = false
    }
  }, [form.strategyId, update])

  // Parameter inventory for the selected immutable version.
  useEffect(() => {
    setParameters([])
    setInventory([])
    if (!form.strategyId || !form.version) return
    let active = true
    api
      .get<{
        parameters: {
          condition_id: string;
          fields: { field: string; current: number | string | boolean; type: string }[];
        }[];
      }>(
        `/api/v1/research/optimization/parameter-schema?strategy_id=${form.strategyId}&strategy_version=${form.version}`,
      )
      .then((data) => {
        if (active) setInventory(data.parameters)
      })
      .catch(() => {
        // Non-fatal: the user can still type parameters manually.
      })
    return () => {
      active = false
    }
  }, [form.strategyId, form.version])

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

  const parsedParameters = useMemo(
    () =>
      parameters
        .map((p) => ({
          condition_id: p.conditionId,
          field: p.field,
          values: parseValues(p.valuesText),
        }))
        .filter((p) => p.values.length > 0),
    [parameters],
  )

  const combinations = estimateCombinations(parsedParameters)
  const costVariantCount = parseValues(form.costVariantsText).length
  const windowsEstimate =
    form.validationMode === 'walk_forward'
      ? Math.max(
          0,
          Math.floor(
            ((new Date(form.end || 0).getTime() - new Date(form.start || 0).getTime()) / 1000 -
              (Number(form.trainCandles) + Number(form.testCandles)) * 60) /
              (Math.max(Number(form.stepCandles), 1) * 60),
          ) + 1,
        )
      : 0
  const walksEstimate = estimateWalks({
    combinations,
    validationMode: form.validationMode,
    windows: windowsEstimate,
    costVariants: costVariantCount,
    paramCount: parsedParameters.length,
    paramSensitivity: form.paramSensitivity,
    bootstrap: form.runBootstrap,
  })
  const tooLarge = walksEstimate > 1500
  const tooManyCombinations = combinations > 200

  const validateLocal = (): string[] => {
    const errors: string[] = []
    if (!form.strategyId) errors.push('Select a strategy.')
    if (!form.version) errors.push('Select an immutable strategy version.')
    if (!form.symbol.trim()) errors.push('Enter a symbol.')
    if (!form.start || !form.end) errors.push('Set the historical date range.')
    if (form.start && form.end && new Date(form.start) >= new Date(form.end))
      errors.push('Start must be earlier than end.')
    if (parsedParameters.length === 0)
      errors.push('Select at least one parameter with at least one allowed value.')
    const objective = OBJECTIVE_OPTIONS.find((o) => o.value === form.objective)
    if (mode === 'forex' && objective?.needsTrades && !form.simulateTrades)
      errors.push(
        `Objective "${objective.label}" needs simulated trades — enable trade simulation or use the setup trigger count.`,
      )
    if (mode === 'forex' && form.simulateTrades) {
      const hasSl = form.stopLossPips !== '' && Number(form.stopLossPips) > 0
      const hasTp = form.takeProfitPips !== '' && Number(form.takeProfitPips) > 0
      if (hasSl !== hasTp) errors.push('Stop-loss and take-profit pips must be provided together.')
    }
    if (form.validationMode === 'walk_forward') {
      const train = Number(form.trainCandles)
      const test = Number(form.testCandles)
      const step = Number(form.stepCandles)
      if (!train || train < 20) errors.push('Walk-forward training window must be at least 20 candles.')
      if (!test || test < 5) errors.push('Walk-forward test window must be at least 5 candles.')
      if (step < test) errors.push('Walk-forward step must be >= the test window (no overlapping test periods).')
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
        timeframe: form.timeframe || 'M1',
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
        timeframe: form.timeframe || undefined,
        start: new Date(form.start + ':00Z').toISOString(),
        end: new Date(form.end + ':00Z').toISOString(),
        parameters: parsedParameters,
        objective: {
          metric: form.objective,
          constraints: {
            ...(form.minDecidedTrades ? { min_decided_trades: Number(form.minDecidedTrades) } : {}),
            ...(form.maxDrawdownPct ? { max_drawdown_pct: Number(form.maxDrawdownPct) } : {}),
          },
        },
        validation_mode: form.validationMode,
        param_sensitivity: form.paramSensitivity,
        run_bootstrap: form.runBootstrap,
        seed: Number(form.seed) || 7,
      }
      if (form.validationMode === 'holdout') {
        body.holdout = { in_sample_pct: Number(form.inSamplePct) || 0.7 }
      }
      if (form.validationMode === 'walk_forward') {
        body.walk_forward = {
          train_candles: Number(form.trainCandles),
          test_candles: Number(form.testCandles),
          step_candles: Number(form.stepCandles),
          window_mode: form.windowMode,
        }
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
          sl_tp_same_candle_policy: 'conservative',
        }
        const variants = parseValues(form.costVariantsText)
        if (variants.length > 0) {
          body.forex_cost_variants = variants.map((v) => ({
            commission_per_lot_per_side: typeof v === 'number' ? v : null,
          }))
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
        const variants = parseValues(form.costVariantsText)
        if (variants.length > 0) {
          body.binary_cost_variants = variants.map((v) => ({
            payout_pct: typeof v === 'number' ? v : null,
          }))
        }
      }
      const run = await api.post<ResearchRun>('/api/v1/research/optimization', body)
      toast({ title: 'Research run queued', description: `${run.validation_mode} over ${run.symbol} ${run.timeframe}.` })
      onRunCreated(run)
    } catch (e) {
      toast({ title: 'Could not create the research run', description: errorText(e), variant: 'destructive' })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <FlaskConical className="h-4 w-4" aria-hidden /> Configure research run
        </CardTitle>
        <CardDescription>
          Tests explicitly selected parameters of a saved, immutable strategy
          version. The optimizer never invents rules, never publishes a
          winner, and never guarantees profitability.
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
              No active {mode === 'forex' ? 'Forex' : 'Binary Options'} strategies found. Create
              and publish one in the Strategies tab first.
            </AlertDescription>
          </Alert>
        ) : null}

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="rs-strategy">Strategy</Label>
            <Select value={form.strategyId} onValueChange={(v) => update('strategyId', v)}>
              <SelectTrigger id="rs-strategy" aria-label="Strategy">
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
            <Label htmlFor="rs-version">Immutable version</Label>
            <Select value={form.version} onValueChange={(v) => update('version', v)} disabled={versions.length === 0}>
              <SelectTrigger id="rs-version" aria-label="Strategy version">
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
            <Label htmlFor="rs-symbol">Symbol</Label>
            <Input
              id="rs-symbol"
              value={form.symbol}
              onChange={(e) => update('symbol', e.target.value.toUpperCase())}
              placeholder="EURUSD"
              maxLength={40}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="rs-timeframe">Timeframe (declared by the strategy)</Label>
            <Select
              value={form.timeframe}
              onValueChange={(v) => update('timeframe', v)}
              disabled={declaredTimeframes.length === 0}
            >
              <SelectTrigger id="rs-timeframe" aria-label="Timeframe">
                <SelectValue
                  placeholder={declaredTimeframes.length ? 'Select timeframe' : 'Select a strategy first'}
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
            <Label htmlFor="rs-start">Start (UTC)</Label>
            <Input id="rs-start" type="datetime-local" value={form.start} onChange={(e) => update('start', e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="rs-end">End (UTC)</Label>
            <Input id="rs-end" type="datetime-local" value={form.end} onChange={(e) => update('end', e.target.value)} />
          </div>
        </div>

        {/* Parameters */}
        <div className="rounded-lg border p-3 space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <Label className="text-sm">Parameter search space</Label>
              <p className="text-xs text-muted-foreground">
                Only fields the strategy already declares can vary (lookback or an existing
                parameter). Comma-separate allowed values.
              </p>
            </div>
          </div>
          {inventory.length > 0 ? (
            <div className="space-y-1.5">
              <Label htmlFor="rs-addparam" className="text-xs text-muted-foreground">
                Add from inventory:
              </Label>
              <Select
                value=""
                onValueChange={(key) => {
                  const [conditionId, field] = key.split('::')
                  if (!parameters.some((p) => p.conditionId === conditionId && p.field === field)) {
                    setParameters((prev) => [...prev, { conditionId, field, valuesText: '' }])
                  }
                }}
              >
                <SelectTrigger id="rs-addparam" aria-label="Add parameter">
                  <SelectValue placeholder="Add a parameter…" />
                </SelectTrigger>
                <SelectContent>
                  {inventory.flatMap((c) =>
                    c.fields
                      .filter((f) => !parameters.some((p) => p.conditionId === c.condition_id && p.field === f.field))
                      .map((f) => (
                        <SelectItem key={`${c.condition_id}::${f.field}`} value={`${c.condition_id}::${f.field}`}>
                          {fieldLabel(c.condition_id, f.field)} (current: {String(f.current)})
                        </SelectItem>
                      )),
                  )}
                </SelectContent>
              </Select>
            </div>
          ) : null}
          {parameters.map((p, idx) => (
            <div key={`${p.conditionId}.${p.field}`} className="grid gap-2 sm:grid-cols-[1fr_2fr_auto] sm:items-end">
              <div className="space-y-1">
                <Label className="text-xs">{fieldLabel(p.conditionId, p.field)}</Label>
                <p className="text-xs text-muted-foreground">existing field</p>
              </div>
              <Input
                aria-label={`Allowed values for ${fieldLabel(p.conditionId, p.field)}`}
                placeholder="e.g. 10, 20, 40"
                value={p.valuesText}
                onChange={(e) =>
                  setParameters((prev) => prev.map((x, i) => (i === idx ? { ...x, valuesText: e.target.value } : x)))
                }
              />
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Remove parameter ${fieldLabel(p.conditionId, p.field)}`}
                onClick={() => setParameters((prev) => prev.filter((_, i) => i !== idx))}
              >
                <Trash2 className="h-4 w-4" aria-hidden />
              </Button>
            </div>
          ))}
          {parsedParameters.length > 0 ? (
            <div className="flex flex-wrap gap-2 text-xs">
              <Badge variant="secondary">{parsedParameters.length} parameter(s)</Badge>
              <Badge variant="outline">{combinations} candidate(s) incl. the original</Badge>
            </div>
          ) : null}
        </div>

        {/* Objective */}
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="space-y-1.5 sm:col-span-1">
            <Label htmlFor="rs-objective">Optimization objective</Label>
            <Select value={form.objective} onValueChange={(v) => update('objective', v)}>
              <SelectTrigger id="rs-objective" aria-label="Objective">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {OBJECTIVE_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              {OBJECTIVE_OPTIONS.find((o) => o.value === form.objective)?.hint}
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="rs-mintrades">Min decided trades (constraint)</Label>
            <Input
              id="rs-mintrades"
              type="number"
              min="0"
              step="1"
              placeholder="none"
              value={form.minDecidedTrades}
              onChange={(e) => update('minDecidedTrades', e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="rs-maxdd">Max drawdown % (constraint)</Label>
            <Input
              id="rs-maxdd"
              type="number"
              min="0"
              step="0.5"
              placeholder="none"
              value={form.maxDrawdownPct}
              onChange={(e) => update('maxDrawdownPct', e.target.value)}
            />
          </div>
        </div>

        {/* Validation workflow */}
        <div className="rounded-lg border p-3 space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="rs-validation">Validation workflow</Label>
            <Select
              value={form.validationMode}
              onValueChange={(v) => update('validationMode', v as ResearchFormState['validationMode'])}
            >
              <SelectTrigger id="rs-validation" aria-label="Validation mode">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(VALIDATION_MODE_META) as ResearchFormState['validationMode'][]).map((m) => (
                  <SelectItem key={m} value={m}>
                    {VALIDATION_MODE_META[m].label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">{VALIDATION_MODE_META[form.validationMode].description}</p>
          </div>
          {form.validationMode === 'holdout' ? (
            <div className="space-y-1.5 sm:max-w-xs">
              <Label htmlFor="rs-ispct">In-sample share (0.5–0.9)</Label>
              <Input
                id="rs-ispct"
                type="number"
                min="0.5"
                max="0.9"
                step="0.05"
                value={form.inSamplePct}
                onChange={(e) => update('inSamplePct', e.target.value)}
              />
            </div>
          ) : null}
          {form.validationMode === 'walk_forward' ? (
            <div className="grid gap-3 sm:grid-cols-4">
              <div className="space-y-1.5">
                <Label htmlFor="rs-train">Train (candles)</Label>
                <Input id="rs-train" type="number" min="20" step="10" value={form.trainCandles} onChange={(e) => update('trainCandles', e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="rs-test">Test (candles)</Label>
                <Input id="rs-test" type="number" min="5" step="10" value={form.testCandles} onChange={(e) => update('testCandles', e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="rs-step">Step (candles)</Label>
                <Input id="rs-step" type="number" min="5" step="10" value={form.stepCandles} onChange={(e) => update('stepCandles', e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label>Window mode</Label>
                <Select value={form.windowMode} onValueChange={(v) => update('windowMode', v as ResearchFormState['windowMode'])}>
                  <SelectTrigger aria-label="Window mode">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="rolling">Rolling</SelectItem>
                    <SelectItem value="expanding">Expanding</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          ) : null}
        </div>

        {/* Execution assumptions + cost variants */}
        {mode === 'forex' ? (
          <div className="rounded-lg border p-3 space-y-3">
            <div className="flex items-center justify-between">
              <Label htmlFor="rs-simulate" className="text-sm">
                Simulate trades (spread / slippage / SL / TP)
              </Label>
              <Switch id="rs-simulate" checked={form.simulateTrades} onCheckedChange={(v) => update('simulateTrades', v)} />
            </div>
            {form.simulateTrades ? (
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="space-y-1.5">
                  <Label htmlFor="rs-spread">Spread (pips)</Label>
                  <Input id="rs-spread" type="number" min="0" step="0.1" value={form.spreadPips} onChange={(e) => update('spreadPips', e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="rs-slippage">Slippage (pips)</Label>
                  <Input id="rs-slippage" type="number" min="0" step="0.1" value={form.slippagePips} onChange={(e) => update('slippagePips', e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="rs-commission">Commission / lot / side</Label>
                  <Input id="rs-commission" type="number" min="0" step="0.1" value={form.commission} onChange={(e) => update('commission', e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="rs-sl">Stop-loss (pips)</Label>
                  <Input id="rs-sl" type="number" min="0" step="1" value={form.stopLossPips} onChange={(e) => update('stopLossPips', e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="rs-tp">Take-profit (pips)</Label>
                  <Input id="rs-tp" type="number" min="0" step="1" value={form.takeProfitPips} onChange={(e) => update('takeProfitPips', e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="rs-costvar">Cost scenarios: commission values</Label>
                  <Input
                    id="rs-costvar"
                    placeholder="e.g. 2, 5, 10"
                    value={form.costVariantsText}
                    onChange={(e) => update('costVariantsText', e.target.value)}
                  />
                </div>
              </div>
            ) : null}
          </div>
        ) : (
          <div className="rounded-lg border p-3 space-y-3">
            <p className="text-xs text-muted-foreground">
              Binary simulation: outcomes compare the signal close with the expiry close on stored
              MT5 candles. MT5 is NOT your broker&apos;s execution feed; OTC feeds are never simulated.
            </p>
            <div className="grid gap-3 sm:grid-cols-5">
              <div className="space-y-1.5">
                <Label htmlFor="rs-expiry">Expiry (candles)</Label>
                <Input id="rs-expiry" type="number" min="1" step="1" value={form.expiryCandles} onChange={(e) => update('expiryCandles', e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="rs-payout">Payout (0–1)</Label>
                <Input id="rs-payout" type="number" min="0" max="1" step="0.05" value={form.payoutPct} onChange={(e) => update('payoutPct', e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="rs-stake">Stake</Label>
                <Input id="rs-stake" type="number" min="0.01" step="1" value={form.stake} onChange={(e) => update('stake', e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label>If entry equals expiry</Label>
                <Select value={form.tiePolicy} onValueChange={(v) => update('tiePolicy', v as ResearchFormState['tiePolicy'])}>
                  <SelectTrigger aria-label="Tie policy">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="refund">Refund stake</SelectItem>
                    <SelectItem value="loss">Count as loss</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="rs-bocostvar">Payout scenarios</Label>
                <Input
                  id="rs-bocostvar"
                  placeholder="e.g. 0.9, 0.6"
                  value={form.costVariantsText}
                  onChange={(e) => update('costVariantsText', e.target.value)}
                />
              </div>
            </div>
          </div>
        )}

        {/* Robustness toggles + seed */}
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="flex items-center justify-between rounded-lg border p-3">
            <Label htmlFor="rs-paramsens" className="text-sm">
              Parameter sensitivity scenarios
            </Label>
            <Switch id="rs-paramsens" checked={form.paramSensitivity} onCheckedChange={(v) => update('paramSensitivity', v)} />
          </div>
          <div className="flex items-center justify-between rounded-lg border p-3">
            <Label htmlFor="rs-bootstrap" className="text-sm">
              Seeded bootstrap stress test
            </Label>
            <Switch id="rs-bootstrap" checked={form.runBootstrap} onCheckedChange={(v) => update('runBootstrap', v)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="rs-seed">Random seed (bootstrap)</Label>
            <Input id="rs-seed" type="number" min="0" step="1" value={form.seed} onChange={(e) => update('seed', e.target.value)} />
          </div>
        </div>

        {/* Size / history warnings */}
        {(tooLarge || tooManyCombinations) && (
          <Alert variant="destructive">
            <AlertDescription>
              This configuration is too large for the platform limits
              {tooManyCombinations ? ` — ${combinations} combinations exceed the 200 limit` : ''}
              {tooLarge ? ` — about ${walksEstimate} engine walks exceed the 1500 budget` : ''}. Narrow
              the value lists, windows or cost scenarios.
            </AlertDescription>
          </Alert>
        )}
        {form.validationMode === 'walk_forward' && windowsEstimate > 0 && windowsEstimate < 2 ? (
          <Alert variant="destructive">
            <AlertDescription>
              The configured period holds fewer than 2 walk-forward windows — extend the date range or
              shrink the windows.
            </AlertDescription>
          </Alert>
        ) : null}
        {form.objective === 'win_rate' ? (
          <Alert>
            <AlertDescription>
              Ranking on win rate alone ignores risk and reward sizes — prefer expectancy or net P&L
              when trades exist.
            </AlertDescription>
          </Alert>
        ) : null}

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

        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={checkCoverage} disabled={checkingCoverage}>
            {checkingCoverage ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <Plus className="h-4 w-4" aria-hidden />
            )}
            Check data coverage
          </Button>
          <Button size="sm" onClick={submit} disabled={submitting || loadingStrategies || tooLarge || tooManyCombinations}>
            {submitting ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <Play className="h-4 w-4" aria-hidden />
            )}
            Start research run
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
              <Badge variant="secondary">{coverage.candles_in_range.toLocaleString()} candles in range</Badge>
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
