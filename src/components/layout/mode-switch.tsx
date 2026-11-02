'use client'

import { CandlestickChart } from 'lucide-react'
import { MODE_LABELS } from '@/lib/platform/modes'
import type { PlatformMode } from '@/lib/api/types'
import { cn } from '@/lib/utils'

interface ModeSwitchProps {
  value: PlatformMode
  onChange: (mode: PlatformMode) => void
  disabled?: boolean
}

/**
 * Forex / Binary Options analysis mode switch.
 * The two modes are strictly separated end-to-end (API filtering, rule
 * validation and default timeframes all respect this selection).
 */
export function ModeSwitch({ value, onChange, disabled }: ModeSwitchProps) {
  return (
    <div
      role="radiogroup"
      aria-label="Analysis mode"
      className="inline-flex items-center rounded-lg border bg-muted/40 p-0.5"
    >
      <CandlestickChart className="mx-1.5 h-3.5 w-3.5 text-muted-foreground" aria-hidden />
      {(Object.keys(MODE_LABELS) as PlatformMode[]).map((mode) => (
        <button
          key={mode}
          role="radio"
          aria-checked={value === mode}
          disabled={disabled}
          onClick={() => onChange(mode)}
          className={cn(
            'rounded-md px-3 py-1 text-xs font-medium transition-colors disabled:opacity-50',
            value === mode
              ? 'bg-background text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground'
          )}
        >
          {MODE_LABELS[mode]}
        </button>
      ))}
    </div>
  )
}
