'use client'

/**
 * Symbol selector populated from the backend (`GET /api/v1/market/symbols`).
 *
 * - typing queries the terminal's symbol list (debounced, bounded);
 * - a manual entry is always possible (some brokers suffix symbols, and the
 *   selector must stay usable when the backend is temporarily unreachable —
 *   the feed then reports "Not connected" honestly);
 * - no symbols are ever invented locally.
 */

import { useEffect, useId, useRef, useState } from 'react'
import { ChevronDown, Loader2, Search } from 'lucide-react'
import { Label } from '@/components/ui/label'
import { api } from '@/lib/api/client'
import type { SymbolList } from '@/lib/api/types'
import { cn } from '@/lib/utils'

export interface SymbolSelectProps {
  value: string
  onChange: (symbol: string) => void
  disabled?: boolean
}

export function SymbolSelect({ value, onChange, disabled }: SymbolSelectProps) {
  const [draft, setDraft] = useState(value)
  const [open, setOpen] = useState(false)
  const [searching, setSearching] = useState(false)
  const [results, setResults] = useState<SymbolList | null>(null)
  const [loadError, setLoadError] = useState(false)
  const boxRef = useRef<HTMLDivElement | null>(null)
  const listId = useId()

  // Derived-state adjustment during render (React's recommended pattern
  // for "reset state when a prop changes") — no cascading effect renders.
  const [prevValue, setPrevValue] = useState(value)
  if (prevValue !== value) {
    setPrevValue(value)
    setDraft(value)
  }

  // Debounced backend lookup (setState only inside async callbacks).
  useEffect(() => {
    if (!open) return
    const term = draft.trim()
    const timer = setTimeout(
      () => {
        if (term.length < 1) {
          setResults(null)
          setLoadError(false)
          setSearching(false)
          return
        }
        setSearching(true)
        api
          .get<SymbolList>('/api/v1/market/symbols', { search: `${term}*`, limit: '20' })
          .then((list) => {
            setResults(list)
            setLoadError(false)
          })
          .catch(() => {
            setResults(null)
            setLoadError(true)
          })
          .finally(() => setSearching(false))
      },
      term.length < 1 ? 0 : 250,
    )
    return () => clearTimeout(timer)
  }, [draft, open])

  // Close on outside click.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const suggestions = (results?.symbols ?? [])
    .filter((s) => s.selectable !== false)
    .slice(0, 12)

  return (
    <div className="relative" ref={boxRef}>
      <Label htmlFor="dashboard-symbol" className="text-xs text-muted-foreground">
        Symbol
      </Label>
      <div className="relative flex items-center">
        <Search
          className="pointer-events-none absolute left-2 h-3.5 w-3.5 text-muted-foreground"
          aria-hidden
        />
        <input
          id="dashboard-symbol"
          value={draft}
          disabled={disabled}
          autoComplete="off"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-label="Symbol (type to search the terminal symbol list)"
          spellCheck={false}
          autoCapitalize="characters"
          className={cn(
            'h-9 w-36 rounded-md border bg-transparent pl-7 pr-7 font-mono text-sm',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50',
          )}
          onChange={(e) => {
            setDraft(e.target.value.toUpperCase())
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              const next = draft.trim().toUpperCase()
              if (next) {
                onChange(next)
                setOpen(false)
              }
            }
            if (e.key === 'Escape') setOpen(false)
          }}
        />
        <button
          type="button"
          aria-label="Toggle symbol suggestions"
          className="absolute right-1.5 rounded p-1 text-muted-foreground hover:bg-muted"
          onClick={() => setOpen((o) => !o)}
          disabled={disabled}
        >
          {searching ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" aria-hidden />
          )}
        </button>
      </div>

      {open && (
        <div
          id={listId}
          role="listbox"
          aria-label="Symbol suggestions"
          className="absolute z-50 mt-1 w-72 rounded-md border bg-popover p-1 shadow-md"
        >
          {!results && !loadError && (
            <p className="px-2 py-1.5 text-xs text-muted-foreground">
              Type at least one character to search the terminal symbols.
            </p>
          )}
          {loadError && (
            <p className="px-2 py-1.5 text-xs text-muted-foreground">
              Symbol list is unavailable (backend unreachable). Press Enter to use
              the typed symbol — the data feed will report honestly if it is
              unknown to the terminal.
            </p>
          )}
          {results && suggestions.length === 0 && (
            <p className="px-2 py-1.5 text-xs text-muted-foreground">
              No terminal symbols match “{draft}”. Press Enter to try it anyway.
            </p>
          )}
          <ul>
            {suggestions.map((s) => (
              <li key={s.name}>
                <button
                  type="button"
                  role="option"
                  aria-selected={s.name === value}
                  className={cn(
                    'flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-muted',
                    s.name === value && 'bg-muted',
                  )}
                  onClick={() => {
                    onChange(s.name)
                    setDraft(s.name)
                    setOpen(false)
                  }}
                >
                  <span className="font-mono font-medium">{s.name}</span>
                  <span className="min-w-0 truncate text-xs text-muted-foreground">
                    {s.description ?? ''}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
