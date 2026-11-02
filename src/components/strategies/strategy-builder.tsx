'use client'

/**
 * Strategy Builder (Phase 3) — professional editor for user-defined
 * strategies.
 *
 * Design rules:
 * - Everything is persisted through the backend; nothing shown as "saved"
 *   is only local state. Unsaved edits are guarded (beforeunload + in-app
 *   confirmation) and never lost silently.
 * - Rule types come from GET /api/v1/rule-types. Planned (non-operational)
 *   types are visible but disabled and clearly badged — the UI never
 *   pretends an unsupported rule works.
 * - Parameter forms are rendered from the registry's params_schema, so new
 *   rule types appear in the editor without frontend changes.
 * - Validation errors from the backend are rendered next to the offending
 *   field via their precise paths.
 * - Publishing shows a preview of the COMPLETE definition first and stores
 *   an immutable version; previous published versions become superseded.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeft,
  Archive,
  Braces,
  ChevronDown,
  ChevronUp,
  Copy,
  FileWarning,
  Layers,
  Loader2,
  Plus,
  Save,
  ShieldCheck,
  ShieldX,
  Trash2,
  X,
} from 'lucide-react'
import { toast } from '@/hooks/use-toast'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { api } from '@/lib/api/client'
import { MODE_LABELS, timeframesForMode } from '@/lib/platform/modes'
import {
  ApiError,
  type Category,
  type MarketRegime,
  type PlatformMode,
  type RuleCondition,
  type RuleGroup,
  type RuleSet,
  type RuleSetValidation,
  type RuleTypeSpec,
  type StrategyDetail,
  type StrategyDirection,
  type StrategyVersion,
} from '@/lib/api/types'

const MARKET_REGIMES: MarketRegime[] = ['any', 'trending', 'ranging', 'volatile', 'quiet']

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = e.details.length
      ? ` (${e.details.map((d) => `${d.path || ''} ${d.message ?? ''}`).join('; ')})`
      : ''
    return `${e.message}${detail}${e.requestId ? ` [${e.requestId}]` : ''}`
  }
  return e instanceof Error ? e.message : 'Unexpected error'
}

let conditionCounter = 0
function nextConditionId(): string {
  conditionCounter += 1
  return `rule_${conditionCounter}`
}

function emptyGroup(logic: 'AND' | 'OR' = 'AND'): RuleGroup {
  return { logic, conditions: [], groups: [] }
}

function defaultRuleSet(mode: PlatformMode, direction: StrategyDirection): RuleSet {
  return {
    schema_version: '2.0',
    mode,
    timeframes: mode === 'binary_options' ? ['M1'] : ['H1'],
    direction,
    conditions: emptyGroup('AND'),
    confirmation: null,
    invalidation: null,
    symbols: [],
    market_regime: 'any',
    evaluation: { on_close_only: true },
  }
}

/** Errors whose path belongs to a subtree (used for inline placement). */
function errorsForPrefix(errors: RuleSetValidation['errors'], prefix: string) {
  return errors.filter((e) => e.path === prefix || e.path.startsWith(`${prefix}.`))
}

interface StrategyBuilderProps {
  strategyId: string
  mode: PlatformMode
  categories: Category[]
  onCategoriesChanged: () => Promise<void> | void
  onClose: () => void
  onSaved: () => Promise<void> | void
}

export function StrategyBuilder({
  strategyId,
  mode,
  categories,
  onCategoriesChanged,
  onClose,
  onSaved,
}: StrategyBuilderProps) {
  const [loading, setLoading] = useState(true)
  const [strategy, setStrategy] = useState<StrategyDetail | null>(null)
  const [versions, setVersions] = useState<StrategyVersion[]>([])
  const [ruleTypes, setRuleTypes] = useState<RuleTypeSpec[]>([])

  // Editable state -----------------------------------------------------------
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [direction, setDirection] = useState<StrategyDirection>('bullish')
  const [ruleSet, setRuleSet] = useState<RuleSet | null>(null)
  const [changelog, setChangelog] = useState('')
  const [errors, setErrors] = useState<RuleSetValidation['errors']>([])

  // Workflow state -----------------------------------------------------------
  const [busy, setBusy] = useState<string | null>(null)
  const [jsonMode, setJsonMode] = useState<'visual' | 'json'>('visual')
  const [jsonText, setJsonText] = useState('')
  const [previewOpen, setPreviewOpen] = useState(false)
  const [leaveConfirmOpen, setLeaveConfirmOpen] = useState(false)
  const baselineRef = useRef<string>('')

  const dirty = useMemo(() => {
    if (!ruleSet) return false
    return (
      JSON.stringify({
        name,
        description,
        direction,
        categoryId: strategy?.category_id ?? null,
        ruleSet,
      }) !== baselineRef.current
    )
  }, [name, description, direction, ruleSet, strategy?.category_id])

  // ---- unsaved-changes guard ------------------------------------------------
  useEffect(() => {
    if (!dirty) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])

  const requestClose = useCallback(() => {
    if (dirty) setLeaveConfirmOpen(true)
    else onClose()
  }, [dirty, onClose])

  // ---- data loading ----------------------------------------------------------
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      try {
        const [detail, vers, types] = await Promise.all([
          api.get<StrategyDetail>(`/api/v1/strategies/${strategyId}`),
          api.get<StrategyVersion[]>(`/api/v1/strategies/${strategyId}/versions`),
          api.get<{ rule_types: RuleTypeSpec[] }>('/api/v1/rule-types'),
        ])
        if (cancelled) return
        setStrategy(detail)
        setVersions(vers)
        setRuleTypes(types.rule_types)
        setName(detail.name)
        setDescription(detail.description ?? '')
        setDirection(detail.direction)
        const rs: RuleSet = detail.rule_set
          ? {
              ...defaultRuleSet(detail.mode, detail.direction),
              ...detail.rule_set,
            }
          : defaultRuleSet(detail.mode, detail.direction)
        setRuleSet(rs)
        setJsonText(JSON.stringify(rs, null, 2))
        baselineRef.current = JSON.stringify({
          name: detail.name,
          description: detail.description ?? '',
          direction: detail.direction,
          categoryId: detail.category_id,
          ruleSet: rs,
        })
      } catch (e) {
        toast({ title: 'Could not load strategy', description: errorText(e), variant: 'destructive' })
        onClose()
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
     
  }, [strategyId])

  const typeBySlug = useMemo(() => {
    const map = new Map<string, RuleTypeSpec>()
    for (const t of ruleTypes) map.set(t.type, t)
    return map
  }, [ruleTypes])

  const availableTypes = useMemo(
    () => ruleTypes.filter((t) => t.available),
    [ruleTypes],
  )
  const plannedTypes = useMemo(
    () => ruleTypes.filter((t) => !t.available),
    [ruleTypes],
  )

  // ---- mutations --------------------------------------------------------------

  function updateRuleSet(updater: (rs: RuleSet) => RuleSet) {
    setRuleSet((prev) => {
      if (!prev) return prev
      const next = updater(structuredClone(prev))
      setJsonText(JSON.stringify(next, null, 2))
      return next
    })
  }

  function setGroupAt(
    root: RuleSet,
    path: string,
    updater: (g: RuleGroup) => RuleGroup,
  ): RuleSet {
    // path examples: ".conditions", ".confirmation", ".invalidation",
    // ".conditions.groups[0]"
    const clone = structuredClone(root)
    const segments: (string | number)[] = []
    const regex = /\.([a-zA-Z_]+)(?:\[(\d+)\])?/g
    let m: RegExpExecArray | null
    while ((m = regex.exec(path)) !== null) {
      segments.push(m[1])
      if (m[2] !== undefined) segments.push(Number(m[2]))
    }
     
    let node: any = clone
    for (let i = 0; i < segments.length - 1; i++) node = node[segments[i]]
    const last = segments[segments.length - 1]
    node[last] = updater(node[last] as RuleGroup)
    return clone
  }

  const groupedErrors = useCallback(
    (prefix: string) => errorsForPrefix(errors, prefix),
    [errors],
  )

  async function handleValidate() {
    if (!ruleSet) return
    let payload: unknown = ruleSet
    if (jsonMode === 'json') {
      try {
        payload = JSON.parse(jsonText)
      } catch (e) {
        toast({
          title: 'JSON syntax error',
          description: (e as Error).message,
          variant: 'destructive',
        })
        return
      }
    }
    setBusy('validate')
    try {
      const res = await api.post<RuleSetValidation>('/api/v1/rule-sets/validate', {
        rule_set: payload,
      })
      if (res.valid) {
        setErrors([])
        toast({ title: 'Rule set is valid', description: `Schema ${res.normalized?.schema_version ?? '?'}` })
        if (jsonMode === 'json' && res.normalized) {
          setRuleSet(res.normalized)
          setJsonText(JSON.stringify(res.normalized, null, 2))
        }
      } else {
        setErrors(res.errors)
        toast({
          title: `Invalid: ${res.errors.length} error(s)`,
          description: res.errors.slice(0, 3).map((er) => `${er.path}: ${er.message}`).join('\n'),
          variant: 'destructive',
        })
      }
    } catch (e) {
      toast({ title: 'Validation failed', description: errorText(e), variant: 'destructive' })
    } finally {
      setBusy(null)
    }
  }

  async function saveDraft(): Promise<number | null> {
    if (!strategy || !ruleSet) return null
    let payload: RuleSet = ruleSet
    if (jsonMode === 'json') {
      try {
        payload = JSON.parse(jsonText)
      } catch (e) {
        toast({
          title: 'JSON syntax error',
          description: (e as Error).message,
          variant: 'destructive',
        })
        return null
      }
    }
    setBusy('save')
    try {
      // Metadata first (name/description/direction/category may have changed).
      const patchRes = await api.patch<StrategyDetail>(`/api/v1/strategies/${strategy.id}`, {
        name: name.trim() || strategy.name,
        description: description.trim() || null,
        direction,
        category_id: strategy.category_id ?? null,
      })
      const version = await api.post<StrategyVersion>(
        `/api/v1/strategies/${strategy.id}/versions`,
        { rule_set: payload, changelog: changelog.trim() || null },
      )
      setStrategy({ ...patchRes, rule_set: version.rule_set })
      setErrors([])
      setChangelog('')
      const vers = await api.get<StrategyVersion[]>(`/api/v1/strategies/${strategy.id}/versions`)
      setVersions(vers)
      baselineRef.current = JSON.stringify({
        name,
        description,
        direction,
        categoryId: patchRes.category_id,
        ruleSet: payload,
      })
      toast({
        title: `Draft v${version.version} saved`,
        description: 'Previous versions remain untouched.',
      })
      await onSaved()
      return version.version
    } catch (e) {
      const err = e as ApiError
      if (err.details?.length) setErrors(err.details as RuleSetValidation['errors'])
      toast({ title: 'Could not save draft', description: errorText(e), variant: 'destructive' })
      return null
    } finally {
      setBusy(null)
    }
  }

  async function handlePublish() {
    if (!strategy) return
    setPreviewOpen(true)
  }

  async function confirmPublish() {
    if (!strategy || !ruleSet) return
    setPreviewOpen(false)
    setBusy('publish')
    try {
      // Publishing persists the current definition as a draft version first.
      const savedVersion = dirty || strategy.current_version === 0 ? await saveDraft() : strategy.current_version
      if (savedVersion === null) return
      const res = await api.post<{ version: StrategyVersion; strategy: StrategyDetail; summary: string[] }>(
        `/api/v1/strategies/${strategy.id}/versions/${savedVersion}/publish`,
      )
      setStrategy({ ...res.strategy, rule_set: res.version.rule_set, summary: res.summary })
      const vers = await api.get<StrategyVersion[]>(`/api/v1/strategies/${strategy.id}/versions`)
      setVersions(vers)
      toast({
        title: `Version ${savedVersion} published`,
        description: 'The definition is now immutable and safe to evaluate later.',
      })
      await onSaved()
    } catch (e) {
      toast({ title: 'Publish failed', description: errorText(e), variant: 'destructive' })
    } finally {
      setBusy(null)
    }
  }

  async function handleArchive() {
    if (!strategy) return
    setBusy('archive')
    try {
      await api.post(`/api/v1/strategies/${strategy.id}/archive`)
      toast({ title: 'Strategy archived', description: name })
      await onSaved()
      onClose()
    } catch (e) {
      toast({ title: 'Could not archive', description: errorText(e), variant: 'destructive' })
    } finally {
      setBusy(null)
    }
  }

  async function handleDuplicate() {
    if (!strategy) return
    setBusy('duplicate')
    try {
      await api.post(`/api/v1/strategies/${strategy.id}/duplicate`, {})
      toast({ title: 'Duplicated', description: `Copy of ${name}` })
      await onSaved()
      onClose()
    } catch (e) {
      toast({ title: 'Could not duplicate', description: errorText(e), variant: 'destructive' })
    } finally {
      setBusy(null)
    }
  }

  // ---- derived -----------------------------------------------------------------

  const roots = categories.filter((c) => !c.parent_id && !c.is_archived)
  const selectedCategory = categories.find((c) => c.id === strategy?.category_id)
  const selectedRoot = selectedCategory?.parent_id
    ? categories.find((c) => c.id === selectedCategory.parent_id)
    : selectedCategory
  const subcategories = selectedRoot?.children?.filter((c) => !c.is_archived) ?? []

  if (loading || !strategy || !ruleSet) {
    return (
      <div className="flex items-center gap-2 py-16 text-sm text-muted-foreground" role="status">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading strategy builder…
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Header ---------------------------------------------------------- */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Button variant="outline" size="sm" onClick={requestClose} aria-label="Back to strategy library">
            <ArrowLeft className="h-4 w-4" aria-hidden /> Library
          </Button>
          <h2 className="truncate text-lg font-semibold">Strategy Builder</h2>
          <Badge variant="secondary" className="font-mono">
            v{strategy.current_version || 0}
          </Badge>
          <StatusBadges strategy={strategy} />
        </div>
        <div className="flex items-center gap-2">
          <Tabs value={jsonMode} onValueChange={(v) => setJsonMode(v as 'visual' | 'json')}>
            <TabsList className="h-8">
              <TabsTrigger value="visual" className="gap-1 px-2 text-xs">
                <Layers className="h-3.5 w-3.5" aria-hidden /> Visual
              </TabsTrigger>
              <TabsTrigger value="json" className="gap-1 px-2 text-xs">
                <Braces className="h-3.5 w-3.5" aria-hidden /> JSON
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
      </div>

      {dirty && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
          You have unsaved changes. Save a draft version to keep them.
        </div>
      )}

      {/* Identity ---------------------------------------------------------- */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Strategy</CardTitle>
          <CardDescription>
            {MODE_LABELS[mode]} mode · direction, organisation and documentation
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="sb-name">Name</Label>
            <Input
              id="sb-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. M1 pullback continuation"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="sb-direction">Direction</Label>
            <Select
              value={direction}
              onValueChange={(v) => setDirection(v as StrategyDirection)}
            >
              <SelectTrigger id="sb-direction" aria-label="Direction">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="bullish">Bullish</SelectItem>
                <SelectItem value="bearish">Bearish</SelectItem>
                <SelectItem value="both">Both directions</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label>Category</Label>
            <CategorySelect
              categories={roots}
              value={strategy.category_id}
              mode={mode}
              onChanged={async () => {
                await onCategoriesChanged()
              }}
              onSelected={(categoryId) => {
                setStrategy((prev) => (prev ? { ...prev, category_id: categoryId } : prev))
              }}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Subcategory</Label>
            <SubcategorySelect
              parent={selectedRoot ?? null}
              subcategories={subcategories}
              value={strategy.category_id}
              disabled={!selectedRoot}
              onChanged={onCategoriesChanged}
              onSelected={(categoryId) => {
                setStrategy((prev) => (prev ? { ...prev, category_id: categoryId } : prev))
              }}
            />
          </div>
          <div className="space-y-1.5 md:col-span-2">
            <Label htmlFor="sb-desc">Description / intent</Label>
            <Textarea
              id="sb-desc"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What is the idea behind this setup? When should it be used?"
            />
          </div>
        </CardContent>
      </Card>

      {/* Scope ------------------------------------------------------------- */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Scope &amp; evaluation</CardTitle>
          <CardDescription>
            Where and how this strategy applies. Binary Options defaults to M1 —
            higher timeframes are only used when you explicitly enable them.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1.5">
            <Label>Timeframes (required data)</Label>
            <div className="flex flex-wrap gap-2" role="group" aria-label="Timeframes">
              {timeframesForMode(mode).map((tf) => {
                const checked = ruleSet.timeframes.includes(tf)
                return (
                  <label
                    key={tf}
                    className={`flex cursor-pointer items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
                      checked
                        ? 'border-primary bg-primary/10 text-primary'
                        : 'bg-muted/40 text-muted-foreground hover:bg-muted'
                    }`}
                  >
                    <Checkbox
                      checked={checked}
                      onCheckedChange={() => {
                        updateRuleSet((rs) => ({
                          ...rs,
                          timeframes: checked
                            ? rs.timeframes.filter((t) => t !== tf)
                            : [...rs.timeframes, tf],
                        }))
                      }}
                      aria-label={`Timeframe ${tf}`}
                    />
                    {tf}
                    {mode === 'binary_options' && tf === 'M1' && (
                      <span className="text-[10px] uppercase text-muted-foreground">default</span>
                    )}
                  </label>
                )
              })}
            </div>
            {groupedErrors('.timeframes').map((e, i) => (
              <p key={i} className="text-xs text-destructive">{e.message}</p>
            ))}
          </div>
          <SymbolsInput
            symbols={ruleSet.symbols ?? []}
            onChange={(symbols) => updateRuleSet((rs) => ({ ...rs, symbols }))}
            errors={groupedErrors('.symbols')}
          />
          <div className="space-y-1.5">
            <Label htmlFor="sb-regime">Market regime (optional)</Label>
            <Select
              value={ruleSet.market_regime ?? 'any'}
              onValueChange={(v) =>
                updateRuleSet((rs) => ({ ...rs, market_regime: v as MarketRegime }))
              }
            >
              <SelectTrigger id="sb-regime" aria-label="Market regime">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MARKET_REGIMES.map((r) => (
                  <SelectItem key={r} value={r}>
                    {r === 'any' ? 'Any regime' : r[0].toUpperCase() + r.slice(1)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
            <div className="min-w-0">
              <Label htmlFor="sb-onclose" className="text-sm">Closed candles only</Label>
              <p className="text-xs text-muted-foreground">
                Declare that this strategy should be evaluated on closed candles.
              </p>
            </div>
            <Switch
              id="sb-onclose"
              checked={ruleSet.evaluation?.on_close_only ?? false}
              onCheckedChange={(v) =>
                updateRuleSet((rs) => ({ ...rs, evaluation: { ...rs.evaluation, on_close_only: v } }))
              }
            />
          </div>
        </CardContent>
      </Card>

      {/* Editor body -------------------------------------------------------- */}
      {jsonMode === 'visual' ? (
        <>
          <RuleSectionCard
            title="Core rules"
            description="The main condition tree. AND = every required rule must hold; OR = at least one. Optional rules never block."
            path=".conditions"
            ruleSet={ruleSet}
            setRuleSet={updateRuleSet}
            setGroupAt={setGroupAt}
            timeframes={ruleSet.timeframes}
            availableTypes={availableTypes}
            plannedTypes={plannedTypes}
            typeBySlug={typeBySlug}
            errors={errors}
          />
          <div className="grid gap-4 lg:grid-cols-2">
            <RuleSectionCard
              title="Confirmation (optional)"
              description="Extra rules that must ALSO hold for a setup to be confirmed."
              path=".confirmation"
              ruleSet={ruleSet}
              setRuleSet={updateRuleSet}
              setGroupAt={setGroupAt}
              timeframes={ruleSet.timeframes}
              availableTypes={availableTypes}
              plannedTypes={plannedTypes}
              typeBySlug={typeBySlug}
              errors={errors}
              clearable
            />
            <RuleSectionCard
              title="Invalidation (optional)"
              description="If these rules hold, the setup is invalid — regardless of the core rules."
              path=".invalidation"
              ruleSet={ruleSet}
              setRuleSet={updateRuleSet}
              setGroupAt={setGroupAt}
              timeframes={ruleSet.timeframes}
              availableTypes={availableTypes}
              plannedTypes={plannedTypes}
              typeBySlug={typeBySlug}
              errors={errors}
              clearable
            />
          </div>
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Notes</CardTitle>
              <CardDescription>Explain the intent of this strategy for your future self.</CardDescription>
            </CardHeader>
            <CardContent>
              <Textarea
                rows={3}
                value={ruleSet.notes ?? ''}
                onChange={(e) => updateRuleSet((rs) => ({ ...rs, notes: e.target.value || null }))}
                placeholder="Why does this setup work? What are its failure modes?"
                aria-label="Strategy notes"
              />
              {groupedErrors('.notes').map((e, i) => (
                <p key={i} className="mt-1 text-xs text-destructive">{e.message}</p>
              ))}
            </CardContent>
          </Card>
        </>
      ) : (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Rule set JSON (advanced)</CardTitle>
            <CardDescription>
              Schema 2.0 — validated against the server whitelist. Use Validate to check before saving.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Textarea
              value={jsonText}
              onChange={(e) => setJsonText(e.target.value)}
              spellCheck={false}
              className="min-h-[360px] font-mono text-xs"
              aria-label="Rule set JSON"
            />
          </CardContent>
        </Card>
      )}

      {/* Global errors ------------------------------------------------------ */}
      {errors.length > 0 && (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs" role="alert">
          <p className="mb-1 flex items-center gap-1.5 font-medium text-destructive">
            <ShieldX className="h-3.5 w-3.5" aria-hidden /> {errors.length} validation error(s)
          </p>
          <ul className="list-inside list-disc space-y-0.5 text-muted-foreground">
            {errors.slice(0, 8).map((er, i) => (
              <li key={i}>
                <code className="rounded bg-muted px-1">{er.path}</code> {er.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Actions + versions -------------------------------------------------- */}
      <Card>
        <CardContent className="flex flex-wrap items-center gap-2 pt-4">
          <Input
            value={changelog}
            onChange={(e) => setChangelog(e.target.value)}
            placeholder="Changelog for this version (optional)"
            aria-label="Changelog"
            className="min-w-48 flex-1"
          />
          <Button variant="outline" onClick={() => void handleValidate()} disabled={busy !== null}>
            {busy === 'validate' ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <ShieldCheck className="h-4 w-4" aria-hidden />
            )}
            Validate
          </Button>
          <Button onClick={() => void saveDraft()} disabled={busy !== null}>
            {busy === 'save' ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <Save className="h-4 w-4" aria-hidden />
            )}
            Save draft
          </Button>
          <Button
            variant="secondary"
            onClick={() => void handlePublish()}
            disabled={busy !== null || strategy.current_version === 0}
            title={
              strategy.current_version === 0
                ? 'Save a draft version first'
                : 'Publish the saved definition (immutable)'
            }
          >
            <ShieldCheck className="h-4 w-4" aria-hidden /> Publish…
          </Button>
          <Button variant="outline" onClick={() => void handleDuplicate()} disabled={busy !== null}>
            <Copy className="h-4 w-4" aria-hidden /> Duplicate
          </Button>
          <Button variant="outline" onClick={() => void handleArchive()} disabled={busy !== null}>
            <Archive className="h-4 w-4" aria-hidden /> Archive
          </Button>
        </CardContent>
      </Card>

      <VersionHistory versions={versions} />

      {/* Publish preview ------------------------------------------------------ */}
      <AlertDialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <AlertDialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-2xl">
          <AlertDialogHeader>
            <AlertDialogTitle>Review before publishing</AlertDialogTitle>
            <AlertDialogDescription>
              Publishing stores this definition as an immutable version. Historical
              analyses will keep referencing the version they were evaluated with.
              {dirty && ' Your unsaved changes will be saved as a new draft version first.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <pre className="max-h-72 overflow-auto rounded-md bg-muted p-3 font-mono text-xs" data-testid="publish-preview">
            {JSON.stringify(ruleSet, null, 2)}
          </pre>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => void confirmPublish()}>
              Save &amp; publish version
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Unsaved-changes guard ------------------------------------------------ */}
      <AlertDialog open={leaveConfirmOpen} onOpenChange={setLeaveConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
            <AlertDialogDescription>
              You have edits that are not saved as a version. Leaving now will
              discard them.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep editing</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setLeaveConfirmOpen(false)
                onClose()
              }}
            >
              Discard changes
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Small building blocks
// --------------------------------------------------------------------------- //

function StatusBadges({ strategy }: { strategy: StrategyDetail }) {
  return (
    <>
      {strategy.published_version_number !== null && (
        <Badge variant="outline" className="border-emerald-600/40 text-emerald-700 dark:text-emerald-400">
          published v{strategy.published_version_number}
        </Badge>
      )}
      <Badge variant="outline" className="capitalize">
        {strategy.status}
      </Badge>
    </>
  )
}

function SymbolsInput({
  symbols,
  onChange,
  errors,
}: {
  symbols: string[]
  onChange: (symbols: string[]) => void
  errors: RuleSetValidation['errors']
}) {
  const [draft, setDraft] = useState('')
  function add() {
    const value = draft.trim().toUpperCase()
    if (!value || symbols.includes(value)) return
    onChange([...symbols, value])
    setDraft('')
  }
  return (
    <div className="space-y-1.5">
      <Label htmlFor="sb-symbols">Symbol scope (optional)</Label>
      <div className="flex gap-2">
        <Input
          id="sb-symbols"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              add()
            }
          }}
          placeholder="e.g. EURUSD (empty = any symbol)"
        />
        <Button size="icon" variant="outline" aria-label="Add symbol" onClick={add}>
          <Plus className="h-4 w-4" aria-hidden />
        </Button>
      </div>
      {symbols.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {symbols.map((s) => (
            <Badge key={s} variant="secondary" className="gap-1 font-mono">
              {s}
              <button
                type="button"
                aria-label={`Remove symbol ${s}`}
                onClick={() => onChange(symbols.filter((x) => x !== s))}
                className="ml-0.5 rounded-full hover:bg-muted"
              >
                <X className="h-3 w-3" aria-hidden />
              </button>
            </Badge>
          ))}
        </div>
      )}
      {errors.map((e, i) => (
        <p key={i} className="text-xs text-destructive">{e.message}</p>
      ))}
    </div>
  )
}

function CategorySelect({
  categories,
  value,
  mode,
  onSelected,
  onChanged,
}: {
  categories: Category[]
  value: string | null
  mode: PlatformMode
  onSelected: (id: string | null) => void
  onChanged: () => Promise<void> | void
}) {
  const [createOpen, setCreateOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)

  async function create() {
    if (!newName.trim()) return
    setBusy(true)
    try {
      const created = await api.post<Category>('/api/v1/strategy-categories', {
        name: newName.trim(),
        mode,
      })
      await onChanged()
      onSelected(created.id)
      setCreateOpen(false)
      setNewName('')
      toast({ title: 'Category created', description: created.name })
    } catch (e) {
      toast({ title: 'Could not create category', description: errorText(e), variant: 'destructive' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Select
        value={value ?? 'none'}
        onValueChange={(v) => {
          if (v === '__create') {
            setCreateOpen(true)
            return
          }
          onSelected(v === 'none' ? null : v)
        }}
      >
        <SelectTrigger aria-label="Category">
          <SelectValue placeholder="None" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="none">None</SelectItem>
          {categories.map((c) => (
            <SelectItem key={c.id} value={c.id}>
              {c.name}
            </SelectItem>
          ))}
          <SelectSeparator />
          <SelectItem value="__create">＋ New category…</SelectItem>
        </SelectContent>
      </Select>
      <CategoryCreateDialog
        open={createOpen}
        onOpenChange={(o) => {
          setCreateOpen(o)
          if (!o) setNewName('')
        }}
        title="New category"
        name={newName}
        setName={setNewName}
        busy={busy}
        onCreate={() => void create()}
      />
    </>
  )
}

function SubcategorySelect({
  parent,
  subcategories,
  value,
  disabled,
  onSelected,
  onChanged,
}: {
  parent: Category | null
  subcategories: Category[]
  value: string | null
  disabled: boolean
  onSelected: (id: string | null) => void
  onChanged: () => Promise<void> | void
}) {
  const [createOpen, setCreateOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)

  async function create() {
    if (!newName.trim() || !parent) return
    setBusy(true)
    try {
      const created = await api.post<Category>('/api/v1/strategy-categories', {
        name: newName.trim(),
        mode: parent.mode,
        parent_id: parent.id,
      })
      await onChanged()
      onSelected(created.id)
      setCreateOpen(false)
      setNewName('')
      toast({ title: 'Subcategory created', description: created.name })
    } catch (e) {
      toast({ title: 'Could not create subcategory', description: errorText(e), variant: 'destructive' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Select
        value={value === parent?.id ? 'root' : (value ?? 'none')}
        disabled={disabled}
        onValueChange={(v) => {
          if (v === '__create') {
            setCreateOpen(true)
            return
          }
          onSelected(v === 'none' || v === 'root' ? (v === 'root' ? parent?.id ?? null : null) : v)
        }}
      >
        <SelectTrigger aria-label="Subcategory">
          <SelectValue placeholder={disabled ? 'Pick a category first' : 'None'} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="root">— Parent category only —</SelectItem>
          <SelectItem value="none">None</SelectItem>
          {subcategories.map((c) => (
            <SelectItem key={c.id} value={c.id}>
              {c.name}
            </SelectItem>
          ))}
          {!disabled && <SelectSeparator />}
          {!disabled && <SelectItem value="__create">＋ New subcategory…</SelectItem>}
        </SelectContent>
      </Select>
      <CategoryCreateDialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open)
          if (!open) setNewName('')
        }}
        title={`New subcategory of ${parent?.name ?? ''}`}
        name={newName}
        setName={setNewName}
        busy={busy}
        onCreate={() => void create()}
      />
    </>
  )
}

function CategoryCreateDialog({
  open,
  onOpenChange,
  title,
  name,
  setName,
  busy,
  onCreate,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  name: string
  setName: (v: string) => void
  busy: boolean
  onCreate: () => void
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>
            Categories organise your strategies per analysis mode.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <Input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Category name"
          aria-label="Category name"
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              onCreate()
            }
          }}
        />
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={onCreate} disabled={busy || !name.trim()}>
            {busy && <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />} Create
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function VersionHistory({ versions }: { versions: StrategyVersion[] }) {
  if (versions.length === 0) return null
  const statusVariant = (s: StrategyVersion['status']) =>
    s === 'published'
      ? 'border-emerald-600/40 text-emerald-700 dark:text-emerald-400'
      : s === 'superseded'
        ? 'border-muted-foreground/30 text-muted-foreground'
        : 'border-amber-600/40 text-amber-700 dark:text-amber-400'
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Version history</CardTitle>
        <CardDescription>
          Every save creates an immutable version. Published definitions never change.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="divide-y text-sm">
          {versions.map((v) => (
            <li key={v.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span className="flex items-center gap-2 font-mono text-xs">
                v{v.version}
                <Badge variant="outline" className={statusVariant(v.status)}>
                  {v.status}
                </Badge>
              </span>
              <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                {v.changelog ?? '—'}
              </span>
              <span className="shrink-0 text-xs text-muted-foreground">
                {new Date(v.created_at).toISOString().slice(0, 16).replace('T', ' ')} UTC
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

// --------------------------------------------------------------------------- //
// Rule tree editing
// --------------------------------------------------------------------------- //

interface RuleSectionProps {
  title: string
  description: string
  path: string
  ruleSet: RuleSet
  setRuleSet: (updater: (rs: RuleSet) => RuleSet) => void
  setGroupAt: (root: RuleSet, path: string, updater: (g: RuleGroup) => RuleGroup) => RuleSet
  timeframes: string[]
  availableTypes: RuleTypeSpec[]
  plannedTypes: RuleTypeSpec[]
  typeBySlug: Map<string, RuleTypeSpec>
  errors: RuleSetValidation['errors']
  clearable?: boolean
}

function RuleSectionCard(props: RuleSectionProps) {
  const { ruleSet, setRuleSet, setGroupAt, path, clearable } = props
   
  const group = path
    .replace(/^\./, '')
    .split('.')
     
    .reduce<any>((node, key) => {
      const m = key.match(/^([a-zA-Z_]+)(?:\[(\d+)\])?$/)
      if (!m || node === undefined) return node
      let next = node?.[m[1]]
      if (m[2] !== undefined) next = next?.[Number(m[2])]
      return next
    }, ruleSet) as RuleGroup | null

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <CardTitle className="text-base">{props.title}</CardTitle>
            <CardDescription>{props.description}</CardDescription>
          </div>
          {clearable && group && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setRuleSet((rs) => setGroupAt(rs, path, () => null as unknown as RuleGroup))}
              aria-label={`Remove ${props.title.toLowerCase()} rules`}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden /> Remove
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {group ? (
          <GroupEditor {...props} group={group} />
        ) : (
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              setRuleSet((rs) =>
                setGroupAt(rs, path, () => emptyGroup('AND') as unknown as RuleGroup),
              )
            }
          >
            <Plus className="h-4 w-4" aria-hidden /> Add rules
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

function GroupEditor({
  group,
  path,
  ruleSet,
  setRuleSet,
  setGroupAt,
  timeframes,
  availableTypes,
  plannedTypes,
  typeBySlug,
  errors,
  depth = 0,
}: RuleSectionProps & { group: RuleGroup; depth?: number }) {
  const prefix = path
  const groupErrors = errorsForPrefix(errors, prefix).filter((e) => e.path === prefix)

  function patchGroup(updater: (g: RuleGroup) => RuleGroup) {
    setRuleSet((rs) => setGroupAt(rs, path, updater))
  }

  function addCondition() {
    patchGroup((g) => ({
      ...g,
      conditions: [
        ...g.conditions,
        {
          id: nextConditionId(),
          type: availableTypes[0]?.type ?? 'price_level',
          timeframe: timeframes[0] ?? 'H1',
          lookback: availableTypes[0]?.default_lookback ?? 20,
          params: {},
        },
      ],
    }))
  }

  return (
    <div className="space-y-3 rounded-md border p-3" data-testid={`group${path}`}>
      {/* Logic toggle */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div
          className="flex overflow-hidden rounded-md border"
          role="group"
          aria-label="Group logic"
        >
          {(['AND', 'OR'] as const).map((logic) => (
            <button
              key={logic}
              type="button"
              aria-pressed={group.logic === logic}
              onClick={() => patchGroup((g) => ({ ...g, logic }))}
              className={`px-3 py-1 text-xs font-semibold transition-colors ${
                group.logic === logic
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-background text-muted-foreground hover:bg-muted'
              }`}
            >
              {logic}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1">
          <Button size="sm" variant="outline" onClick={addCondition}>
            <Plus className="h-3.5 w-3.5" aria-hidden /> Rule
          </Button>
          {depth < 3 && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => patchGroup((g) => ({ ...g, groups: [...g.groups, emptyGroup('OR')] }))}
            >
              <Plus className="h-3.5 w-3.5" aria-hidden /> Group
            </Button>
          )}
        </div>
      </div>

      {groupErrors.map((e, i) => (
        <p key={i} className="text-xs text-destructive">{e.message}</p>
      ))}

      {/* Conditions */}
      <div className="space-y-2">
        {group.conditions.map((cond, index) => {
          const condPath = `${prefix}.conditions[${index}]`
          return (
            <ConditionCard
              key={cond.id}
              condition={cond}
              path={condPath}
              index={index}
              total={group.conditions.length}
              timeframes={timeframes}
              availableTypes={availableTypes}
              plannedTypes={plannedTypes}
              typeBySlug={typeBySlug}
              errors={errors}
              onChange={(next) =>
                patchGroup((g) => ({
                  ...g,
                  conditions: g.conditions.map((c, i) => (i === index ? next : c)),
                }))
              }
              onRemove={() =>
                patchGroup((g) => ({
                  ...g,
                  conditions: g.conditions.filter((_, i) => i !== index),
                }))
              }
              onMove={(dir) =>
                patchGroup((g) => {
                  const next = [...g.conditions]
                  const target = index + dir
                  if (target < 0 || target >= next.length) return g
                  ;[next[index], next[target]] = [next[target], next[index]]
                  return { ...g, conditions: next }
                })
              }
            />
          )
        })}
      </div>

      {/* Nested groups */}
      <div className="space-y-2">
        {group.groups.map((nested, i) => (
          <GroupEditor
            key={`${path}.groups[${i}]`}
            {...({
              ruleSet,
              setRuleSet,
              setGroupAt,
              timeframes,
              availableTypes,
              plannedTypes,
              typeBySlug,
              errors,
            } as RuleSectionProps)}
            group={nested}
            path={`${path}.groups[${i}]`}
            depth={depth + 1}
          />
        ))}
      </div>
    </div>
  )
}

function ConditionCard({
  condition,
  path,
  index,
  total,
  timeframes,
  availableTypes,
  plannedTypes,
  typeBySlug,
  errors,
  onChange,
  onRemove,
  onMove,
}: {
  condition: RuleCondition
  path: string
  index: number
  total: number
  timeframes: string[]
  availableTypes: RuleTypeSpec[]
  plannedTypes: RuleTypeSpec[]
  typeBySlug: Map<string, RuleTypeSpec>
  errors: RuleSetValidation['errors']
  onChange: (next: RuleCondition) => void
  onRemove: () => void
  onMove: (dir: -1 | 1) => void
}) {
  const spec = typeBySlug.get(condition.type)
  const ownErrors = errorsForPrefix(errors, path)
  const paramErrors = (key: string) =>
    ownErrors.filter((e) => e.path === `${path}.params.${key}`)
  const generalErrors = ownErrors.filter((e) => !e.path.includes('.params.'))

  function changeType(type: string) {
    const nextSpec = typeBySlug.get(type)
    onChange({
      ...condition,
      type,
      lookback: nextSpec?.default_lookback ?? condition.lookback,
      params: {},
    })
  }

  function setParam(key: string, raw: string, schemaType: string) {
    let value: unknown
    if (schemaType === 'integer') value = raw === '' ? undefined : parseInt(raw, 10)
    else if (schemaType === 'number') value = raw === '' ? undefined : parseFloat(raw)
    else if (schemaType === 'boolean') value = raw === 'true'
    else value = raw
    const params = { ...condition.params }
    if (value === undefined || value === null || Number.isNaN(value as number)) {
      delete params[key]
    } else {
      params[key] = value
    }
    onChange({ ...condition, params })
  }

  return (
    <div className="rounded-md border bg-background p-3" data-condition-id={condition.id}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Select value={condition.type} onValueChange={changeType}>
            <SelectTrigger className="h-8 w-56" aria-label={`Rule type of ${condition.id}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {availableTypes.map((t) => (
                <SelectItem key={t.type} value={t.type}>
                  {t.title}
                </SelectItem>
              ))}
              <SelectSeparator />
              {plannedTypes.map((t) => (
                <SelectItem key={t.type} value={t.type} disabled>
                  {t.title} — planned
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Badge variant="outline" className="font-mono text-[10px]">
            {condition.id}
          </Badge>
        </div>
        <TooltipProvider delayDuration={200}>
          <div className="flex items-center gap-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-7 w-7"
                  aria-label="Move rule up"
                  disabled={index === 0}
                  onClick={() => onMove(-1)}
                >
                  <ChevronUp className="h-3.5 w-3.5" aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Rule order (earlier first)</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-7 w-7"
                  aria-label="Move rule down"
                  disabled={index === total - 1}
                  onClick={() => onMove(1)}
                >
                  <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Rule order (later last)</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-7 w-7"
                  aria-label={`Remove rule ${condition.id}`}
                  onClick={onRemove}
                >
                  <Trash2 className="h-3.5 w-3.5 text-destructive" aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Remove rule</TooltipContent>
            </Tooltip>
          </div>
        </TooltipProvider>
      </div>

      <div className="grid gap-2 sm:grid-cols-[1fr_2fr]">
        <div className="space-y-2">
          <div className="space-y-1">
            <Label htmlFor={`${condition.id}-tf`} className="text-xs">Timeframe</Label>
            <Select
              value={condition.timeframe}
              onValueChange={(v) => onChange({ ...condition, timeframe: v })}
            >
              <SelectTrigger id={`${condition.id}-tf`} className="h-8" aria-label="Rule timeframe">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {timeframes.map((tf) => (
                  <SelectItem key={tf} value={tf}>
                    {tf}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {ownErrors.some((e) => e.path === `${path}.timeframe`) && (
              <p className="text-xs text-destructive">
                {ownErrors.find((e) => e.path === `${path}.timeframe`)?.message}
              </p>
            )}
          </div>
          <div className="space-y-1">
            <Label htmlFor={`${condition.id}-lb`} className="text-xs">
              Lookback{spec ? ` (${spec.default_lookback} default)` : ''}
            </Label>
            <Input
              id={`${condition.id}-lb`}
              type="number"
              min={1}
              max={500}
              className="h-8"
              value={condition.lookback}
              onChange={(e) =>
                onChange({ ...condition, lookback: parseInt(e.target.value, 10) || 1 })
              }
            />
          </div>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <Checkbox
              checked={condition.optional ?? false}
              onCheckedChange={(v) => onChange({ ...condition, optional: v === true })}
              aria-label={`Rule ${condition.id} optional`}
            />
            Optional (never blocks)
          </label>
        </div>

        <div className="space-y-2">
          {!spec && (
            <p className="flex items-center gap-1.5 text-xs text-destructive">
              <FileWarning className="h-3.5 w-3.5" aria-hidden /> Unknown rule type — pick one from the list.
            </p>
          )}
          {spec &&
            Object.entries(spec.params_schema).map(([key, schema]) => (
              <div key={key} className="space-y-1">
                <Label htmlFor={`${condition.id}-${key}`} className="text-xs">
                  {key}
                  {spec.required_params.includes(key) && <span className="text-destructive"> *</span>}
                </Label>
                {schema.enum ? (
                  <Select
                    value={String(condition.params[key] ?? '')}
                    onValueChange={(v) => setParam(key, v, 'string')}
                  >
                    <SelectTrigger id={`${condition.id}-${key}`} className="h-8" aria-label={key}>
                      <SelectValue placeholder={`Select ${key.replace(/_/g, ' ')}`} />
                    </SelectTrigger>
                    <SelectContent>
                      {schema.enum.map((opt) => (
                        <SelectItem key={opt} value={opt}>
                          {opt.replace(/_/g, ' ')}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : schema.type === 'boolean' ? (
                  <label className="flex items-center gap-2 text-sm">
                    <Checkbox
                      id={`${condition.id}-${key}`}
                      checked={condition.params[key] === true}
                      onCheckedChange={(v) => setParam(key, v === true ? 'true' : '', 'boolean')}
                      aria-label={key}
                    />
                    <span className="text-xs text-muted-foreground">enabled</span>
                  </label>
                ) : (
                  <Input
                    id={`${condition.id}-${key}`}
                    type="number"
                    step="any"
                    className="h-8"
                    value={condition.params[key] === undefined ? '' : String(condition.params[key])}
                    onChange={(e) => setParam(key, e.target.value, schema.type)}
                    aria-label={key}
                  />
                )}
                {schema.description && (
                  <p className="text-[11px] leading-snug text-muted-foreground">{schema.description}</p>
                )}
                {paramErrors(key).map((e, i) => (
                  <p key={i} className="text-xs text-destructive">{e.message}</p>
                ))}
              </div>
            ))}
          {generalErrors.map((e, i) => (
            <p key={i} className="text-xs text-destructive">{e.message}</p>
          ))}
        </div>
      </div>
    </div>
  )
}
