'use client'

import { Moon, Sun } from 'lucide-react'
import { useTheme } from 'next-themes'
import { useSyncExternalStore } from 'react'
import { Button } from '@/components/ui/button'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'

const emptySubscribe = () => () => {}

/**
 * True only after hydration on the client. Server snapshot stays false so the
 * first client render matches SSR exactly (no hydration mismatch from
 * theme-dependent markup), then React re-renders with the client snapshot.
 */
function useMounted(): boolean {
  return useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false
  )
}

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme()
  const mounted = useMounted()
  const isDark = resolvedTheme === 'dark'

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="outline"
            size="icon"
            aria-label={
              !mounted
                ? 'Toggle dark / light mode'
                : isDark
                  ? 'Switch to light mode'
                  : 'Switch to dark mode'
            }
            onClick={() => setTheme(isDark ? 'light' : 'dark')}
          >
            <Sun className="h-4 w-4 rotate-0 scale-100 transition-transform dark:-rotate-90 dark:scale-0" />
            <Moon className="absolute h-4 w-4 rotate-90 scale-0 transition-transform dark:rotate-0 dark:scale-100" />
          </Button>
        </TooltipTrigger>
        <TooltipContent>Toggle dark / light</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
