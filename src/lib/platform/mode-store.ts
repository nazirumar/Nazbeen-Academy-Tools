/**
 * Mode selection store (Forex / Binary Options).
 *
 * Zustand + persist keeps the user's last-used analysis mode across visits.
 * Rehydration is manual (see page.tsx) so the first paint matches the server
 * render and hydration mismatches are impossible.
 */

import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import type { PlatformMode } from '@/lib/api/types'

interface ModeState {
  mode: PlatformMode
  setMode: (mode: PlatformMode) => void
}

export const useModeStore = create<ModeState>()(
  persist(
    (set) => ({
      mode: 'forex',
      setMode: (mode) => set({ mode }),
    }),
    {
      name: 'ta-workbench.mode',
      storage: createJSONStorage(() => localStorage),
      skipHydration: true,
    }
  )
)
