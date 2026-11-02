'use client'

import { useCallback, useEffect, useState } from 'react'
import {
  BadgeCheck,
  BrainCircuit,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Eye,
  Loader2,
  ShieldQuestion,
  Sparkles,
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
import { api } from '@/lib/api/client'
import { ApiError } from '@/lib/api/types'
import type {
  AIExplanationResponse,
  AIStatus,
  CitedItem,
} from '@/lib/api/types'
import { cn } from '@/lib/utils'

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = e.details.length
      ? ` (${e.details.map((d) => `${d.path || ''} ${d.message ?? ''}`).join('; ')})`
      : ''
    return `${e.message}${detail}`
  }
  return e instanceof Error ? e.message : 'Unexpected error'
}

/** Render one cited assertion with its deterministic evidence refs. */
function CitedList({ items, emptyHint }: { items: CitedItem[]; emptyHint?: string }) {
  if (items.length === 0) {
    return emptyHint ? (
      <p className="text-xs text-muted-foreground">{emptyHint}</p>
    ) : null
  }
  return (
    <ul className="space-y-1.5">
      {items.map((item, i) => (
        <li key={i} className="rounded-md bg-muted/40 px-2 py-1.5 text-sm">
          <span>{item.text}</span>
          <span className="mt-1 flex flex-wrap gap-1">
            {item.refs.map((ref) => (
              <Badge
                key={ref}
                variant="outline"
                className="font-mono text-[10px] text-muted-foreground"
                title="Deterministic evidence reference from the analysis report"
              >
                <BadgeCheck className="mr-0.5 h-2.5 w-2.5" aria-hidden />
                {ref}
              </Badge>
            ))}
          </span>
        </li>
      ))}
    </ul>
  )
}

function StringList({ items, emptyHint }: { items: string[]; emptyHint?: string }) {
  if (items.length === 0) {
    return emptyHint ? <p className="text-xs text-muted-foreground">{emptyHint}</p> : null
  }
  return (
    <ul className="list-inside list-disc space-y-0.5 text-sm text-muted-foreground">
      {items.map((line, i) => (
        <li key={i}>{line}</li>
      ))}
    </ul>
  )
}

function Section({
  title,
  children,
  defaultOpen = true,
}: {
  title: string
  children: React.ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-md border">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground hover:bg-muted/30"
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5" aria-hidden />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" aria-hidden />
        )}
        {title}
      </button>
      {open && <div className="space-y-2 px-3 pb-3">{children}</div>}
    </div>
  )
}

export interface AIExplanationPanelProps {
  /** The persisted analysis-report id (row id == payload analysis_id). */
  analysisId: string | null
  /** Human-readable context of the report being explained. */
  reportLabel?: string
}

/**
 * Phase 7 AI explanation panel.
 *
 * Visually distinct from the deterministic report: violet accents, an
 * explicit "AI-generated interpretation" badge, and every factual item
 * carries the evidence refs it must be checked against. When the provider
 * is not configured the deterministic panel remains fully usable — this
 * panel simply explains why it is unavailable.
 */
export function AIExplanationPanel({ analysisId, reportLabel }: AIExplanationPanelProps) {
  const [status, setStatus] = useState<AIStatus | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<AIExplanationResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    try {
      api
        .get<AIStatus>('/api/v1/ai/status')
        .then((s) => {
          if (!cancelled) setStatus(s)
        })
        .catch((e) => {
          if (!cancelled) setStatusError(errorText(e))
        })
    } catch (e) {
      // Never let a status failure break the deterministic report view.
      if (!cancelled) setStatusError(errorText(e))
    }
    return () => {
      cancelled = true
    }
  }, [])

  const runExplanation = useCallback(async () => {
    if (!analysisId) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const explanation = await api.post<AIExplanationResponse>('/api/v1/ai/explanations', {
        analysis_id: analysisId,
      })
      setResult(explanation)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setLoading(false)
    }
  }, [analysisId])

  const unconfigured = status !== null && (!status.enabled || !status.provider_configured)

  return (
    <Card
      className="border-violet-500/40"
      data-testid="ai-explanation-panel"
    >
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <BrainCircuit className="h-4 w-4 text-violet-600 dark:text-violet-400" aria-hidden />
            AI explanation
          </CardTitle>
          <Badge
            variant="outline"
            className="border-violet-500/50 text-[10px] text-violet-700 dark:text-violet-300"
          >
            <Sparkles className="mr-1 h-3 w-3" aria-hidden /> AI-generated interpretation
          </Badge>
        </div>
        <CardDescription>
          Explains the deterministic report in plain language — every claim must
          carry a checkable evidence reference. The AI never evaluates rules,
          never changes strategy versions and never writes data.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {statusError && (
          <p className="rounded-md border border-amber-600/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-400" role="alert">
            AI status unavailable: {statusError}. The deterministic analysis remains fully usable.
          </p>
        )}

        {status && unconfigured && (
          <div className="rounded-md border bg-muted/30 px-3 py-3 text-sm" data-testid="ai-unconfigured">
            <p className="flex items-center gap-1.5 font-medium">
              <ShieldQuestion className="h-4 w-4" aria-hidden />
              AI explanations are unavailable — the provider is not configured.
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Set AI_ENABLED, AI_BASE_URL, AI_MODEL and AI_API_KEY (server-side) to
              enable this panel. The deterministic analysis engine is NOT affected
              and remains fully usable.
            </p>
          </div>
        )}

        {!result && !loading && !error && status !== null && !unconfigured && (
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => void runExplanation()} disabled={!analysisId} size="sm">
              <BrainCircuit className="h-4 w-4" aria-hidden />
              Explain this report with AI
            </Button>
            <p className="text-xs text-muted-foreground">
              {analysisId
                ? 'Explains the report currently shown above.'
                : 'Run or open an analysis first.'}
            </p>
          </div>
        )}

        {loading && (
          <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status" data-testid="ai-loading">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            Generating a grounded explanation…
          </p>
        )}

        {error && (
          <div className="rounded-md border border-red-600/40 bg-red-500/5 px-3 py-2 text-sm text-red-700 dark:text-red-400" role="alert" data-testid="ai-error">
            <p className="flex items-center gap-1.5 font-medium">
              <CircleAlert className="h-4 w-4" aria-hidden />
              Explanation unavailable: {error}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Failed answers are discarded (fail-closed) — nothing unvalidated is ever
              shown. The deterministic report above is unaffected.
            </p>
            {status !== null && !unconfigured && (
              <Button variant="outline" size="sm" className="mt-2" onClick={() => void runExplanation()}>
                Try again
              </Button>
            )}
          </div>
        )}

        {result && (
          <div className="space-y-3" data-testid="ai-explanation">
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <Badge variant="outline" className="font-mono">
                {result.provider.name} · {result.provider.model}
              </Badge>
              <span>generated {result.generated_at}</span>
              <Badge variant="outline" className="text-[10px]">
                {result.validation.checked_refs} evidence refs checked
              </Badge>
            </div>
            {reportLabel && (
              <p className="text-xs text-muted-foreground">Explaining: {reportLabel}</p>
            )}

            <Section title="Summary">
              <p className="text-sm">{result.explanation.summary}</p>
            </Section>

            <Section title="Observed market context">
              <p className="text-sm">{result.explanation.market_context}</p>
            </Section>

            <Section title="Conditions met">
              <CitedList items={result.explanation.conditions_met} emptyHint="None recorded." />
            </Section>

            <Section title="Conditions not met" defaultOpen={false}>
              <CitedList items={result.explanation.conditions_not_met} emptyHint="None recorded." />
            </Section>

            <Section title="Inconclusive or unsupported" defaultOpen={false}>
              <CitedList
                items={result.explanation.conditions_inconclusive_or_unsupported}
                emptyHint="None — unknown conditions are never treated as success."
              />
            </Section>

            <Section title="Supporting evidence" defaultOpen={false}>
              <CitedList items={result.explanation.evidence_support} />
            </Section>

            <Section title="Data-quality & invalidation notes" defaultOpen={false}>
              <StringList items={result.explanation.data_quality_notes} />
              <StringList items={result.explanation.invalidation_notes} />
            </Section>

            <Section title="Uncertainty & unavailable information" defaultOpen={false}>
              <StringList items={result.explanation.uncertainty} />
              <StringList items={result.explanation.unavailable_information} />
            </Section>

            <Section title="Suggested further observations" defaultOpen={false}>
              <StringList items={result.explanation.suggested_observations} emptyHint="None supplied." />
            </Section>

            <p className={cn('flex items-start gap-1.5 text-xs text-muted-foreground')}>
              <Eye className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
              {result.note} Schema, evidence-reference and numeric-traceability
              checks passed — that proves grounding to the supplied evidence, not
              factual market truth. Inspect the deterministic report above for the
              authoritative data.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
