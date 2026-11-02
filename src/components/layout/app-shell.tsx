'use client'

import { Activity } from 'lucide-react'
import { ModeSwitch } from './mode-switch'
import { ThemeToggle } from './theme-toggle'
import type { PlatformMode } from '@/lib/api/types'

interface AppShellProps {
  mode: PlatformMode
  onModeChange: (mode: PlatformMode) => void
  children: React.ReactNode
}

/**
 * Application chrome: sticky header, main content area and a footer that
 * sticks to the bottom of the viewport on short pages (min-h-dvh flex).
 */
export function AppShell({ mode, onModeChange, children }: AppShellProps) {
  return (
    <div className="flex min-h-dvh flex-col bg-background text-foreground">
      <header className="sticky top-0 z-40 border-b bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="mx-auto flex w-full max-w-7xl items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <Activity className="h-4 w-4" aria-hidden />
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold leading-tight">
                TA Workbench
              </p>
              <p className="truncate text-xs text-muted-foreground leading-tight">
                User-defined technical analysis — Forex &amp; Binary Options
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <ModeSwitch value={mode} onChange={onModeChange} />
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">
        {children}
      </main>

      <footer className="mt-auto border-t">
        <div className="mx-auto flex w-full max-w-7xl flex-col gap-1 px-4 py-4 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <p>
            Phase 7 · AI-assisted strategy analysis &amp; explainable research ·
            No order execution
          </p>
          <p>Market data via MetaTrader 5 · Strategies are never executed automatically</p>
        </div>
      </footer>
    </div>
  )
}
