'use client'

/**
 * Four-state condition sections.
 *
 * Flattens the evaluation tree of a report into the four REQUIRED sections:
 * conditions met / not met / inconclusive / unsupported. Inconclusive and
 * unsupported conditions are never rendered as success — each state has its
 * own accessible icon + colour treatment (mirrors the engine panel styles).
 */

import { useState } from 'react'
import { Ban, CheckCircle2, ChevronDown, ChevronRight, HelpCircle, XCircle } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import type {
  ConditionStatus,
  EvaluationNode,
} from '@/lib/api/types'
import { cn } from '@/lib/utils'

interface FlatCondition {
  id: string
  description: string
  status: ConditionStatus
  timeframe?: string
  reason?: string
  section: 'core' | 'confirmation' | 'invalidation'
}

function statusOf(node: EvaluationNode): ConditionStatus {
  return node.status ?? (node.satisfied ? 'met' : 'not_met')
}

function flatten(node: EvaluationNode, section: FlatCondition['section'], out: FlatCondition[]): void {
  if (node.kind === 'condition') {
    out.push({
      id: node.id,
      description: node.description,
      status: statusOf(node),
      timeframe: node.evidence?.timeframe,
      reason: node.evidence?.reason ?? node.evidence?.status_reason,
      section,
    })
  }
  for (const child of node.children ?? []) flatten(child, section, out)
}

export function collectConditions(report: {
  evaluation: { core: EvaluationNode; confirmation: EvaluationNode | null; invalidation: EvaluationNode | null }
}): FlatCondition[] {
  const out: FlatCondition[] = []
  flatten(report.evaluation.core, 'core', out)
  if (report.evaluation.confirmation) flatten(report.evaluation.confirmation, 'confirmation', out)
  if (report.evaluation.invalidation) flatten(report.evaluation.invalidation, 'invalidation', out)
  return out
}

const SECTIONS: {
  status: ConditionStatus
  title: string
  blurb: string
  icon: React.ReactNode
  accent: string
}[] = [
  {
    status: 'met',
    title: 'Conditions met',
    blurb: 'Evaluated against the data and satisfied.',
    icon: <CheckCircle2 className="h-4 w-4 text-emerald-600 dark:text-emerald-400" aria-hidden />,
    accent: 'text-emerald-700 dark:text-emerald-400',
  },
  {
    status: 'not_met',
    title: 'Conditions not met',
    blurb: 'Evaluated against the data and NOT satisfied.',
    icon: <XCircle className="h-4 w-4 text-red-500" aria-hidden />,
    accent: 'text-red-600 dark:text-red-400',
  },
  {
    status: 'inconclusive',
    title: 'Inconclusive',
    blurb: 'Could not be evaluated (missing/insufficient data) — this is NOT a failure and NOT a pass.',
    icon: <HelpCircle className="h-4 w-4 text-amber-600 dark:text-amber-400" aria-hidden />,
    accent: 'text-amber-700 dark:text-amber-400',
  },
  {
    status: 'unsupported',
    title: 'Unsupported',
    blurb: 'Rule type has no engine implementation — never treated as success.',
    icon: <Ban className="h-4 w-4 text-slate-500 dark:text-slate-400" aria-hidden />,
    accent: 'text-slate-600 dark:text-slate-400',
  },
]

function Section({
  meta,
  items,
  defaultOpen,
}: {
  meta: (typeof SECTIONS)[number]
  items: FlatCondition[]
  defaultOpen: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-lg border" data-testid={`conditions-${meta.status}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left hover:bg-muted/40"
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        )}
        {meta.icon}
        <span className={cn('text-sm font-semibold', meta.accent)}>{meta.title}</span>
        <Badge variant="outline" className="ml-auto font-mono text-[10px]">
          {items.length}
        </Badge>
      </button>
      {open && (
        <div className="border-t px-3 py-2">
          <p className="mb-2 text-xs text-muted-foreground">{meta.blurb}</p>
          {items.length === 0 ? (
            <p className="text-xs text-muted-foreground">No conditions in this state.</p>
          ) : (
            <ul className="space-y-1.5">
              {items.map((c) => (
                <li key={`${c.section}-${c.id}`} className="rounded-md bg-muted/40 px-2.5 py-1.5">
                  <p className="text-sm">
                    <span className="mr-1.5 font-mono text-[10px] text-muted-foreground">
                      {c.id}
                    </span>
                    {c.description}
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {c.timeframe ? `timeframe ${c.timeframe}` : ''}
                    {c.section !== 'core' ? ` · ${c.section} rule` : ''}
                    {c.reason ? ` — ${c.reason}` : ''}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

export function ConditionSections({
  report,
}: {
  report: {
    evaluation: { core: EvaluationNode; confirmation: EvaluationNode | null; invalidation: EvaluationNode | null }
  }
}) {
  const conditions = collectConditions(report)
  return (
    <div className="space-y-2" data-testid="condition-sections">
      {SECTIONS.map((meta, i) => (
        <Section
          key={meta.status}
          meta={meta}
          items={conditions.filter((c) => c.status === meta.status)}
          defaultOpen={i < 2}
        />
      ))}
    </div>
  )
}
