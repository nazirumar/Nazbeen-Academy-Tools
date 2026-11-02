'use client'

/**
 * Overlay visibility controls.
 *
 * Only overlay kinds that the CURRENT analysis report actually contains can
 * be switched on. Kinds with zero features are listed as unavailable for
 * this report; concepts the engine cannot detect at all (planned rule
 * types) are shown as such — a toggle that draws nothing must not exist.
 */

import { AlertCircle, Layers } from 'lucide-react'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import {
  ALL_OVERLAY_KINDS,
  OVERLAY_KIND_LABELS,
  countOverlaysByKind,
  type OverlayCatalog,
  type OverlayKind,
} from '@/lib/chart/overlay-model'
import { cn } from '@/lib/utils'

export interface OverlayControlsProps {
  catalog: OverlayCatalog
  visible: Record<OverlayKind, boolean>
  onToggle: (kind: OverlayKind, value: boolean) => void
  hasReport: boolean
}

export function OverlayControls({ catalog, visible, onToggle, hasReport }: OverlayControlsProps) {
  const counts = countOverlaysByKind(catalog)

  return (
    <div className="space-y-2" data-testid="overlay-controls">
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Layers className="h-3.5 w-3.5" aria-hidden />
        Overlays come only from the selected analysis report — the chart never
        draws a concept the engine did not return.
      </p>
      {!hasReport && (
        <p className="rounded-md bg-muted/50 px-2.5 py-1.5 text-xs text-muted-foreground">
          Run or open an analysis to enable overlays.
        </p>
      )}
      <ul className="space-y-1.5">
        {ALL_OVERLAY_KINDS.map((kind) => {
          const count = counts[kind]
          const disabled = !hasReport || count === 0
          return (
            <li key={kind} className="flex items-center justify-between gap-2">
              <Label
                htmlFor={`overlay-${kind}`}
                className={cn(
                  'text-sm',
                  disabled && 'text-muted-foreground/60',
                )}
              >
                {OVERLAY_KIND_LABELS[kind]}
                <span className="ml-1.5 font-mono text-[10px] text-muted-foreground">
                  {count}
                </span>
              </Label>
              <Switch
                id={`overlay-${kind}`}
                checked={hasReport && count > 0 && visible[kind]}
                disabled={disabled}
                onCheckedChange={(v) => onToggle(kind, v)}
                aria-label={`${OVERLAY_KIND_LABELS[kind]} (${count} in report)`}
              />
            </li>
          )
        })}
      </ul>
      {catalog.unsupportedKinds.length > 0 && (
        <div className="space-y-1 rounded-md border border-dashed p-2">
          {catalog.unsupportedKinds.map((u) => (
            <p
              key={u.kind}
              className="flex items-start gap-1.5 text-xs text-muted-foreground"
            >
              <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
              <span>
                <span className="font-medium">{u.kind.replace(/_/g, ' ')}:</span> {u.reason}
              </span>
            </p>
          ))}
        </div>
      )}
    </div>
  )
}
